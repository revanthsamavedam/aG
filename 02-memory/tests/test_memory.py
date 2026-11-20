"""Plain-script tests for per-user team memory — no pytest.

    python3 tests/test_memory.py

Run from the 02-memory/ folder. Each check prints PASS/FAIL and
the script exits non-zero if anything fails.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fake_client import FakeFoundryClient, fake_members
from memory import MemoryStore, build_memory_context, run_team_with_memory
from team import Team

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}"
          + (f" — {detail}" if detail and not cond else ""))


def test_store_round_trip() -> None:
    store = MemoryStore()
    recorded = store.record_from_reply(
        "t1", "u1", "dev", "Decision: use FastAPI for the service.")
    check("round-trip: record returns 1 entry", len(recorded) == 1,
          f"got {len(recorded)}")
    recalled = store.recall("t1", "u1")
    check("round-trip: recall returns what was recorded",
          len(recalled) == 1 and recalled[0]["text"] == "use FastAPI for the service.",
          f"got {recalled}")
    check("round-trip: kind stored as decision",
          recalled[0]["kind"] == "decision", f"got {recalled[0]['kind']}")
    check("round-trip: user_id stored on the entry",
          recalled[0]["user_id"] == "u1", f"got {recalled[0].get('user_id')}")
    check("round-trip: other team sees nothing",
          store.recall("other-team", "u1") == [])
    check("round-trip: other user sees nothing",
          store.recall("t1", "u2") == [])
    store.close()


def test_extraction_all_three_kinds() -> None:
    store = MemoryStore()
    reply = (
        "Some working text, no prefix here.\n"
        "Decision: use the public JSON quotes API.\n"
        "A plain sentence with no prefix.\n"
        "Learned: the free API needs no key.\n"
        "Best practice: add a 60s cache.\n"
        "decision: lowercase prefix also counts.\n"
        "Decision:   \n"  # empty value — dropped
        "Best practice:cache without a space after the colon is not a prefix.\n")
    recorded = store.record_from_reply("t1", "u1", "researcher", reply)
    kinds = [r["kind"] for r in recorded]
    check("extraction: all three kinds recorded",
          kinds.count("decision") == 2 and kinds.count("learning") == 1
          and kinds.count("best_practice") == 1,
          f"got {kinds}")
    check("extraction: non-prefixed lines ignored",
          all("no prefix" not in r["text"] and "working text" not in r["text"]
              for r in recorded))
    check("extraction: empty value dropped",
          all(r["text"] for r in recorded))
    check("extraction: no-space-after-colon line ignored",
          all("cache without a space" not in r["text"] for r in recorded))
    check("extraction: no prefixed lines records nothing",
          store.record_from_reply("t1", "u1", "dev", "just prose\nmore prose") == [])
    store.close()


def test_recall_keyword_filtering() -> None:
    store = MemoryStore()
    store.record_from_reply("t1", "u1", "dev", "Decision: use FastAPI for the service.")
    store.record_from_reply("t1", "u1", "qa", "Learned: empty upstream list returns 503.")
    store.record_from_reply("t1", "u1", "dev", "Best practice: add a 60s cache.")
    hits = store.recall("t1", "u1", query="cache")
    check("recall: keyword filters to matching entries",
          len(hits) == 1 and "cache" in hits[0]["text"].lower(),
          f"got {[h['text'] for h in hits]}")
    hits = store.recall("t1", "u1", query="FASTAPI")
    check("recall: keyword match is case-insensitive", len(hits) == 1,
          f"got {len(hits)}")
    check("recall: no query returns all, newest first",
          len(store.recall("t1", "u1")) == 3
          and store.recall("t1", "u1")[0]["kind"] == "best_practice")
    check("recall: unknown word returns nothing",
          store.recall("t1", "u1", query="zebra") == [])
    check("recall: limit is honoured", len(store.recall("t1", "u1", limit=2)) == 2)
    check("recall: keyword search is per-user too",
          store.recall("t1", "u2", query="cache") == [])
    store.close()


def test_list_and_clear() -> None:
    store = MemoryStore()
    store.record_from_reply("t1", "u1", "dev", "Decision: use FastAPI.")
    store.record_from_reply("t1", "u1", "dev", "Learned: httpx is async.")
    store.record_from_reply("t2", "u1", "dev", "Decision: use Flask.")
    store.record_from_reply("t1", "u2", "dev", "Decision: use Django.")
    check("list: per-team-per-user listing",
          len(store.list_entries("t1", "u1")) == 2
          and len(store.list_entries("t2", "u1")) == 1
          and len(store.list_entries("t1", "u2")) == 1)
    check("list: kind filter",
          len(store.list_entries("t1", "u1", kind="learning")) == 1)
    n = store.clear("t1", "u1", kind="decision")
    check("clear: kind-scoped clear deletes only that kind",
          n == 1 and len(store.list_entries("t1", "u1")) == 1, f"deleted {n}")
    n = store.clear("t1", "u1")
    check("clear: whole-user clear", n == 1 and store.list_entries("t1", "u1") == [])
    check("clear: other team untouched", len(store.list_entries("t2", "u1")) == 1)
    check("clear: other user untouched", len(store.list_entries("t1", "u2")) == 1)
    store.close()


def test_per_user_isolation() -> None:
    """The per-user rule: one user's memory never leaks to another."""
    store = MemoryStore()
    store.record_from_reply("t1", "u1", "dev", "Decision: use FastAPI.")
    store.record_from_reply("t1", "u2", "dev", "Decision: use Django.")
    store.record_from_reply("t1", "u1", "qa", "Learned: FastAPI needs no key.")
    check("isolation: recall returns only the caller's entries",
          len(store.recall("t1", "u1")) == 2
          and all(e["user_id"] == "u1" for e in store.recall("t1", "u1"))
          and len(store.recall("t1", "u2")) == 1
          and store.recall("t1", "u2")[0]["text"] == "use Django.")
    ctx_u1 = build_memory_context(store, "t1", "u1")
    ctx_u2 = build_memory_context(store, "t1", "u2")
    check("isolation: context holds only the caller's memories",
          "FastAPI" in ctx_u1 and "Django" not in ctx_u1
          and "Django" in ctx_u2 and "FastAPI" not in ctx_u2)
    check("isolation: unknown user gets empty context",
          build_memory_context(store, "t1", "nobody") == "")
    check("isolation: unknown user recalls nothing",
          store.recall("t1", "nobody") == [])
    check("isolation: same text recorded for two users stays separate",
          len(store.list_entries("t1", "u1")) == 2
          and len(store.list_entries("t1", "u2")) == 1)
    store.close()


