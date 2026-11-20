# foundry-team-tutorial

Four things to build around Azure AI Foundry agents, each in its own
folder. Each folder is self-contained: working code, a tutorial README
that teaches it code-first, plain-script tests, and a demo that runs
with no Azure needed.

The order is the build order. Each folder stands on the one before
it, but runs on its own.

| Folder | What it is |
|---|---|
| `01-team-of-agents/` | A Leader that plans (max 3 steps), delegates to specialist Foundry agents, and integrates their replies. The foundation. |
| `02-memory/` | Memory across conversations: a SQLite store for decisions, learnings, and best practices, extracted from agent replies and injected into the next run. |
| `03-scheduled-work/` | Scheduled work: job definitions, a cron-friendly runner, a JSONL run log, and a printed report. |
| `04-evaluation/` | Evaluation: a task suite with checks, a runner that scores the team, results recorded over time, and a comparison that shows what changed between two runs. |

## Prerequisites

- Python 3.12, stdlib only. No packages to install.
- No Azure needed to learn: every folder runs against a fake Foundry
  client with canned replies. The real client is in `01` and is used
  only with `--real` plus your own project.
- For live runs: a Foundry project, the specialist agent ids, and a
  key/token (via secure entry, never pasted in chat). See
  `01-team-of-agents/README.md`, "Wiring real agents".

## Run each demo

```bash
cd 01-team-of-agents && python3 demo.py                      # team, fake client
cd ../02-memory && python3 demo.py --fresh                  # memory, two runs
cd ../03-scheduled-work && python3 demo.py                  # one scheduled job
cd ../04-evaluation && python3 demo.py                      # baseline, degraded, compare
```

## Run each test suite

```bash
cd 01-team-of-agents && python3 tests/test_foundry_team.py  # 13 tests
cd ../02-memory && python3 tests/test_memory.py              # 27 tests
cd ../03-scheduled-work && python3 tests/test_scheduled.py  # 10 tests
cd ../04-evaluation && python3 tests/test_evaluation.py      # 22 tests
```

72 tests total, all plain scripts, no pytest.

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

## The four features, in one line each

1. **Team** — one Leader, five specialists, each a separate Foundry
   agent with its own instructions and tools.
2. **Memory** — what the team decided and learned survives the
   conversation it happened in.
3. **Scheduled work** — the team runs on a clock, not just when
   someone asks.
4. **Evaluation** — you know whether a change made the team better
   or worse, because the suite says so.
