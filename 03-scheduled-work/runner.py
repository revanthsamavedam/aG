"""runner.py — a cron-friendly CLI for scheduled team work.

Not a cloud scheduler: this is a plain script you put in crontab.
crontab wakes it up, it decides what is due, runs it, logs it, exits.

    python3 runner.py --once            run every due job, then exit
    python3 runner.py --job <name>      run one job now, due or not
    python3 runner.py --list            show jobs and their next due time

Due-ness: a job is due when now - last_run >= interval_seconds.
last_run lives in state.json ({"last_run": {"<name>": <epoch>}}).
A job that has never run is due immediately.

Each run appends one JSON record to runs.jsonl:
    {"job", "started", "finished", "status", "steps",
     "reply_excerpt"} — status is "ok", "partial" (a specialist
    failed but the team finished), or "error" (the run itself raised).

Every run uses the fake client — no Azure. Swap FakeFoundryClient
for FoundryAgentClient + load_members() the same way demo.py in
01-team-of-agents does --real, when the agents are wired up.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_client import FakeFoundryClient, fake_members
from jobs import Job, load_jobs
from team import Team

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JOBS = os.path.join(_HERE, "jobs.json")
DEFAULT_STATE = os.path.join(_HERE, "state.json")
DEFAULT_RUNS = os.path.join(_HERE, "runs.jsonl")

EXCERPT_LEN = 300


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# --- state + log ------------------------------------------------------------
def load_state(path: str) -> dict[str, float]:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return {k: float(v) for k, v in json.load(f).get("last_run", {}).items()}


def save_state(path: str, state: dict[str, float]) -> None:
    with open(path, "w") as f:
        json.dump({"last_run": state}, f, indent=2)


def append_run(path: str, record: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


# --- one job ----------------------------------------------------------------
def run_job(job: Job, state: dict[str, float], runs_path: str,
            now: float | None = None) -> dict:
    """Execute the team for one job, log it, update state, return the record."""
    now = time.time() if now is None else now
    started = _iso(now)
    team = Team(fake_members(), FakeFoundryClient(),
                print_fn=lambda *args, **kwargs: None)  # quiet: we print our own report
    try:
        final = team.run(job.task)
        status = "partial" if "⚠️" in final else "ok"
    except Exception as exc:  # noqa: BLE001 — a failed run is a log entry, not a crash
        final = f"{type(exc).__name__}: {exc}"
        status = "error"
    finished = _iso(time.time())
    record = {
        "job": job.name,
        "started": started,
        "finished": finished,
        "status": status,
        "steps": [{"handle": m.handle, "subtask": s}
                  for m, s in team.last_plan],
        "reply_excerpt": final[:EXCERPT_LEN],
    }
    append_run(runs_path, record)
    state[job.name] = now  # updated even on error, so a failing job
    return record          # doesn't re-run on every cron tick


def format_report(record: dict) -> str:
    steps = ", ".join(s["handle"] for s in record["steps"]) or "(none)"
    return "\n".join([
        f"Job: {record['job']}",
        f"Outcome: {record['status']}",
        f"Steps: {steps}",
        f"Started: {record['started']}",
        f"Finished: {record['finished']}",
        f"Reply: {record['reply_excerpt']}",
    ])


# --- CLI --------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run scheduled Foundry team jobs.")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true",
                       help="run every due job, then exit")
    group.add_argument("--job", metavar="NAME",
                       help="run one job now, due or not")
    group.add_argument("--list", action="store_true",
                       help="show jobs and their next due time")
    ap.add_argument("--jobs", default=DEFAULT_JOBS, help="jobs.json path")
    ap.add_argument("--state", default=DEFAULT_STATE, help="state.json path")
    ap.add_argument("--runs", default=DEFAULT_RUNS, help="runs.jsonl path")
    args = ap.parse_args(argv)

    jobs = load_jobs(args.jobs)
    by_name = {j.name: j for j in jobs}
    state = load_state(args.state)
    now = time.time()

    if args.list:
        for job in jobs:
            last = state.get(job.name)
            print(f"{job.name}  every {job.interval_seconds}s"
                  f"  last run: {_iso(last) if last else 'never'}"
                  f"  next due: {_iso(job.next_due(last))}"
                  + (f"  (cron: {job.cron})" if job.cron else ""))
            if job.description:
                print(f"    {job.description}")
        return 0

    if args.job:
        job = by_name.get(args.job)
        if not job:
            print(f"unknown job: {args.job} (see --list)")
            return 1
        record = run_job(job, state, args.runs, now=now)
        save_state(args.state, state)
        print(format_report(record))
        return 0 if record["status"] != "error" else 1

    # --once
    ran = 0
    for job in jobs:
        if not job.is_due(state.get(job.name), now):
            print(f"skip {job.name} — next due {_iso(job.next_due(state.get(job.name)))}")
            continue
        record = run_job(job, state, args.runs, now=now)
        print(format_report(record))
        print()
        ran += 1
    save_state(args.state, state)
    if ran == 0:
        print("nothing due.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