def test_memory_context_contains_decision() -> None:
    store = MemoryStore()
    check("context: empty store gives empty string",
          build_memory_context(store, "t1", "u1") == "")
    store.record_from_reply("t1", "u1", "dev",
                            "Decision: use the public JSON quotes API.")
    store.record_from_reply("t1", "u1", "qa",
                            "Learned: empty upstream list returns 503.")
    ctx = build_memory_context(store, "t1", "u1")
    check("context: contains a recorded decision",
          "use the public JSON quotes API" in ctx, ctx)
    check("context: grouped under Decisions:",
          "Decisions:" in ctx and "Learnings:" in ctx)
    check("context: attributes the member",
          "@dev" in ctx and "@qa" in ctx)
    check("context: names the user it belongs to",
          "u1" in ctx, ctx)
    store.close()


def test_end_to_end_run_records_memory() -> None:
    store = MemoryStore()
    team = Team(fake_members(), FakeFoundryClient())
    final = run_team_with_memory(
        team,
        "Research the best free quote API, build a fastapi service "
        "with a random quote about nvidia, and write a README for it",
        store, "t1", "u1")
    check("e2e: team run returns a final answer", bool(final), repr(final))
    entries = store.list_entries("t1", "u1")
    check("e2e: run recorded memory entries", len(entries) >= 3,
          f"got {len(entries)}")
    kinds = {e["kind"] for e in entries}
    check("e2e: recorded entries span kinds",
          "decision" in kinds and "learning" in kinds,
          f"got {kinds}")
    check("e2e: other user has nothing after this run",
          store.list_entries("t1", "u2") == [])
    # Second run: the memory block is actually injected into a prompt.
    client = team.client
    calls_before = len(client.calls)
    run_team_with_memory(team, "Add a cache to the quote service",
                         store, "t1", "u1")
    second_run_prompts = [p for _, p in client.calls[calls_before:]]
    check("e2e: second run's prompts carry the memory block",
          any("Memory for user" in p and "public JSON quotes API" in p
              for p in second_run_prompts))
    # A run as a different user gets no memory block.
    calls_before = len(client.calls)
    run_team_with_memory(team, "Add a cache to the quote service",
                         store, "t1", "u2")
    u2_prompts = [p for _, p in client.calls[calls_before:]]
    check("e2e: a different user's run gets no memory block",
          all("Memory for user" not in p for p in u2_prompts))
    store.close()


def main() -> int:
    test_store_round_trip()
    test_extraction_all_three_kinds()
    test_recall_keyword_filtering()
    test_list_and_clear()
    test_per_user_isolation()
    test_memory_context_contains_decision()
    test_end_to_end_run_records_memory()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        for name, _, detail in failed:
            print(f"  FAILED: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
