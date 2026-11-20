"""Tests for the evaluation suite — plain script, no pytest.

    python3 tests/test_evaluation.py

Covers: scoring (contains / not_contains / regex, pass and fail),
JSONL recording, comparison (regression, fix, unchanged, new,
removed), and an end-to-end run of the real suite on the fake client
(baseline passes; degraded fails exactly one task).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import (compare_records, load_record, load_tasks, make_fake_team,
                  run_suite, score_check, write_record)

PASSED = 0
FAILED = 0


def check(name: str, cond: bool) -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}")


class _StubMember:
    """Member-like object, so last_results has the same shape as the
    real Team's (member, reply) pairs."""

    def __init__(self, handle: str):
        self.handle = handle


class StubTeam:
    """A team stand-in: run() returns a canned answer per task text,
    last_results carries the per-step replies."""

    def __init__(self, answers: dict[str, str]):
        self.answers = answers
        self.last_results = []

    def run(self, task: str) -> str:
        ans = self.answers.get(task, "")
        self.last_results = [(_StubMember("dev"), ans)]
        return ans


def fake_record(run_id: str, task_outcomes: dict[str, bool]) -> dict:
    """A minimal run record, for comparison tests."""
    tasks = [{"id": tid, "passed": p, "checks": []}
             for tid, p in task_outcomes.items()]
    n = sum(task_outcomes.values())
    return {"run_id": run_id, "label": run_id, "tasks": tasks,
            "tasks_passed": n, "tasks_total": len(tasks),
            "pass_rate": n / len(tasks) if tasks else 0.0}


def main() -> None:
    print("scoring")
    check("contains passes when the text is present",
          score_check({"type": "contains", "text": "FastAPI"},
                      "Built it: FastAPI app"))
    check("contains fails when the text is absent",
          not score_check({"type": "contains", "text": "Nvidia"},
                          "Built it: FastAPI app"))
    check("contains is case-insensitive",
          score_check({"type": "contains", "text": "fastapi"},
                      "Built it: FastAPI app"))
    check("not_contains passes when the text is absent",
          score_check({"type": "not_contains", "text": "TODO"},
                      "Built it: FastAPI app"))
    check("not_contains fails when the text is present",
          not score_check({"type": "not_contains", "text": "TODO"},
                          "TODO: add caching"))
    check("regex passes on a match",
          score_check({"type": "regex", "pattern": r"GET /quote(/nvidia)?"},
                      "GET /quote/nvidia returns only Nvidia quotes"))
    check("regex fails without a match",
          not score_check({"type": "regex", "pattern": r"GET /quote(/nvidia)?"},
                          "GET /health returns 200"))
    try:
        score_check({"type": "fuzzy", "text": "x"}, "answer")
        check("unknown check type raises ValueError", False)
    except ValueError:
        check("unknown check type raises ValueError", True)

    print("runner (stub team)")
    tasks = [{"id": "t1", "task": "do the thing",
              "checks": [{"type": "contains", "text": "FastAPI"}]}]
    team = StubTeam({"do the thing": "Built it: FastAPI app"})
    record = run_suite(tasks, lambda: team, print_fn=lambda *a: None)
    check("run_suite marks a task passed when all checks pass",
          record["tasks"][0]["passed"] is True
          and record["pass_rate"] == 1.0)
    check("run_suite records the per-step replies",
          record["tasks"][0]["steps"]
          == [{"handle": "dev", "reply": "Built it: FastAPI app"}])

    print("recording")
    with tempfile.TemporaryDirectory() as tmp:
        path = write_record(record, tmp)
        check("write_record writes to results/<run_id>.jsonl",
              os.path.basename(path) == f"{record['run_id']}.jsonl")
        raw = open(path).read()
        check("record file is one JSON line",
              len(raw.strip().splitlines()) == 1
              and json.loads(raw)["run_id"] == record["run_id"])
        check("load_record reads the record back",
              load_record(path)["pass_rate"] == 1.0)
        # Two runs in the same second share a timestamp run id —
        # they must still land in separate files, one line each.
        twin = dict(record)
        twin_path = write_record(twin, tmp)
        check("same-second rerun gets its own file, not an append",
              twin_path != path
              and load_record(twin_path)["run_id"] != record["run_id"]
              and len(open(twin_path).read().strip().splitlines()) == 1
              and len(open(path).read().strip().splitlines()) == 1)

    print("comparison")
    old = fake_record("r1", {"a": True, "b": False, "c": True, "d": True})
    new = fake_record("r2", {"a": False, "b": True, "c": True, "e": True})
    comp = compare_records(old, new)
    check("comparison detects a regression (pass -> fail)",
          comp["regressions"] == ["a"])
    check("comparison detects a fix (fail -> pass)",
          comp["fixes"] == ["b"])
    check("comparison detects unchanged tasks",
          comp["unchanged"] == ["c"])
    check("comparison detects a new task",
          comp["new"] == ["e"])
    check("comparison detects a removed task",
          comp["removed"] == ["d"])

    print("end to end (real fake client, real tasks.json)")
    suite = load_tasks()
    baseline = run_suite(suite, lambda: make_fake_team(degraded=False),
                         print_fn=lambda *a: None)
    check("baseline: every task in tasks.json passes",
          baseline["tasks_passed"] == baseline["tasks_total"] == 4)
    degraded = run_suite(suite, lambda: make_fake_team(degraded=True),
                         print_fn=lambda *a: None)
    failed = [t["id"] for t in degraded["tasks"] if not t["passed"]]
    check("degraded: exactly one task fails",
          failed == ["build-quote-service"])
    comp = compare_records(baseline, degraded)
    check("degraded: comparison shows exactly one regression",
          comp["regressions"] == ["build-quote-service"]
          and comp["fixes"] == []
          and len(comp["unchanged"]) == 3)

    print(f"\n{PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
