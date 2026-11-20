# 04 — evaluation: score the team, track it over time

The team from `01-team-of-agents` works. How do you know it still
works after you change a prompt, swap an agent, or touch the Leader?
You run a **task suite** against it and compare runs.

This folder adds exactly that: a small suite of tasks with checks, a
runner that scores the team once per task, one JSON record per run in
`results/`, and a comparison that shows what changed between two runs.
Everything runs on the fake client — no Azure, no key.

## How a task and its checks are defined

`tasks.json` — one entry per task, a prompt plus checks on the answer:

```json
{
  "id": "build-quote-service",
  "task": "Build a fastapi service with a random quote about nvidia",
  "checks": [
    {"type": "contains", "text": "FastAPI"},
    {"type": "contains", "text": "Nvidia"},
    {"type": "not_contains", "text": "TODO"},
    {"type": "regex", "pattern": "GET /quote(/nvidia)?"}
  ]
}
```

Three check types, in `eval.py:score_check`:

- `contains` — the answer must mention the text (case-insensitive).
- `not_contains` — the answer must not mention it (case-insensitive).
- `regex` — `re.search` with the pattern as written, so it is exact:
  use it when a substring is too loose, e.g. an endpoint shape.

A task passes only when **every** check passes.

## How scoring works

`run_suite` in `eval.py` runs each task through the team once,
scores the checks against the **final integrated answer** — what the
user actually sees — and prints one line per task plus an overall
pass rate (tasks passed / tasks run):

```
[PASS] build-quote-service (4/4 checks)
[PASS] research-build-readme (3/3 checks)
[FAIL] test-quote-service (2/3 checks)
        failed check: {'type': 'contains', 'text': '503'}
Overall: 3/4 tasks passed (11/12 checks) — pass rate 75%
```

Two things worth knowing before you write checks:

1. **Checks see the integrated answer, not the raw specialist
   replies.** The runner records the per-step replies in the run
   record, but scores only the final answer.
2. **The template integration truncates.** With no model planner the
   Leader joins each step's first line, cut to 200 characters. A
   keyword buried late in a specialist's reply will not be seen by a
   check — that is a real eval lesson, not a bug: you score the
   output, not the work.

## How to read a comparison

`python3 eval.py --compare results/<run1>.jsonl results/<run2>.jsonl`
compares two runs task by task:

```
Comparing 20261119T235500 (baseline) -> 20261119T235501 (degraded)
  pass rate: 100% -> 75%
  REGRESSION  build-quote-service: pass -> fail
  unchanged   research-build-readme
  unchanged   test-quote-service
  unchanged   review-quote-service
```

- `REGRESSION` — a task that passed and now fails. This is the one
  to chase.
- `FIX` — a task that failed and now passes.
- `unchanged` — same outcome in both runs.
- `new task` / `removed` — a task id present in only one run (the
  suite changed between runs, so compare with care).

## Run it

```bash
cd ~/workspace/foundry-team-tutorial/04-evaluation

python3 tests/test_evaluation.py   # 22 tests, plain script, no pytest
python3 demo.py                    # baseline run, degraded run, comparison
python3 eval.py                    # run the suite once, record to results/
python3 eval.py --degraded         # same, with the weaker fake Dev
python3 eval.py --compare results/<run1>.jsonl results/<run2>.jsonl
```

## The demo, and what it catches

`demo.py` runs the suite twice. The second run uses a degraded fake
client: Dev's reply drops the `/quote/nvidia` endpoint (swapped in
via the fake client's `replies=` override — the vendored
`fake_client.py` is untouched). The comparison shows exactly one
regression, `build-quote-service` pass -> fail, because its `Nvidia`
check no longer matches. That is the whole point of the folder: a
small, boring suite that notices the day a specialist quietly stops
doing part of its job.

## Files

| File | What it is |
|---|---|
| `foundry_client.py`, `team.py`, `members.py`, `fake_client.py` | Vendored unchanged from `~/workspace/foundry-team/` (see `01-team-of-agents` for what each does). |
| `tasks.json` | The suite: 4 tasks, each with 2–4 checks. |
| `eval.py` | `load_tasks`, `score_check`, `run_suite`, `write_record` / `load_record`, `compare_records` + CLI. |
| `demo.py` | Baseline run, degraded run, comparison — the regression caught live. |
| `tests/test_evaluation.py` | Plain-script tests, no pytest. |
| `results/` | One `<run_id>.jsonl` per run (run id = timestamp). |

## What it does not do

- **No LLM judge.** Checks are string matches on the answer. That
  keeps the suite deterministic and free, and it is also its limit:
  a check cannot tell a good answer from a lucky substring. An
  LLM-as-judge scorer is the natural next step, not this folder.
- **No real Azure runs yet.** The runner takes any team factory, so
  pointing it at the real client is one `make_team` change — but the
  suite as shipped runs fake-only, like everything else in this
  tutorial.
