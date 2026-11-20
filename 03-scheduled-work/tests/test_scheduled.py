"""Plain-script tests for scheduled work (no pytest).

Run:  python3 tests/test_scheduled.py
Exit 0 when every test passes, 1 otherwise.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import Job, load_jobs
from runner import format_report, load_state, main, run_job

PASSED = []
FAILED = []


def check(name, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — record, keep running the rest
        FAILED.append((name, f"{type(exc).__name__}: {exc}"))
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    else:
        PASSED.append(name)
        print(f"PASS {name}")


def _job(name="test-job", interval=3600):
    return Job(name=name, task="Research the best quote API, then test it",
               interval_seconds=interval)


def _tmp_paths():
    tmp = tempfile.mkdtemp()
    return (tmp,
            os.path.join(tmp, "jobs.json"),
            os.path.join(tmp, "state.json"),
            os.path.join(tmp, "runs.jsonl"))


def _write_jobs(path, jobs):
    with open(path, "w") as f:
        json.dump({"jobs": [{
            "name": j.name, "task": j.task,
            "interval_seconds": j.interval_seconds} for j in jobs]}, f)


def _read_runs(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# --- due logic ---------------------------------------------------------------
def test_due_when_never_run():
    job = _job()
    assert job.is_due(None, now=1_000_000) is True


def test_due_when_last_run_long_ago():
    job = _job(interval=3600)
    assert job.is_due(1_000_000, now=1_000_000 + 7200) is True  # 2h > 1h


def test_not_due_when_just_run():
    job = _job(interval=3600)
    assert job.is_due(1_000_000, now=1_000_000 + 60) is False  # 1 min < 1h


def test_due_exactly_at_interval():
    job = _job(interval=3600)
    assert job.is_due(1_000_000, now=1_000_000 + 3600) is True


def test_load_jobs_reads_json():
    tmp, jobs_path, _, _ = _tmp_paths()
    _write_jobs(jobs_path, [_job("a", 60), _job("b", 120)])
    jobs = load_jobs(jobs_path)
    assert [j.name for j in jobs] == ["a", "b"], jobs
    assert jobs[0].interval_seconds == 60


# --- runner ------------------------------------------------------------------
def test_runner_executes_job_and_writes_log_entry():
    tmp, jobs_path, state_path, runs_path = _tmp_paths()
    job = _job()
    _write_jobs(jobs_path, [job])
    state: dict[str, float] = {}
    record = run_job(job, state, runs_path, now=1_000_000)
    assert record["job"] == "test-job"
    assert record["status"] == "ok", record
    assert record["started"] and record["finished"], record
    assert record["reply_excerpt"], record
    assert record["steps"], record  # the team planned at least one step
    entries = _read_runs(runs_path)
    assert len(entries) == 1, entries
    assert entries[0]["job"] == "test-job"
    assert entries[0]["status"] == "ok"
    assert state["test-job"] == 1_000_000


def test_runner_job_flag_runs_exactly_one_job():
    tmp, jobs_path, state_path, runs_path = _tmp_paths()
    _write_jobs(jobs_path, [_job("a", 60), _job("b", 60)])
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["--job", "b", "--jobs", jobs_path,
                     "--state", state_path, "--runs", runs_path])
    assert code == 0
    entries = _read_runs(runs_path)
    assert [e["job"] for e in entries] == ["b"], entries
    assert "b" in buf.getvalue()


def test_runner_once_runs_only_due_jobs():
    tmp, jobs_path, state_path, runs_path = _tmp_paths()
    _write_jobs(jobs_path, [_job("due", 60), _job("fresh", 3600)])
    # "fresh" ran 60s ago with a 1h interval -> not due; "due" never ran.
    # last_run is in real epoch seconds: main() compares against time.time().
    import time as _time
    with open(state_path, "w") as f:
        json.dump({"last_run": {"fresh": _time.time() - 60}}, f)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["--once", "--jobs", jobs_path,
                     "--state", state_path, "--runs", runs_path])
    assert code == 0
    entries = _read_runs(runs_path)
    assert [e["job"] for e in entries] == ["due"], entries
    assert "skip fresh" in buf.getvalue()
    # State now has both jobs (fresh kept, due recorded).
    state = load_state(state_path)
    assert "due" in state and "fresh" in state, state


def test_runner_unknown_job_exits_1():
    tmp, jobs_path, state_path, runs_path = _tmp_paths()
    _write_jobs(jobs_path, [_job("a", 60)])
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["--job", "nope", "--jobs", jobs_path,
                     "--state", state_path, "--runs", runs_path])
    assert code == 1
    assert _read_runs(runs_path) == []


def test_report_contains_job_name_and_outcome():
    record = {"job": "quote-service-check", "status": "ok",
              "steps": [{"handle": "researcher", "subtask": "find APIs"},
                        {"handle": "qa", "subtask": "test it"}],
              "started": "2026-11-19T00:00:00+00:00",
              "finished": "2026-11-19T00:00:01+00:00",
              "reply_excerpt": "Done — Researcher, QA."}
    report = format_report(record)
    assert "quote-service-check" in report, report
    assert "ok" in report, report
    assert "researcher" in report and "qa" in report, report


if __name__ == "__main__":
    check("due when never run", test_due_when_never_run)
    check("due when last run long ago", test_due_when_last_run_long_ago)
    check("not due when just run", test_not_due_when_just_run)
    check("due exactly at the interval", test_due_exactly_at_interval)
    check("load_jobs reads jobs.json", test_load_jobs_reads_json)
    check("runner executes a job and writes a log entry",
          test_runner_executes_job_and_writes_log_entry)
    check("--job runs exactly one job", test_runner_job_flag_runs_exactly_one_job)
    check("--once runs only due jobs", test_runner_once_runs_only_due_jobs)
    check("unknown --job exits 1", test_runner_unknown_job_exits_1)
    check("report contains job name and outcome",
          test_report_contains_job_name_and_outcome)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name, err in FAILED:
            print(f"  FAILED: {name}: {err}")
        raise SystemExit(1)
