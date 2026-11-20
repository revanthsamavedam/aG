"""Managed memory — the Foundry Memory Service, per user.

This replaces the hand-rolled SQLite store that used to live here.
That version is in git history (commits c4dd80bd and 9b75a064 on
revanthsamavedam/aG) if you want to see what the service replaces:
a local table, a regex extractor, and keyword search. The service
now owns all three — storage, extraction, consolidation, retrieval —
and this module is a thin wrapper that speaks its API.

The service, in one paragraph (Microsoft Learn, "Create and use
memory in Foundry Agent Service", Memory is in public preview):
a memory store is a managed, long-term store attached to an agent.
You create one store per agent, then the service extracts what
matters from conversation content, consolidates duplicates, and
retrieves the relevant memories when asked. It is backed by Cosmos
DB. The Python SDK is azure-ai-projects (>= 2.3.0); every call below
goes through ``project_client.beta.memory_stores``.

The per-user rule survives the move, and it is the service's own
shape, not ours: every call carries a ``scope``, and the scope IS
the user id. Each scope keeps an isolated collection of memory
items, so one user's memories are never returned for another user.
When the memory search tool is attached to an agent instead, the
same isolation is ``scope="{{$userId}}"`` plus an
``x-memory-user-id`` header on each response call (or the caller's
Entra identity as a fallback). The low-level APIs used here take
the scope explicitly on every call.

What changed vs the SQLite version:
  * No local file. The store lives in Foundry (Cosmos DB under it).
  * No regex extractor. You hand the service the raw reply text;
    it extracts the facts. The fake service in fake_memory.py
    extracts Decision:/Learned:/Best practice: lines so the demo
    stays deterministic — that is the fake's stand-in for the
    service's LLM extraction, not what the real service does.
  * No dump-all recall. The service retrieves by relevance to a
    query, so build_memory_context() searches with the task text
    and injects only the memories relevant to this task. That is
    also how the service is meant to be used: contextual memories
    are retrieved per turn, based on the latest messages.
  * No member attribution. The service stores content, not our
    member handle, so the injected block lists memories without
    an (@dev) tag. The SQLite version kept the handle.
  * No clear(). Deleting individual memories is a separate
    memory-items API the wrapper does not cover; use the portal
    or the items API for that.

Real run: FOUNDRY_PROJECT_ENDPOINT plus the chat and embedding
model deployment names, then connect_from_env(). Auth is
DefaultAzureCredential (Entra) — the production path. The module
imports with no Azure SDK installed; the SDK import happens inside
connect_from_env(), so tests and the demo (fake) never need it.
"""
from __future__ import annotations

import os

# The message item shape the service takes for update and search
# (Learn doc, "Add memories" / "Search for memories" samples).
# A plain dict is what the SDK accepts for items.
def _message_item(text: str) -> dict:
    return {"role": "user", "content": text, "type": "message"}


