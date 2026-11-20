"""Demo: run a task through the team of Foundry agents.

Default is the fake client — no Azure, no key, the whole team runs
locally. With --real it uses the real FoundryAgentClient and the
members from the environment (FOUNDRY_AGENT_*) or --config.

    python3 demo.py
    python3 demo.py --task "Research the best free quote API, build the fastapi service, and write a README"
    python3 demo.py --real
    python3 demo.py --real --config team.json

The Leader's planner comes from the looper provider when a key is
available (LOOPER_MODEL / LOOPER_API_KEY / LOOPER_BASE_URL, or
azure/...); without one it says so and uses the keyword plan.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_client import FakeFoundryClient, fake_members
from foundry_client import FoundryAgentClient
from members import load_members
from team import Team, _default_planner

DEFAULT_TASK = "Build a fastapi service with a random quote about nvidia"
RICH_TASK = ("Research the best free quote API, build a fastapi service "
             "with a random quote about nvidia, and write a README for it")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run a task through the Foundry team.")
    ap.add_argument("--task", default=None, help="Task to run (default: the Nvidia service)")
    ap.add_argument("--real", action="store_true", help="Use the real Foundry client")
    ap.add_argument("--config", default=None, help="Team JSON config (real mode)")
    args = ap.parse_args()

    planner = _default_planner()
    if planner:
        print(f"(Leader planning with model {planner.provider.model})")
    else:
        print("(no provider key — Leader uses the keyword plan)")

    if args.real:
        endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
        api_key = os.environ.get("FOUNDRY_API_KEY", "")
        if not endpoint or not api_key:
            print("Real mode needs FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_API_KEY.")
            raise SystemExit(1)
        members, skipped = load_members(args.config)
        client = FoundryAgentClient(endpoint, api_key)
        print(f"(real client: {len(members)} members wired, "
              f"skipped: {', '.join(skipped) or 'none'})")
    else:
        members = fake_members()
        client = FakeFoundryClient()
        print("(fake client — no Azure, canned replies per role)")

    team = Team(members, client, planner=planner)
    tasks = [args.task] if args.task else [DEFAULT_TASK, RICH_TASK]
    for i, task in enumerate(tasks, start=1):
        print(f"\n{'=' * 60}\nTask {i}: {task}\n{'=' * 60}")
        team.run(task)


if __name__ == "__main__":
    main()
