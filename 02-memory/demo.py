"""Demo: the team remembers across conversations.

Runs end to end with the fake client — no Azure, no key.

    python3 demo.py

Run 1 does a task; the specialists' replies contain Decision: /
Learned: / Best practice: lines, which get recorded into the
SQLite store (printed as they land). Run 2 shows the memory block
that gets injected into the prompt, then the team runs with it.
Stored entries are printed at the end.

The store lives at ./team_memory.db by default (pass --db to change
it, or --fresh to start empty). Re-run the demo and run 2's memory
block will include entries from the previous run too — that is the
point: memory persists across processes, not just across runs.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_client import FakeFoundryClient, fake_members
from memory import MemoryStore, build_memory_context, run_team_with_memory
from team import Team

TEAM_ID = "nvidia-quote-service"
TASK_1 = ("Research the best free quote API, build a fastapi service "
          "with a random quote about nvidia, and write a README for it")
TASK_2 = "Add a cache to the quote service and update the README"


def _print_entries(store: MemoryStore, team_id: str) -> None:
    entries = store.list_entries(team_id)
    if not entries:
        print("(no entries stored yet)")
        return
    for e in entries:
        print(f"  [{e['kind']}] @{e['member']}: {e['text']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Team memory demo (fake client).")
    ap.add_argument("--db", default="team_memory.db",
                    help="SQLite file for the store (default: team_memory.db)")
    ap.add_argument("--fresh", action="store_true",
                    help="Clear this team's memory before run 1")
    args = ap.parse_args()

    store = MemoryStore(args.db)
    if args.fresh:
        n = store.clear(TEAM_ID)
        print(f"(cleared {n} stored entr{'y' if n == 1 else 'ies'} "
              f"for team '{TEAM_ID}')")
    team = Team(fake_members(), FakeFoundryClient())
    print("(fake client — no Azure, canned replies per role)")

    print(f"\n{'=' * 60}\nRun 1: {TASK_1}\n{'=' * 60}")
    run_team_with_memory(team, TASK_1, store, TEAM_ID)

    print(f"\n--- What was recorded from run 1 ---")
    _print_entries(store, TEAM_ID)

    print(f"\n{'=' * 60}\nRun 2: {TASK_2}\n{'=' * 60}")
    context = build_memory_context(store, TEAM_ID)
    print("--- Memory context injected into run 2's prompt ---")
    print(context or "(no memory yet — nothing injected)")
    run_team_with_memory(team, TASK_2, store, TEAM_ID)

    print(f"\n--- Stored entries for team '{TEAM_ID}' at the end ---")
    _print_entries(store, TEAM_ID)
    print(f"\nStore file: {os.path.abspath(args.db)}")
    store.close()


if __name__ == "__main__":
    main()
