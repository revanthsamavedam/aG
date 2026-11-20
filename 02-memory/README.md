# 02 — memory: the managed Memory Service, per user

The team from `01-team-of-agents/` forgets everything when a run
ends. This folder adds memory — but not a hand-rolled store. The
memory lives in the **Foundry Memory Service** (public preview,
Ignite 2025): a managed, long-term memory store natively integrated
with Foundry Agent Service, backed by Cosmos DB. The service owns
storage, extraction, consolidation, and retrieval. This folder is a
thin wrapper that speaks its API and keeps the tutorial's per-user
rule.

The hand-rolled SQLite version used to live here (a local table, a
regex extractor, keyword search). It is in git history — commits
`c4dd80bd` and `9b75a064` on `revanthsamavedam/aG` — if you want to
see what the service replaces. Do not build on it; it is the
"what the service does underneath" reference, not the main path.

## The service, in one paragraph

A memory store is a managed store attached to an agent. You create
one store per agent, then the service extracts what matters from
conversation content, consolidates duplicates, and retrieves the
relevant memories when asked. The Python SDK is `azure-ai-projects`
(>= 2.3.0); every call below goes through
`project_client.beta.memory_stores`. Source: Microsoft Learn,
"Create and use memory in Foundry Agent Service".

```
run 1 (user u1) ──▶ specialists reply ──▶ begin_update_memories()
                 (raw reply text)            │  scope = user id
                                             ▼
                                      Memory Service (Cosmos DB)
                                      extract + consolidate
                                             │
run 2 (same user, new task) ──▶ search_memories(task text)
          ──▶ relevant memories ──▶ prepended to the task prompt
run 2 (other user) ──▶ same store, different scope ──▶ nothing
```

## Files

| File | What it is |
|---|---|
| `foundry_client.py`, `team.py`, `members.py` | Vendored **unchanged** from `~/workspace/foundry-team/` (sha256-verified). |
| `fake_client.py` | Vendored, with **one extension**: each canned reply ends with `Decision:` / `Learned:` / `Best practice:` lines, so the fake service has something to extract. That is the only change. |
| `memory.py` | `ManagedMemoryStore`, `build_memory_context()`, `run_team_with_memory()`, `connect_from_env()`. |
| `fake_memory.py` | `FakeMemoryStores` — mimics `client.beta.memory_stores` offline. |
| `demo.py` | Two users with the fake service: run 1 records for `revanth`, run 2 runs as `alex` and shows his memory is empty, then records his own. |
| `tests/test_memory.py` | Plain-script tests, no pytest. |

## The API — `memory.py`, `ManagedMemoryStore`

One store per team (the store name is the team id), many users
inside it, partitioned by `scope`:

```python
store = ManagedMemoryStore(memory_stores, "nvidia-quote-service",
                           definition=definition)
store.ensure_store()                        # get, or create with the definition
store.record(user_id, reply_text)           # -> list of operations the service did
store.recall(user_id, query, limit=5)       # -> [{"memory_id", "content"}, ...]
```

The three service calls, exactly as the Learn doc shows them:

**Create the store** — one per agent, with the chat and embedding
model deployments that process memory content:

```python
from azure.ai.projects.models import (
    MemoryStoreDefaultDefinition, MemoryStoreDefaultOptions)

definition = MemoryStoreDefaultDefinition(
    chat_model=chat_model, embedding_model=embedding_model,
    options=MemoryStoreDefaultOptions(
        chat_summary_enabled=True, user_profile_enabled=True))
store = project_client.beta.memory_stores.create(
    name="my_memory_store", definition=definition,
    description="Memory store for the quote-service team")
```

**Record** — hand the service conversation content; it extracts
and consolidates (a long-running operation, about a minute on the
real service — we wait for the poller):

```python
poller = project_client.beta.memory_stores.begin_update_memories(
    name=store_name,
    scope=user_id,                       # <-- the per-user rule
    items=[{"role": "user", "content": reply, "type": "message"}],
    update_delay=0,                      # 0 = update immediately
)
result = poller.result()                 # .memory_operations: kind, memory_id, content
```

**Recall** — ask what is relevant to a query, for this user only:

```python
response = project_client.beta.memory_stores.search_memories(
    name=store_name,
    scope=user_id,                       # <-- the per-user rule
    items=[{"role": "user", "content": query, "type": "message"}],
    options={"max_memories": 5},
)
# response.memories: each .memory_item.memory_id / .memory_item.content
```

`connect_from_env(store_name)` builds the real store from
`FOUNDRY_PROJECT_ENDPOINT`,
`MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME`, and
`MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME`, with
`DefaultAzureCredential` (Entra). It raises a clear error when a
variable is missing, before importing the SDK.

## Scope is the per-user rule

