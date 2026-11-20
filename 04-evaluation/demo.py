"""Demo: run the suite twice and compare, so you can see a regression
get caught.

    python3 demo.py

Run 1 is the baseline (the normal fake client). Run 2 uses a degraded
fake client: Dev's reply drops the /quote/nvidia endpoint, so the
"Nvidia" check on build-quote-service should fail. The comparison at
the end shows exactly that — one regression, everything else
unchanged. No Azure, no key.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eval import (compare_records, load_tasks, make_fake_team,
                  print_comparison, run_suite, write_record)

DEFAULT_RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "results")


def main() -> None:
    tasks = load_tasks()
    print(f"Suite: {len(tasks)} tasks from tasks.json\n")

    print("=== Run 1: baseline (normal fake client) ===")
    baseline = run_suite(tasks, lambda: make_fake_team(degraded=False),
                         label="baseline")
    baseline_path = write_record(baseline, DEFAULT_RESULTS)
    print(f"Recorded to {baseline_path}\n")

    print("=== Run 2: degraded (Dev drops the Nvidia endpoint) ===")
    degraded = run_suite(tasks, lambda: make_fake_team(degraded=True),
                         label="degraded")
    degraded_path = write_record(degraded, DEFAULT_RESULTS)
    print(f"Recorded to {degraded_path}\n")

    print("=== Comparison: baseline -> degraded ===")
    print_comparison(compare_records(baseline, degraded), baseline, degraded)
    print()
    print("The degraded run lost the Nvidia endpoint, and the suite "
          "caught it: build-quote-service went pass -> fail.")


if __name__ == "__main__":
    main()