class ManagedMemoryStore:
    """One Foundry memory store — one team, many users.

    ``memory_stores`` is the operations object, duck-typed so the
    fake can stand in: the real one is
    ``project_client.beta.memory_stores`` (azure-ai-projects), the
    fake is ``FakeMemoryStores`` from fake_memory.py. ``store_name``
    is the team id (one dedicated store per agent, per the docs).
    ``definition`` is passed straight to create(): the real path
    supplies a MemoryStoreDefaultDefinition, the fake takes a dict.
    """

    def __init__(self, memory_stores, store_name: str, definition=None):
        self.memory_stores = memory_stores
        self.store_name = store_name
        self.definition = definition

    def ensure_store(self):
        """Return the store, creating it first if it does not exist.

        Create needs a definition (chat + embedding model). The real
        path gets one from connect_from_env(); without it, this
        raises a clear error instead of failing inside the SDK.
        """
        try:
            return self.memory_stores.get(self.store_name)
        except Exception:  # noqa: BLE001 — any "not found" means create
            pass
        if self.definition is None:
            raise ValueError(
                f"memory store {self.store_name!r} does not exist and no "
                "definition was given — pass chat_model/embedding_model "
                "via connect_from_env() so the store can be created.")
        return self.memory_stores.create(
            name=self.store_name,
            definition=self.definition,
            description=f"Memory store for team {self.store_name}")

    def record(self, user_id: str, text: str, update_delay: int = 0) -> list[dict]:
        """Hand one reply to the service for extraction.

        The service preprocesses and postprocesses the content —
        extraction and consolidation — which takes about a minute
        on the real service, so begin_update_memories is a
        long-running operation: we wait for the poller here and
        return the operations it performed, as plain dicts:
        {"kind", "memory_id", "content"}. ``scope`` is the user id;
        that is the per-user rule, enforced on every call.
        ``update_delay=0`` triggers the update immediately instead
        of waiting for inactivity (the docs use 0 in samples; the
        production default is 300 seconds).
        """
        poller = self.memory_stores.begin_update_memories(
            name=self.store_name,
            scope=user_id,
            items=[_message_item(text)],
            update_delay=update_delay,
        )
        result = poller.result()
        return [{"kind": str(op.kind),
                 "memory_id": op.memory_item.memory_id,
                 "content": op.memory_item.content}
                for op in result.memory_operations]

    def recall(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        """Memories relevant to ``query``, for this user only.

        search_memories is semantic on the real service; the fake
        does keyword match. Either way the scope is the user id, so
        another user's memories are never returned. Returns plain
        dicts: {"memory_id", "content"}.
        """
        response = self.memory_stores.search_memories(
            name=self.store_name,
            scope=user_id,
            items=[_message_item(query)],
            options={"max_memories": limit},
        )
        return [{"memory_id": m.memory_item.memory_id,
                 "content": m.memory_item.content}
                for m in response.memories]


# -- injection ----------------------------------------------------------

def build_memory_context(store: ManagedMemoryStore, user_id: str,
                         task: str, limit: int = 5) -> str:
    """Format one user's memories relevant to ``task`` as a prompt block.

    Empty string when nothing relevant is stored — the wrapper then
    injects nothing, so a first run behaves exactly like a
    memory-less run. Unlike the SQLite version, this does not dump
    every stored memory: it asks the service what is relevant to
    this task, and injects only that.
    """
    memories = store.recall(user_id, task, limit=limit)
    if not memories:
        return ""
    lines = [f"[Memory for user '{user_id}' — relevant memories from "
             f"earlier runs, follow them unless the task says otherwise]"]
    for m in memories:
        lines.append(f"- {m['content']}")
    return "\n".join(lines)


def run_team_with_memory(team, task: str, store: ManagedMemoryStore,
                         user_id: str) -> str:
    """Run the team with one user's memory: inject, run, record.

    1. build_memory_context() asks the service for memories
       relevant to this task, for this user.
    2. The block is prepended to the task prompt as additional
       instructions — the ONLY injection point. Team.run itself is
       untouched (vendored file, not modified).
    3. After the run, every specialist reply in team.last_results
       is handed to the service (record), which extracts and
       stores what matters under this user's scope, so the next
       run for this user remembers this one. Another user's memory
       is untouched.
    Returns the team's final answer, same as Team.run.
    """
    context = build_memory_context(store, user_id, task)
    prompt = task
    if context:
        prompt = (f"{context}\n\n"
                  f"[Additional instructions — apply the memory "
                  f"above to this task]\n{task}")
    final = team.run(prompt)
    recorded = []
    for member, reply in team.last_results:
        recorded.extend(store.record(user_id, reply))
    if recorded:
        team.print(f"🧠 Recorded {len(recorded)} memory entr"
                   f"{'y' if len(recorded) == 1 else 'ies'} "
                   f"for user '{user_id}' (store '{store.store_name}').")
    return final


# -- real connection ------------------------------------------------------

def connect_from_env(store_name: str) -> ManagedMemoryStore:
    """Build the real store from environment variables.

    Needs FOUNDRY_PROJECT_ENDPOINT, plus
    MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME and
    MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME (the models that
    process memory content). Auth is DefaultAzureCredential
    (Entra); the Learn doc covers key-based auth as the alternative
    when roles are not feasible. Raises a clear error when a
    variable is missing, before importing the SDK.
    """
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    chat_model = os.environ.get("MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME")
    embedding_model = os.environ.get(
        "MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME")
    missing = [name for name, value in (
        ("FOUNDRY_PROJECT_ENDPOINT", endpoint),
        ("MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME", chat_model),
        ("MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME", embedding_model),
    ) if not value]
    if missing:
        raise RuntimeError(
            "missing environment variables for the real Memory Service: "
            + ", ".join(missing))

    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import (
        MemoryStoreDefaultDefinition, MemoryStoreDefaultOptions)
    from azure.identity import DefaultAzureCredential

    project_client = AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    )
    definition = MemoryStoreDefaultDefinition(
        chat_model=chat_model,
        embedding_model=embedding_model,
        options=MemoryStoreDefaultOptions(
            chat_summary_enabled=True,
            user_profile_enabled=True,
        ),
    )
    return ManagedMemoryStore(
        project_client.beta.memory_stores, store_name,
        definition=definition)