`scope` is the partition key inside a store. Every call in this
folder passes `scope=user_id`, and the service never returns
another scope's memories. That is the same isolation the old
SQLite store enforced with `user_id` on every row — now it is the
service's own shape. Two more facts from the Learn doc:

- When the memory search tool is attached to an agent instead of
  calling these APIs directly, set `scope` to `"{{$userId}}"` and
  pass `x-memory-user-id: <user-id>` as a header on each response
  call. Without the header, the scope falls back to the caller's
  Entra identity (tenant + object id).
- For the low-level APIs used here, pass `scope` explicitly on
  every call. Automatic identity extraction is not supported
  for these operations.

## The memory search tool (the other way in)

Instead of calling update/search yourself, you can attach the tool
to a prompt agent and let the agent read and write memory during
conversations:

```python
from azure.ai.projects.models import MemorySearchPreviewTool, PromptAgentDefinition

tool = MemorySearchPreviewTool(
    memory_store_name=store_name, scope=user_id, update_delay=1)
agent = project_client.agents.create_version(
    agent_name="MyAgent",
    definition=PromptAgentDefinition(
        model=chat_model, instructions="...", tools=[tool]))
```

After each response the service calls `update_memories`
internally, debounced by `update_delay` (300 seconds by default;
1 in samples). This folder uses the low-level APIs instead, because
the team here is five separate agents plus a Leader — the wrapper
records each specialist's reply explicitly, rather than relying on
one agent's tool.

## Injection — where it happens

`memory.py`, `run_team_with_memory(team, task, store, user_id)`:

```python
context = build_memory_context(store, user_id, task)  # "" when nothing relevant
prompt = task
if context:
    prompt = (f"{context}\n\n"
              f"[Additional instructions — apply the memory "
              f"above to this task]\n{task}")
final = team.run(prompt)                          # Team.run is untouched
for member, reply in team.last_results:           # record this run
    store.record(user_id, reply)
```

The injection point is that one prepend. `Team.run` (vendored)
never sees memory as a parameter — it just gets a longer prompt.
One deliberate change from the SQLite version: `build_memory_context`
searches with the **task text** and injects only the memories
relevant to this task. The old version dumped every stored memory.
Relevance retrieval is how the service is meant to be used
("contextual memories are retrieved per turn, based on the latest
messages"), and it keeps the prompt short as memory grows.

## The fake — `fake_memory.py`

`FakeMemoryStores` mimics `client.beta.memory_stores` with the same
method names and result shapes (`.name`, `.memory_operations`,
`.memories`, `.memory_item.memory_id`, `.memory_item.content`,
poller `.result()`). What it simplifies, stated plainly:

- **Extraction:** the real service extracts with an LLM. The fake
  extracts `Decision:` / `Learned:` / `Best practice:` lines
  (prefix + a space, case-insensitive). Deterministic; the real
  service is not.
- **Consolidation:** the real service merges duplicate topics.
  The fake dedups identical content within a scope.
- **Retrieval:** the real service is semantic. The fake is keyword
  match on the query words, newest first, capped by max_memories.
- **Scoping:** exact. Everything is keyed by (store, scope), and
  a search in one scope never returns another scope's memories.
- **Latency:** the real update takes about a minute. The fake
  completes immediately.

Every fake call is recorded in `.calls`, so the tests assert the
exact shapes `ManagedMemoryStore` sends the service (scope,
update_delay, items, options).

## Run it

```bash
cd ~/workspace/foundry-team-tutorial/02-memory
python3 tests/test_memory.py   # 38 tests
python3 demo.py                # fake service, two users, isolation shown
```

Real run (needs a Foundry project; never paste credentials in chat):

```bash
export FOUNDRY_PROJECT_ENDPOINT="https://<account>.services.ai.azure.com/api/projects/<project>"
export MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME="<chat-model>"
export MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME="<embedding-model>"
pip install "azure-ai-projects>=2.3.0" azure-identity
python3 -c "from memory import connect_from_env; s = connect_from_env('nvidia-quote-service'); s.ensure_store()"
```

## What the service does underneath (and what this folder does not do)

- **No member attribution.** The service stores content, not our
  specialist handle, so the injected block lists memories without
  an `(@dev)` tag. The SQLite version kept the handle.
- **No clear().** Deleting individual memories is a separate
  memory-items API (create/read/update/list/delete individual
  records, in the latest preview). This wrapper does not cover it;
  use the portal or the items API.
- **No dedup key to manage.** Consolidation is the service's job;
  the fake's dedup is only its stand-in.
- **Preview.** Memory is in public preview; the API surface
  (under `client.beta`) can change. The SDK version verified
  against here is `azure-ai-projects` 2.11.0, which sends
  `api-version=v1` for memory stores. The Learn doc's REST
  examples use `2025-11-15-preview`; if the REST and SDK versions
  disagree, trust the SDK you have installed.
