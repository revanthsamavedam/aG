# 01 — Team of agents

The Team Room's Leader pattern, but the specialists are **Foundry agents
hosted in Azure** instead of looper members. The Leader plans (max 3
ordered steps), delegates each step to the right specialist Foundry
agent, and integrates their replies into one final answer.

The Leader's planning/integration calls reuse the looper's provider (a
plain OpenAI-compatible model call), not a Foundry agent — the Leader
is orchestration, not a hosted agent. When no provider/key is available
the Leader falls back to a keyword plan, so the team still runs.

Local only. No database, no repo push, nothing created in Azure.

## The shape

```
task ──▶ Leader plans (≤3 steps, dependencies first)
              │  model plan via the looper provider, else keyword plan
              ▼
        step 1 ──▶ Foundry agent (thread → message → run → poll → reply)
        step 2 ──▶ Foundry agent (prompt carries step 1's result)
        step 3 ──▶ Foundry agent (prompt carries steps 1–2)
              │
              ▼
        Leader integrates ──▶ one final answer
```

Each delegation is a **fresh thread** — the Leader passes earlier
results in the prompt, so specialists build on each other without
sharing a thread. A specialist that fails does not kill the run: its
error is recorded as that step's result and the team continues.

## Tutorial — how it works, code-first

Three ideas, three functions: `Leader.plan`, `Team.run`,
`Leader.integrate`. Everything else is plumbing.

### 1. The Leader plans — `team.py`, `Leader.plan()`

`plan(task, members)` tries the model first, falls back to keywords:

- **Model plan** (`_plan_with_model`): builds a system prompt listing
  the specialists as `@handle (Name: role)`, asks for at most 3
  `STEP @handle: subtask` lines, and calls `planner.call(...)` —
  that's the looper's `OpenAICompatibleClient`, one chat completions
  POST. `_parse_plan` reads the STEP lines with a regex, keeps only
  handles that exist in the team, dedupes, caps at 3. A line naming
  an unknown handle is ignored.
- **Keyword plan** (`_plan_with_keywords`): scores the task against
  each handle's words in `KEYWORDS` (researcher: research / find /
  compare …, dev: build / code / api …, qa: test / verify …,
  writer: write / doc / readme …, reviewer: review / audit …),
  keeps the ones that score above 0, orders them by `PHASE_ORDER`
  (researcher → dev → qa → writer → reviewer, so dependencies come
  first), caps at 3. If nothing matches, the first member gets the
  whole task.

The planner comes from `_default_planner()`: it imports the looper
provider from `~/workspace/custom-looper` and resolves
`LOOPER_MODEL` / `LOOPER_API_KEY` / `LOOPER_BASE_URL`. No key, or no
looper on disk, means the planner is `None` and the Leader uses the
keyword plan. The team always runs.

### 2. The team delegates — `team.py`, `Team.run()`

`run(task)`:

- Splits members into available (has an agent id) and skipped (no
  agent id — printed with a note, never called). If nobody is
  available it says so and returns.
- Plans, prints the plan.
- For each step, in plan order: builds the prompt as the subtask plus
  `(Original task: ...)` plus, when there are earlier results, a
  `[Results so far]` block with the first 400 chars of each earlier
  reply. Then calls `client.call_agent(member.agent_id, prompt)`.
  If that specialist raises, the error is recorded as that step's
  result (`⚠️ Dev failed: ...`) and the run continues — one failing
  agent never kills the team.
- Each delegation is a fresh thread. Specialists never share a
  thread; the Leader passes earlier results in the prompt instead.

### 3. The Leader integrates — `team.py`, `Leader.integrate()`

- With a planner: joins the results as `## Name` + reply blocks,
  asks the model to integrate them into one final answer, keeping
  what each specialist found.
- Without one: the template fallback — `Done — Researcher, Dev,
  Writer.` plus the first line of each reply. The work is all in the
  joined text; the template just formats it.

### 4. The Foundry client — `foundry_client.py`

One agent call is five REST calls. `call_agent(agent_id, prompt,
thread_id=None)`:

1. `create_thread()` — `POST /threads` → thread id. Skipped when a
   thread id is passed in.
2. `add_message(thread_id, prompt)` — `POST
   /threads/{tid}/messages`, role user.
3. `create_run(thread_id, agent_id)` — `POST
   /threads/{tid}/runs` with `{"agent_id": ...}`. (The older
   Assistants API used `assistant_id` here; the Agent Service uses
   `agent_id`.)
4. Poll `get_run(thread_id, run_id)` every 2s until the status is
   terminal. `completed` → read the reply. `requires_action` →
   raise `FoundryError` (v1 has no function-tool handler).
   `failed` / `cancelled` / `expired` → raise with the run's
   `last_error`. Past `run_timeout` (600s, matching the server-side
   ~10 min expiry) → raise.
