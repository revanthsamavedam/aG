"""Demo: the team remembers — per user, across conversations.

Runs end to end with the fake client — no Azure, no key.

    python3 demo.py

Run 1 does a task as user "revanth"; the specialists' replies
contain Decision: / Learned: / Best practice: lines, which get
recorded into the SQLite store under (team, revanth). Then the demo
shows that user "alex" has no memory yet — his context is empty,
so nothing leaks across users. Run 2 does a task as alex and
records his own entries, separately.

Memory is per user: every row carries a user_id, and every read
filters on it. The store lives at ./team_memory.db by default
(pass --db to change it, or --fresh to start empty). Re-run the
demo and each user's memory block will include entries from their
previous runs too — memory persists across processes, not just
across runs.
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
USER_A = "revanth"
USER_B = "alex"
TASK_1 = ("Research the best free quote API, build a fastapi service "
          "with a random quote about nvidia, and write a README for it")
TASK_2 = "Add a cache to the quote service and update the README"


def _print_entries(store: MemoryStore, team_id: str, user_id: str) -> None:
    entries = store.list_entries(team_id, user_id)
    if not entries:
        print("  (no entries stored yet)")
        return
    for e in entries:
        print(f"  [{e['kind']}] @{e['member']}: {e['text']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Per-user team memory demo (fake client).")
    ap.add_argument("--db", default="team_memory.db",
                    help="SQLite file for the store (default: team_memory.db)")
    ap.add_argument("--fresh", action="store_true",
                    help="Clear both demo users' memory before run 1")
    args = ap.parse_args()

    store = MemoryStore(args.db)
    if args.fresh:
        n_a = store.clear(TEAM_ID, USER_A)
        n_b = store.clear(TEAM_ID, USER_B)
        print(f"(cleared {n_a + n_b} stored entr{'y' if n_a + n_b == 1 else 'ies'} "
              f"for team '{TEAM_ID}')")
    team = Team(fake_members(), FakeFoundryClient())
    print("(fake client — no Azure, canned replies per role)")

    print(f"\n{'=' * 60}\nRun 1 (user '{USER_A}'): {TASK_1}\n{'=' * 60}")
    run_team_with_memory(team, TASK_1, store, TEAM_ID, USER_A)

    print(f"\n--- What was recorded from run 1 (user '{USER_A}') ---")
    _print_entries(store, TEAM_ID, USER_A)

    print(f"\n--- Memory context for user '{USER_B}' (a different user) ---")
    context_b = build_memory_context(store, TEAM_ID, USER_B)
    print(context_b or "(no memory yet — nothing injected, nothing leaked)")

    print(f"\n{'=' * 60}\nRun 2 (user '{USER_B}'): {TASK_2}\n{'=' * 60}")
    context = build_memory_context(store, TEAM_ID, USER_B)
    print("--- Memory context injected into run 2's prompt ---")
    print(context or "(no memory yet — nothing injected)")
    run_team_with_memory(team, TASK_2, store, TEAM_ID, USER_B)

    print(f"\n--- Stored entries for user '{USER_A}' at the end ---")
    _print_entries(store, TEAM_ID, USER_A)
    print(f"\n--- Stored entries for user '{USER_B}' at the end ---")
    _print_entries(store, TEAM_ID, USER_B)
    print(f"\nStore file: {os.path.abspath(args.db)}")
    store.close()


if __name__ == "__main__":
    main()
