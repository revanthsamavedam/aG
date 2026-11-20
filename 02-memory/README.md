# 02 — memory: the team remembers across conversations

The team from `01-team-of-agents/` forgets everything when a run
ends. This folder adds the smallest useful memory: after each run,
the specialists' replies are mined for `Decision:` / `Learned:` /
`Best practice:` lines, stored in SQLite, and injected into the next
run's prompt as additional instructions. The team stops re-deciding
what it already decided.

Stdlib only (`sqlite3`). Fake client only — no Azure, no key.

## The loop

```
run 1 ──▶ specialists reply ──▶ record_from_reply() mines lines
                                     │  (memory.py)
                                     ▼
                              SQLite: entries table
                                     │
run 2 ──▶ build_memory_context() formats a block ──▶ prepended to
          the task prompt ──▶ Team.run(prompt) ──▶ replies mined again
```

## Files

| File | What it is |
|---|---|
| `foundry_client.py`, `team.py`, `members.py` | Vendored **unchanged** from `~/workspace/foundry-team/` (sha256-verified). |
| `fake_client.py` | Vendored, with **one extension**: each canned reply ends with `Decision:` / `Learned:` / `Best practice:` lines, so the demo has something to extract. That is the only change. |
| `memory.py` | `MemoryStore`, `build_memory_context()`, `run_team_with_memory()`. |
| `demo.py` | Two runs with the fake client: run 1 records, run 2 injects. |
| `tests/test_memory.py` | Plain-script tests, no pytest. |

## The store — `memory.py`, `MemoryStore`

One table, one `kind` column, scoped by `team_id` so several teams
can share one file:

```sql
CREATE TABLE entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,   -- "decision" | "learning" | "best_practice"
    member     TEXT NOT NULL,   -- the specialist's handle, e.g. "dev"
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL    -- UTC ISO timestamp
);
```

API:

```python
store = MemoryStore("team_memory.db")          # or ":memory:" for tests
store.record_from_reply(team_id, member, text) # -> list of entries recorded
store.recall(team_id, query=None, limit=10)    # -> newest first
store.list_entries(team_id, kind=None)         # -> newest first
store.clear(team_id, kind=None)                # -> how many deleted
```

`recall()` with a query is keyword match: an entry matches if its
text contains any word of the query (case-insensitive substring).
No query returns the most recent entries. No embeddings, no ranking
beyond recency — that is a deliberate v1 limit; a real retriever can
replace `recall()` later without touching the rest.

## Extraction rules — `MemoryStore.record_from_reply()`

Only lines that start with one of the three prefixes count, and the
prefix must be followed by a space:

```
Decision: use the public JSON quotes API.      -> decision
Learned: the free API needs no key.            -> learning
Best practice: add a 60s cache.               -> best_practice
```

- Prefix match is case-insensitive (`decision:` counts).
- The value is the rest of the line, stripped; empty values dropped.
- Every other line is ignored — no guessing, no LLM call.
- One line = one entry. A reply with no prefixed lines records
  nothing.

This is why the convention matters: the specialists' instructions
(in the real Foundry agents, not the fake) should tell them to end
replies with `Decision:` / `Learned:` / `Best practice:` lines.
Memory is only as good as what the agents bother to write down.

## Injection — where it happens

`memory.py`, `run_team_with_memory(team, task, store, team_id)`:

```python
context = build_memory_context(store, team_id)   # "" when no memory yet
prompt = task
if context:
    prompt = (f"{context}\n\n"
              f"[Additional instructions — apply the team memory "
              f"above to this task]\n{task}")
final = team.run(prompt)                          # Team.run is untouched
for member, reply in team.last_results:           # record this run
    store.record_from_reply(team_id, member.handle, reply)
```

The injection point is that one prepend. `Team.run` (vendored)
never sees memory as a parameter — it just gets a longer prompt.
`build_memory_context()` formats the block, grouped by kind:

```
[Team memory — from earlier runs, follow it unless the task says otherwise]
Decisions:
- use the public JSON quotes API, not a paid quotes API. (@researcher)
Learnings:
- the free API returns one random quote per request, no key required. (@researcher)
Best practices:
- keep main.py small — one route per endpoint, no business logic in the route handler. (@dev)
```

## Run it

```bash
cd ~/workspace/foundry-team-tutorial/02-memory
python3 tests/test_memory.py   # 27 tests
python3 demo.py --fresh        # two runs, fresh store
python3 demo.py                # re-run: run 2's block now includes older entries too
```

The store lives at `./team_memory.db` (`--db` to change it). Re-run
the demo and run 2's memory block will include entries from the
previous run too — memory persists across processes, not just
across runs.

## What it does not do (v1 limits)

- **No dedup.** The fake client gives the same canned replies every
  run, so run 2 records the same 5 entries again. Real agents vary
  their replies; a production store wants a dedup or upsert on
  `(team_id, kind, text)`.
- **No retrieval beyond keywords.** `recall(query)` is substring
  match. Embeddings / vector search is the obvious next step.
- **No forgetting.** `clear()` deletes by team or kind, but there is
  no expiry or supersede — an old decision stays until cleared.
- **Prompt injection only.** Memory reaches the team through the
  task prompt, not through a Foundry thread or a tool. That keeps
  the vendored `team.py` untouched, at the cost of prompt length.
