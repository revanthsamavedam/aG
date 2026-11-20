"""demo.py — scheduled work end to end, no Azure.

Loads the example jobs, runs one job now (quote-service-check),
prints the report, and shows the runs.jsonl entry it wrote.

State and the log go in a temp dir, so the demo never touches the
real state.json / runs.jsonl — run `runner.py --job ...` for that.

    python3 demo.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jobs import load_jobs
from runner import format_report, run_job

_HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    jobs = load_jobs(os.path.join(_HERE, "jobs.json"))
    job = next(j for j in jobs if j.name == "quote-service-check")

    print(f"Loaded {len(jobs)} jobs from jobs.json:")
    for j in jobs:
        print(f"  - {j.name} (every {j.interval_seconds}s, cron {j.cron})")

    with tempfile.TemporaryDirectory() as tmp:
        runs_path = os.path.join(tmp, "runs.jsonl")
        state: dict[str, float] = {}
        print(f"\nRunning {job.name} now...")
        record = run_job(job, state, runs_path)

        print(f"\n--- report ---")
        print(format_report(record))

        print(f"\n--- runs.jsonl entry it wrote ---")
        with open(runs_path) as f:
            print(f.read().strip())

        print(f"\n--- state after the run ---")
        print(json.dumps({"last_run": state}, indent=2))
        print("\nNext step: put it in crontab — see the README.")


if __name__ == "__main__":
    main()
