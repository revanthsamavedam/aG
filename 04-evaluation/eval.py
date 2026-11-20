"""eval.py — a task suite that scores the team and tracks results over time.

A task is one prompt for the team plus a list of checks on the answer
it should produce. A check is one of:

    {"type": "contains",     "text": "FastAPI"}   # answer must mention it
    {"type": "not_contains", "text": "TODO"}      # answer must not mention it
    {"type": "regex",        "pattern": "GET /quote(/nvidia)?"}

Scoring runs against the final integrated answer — what the user
actually sees — not the raw specialist replies. (The Leader's
template integration keeps only each step's first 200 characters, so
a keyword buried late in a specialist's reply will not be seen.
That is a real eval lesson: score the output, not the work.)

Each run writes one JSON line to results/<run_id>.jsonl, where
run_id is a timestamp. Compare two runs to see what changed:

    python3 eval.py                        # run the suite, record the run
    python3 eval.py --degraded             # same, with a weaker fake Dev
    python3 eval.py --compare results/<run1>.jsonl results/<run2>.jsonl

Everything runs on the fake client — no Azure, no key.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_client import FakeFoundryClient, fake_members
from team import Team

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TASKS = os.path.join(HERE, "tasks.json")
DEFAULT_RESULTS = os.path.join(HERE, "results")

# The degraded Dev: same service, but the /quote/nvidia endpoint is
# gone — the "Nvidia" check on build-quote-service should catch it.
DEGRADED_DEV = (
    'Built it: FastAPI app with GET /quote returning '
    '{"quote": ..., "author": ...}. main.py is ~25 lines '
    "(FastAPI, httpx, random). Run: uvicorn main:app --reload.")


# -- suite ---------------------------------------------------------------

def load_tasks(path: str = DEFAULT_TASKS) -> list[dict]:
    """tasks.json -> [{"id", "task", "checks": [...]}, ...]."""
    with open(path) as f:
        return json.load(f)


def score_check(check: dict, answer: str) -> bool:
    """One check against the final answer. contains / not_contains are
    case-insensitive; regex is re.search with the pattern as written,
    so it gives you exact control (and exact responsibility)."""
    kind = check.get("type")
    if kind == "contains":
        return check["text"].lower() in answer.lower()
    if kind == "not_contains":
        return check["text"].lower() not in answer.lower()
    if kind == "regex":
        return re.search(check["pattern"], answer) is not None
    raise ValueError(f"unknown check type: {kind!r} (want contains, "
                     "not_contains, or regex)")


def score_task(task: dict, answer: str) -> list[dict]:
    """Every check on one task, each with its pass/fail."""
    return [{"check": c, "passed": score_check(c, answer)}
            for c in task["checks"]]


# -- runner --------------------------------------------------------------

def make_fake_team(degraded: bool = False) -> Team:
    """A team on the fake client. degraded=True swaps in the weaker
    Dev reply (via the fake client's replies= override — the vendored
    fake_client.py itself is untouched)."""
    client = FakeFoundryClient(
        replies={"dev": DEGRADED_DEV} if degraded else None)
    # Silence the team's per-step chatter; the runner prints its own
    # per-task summary below.
    return Team(fake_members(), client, print_fn=lambda *a: None)


def run_task(team: Team, task: dict) -> dict:
    """One task through the team: final answer, per-step replies,
    and the scored checks."""
    answer = team.run(task["task"])
    steps = [{"handle": m.handle, "reply": r}
             for m, r in getattr(team, "last_results", [])]
    checks = score_task(task, answer)
    return {
        "id": task["id"],
        "task": task["task"],
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "answer": answer,
        "steps": steps,
    }


def run_suite(tasks: list[dict], make_team, label: str = "",
              print_fn=print) -> dict:
    """Run every task once, print per-task pass/fail, return the run
    record. make_team is a callable returning a Team (so tests can
    hand in a stub team)."""
    team = make_team()
    results = [run_task(team, t) for t in tasks]
    tasks_passed = sum(1 for r in results if r["passed"])
    checks_passed = sum(1 for r in results for c in r["checks"] if c["passed"])
    checks_total = sum(len(r["checks"]) for r in results)
    record = {
        "run_id": datetime.now().strftime("%Y%m%dT%H%M%S"),
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "label": label,
        "tasks": results,
        "tasks_passed": tasks_passed,
        "tasks_total": len(results),
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        # Overall pass rate = tasks passed / tasks run.
        "pass_rate": tasks_passed / len(results) if results else 0.0,
    }
    for r in results:
        n = sum(1 for c in r["checks"] if c["passed"])
        status = "PASS" if r["passed"] else "FAIL"
        print_fn(f"[{status}] {r['id']} ({n}/{len(r['checks'])} checks)")
        for c in r["checks"]:
            if not c["passed"]:
                print_fn(f"        failed check: {c['check']}")
    print_fn(f"Overall: {tasks_passed}/{len(results)} tasks passed "
             f"({checks_passed}/{checks_total} checks) "
             f"— pass rate {record['pass_rate']:.0%}")
    return record


# -- recording -----------------------------------------------------------

def write_record(record: dict, results_dir: str = DEFAULT_RESULTS) -> str:
    """Write one JSON line — the whole run — to
    results/<run_id>.jsonl. Returns the file path.

    Two runs in the same second share a timestamp run id, so if the
    file already exists the id gets a -2, -3, ... suffix (and the
    record is updated to match): one run, one file, always."""
    os.makedirs(results_dir, exist_ok=True)
    run_id = record["run_id"]
    n = 1
    while True:
        suffix = "" if n == 1 else f"-{n}"
        path = os.path.join(results_dir, f"{run_id}{suffix}.jsonl")
        if not os.path.exists(path):
            break
        n += 1
    if n > 1:
        record["run_id"] = f"{run_id}-{n}"
    with open(path, "w") as f:
        f.write(json.dumps(record) + "\n")
    return path


def load_record(path: str) -> dict:
    """Read back one run record (a .jsonl file with a single line)."""
    with open(path) as f:
        lines = [ln for ln in f if ln.strip()]
    if len(lines) != 1:
        raise ValueError(f"{path}: expected 1 JSON line, found {len(lines)}")
    return json.loads(lines[0])


# -- comparison ----------------------------------------------------------

def compare_records(old: dict, new: dict) -> dict:
    """What changed between two runs, per task id. A task counts as
    passed only when every check passed."""
    old_tasks = {t["id"]: t for t in old["tasks"]}
    new_tasks = {t["id"]: t for t in new["tasks"]}
    comp = {"regressions": [], "fixes": [], "unchanged": [],
            "new": [], "removed": []}
    for tid, nt in new_tasks.items():
        if tid not in old_tasks:
            comp["new"].append(tid)
        elif old_tasks[tid]["passed"] and not nt["passed"]:
            comp["regressions"].append(tid)
        elif not old_tasks[tid]["passed"] and nt["passed"]:
            comp["fixes"].append(tid)
        else:
            comp["unchanged"].append(tid)
    for tid in old_tasks:
        if tid not in new_tasks:
            comp["removed"].append(tid)
    return comp


def print_comparison(comp: dict, old: dict, new: dict,
                     print_fn=print) -> None:
    """The comparison, in plain words."""
    print_fn(f"Comparing {old['run_id']} ({old['label'] or 'run 1'}) "
             f"-> {new['run_id']} ({new['label'] or 'run 2'})")
    print_fn(f"  pass rate: {old['pass_rate']:.0%} -> {new['pass_rate']:.0%}")
    for tid in comp["regressions"]:
        print_fn(f"  REGRESSION  {tid}: pass -> fail")
    for tid in comp["fixes"]:
        print_fn(f"  FIX         {tid}: fail -> pass")
    for tid in comp["unchanged"]:
        print_fn(f"  unchanged   {tid}")
    for tid in comp["new"]:
        print_fn(f"  new task    {tid} (only in the second run)")
    for tid in comp["removed"]:
        print_fn(f"  removed     {tid} (only in the first run)")
    if not comp["regressions"] and not comp["fixes"]:
        print_fn("  No task changed outcome.")


# -- CLI -----------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run the evaluation suite against the Foundry team.")
    ap.add_argument("--tasks", default=DEFAULT_TASKS,
                    help="Task suite JSON (default: tasks.json)")
    ap.add_argument("--results-dir", default=DEFAULT_RESULTS,
                    help="Where run records go (default: results/)")
    ap.add_argument("--degraded", action="store_true",
                    help="Run with the weaker fake Dev reply")
    ap.add_argument("--compare", nargs=2, metavar=("RUN1", "RUN2"),
                    help="Compare two run records instead of running")
    args = ap.parse_args()

    if args.compare:
        old, new = (load_record(p) for p in args.compare)
        print_comparison(compare_records(old, new), old, new)
        return

    tasks = load_tasks(args.tasks)
    label = "degraded" if args.degraded else "baseline"
    record = run_suite(tasks, lambda: make_fake_team(args.degraded),
                       label=label)
    path = write_record(record, args.results_dir)
    print(f"Recorded to {path}")


if __name__ == "__main__":
    main()
