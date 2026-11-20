# 03 — scheduled work

The team from `01-team-of-agents`, running on a schedule. Same
Leader-plans / delegates / integrates flow — the new part is only
*when* it runs and *what gets recorded*.

No cloud scheduler, no daemon. A plain script you put in crontab:
crontab wakes it up, the script decides what is due, runs it, logs
it, exits.

## The shape

```
crontab ──▶ runner.py --once ──▶ state.json: last_run per job
                                  │
                                  ▼ due? (now - last_run >= interval)
                                  │
                            yes ──▶ Team.run(task) ──▶ runs.jsonl (one record)
                                  │                     + short report on stdout
                            no  ──▶ skip, print next due time
```

## The job definition

`jobs.json` — one object per job:

```json
{
  "jobs": [
    {
      "name": "quote-service-check",
      "task": "Check the quote service is healthy: verify GET /quote ...",
      "interval_seconds": 86400,
      "cron": "0 9 * * *",
      "description": "Daily health check of the quote service."
    }
  ]
}
```

- `name` — unique id, used by `--job` and in the log.
- `task` — what the team should do; handed straight to `Team.run`.
- `interval_seconds` — how often it runs. **This is what the
  runner uses.** 86400 = daily, 604800 = weekly.
- `cron` — display only. It records the crontab intent ("daily at
  9am") so a human can read it; **there is no cron parser**. The
  runner is interval-based, crontab just calls it.
- `description` — one-liner shown by `--list`.

`jobs.py` is the loader (`load_jobs`) plus the due-ness check on the
`Job` itself: `job.is_due(last_run, now)`.

## How due-ness works

`state.json` holds one timestamp per job:

```json
{ "last_run": { "quote-service-check": 1763596800.0 } }
```

A job is due when it has never run, or `now - last_run >=
interval_seconds`. After every run — even a failed one, so a broken
job doesn't re-fire on every cron tick — `last_run` is updated.
Each run also appends one JSON record to `runs.jsonl`:

```json
{"job": "quote-service-check", "started": "2026-11-19T09:00:00+00:00",
 "finished": "2026-11-19T09:00:04+00:00", "status": "ok",
 "steps": [{"handle": "researcher", "subtask": "..."}, ...],
 "reply_excerpt": "Done — Researcher, QA."}
```

`status` is `ok`, `partial` (a specialist failed but the team
finished), or `error` (the run itself raised).

## Commands

```bash
cd ~/workspace/foundry-team-tutorial/03-scheduled-work
python3 tests/test_scheduled.py   # 10 tests, plain script, no pytest
python3 demo.py                   # fake client, runs one job, shows the log entry
python3 runner.py --list          # jobs + next due time
python3 runner.py --job quote-service-check   # run one job now, due or not
python3 runner.py --once          # run every due job, then exit
```

The demo writes its state and log to a temp dir, so it never touches
the real `state.json` / `runs.jsonl`. `--job` and `--once` use the
real files in this folder.

## Putting it in crontab

Run `--once` often (hourly is the usual choice) and let due-ness
decide what actually runs — that way a daily job runs once a day
even if the machine was off at 9am. Add with `crontab -e`:

```
0 * * * * cd ~/workspace/foundry-team-tutorial/03-scheduled-work && python3 runner.py --once >> cron.log 2>&1
```

The `cron` field in `jobs.json` is the human-readable version of
the same intent (e.g. `0 9 * * *` for the daily check); the runner
never parses it. If you want a job to run at a specific wall-clock
time rather than "every N seconds from whenever it last ran", make
the interval match the crontab cadence (daily job + daily cron line
at 9am) — the first run anchors the schedule.

## Files

| File | What it is |
|---|---|
| `foundry_client.py`, `team.py`, `members.py`, `fake_client.py` | Copied unchanged from `~/workspace/foundry-team/` (the 01 team). |
| `jobs.py` | `Job` (name, task, interval, optional cron string) + `load_jobs` + `is_due`. |
| `jobs.json` | 2 example jobs: daily `quote-service-check`, weekly `research-roundup`. |
| `runner.py` | The cron-friendly CLI: `--once`, `--job <name>`, `--list`. Owns `state.json` and `runs.jsonl`. |
| `demo.py` | End-to-end with the fake client: runs one job, prints the report and the `runs.jsonl` entry. |
| `tests/test_scheduled.py` | Plain-script tests, no pytest. |
| `state.json`, `runs.jsonl`, `cron.log` | Created by running the runner; not checked in. |

## What it does not do (next steps)

- **No Teams / email reporting.** The report goes to stdout and
  `runs.jsonl` only. The next step is a reporter that reads the
  latest record and posts it — same shape as the looper's Teams
  webhook (private channel, 28 KB limit), or an email draft.
- **No real Azure yet.** Everything runs on the fake client. Swap
  `FakeFoundryClient` for `FoundryAgentClient` + `load_members()`
  the way `01-team-of-agents/demo.py` does `--real`.
- **No retries or alerting.** A job that errors is logged as
  `error` and retried at its next interval; nothing pages anyone.
