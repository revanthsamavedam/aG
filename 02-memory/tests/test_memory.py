"""Plain-script tests for managed memory (the Foundry Memory Service) — no pytest.

    python3 tests/test_memory.py

Run from the 02-memory/ folder. Each check prints PASS/FAIL and
the script exits non-zero if anything fails. Everything runs
against FakeMemoryStores — no Azure, no key.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fake_client import FakeFoundryClient, fake_members
from fake_memory import FakeMemoryStores
from memory import (ManagedMemoryStore, build_memory_context,
                    connect_from_env, run_team_with_memory)
from team import Team

RESULTS: list[tuple[str, bool, str]] = []
STORE = "nvidia-quote-service"


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}"
          + (f" — {detail}" if detail and not cond else ""))


def _store() -> tuple[ManagedMemoryStore, FakeMemoryStores]:
    memory_stores = FakeMemoryStores()
    # The definition is a real-service requirement (chat + embedding
    # model); the fake ignores its contents, so placeholder names do.
    store = ManagedMemoryStore(
        memory_stores, STORE,
        definition={"kind": "default", "chat_model": "fake-chat-model",
                    "embedding_model": "fake-embedding-model"})
    store.ensure_store()
    return store, memory_stores


def test_ensure_store_creates_and_gets() -> None:
    memory_stores = FakeMemoryStores()
    store = ManagedMemoryStore(
        memory_stores, STORE,
        definition={"kind": "default", "chat_model": "fake-chat-model",
                    "embedding_model": "fake-embedding-model"})
    created = store.ensure_store()
    check("ensure_store: creates the store", created.name == STORE)
    again = store.ensure_store()
    check("ensure_store: second call returns the same store",
          again.name == STORE)
    creates = [c for c in memory_stores.calls if c[0] == "create"]
    check("ensure_store: created exactly once", len(creates) == 1,
          f"got {len(creates)} creates")
    check("ensure_store: create carried the definition",
          creates[0][1]["definition"] is not None)
    # No definition and no existing store: a clear error, not an SDK failure.
    bare = ManagedMemoryStore(FakeMemoryStores(), "missing-store")
    try:
        bare.ensure_store()
    except ValueError as exc:
        check("ensure_store: no definition raises a clear error",
              "definition" in str(exc), str(exc))
    else:
        check("ensure_store: no definition raises a clear error", False,
              "no error raised")


def test_record_extracts_three_kinds() -> None:
    store, _ = _store()
    reply = (
        "Some working text, no prefix here.\n"
        "Decision: use the public JSON quotes API.\n"
        "A plain sentence with no prefix.\n"
        "Learned: the free API needs no key.\n"
        "Best practice: add a 60s cache.\n"
        "decision: lowercase prefix also counts.\n"
        "Decision:   \n"  # empty value — dropped
        "Best practice:cache without a space after the colon is not a prefix.\n")
    ops = store.record("u1", reply)
    contents = [o["content"] for o in ops]
    check("record: all three kinds extracted",
          len(ops) == 4 and
          "use the public JSON quotes API." in contents and
          "the free API needs no key." in contents and
          "add a 60s cache." in contents,
          f"got {contents}")
    check("record: op dicts carry kind, memory_id, content",
          all(set(o) == {"kind", "memory_id", "content"} for o in ops))
    check("record: non-prefixed lines ignored",
          all("no prefix" not in c and "working text" not in c
              for c in contents))
    check("record: empty value dropped", all(contents))
    check("record: no-space-after-colon line ignored",
          all("cache without a space" not in c for c in contents))
    check("record: no prefixed lines records nothing",
          store.record("u1", "just prose\nmore prose") == [])


def test_consolidation_dedups() -> None:
    store, _ = _store()
    first = store.record("u1", "Decision: use FastAPI.")
    second = store.record("u1", "Decision: use FastAPI.")
    check("consolidation: first record stores the memory",
          len(first) == 1, f"got {len(first)}")
    check("consolidation: same line twice stores it once",
          second == [] and len(store.recall("u1", "FastAPI")) == 1,
          f"got {len(second)} ops on second record")


def test_recall_is_per_user() -> None:
    store, _ = _store()
    store.record("u1", "Decision: use FastAPI.")
    store.record("u2", "Decision: use Django.")
    store.record("u1", "Learned: FastAPI needs no key.")
    u1 = store.recall("u1", "FastAPI")
    u2 = store.recall("u2", "Django")
    check("recall: returns only the caller's memories",
          len(u1) == 2 and all("FastAPI" in m["content"] for m in u1),
          f"got {[m['content'] for m in u1]}")
    check("recall: other user sees only their own",
          len(u2) == 1 and u2[0]["content"] == "use Django.",
          f"got {[m['content'] for m in u2]}")
    check("recall: unknown user gets nothing",
          store.recall("u1", "FastAPI") != [] and
          store.recall("nobody", "FastAPI") == [])
    check("recall: same text for two users stays separate",
          len(store.recall("u1", "use")) == 1 and
          len(store.recall("u2", "use")) == 1)


def test_recall_keyword_and_limit() -> None:
    store, _ = _store()
    store.record("u1", "Decision: use FastAPI for the service.")
    store.record("u1", "Learned: empty upstream list returns 503.")
    store.record("u1", "Best practice: add a 60s cache.")
    hits = store.recall("u1", "cache")
    check("recall: query filters to matching memories",
          len(hits) == 1 and "cache" in hits[0]["content"].lower(),
          f"got {[m['content'] for m in hits]}")
    hits = store.recall("u1", "FASTAPI")
    check("recall: match is case-insensitive", len(hits) == 1,
          f"got {len(hits)}")
    check("recall: limit is honoured",
          len(store.recall("u1", "a", limit=2)) == 2)


def test_record_sends_scope_and_update_delay() -> None:
    store, memory_stores = _store()
    store.record("u1", "Decision: use FastAPI.")
    calls = [c for c in memory_stores.calls
             if c[0] == "begin_update_memories"]
    check("record: calls begin_update_memories once", len(calls) == 1,
          f"got {len(calls)}")
    kwargs = calls[0][1]
    check("record: scope is the user id", kwargs["scope"] == "u1",
          f"got scope={kwargs.get('scope')!r}")
    check("record: update_delay is 0 (immediate)",
          kwargs["update_delay"] == 0, f"got {kwargs.get('update_delay')}")
    check("record: items carry the raw reply",
          kwargs["items"] == [{"role": "user",
                               "content": "Decision: use FastAPI.",
                               "type": "message"}],
          f"got {kwargs.get('items')}")


def test_recall_sends_scope_and_options() -> None:
    store, memory_stores = _store()
    store.record("u1", "Decision: use FastAPI.")
    store.recall("u1", "FastAPI", limit=3)
    calls = [c for c in memory_stores.calls if c[0] == "search_memories"]
    check("recall: calls search_memories", len(calls) == 1,
          f"got {len(calls)}")
    kwargs = calls[0][1]
    check("recall: scope is the user id", kwargs["scope"] == "u1",
          f"got scope={kwargs.get('scope')!r}")
    check("recall: options carry max_memories",
          kwargs["options"] == {"max_memories": 3},
          f"got {kwargs.get('options')}")


def test_build_memory_context() -> None:
    store, _ = _store()
    check("context: unknown user gets empty string",
          build_memory_context(store, "nobody", "quote service") == "")
    store.record("u1", "Decision: use the public JSON quotes API.")
    store.record("u1", "Learned: empty upstream list returns 503.")
    store.record("u2", "Decision: use Django.")
    ctx = build_memory_context(store, "u1", "quote API")
    check("context: contains a recorded memory",
          "use the public JSON quotes API" in ctx, ctx)
    check("context: names the user it belongs to", "u1" in ctx, ctx)
    check("context: never contains another user's memories",
          "Django" not in ctx, ctx)


def test_end_to_end_run_records_memory() -> None:
    store, _ = _store()
    team = Team(fake_members(), FakeFoundryClient())
    final = run_team_with_memory(
        team,
        "Research the best free quote API, build a fastapi service "
        "with a random quote about nvidia, and write a README for it",
        store, "u1")
    check("e2e: team run returns a final answer", bool(final), repr(final))
    entries = store.recall("u1", "quote API")
    check("e2e: run recorded memories", len(entries) >= 3,
          f"got {len(entries)}")
    check("e2e: other user has nothing after this run",
          store.recall("u2", "quote API") == [])
    # Second run: the memory block is actually injected into a prompt.
    client = team.client
    calls_before = len(client.calls)
    run_team_with_memory(team, "Add a cache to the quote service",
                         store, "u1")
    second_run_prompts = [p for _, p in client.calls[calls_before:]]
    check("e2e: second run's prompts carry the memory block",
          any("Memory for user" in p and "public JSON quotes API" in p
              for p in second_run_prompts))
    # A run as a different user gets no memory block.
    calls_before = len(client.calls)
    run_team_with_memory(team, "Add a cache to the quote service",
                         store, "u2")
    u2_prompts = [p for _, p in client.calls[calls_before:]]
    check("e2e: a different user's run gets no memory block",
          all("Memory for user" not in p for p in u2_prompts))


def test_connect_from_env_missing_vars() -> None:
    saved = {k: os.environ.pop(k, None) for k in (
        "FOUNDRY_PROJECT_ENDPOINT",
        "MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME",
        "MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME")}
    try:
        try:
            connect_from_env("some-store")
        except RuntimeError as exc:
            check("connect_from_env: missing vars raise RuntimeError", True)
            check("connect_from_env: error names the missing vars",
                  "FOUNDRY_PROJECT_ENDPOINT" in str(exc) and
                  "MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME" in str(exc),
                  str(exc))
        else:
            check("connect_from_env: missing vars raise RuntimeError",
                  False, "no error raised")
            check("connect_from_env: error names the missing vars", False)
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def main() -> int:
    test_ensure_store_creates_and_gets()
    test_record_extracts_three_kinds()
    test_consolidation_dedups()
    test_recall_is_per_user()
    test_recall_keyword_and_limit()
    test_record_sends_scope_and_update_delay()
    test_recall_sends_scope_and_options()
    test_build_memory_context()
    test_end_to_end_run_records_memory()
    test_connect_from_env_missing_vars()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        for name, _, detail in failed:
            print(f"  FAILED: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
