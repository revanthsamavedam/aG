"""demo.py — Foundry Routines end to end, no Azure.

Loads the example routines, creates them in the FAKE Routines
service, dispatches one manually, and shows the run history.

The fake (fake_routines.py) mimics client.beta.routines: create,
dispatch, list_runs. The real service queues the run and the agent
runs asynchronously; the fake completes immediately so the demo
can show the run record in one pass.

    python3 demo.py

Real run: set FOUNDRY_PROJECT_ENDPOINT, then replace
FakeRoutines() with connect_from_env() from routines.py. No
credentials are hardcoded or asked for here. A routine invokes
one agent — the examples point at the specialist the job needs
(QA for the health check, researcher for the roundup).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_routines import FakeRoutines
from routines import RoutineManager, format_routine, format_run, load_routines

_HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    print("FAKE Routines service — the real run needs a Foundry project")
    print("endpoint (see routines.py: connect_from_env). Nothing here")
    print("touches Azure.")
    routines = load_routines(os.path.join(_HERE, "routines.json"))
    print(f"\nLoaded {len(routines)} routines from routines.json:")
    for r in routines:
        print(f"  - {r.name} (agent {r.agent_name}, "
              f"{r.cron_expression} {r.time_zone})")

    manager = RoutineManager(FakeRoutines())
    print("\n--- creating them in Foundry (fake) ---")
    for routine in manager.create_all(routines):
        print(format_routine(routine))
        print()

    target = "quote-service-check"
    print(f"--- dispatching {target} now (manual test run) ---")
    result = manager.dispatch(target)
    print(f"dispatch_id: {result.dispatch_id}")
    print(f"task_id:     {result.task_id}")
    print("(queued — the fake completes it at once; the real service")
    print(" runs it asynchronously, so check the run history.)")

    print(f"\n--- run history for {target} ---")
    for run in manager.list_runs(target):
        print(format_run(run))

    print("\nNext step: create these for real — see the README, "
          "'Creating the routines'.")


if __name__ == "__main__":
    main()