5. `_last_assistant_text(thread_id)` — `GET
   /threads/{tid}/messages`, take the latest assistant message,
   join its text parts.

All of it goes through `_request`, one urllib call with
`Authorization: Bearer <key>` and
`?api-version=2025-05-15-preview`. A `FoundryError` always names the
step and the HTTP status + body, so a bad endpoint, key, or agent id
is diagnosable from the error alone.

The `FakeFoundryClient` (`fake_client.py`) has the same
`call_agent` interface and returns canned replies per role — that's
what the demo and tests use, so everything runs with no Azure.

### Try it (10 minutes)

1. Run the demo and watch the plan and the delegation order:
   `python3 demo.py`
2. In `team.py`, find `_plan_with_keywords` and add one word to the
   dev list, e.g. `"endpoint"`. Re-run the demo and watch whether
   the plan changes.
3. In `demo.py`, change the task to something research-only
   (`"Compare the best free quote APIs"`) and watch the plan pick
   the researcher first.
4. Break it on purpose: set one member's agent id to `""` in a JSON
   config and run — the team skips it with a note and runs with the
   rest.

## Files

| File | What it is |
|---|---|
| `foundry_client.py` | `FoundryAgentClient` — stdlib-only (urllib) REST client for the Agent Service: create thread, add message, create run, poll, read the reply. |
| `team.py` | `Member`, `Leader` (plan + integrate), `Team.run(task)`. |
| `members.py` | The default team of 5 + loading from env or a JSON config. |
| `fake_client.py` | `FakeFoundryClient` — same `call_agent` interface, canned replies per role. |
| `demo.py` | Runs a task through the team (fake by default, `--real` for Azure). |
| `tests/test_foundry_team.py` | Plain-script tests, no pytest. |
| `team.example.json` | Example JSON config with placeholder agent ids. |

## Quick start (no Azure)

```bash
cd ~/workspace/foundry-team-tutorial/01-team-of-agents
python3 tests/test_foundry_team.py   # 13 tests
python3 demo.py                      # fake client, two tasks
```

The demo runs the Nvidia FastAPI task twice: once as written (keyword
plan → Dev only) and once in a richer form that exercises the full
researcher → dev → writer delegation. With a provider key set
(`LOOPER_API_KEY` etc.) the Leader plans with the model instead.

## Wiring real agents

1. **Create the specialists in the Foundry portal.** One agent per
   member, each with its own instructions for its role:
   Researcher, Dev, QA, Writer, Reviewer. Copy each agent's id
   (`asst_...`).
2. **Give the Researcher web access.** Foundry agents get web access
   natively via the Web Search tool (Grounding with Bing Search) —
   attach it to the Researcher when you create the agent in the
   portal, so it can actually look things up. Attaching
   `WebSearchTool` directly to a prompt agent needs no separate Bing
   resource; only the explicit `bing_grounding` tool needs a Bing
   project connection.
3. **Set the environment:**
   ```bash
   export FOUNDRY_PROJECT_ENDPOINT="https://<Resource>.services.ai.azure.com/api/projects/<Project>"
   export FOUNDRY_API_KEY="..."            # Entra ID token in production; API key works for v1
   export FOUNDRY_AGENT_RESEARCHER="asst_..."
   export FOUNDRY_AGENT_DEV="asst_..."
   export FOUNDRY_AGENT_QA="asst_..."
   export FOUNDRY_AGENT_WRITER="asst_..."
   export FOUNDRY_AGENT_REVIEWER="asst_..."
   ```
   Or put the agent ids in a JSON config (see `team.example.json`)
   and pass `--config team.json`.
4. **Run it for real:**
   ```bash
   python3 demo.py --real
   python3 demo.py --real --task "Build a fastapi service with a random quote about nvidia"
   ```
   Members with no agent id are skipped with a note; the team runs
   with whoever is wired up.

## What Revanth needs to give to go live

- The **project endpoint** (`FOUNDRY_PROJECT_ENDPOINT`).
- The **agent ids** for the specialists he creates in the portal.
- The **key/token** via secure entry, never pasted in chat. Entra ID
  is the production path; the API key is the v1 shortcut.

Nothing here calls Azure until `--real` is passed with those set.

## What it does not do (v1 limits)

- **No function tools.** If a run goes to `requires_action` (the agent
  wants a function-tool output), `call_agent` raises a clear error
  instead of hanging. The agents should be created with no function
  tools, or a tool-output handler gets added later.
- **No memory, no scheduling, no evaluation.** Those live in the
  sibling folders of this repo: `../02-memory`,
  `../03-scheduled-work`, `../04-evaluation`.
- **No auth beyond the Bearer token.** Entra ID / managed identity
  is documented as the production path but not wired in v1 (API key
  only, same as the looper's Azure support).
- **Runs expire server-side after ~10 min** waiting on tool output;
  the client's `run_timeout` (default 600s) matches that.
