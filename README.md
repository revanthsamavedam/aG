# foundry-team-tutorial

Six things to build around Azure AI Foundry agents, each in its own
folder. Each folder is self-contained: working code, a tutorial README
that teaches it code-first, plain-script tests, and a demo that runs
with no Azure needed.

The order is the build order. Each folder stands on the one before
it, but runs on its own.

| Folder | What it is |
|---|---|
| `01-team-of-agents/` | A Leader that plans (max 3 steps), delegates to specialist Foundry agents, and integrates their replies. The foundation. |
| `02-memory/` | Memory across conversations: the managed Foundry Memory Service (per user, scoped by user id) — extraction, consolidation, and retrieval done by the service, with a fake for offline learning. |
| `03-scheduled-work/` | Scheduled work: Foundry Routines — one trigger + one agent per routine, dispatch on demand, run history kept by Foundry. |
| `04-evaluation/` | Evaluation: a task suite with checks, a runner that scores the team, results recorded over time, and a comparison that shows what changed between two runs. |
| `05-foundry-config/` | Config as code: the whole setup — project, agents, memory, routines, Teams triggers, MCP tools — in one `foundry.yaml`, plus a provisioner CLI that creates it all, a manual portal guide, and the missing parent/child piece (the Leader as a real Foundry agent, via an Agent Framework sequential workflow or A2A). |
| `06-chat-ui/` | A generic chat UI: React + CopilotKit talking directly to a FastAPI agent server over AG-UI (no Node runtime — FastAPI is the runtime), with a registry for your own React components the agent can trigger (RiskCard example). |

## Prerequisites

- Python 3.12. Tests and demos are stdlib only — no packages to
  install, no Azure needed to learn: every folder runs against a
  fake with canned replies.
- The real clients need the SDK: `pip install
  "azure-ai-projects>=2.4.0" azure-identity` (Memory needs >=
  2.3.0, Routines >= 2.4.0). The SDK import happens inside the
  `connect_from_env()` functions, so nothing else needs it.
- For live runs: a Foundry project, the specialist agent ids, and
  a key/token (via secure entry, never pasted in chat). See
  `01-team-of-agents/README.md`, "Wiring real agents".
- Folder 05's provisioner needs the SDK plus a login:
  `pip install "azure-ai-projects>=2.4.0" azure-identity` and
  `az login`, with `FOUNDRY_PROJECT_ENDPOINT` set. Its sequential
  workflow file needs `pip install agent-framework
  agent-framework-foundry` to run.

## Run each demo

```bash
cd 01-team-of-agents && python3 demo.py                      # team, fake client
cd ../02-memory && python3 demo.py                           # memory service, two users
cd ../03-scheduled-work && python3 demo.py                  # routines, fake service
cd ../04-evaluation && python3 demo.py                      # baseline, degraded, compare
cd ../05-foundry-config && python3 demo.py                  # config, plan + apply to fake
cd ../06-chat-ui/agent && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/uvicorn server:app --port 8000  # agent server, demo mode
# then in another terminal: cd ../frontend && npm install && npm run dev  # chat UI on http://localhost:5173
```

## Run each test suite

```bash
cd 01-team-of-agents && python3 tests/test_foundry_team.py  # 13 tests
cd ../02-memory && python3 tests/test_memory.py              # 38 tests
cd ../03-scheduled-work && python3 tests/test_routines.py   # 46 tests
cd ../04-evaluation && python3 tests/test_evaluation.py      # 22 tests
cd ../05-foundry-config && python3 -m unittest discover -s tests -v  # 40 tests
cd ../06-chat-ui/agent && python3 test_wiring.py             # wiring check: tool output matches the RiskCard contract
```

159 tests total, all plain scripts, no pytest. (06's check is a
wiring check, not part of the 159.)

## What this repo is not

- Not a production platform. The fake clients are canned replies;
  the real client is untested against a live Foundry project until
  someone wires in an endpoint and agent ids.
- Not the looper. The Leader's planning calls reuse the looper's
  provider (a plain model call), but the specialists are Foundry
  agents, and the loop here is the Foundry run lifecycle, not the
  looper's loop.
- No auth beyond a Bearer token in v1. Entra ID / managed identity
  is the production path, documented in `01`, not wired in.

## The six features, in one line each

1. **Team** — one Leader, five specialists, each a separate Foundry
   agent with its own instructions and tools.
2. **Memory** — what the team decided and learned survives the
   conversation it happened in, in the managed Memory Service,
   per user.
3. **Scheduled work** — the team runs on a clock, not just when
   someone asks, via Foundry Routines.
4. **Evaluation** — you know whether a change made the team better
   or worse, because the suite says so.
5. **Config as code** — the whole setup in one YAML file and a
   provisioner that rebuilds it, with the Leader as a real Foundry
   agent (parent) delegating to the specialists (children).
6. **Chat UI** — a generic chat UI for the team: the browser talks
   straight to a FastAPI agent server over AG-UI, and the agent can
   trigger your own React components instead of plain text.
