"""FakeMemoryStores — a stand-in for ``project_client.beta.memory_stores``.

Same method names and result shapes as the real SDK, so
ManagedMemoryStore runs unchanged against it. No Azure, no key,
no network. Used by demo.py and the tests.

What the fake models, and what it simplifies:
  * Extraction: the real service extracts facts from conversation
    content with an LLM. The fake extracts lines starting with
    Decision: / Learned: / Best practice: (prefix + a space,
    case-insensitive) — the same rule the old hand-rolled store
    used, so the canned replies in fake_client.py still produce
    memories. It is deterministic; the real service is not.
  * Consolidation: the real service merges duplicate topics. The
    fake dedups identical content within a scope — recording the
    same line twice stores it once.
  * Retrieval: the real service is semantic. The fake is keyword
    match on the query words, newest first, capped by max_memories.
  * Scoping: exactly like the service. Everything is keyed by
    (store_name, scope), and scope is the user id. A search with
    one scope never returns another scope's memories.
  * The update is a poller, like the real one: begin_update_memories
    returns a FakeUpdatePoller; .result() returns the completed
    result with .memory_operations. The real update takes about a
    minute; the fake completes immediately.

Every call is recorded in .calls (method name + kwargs), so tests
can check the exact shapes ManagedMemoryStore sends the service.
"""
from __future__ import annotations

import re

_PREFIXES = {
    "decision:": "decision",
    "learned:": "learning",
    "best practice:": "best_practice",
}


class _FakeMemoryItem:
    """Shape of SDK memory items: .memory_id and .content."""
    def __init__(self, memory_id: str, content: str, kind: str = "fact"):
        self.memory_id = memory_id
        self.content = content
        self.kind = kind


class _FakeMemoryOperation:
    """Shape of SDK memory operations: .kind and .memory_item."""
    def __init__(self, kind: str, item: _FakeMemoryItem):
        self.kind = kind
        self.memory_item = item


class _FakeMemorySearchItem:
    """Shape of SDK search hits: .memory_item."""
    def __init__(self, item: _FakeMemoryItem):
        self.memory_item = item


class _FakeUpdateResult:
    def __init__(self, update_id: str, operations: list[_FakeMemoryOperation]):
        self.update_id = update_id
        self.memory_operations = operations


class _FakeUpdatePoller:
    """Shape of the SDK's UpdateMemoriesLROPoller: .result() and .update_id."""
    def __init__(self, update_id: str, operations: list[_FakeMemoryOperation]):
        self.update_id = update_id
        self._operations = operations

    def result(self) -> _FakeUpdateResult:
        return _FakeUpdateResult(self.update_id, self._operations)


class _FakeSearchResult:
    def __init__(self, memories: list[_FakeMemorySearchItem]):
        self.memories = memories


class _FakeMemoryStoreDetails:
    """Shape of the SDK's MemoryStoreDetails: .name and .id."""
    def __init__(self, name: str):
        self.name = name
        self.id = f"store-{name}"


def _extract(text: str) -> list[str]:
    """Pull Decision:/Learned:/Best practice: lines out of reply text.

    The fake's stand-in for the service's LLM extraction. Same
    strict rule as the old store: prefix + a space, case-insensitive,
    empty values dropped, everything else ignored.
    """
    found = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.lower()
        for prefix in _PREFIXES:
            if low.startswith(prefix):
                rest = stripped[len(prefix):]
                if rest and rest[0].isspace():
                    value = rest.strip()
                    if value:
                        found.append(value)
                break
    return found


class FakeMemoryStores:
    """In-memory mimic of client.beta.memory_stores."""

    def __init__(self):
        self.stores: dict[str, _FakeMemoryStoreDetails] = {}
        # (store_name, scope) -> list of {"id", "content"} in record order
        self._memories: dict[tuple[str, str], list[dict]] = {}
        self._next_id = 1
        self._next_update = 1
        self.calls: list[tuple[str, dict]] = []

    # -- stores -----------------------------------------------------------
    def create(self, name: str, definition=None, description=None):
        self.calls.append(("create", {"name": name, "definition": definition,
                                      "description": description}))
        details = _FakeMemoryStoreDetails(name)
        self.stores[name] = details
        return details

    def get(self, name: str):
        self.calls.append(("get", {"name": name}))
        if name not in self.stores:
            raise KeyError(f"no memory store named {name!r}")
        return self.stores[name]

    # -- update -----------------------------------------------------------
    def begin_update_memories(self, name: str, *, scope: str, items,
                              previous_update_id=None, update_delay: int = 0):
        self.calls.append(("begin_update_memories", {
            "name": name, "scope": scope, "items": items,
            "previous_update_id": previous_update_id,
            "update_delay": update_delay}))
        if name not in self.stores:
            raise KeyError(f"no memory store named {name!r}")
        key = (name, scope)
        bucket = self._memories.setdefault(key, [])
        seen = {m["content"] for m in bucket}
        operations = []
        for item in items or []:
            content = item.get("content", "") if isinstance(item, dict) else ""
            for value in _extract(content):
                if value in seen:
                    continue  # consolidation: duplicates merge
                seen.add(value)
                memory_id = f"mem-{self._next_id}"
                self._next_id += 1
                bucket.append({"id": memory_id, "content": value})
                operations.append(_FakeMemoryOperation(
                    "create", _FakeMemoryItem(memory_id, value)))
        update_id = f"upd-{self._next_update}"
        self._next_update += 1
        return _FakeUpdatePoller(update_id, operations)

    # -- search -----------------------------------------------------------
    def search_memories(self, name: str, *, scope: str, items, options=None):
        self.calls.append(("search_memories", {
            "name": name, "scope": scope, "items": items, "options": options}))
        if name not in self.stores:
            raise KeyError(f"no memory store named {name!r}")
        query = ""
        for item in items or []:
            if isinstance(item, dict):
                query += " " + str(item.get("content", ""))
        words = [w for w in re.split(r"\W+", query.lower()) if w]
        limit = 5
        if isinstance(options, dict):
            limit = options.get("max_memories", 5)
        elif options is not None:
            limit = getattr(options, "max_memories", 5)
        bucket = self._memories.get((name, scope), [])
        hits = []
        for entry in reversed(bucket):  # newest first
            text = entry["content"].lower()
            if not words or any(w in text for w in words):
                hits.append(_FakeMemorySearchItem(
                    _FakeMemoryItem(entry["id"], entry["content"])))
            if len(hits) >= limit:
                break
        return _FakeSearchResult(hits)
