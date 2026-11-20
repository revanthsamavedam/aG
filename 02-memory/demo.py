"""Demo: the team remembers — via the managed Memory Service, per user.

Runs end to end with the FAKE memory service — no Azure, no key.

    python3 demo.py

Run 1 does a task as user "revanth". The specialists' replies go
to the service (begin_update_memories), which extracts and stores
what matters under revanth's scope. Then the demo shows that user
"alex" searches the same store and gets nothing back — same store,
different scope, nothing leaks. Run 2 does a task as alex; his
replies are stored under his own scope.

The fake service (fake_memory.py) mimics the real one: extraction
of Decision:/Learned:/Best practice: lines, dedup of identical
content, keyword retrieval, per-scope isolation. The real service
does extraction with an LLM and retrieval semantically; the fake
is deterministic so the demo is repeatable.

Real run: set FOUNDRY_PROJECT_ENDPOINT,
MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME and
MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME, then replace
FakeMemoryStores() with connect_from_env("nvidia-quote-service")
from memory.py. No credentials are hardcoded or asked for here.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_client import FakeFoundryClient, fake_members
from fake_memory import FakeMemoryStores
from memory import (ManagedMemoryStore, build_memory_context,
                    run_team_with_memory)
from team import Team

STORE_NAME = "nvidia-quote-service"
USER_A = "revanth"
USER_B = "alex"
TASK_1 = ("Research the best free quote API, build a fastapi service "
          "with a random quote about nvidia, and write a README for it")
TASK_2 = "Add a cache to the quote service and update the README"


def _print_memories(store: ManagedMemoryStore, user_id: str, query: str) -> None:
    memories = store.recall(user_id, query)
    if not memories:
        print("  (no memories found)")
        return
    for m in memories:
        print(f"  [{m['memory_id']}] {m['content']}")


def main() -> None:
    print("FAKE memory service — the real run needs a Foundry project")
    print("endpoint + chat/embedding model deployments (see memory.py:")
    print("connect_from_env). Nothing here touches Azure.")
    memory_stores = FakeMemoryStores()
    # The definition is a real-service requirement (chat + embedding
    # model); the fake ignores its contents, so placeholder names do.
    store = ManagedMemoryStore(
        memory_stores, STORE_NAME,
        definition={"kind": "default", "chat_model": "fake-chat-model",
                    "embedding_model": "fake-embedding-model"})
    store.ensure_store()
    print(f"\nMemory store: '{STORE_NAME}' (created by ensure_store)")

    team = Team(fake_members(), FakeFoundryClient())
    print("(fake Foundry client — no Azure, canned replies per role)")

    print(f"\n{'=' * 60}\nRun 1 (user '{USER_A}'): {TASK_1}\n{'=' * 60}")
    run_team_with_memory(team, TASK_1, store, USER_A)

    print(f"\n--- Memories the service extracted from run 1 ---")
    print(f"(search as '{USER_A}' for the run-1 task)")
    _print_memories(store, USER_A, TASK_1)

    print(f"\n--- Same store, searched as a different user ('{USER_B}') ---")
    print(f"(search as '{USER_B}' for the same task)")
    _print_memories(store, USER_B, TASK_1)

    print(f"\n{'=' * 60}\nRun 2 (user '{USER_B}'): {TASK_2}\n{'=' * 60}")
    context = build_memory_context(store, USER_B, TASK_2)
    print("--- Memory context injected into run 2's prompt ---")
    print(context or "(no memory yet — nothing injected)")
    run_team_with_memory(team, TASK_2, store, USER_B)

    print(f"\n--- Memories for user '{USER_A}' at the end (run-1 task) ---")
    _print_memories(store, USER_A, TASK_1)
    print(f"\n--- Memories for user '{USER_B}' at the end (run-2 task) ---")
    _print_memories(store, USER_B, TASK_2)
    print("\nEach user sees only their own scope. The store holds both.")


if __name__ == "__main__":
    main()
