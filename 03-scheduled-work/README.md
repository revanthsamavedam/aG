# 03 — scheduled work: Foundry Routines

The team from `01-team-of-agents`, running on a schedule. Same
Leader-plans / delegates / integrates flow — the new part is only
*when* it runs, and Foundry owns the clock now.

No crontab, no daemon, no run log to maintain. A **routine** is a
named automation rule in Foundry Agent Service: one trigger (when
to fire) plus one action (which agent to invoke). Foundry queues
the invocation, runs the agent, and stores a run record you can
inspect later. Routines are generally available since Sep 24, 2026.

The hand-rolled runner used to live here (`jobs.py`, `runner.py`,
`state.json`, `runs.jsonl` — a script crontab woke up, interval
due-ness, a JSONL log). It is in git history — commit `c4dd80bd`
on `revanthsamavedam/aG` — if you want to see what the service
replaces. Do not build on it; it is the "what the service does
underneath" reference, not the main path.

## One honest limit, up front

A routine invokes **one agent**, not the whole team. The team in
01 is five specialists plus a Leader, and no routine fires all
five as a unit. Each routine below points at the specialist the
job needs: QA for the health check, researcher for the roundup.
If you want the whole team on a schedule, make the Leader a
Foundry agent too — a coordinator agent — and point the routine
at it. That is a design decision, not something this folder hides.

## The shape

```
Foundry project ──▶ routine "quote-service-check"
                       trigger: schedule "0 9 * * *" America/New_York
                       action:  invoke agent "qa" (Responses API)
                                input: "Check the quote service..."
                       │
                       ▼ fires daily 9am
                 agent runs ──▶ run record (id, phase, started, ended,
                                dispatch_id, response_id)
```

## Files

| File | What it is |
|---|---|
| `foundry_client.py`, `team.py`, `members.py`, `fake_client.py` | Copied unchanged from `~/workspace/foundry-team/` (the 01 team). |
| `routines.py` | `Routine` + `load_routines` + `RoutineManager` + `connect_from_env()` + the CLI. |
| `fake_routines.py` | `FakeRoutines` — mimics `client.beta.routines` offline. |
| `routines.json` | 2 example routines: daily `quote-service-check`, weekly `research-roundup`. |
| `demo.py` | End-to-end with the fake: creates both routines, dispatches one, shows the run history. |
| `tests/test_routines.py` | Plain-script tests, no pytest. |

## The routine definition

`routines.json` — one object per routine:

```json
{
  "routines": [
    {
      "name": "quote-service-check",
      "agent_name": "qa",
      "prompt": "Check the quote service is healthy: verify GET /quote ...",
      "cron_expression": "0 9 * * *",
      "time_zone": "America/New_York",
      "description": "Daily health check of the quote service."
    }
  ]
}
```

- `name` — unique id, used by the CLI and in Foundry.
- `agent_name` — the Foundry agent's name. The examples use the
  specialist handle (`qa`, `researcher`) as a placeholder; a real
  deployment uses the agent's name in Foundry.
- `prompt` — the input handed to the agent on each firing.
- `cron_expression` — five-field cron. **Minimum interval five
  minutes**, enforced by the service. `0 9 * * *` = daily 9am,
  `0 9 * * 1` = Mondays 9am.
- `time_zone` — IANA time zone the cron is evaluated in. Required:
  the portal uses the browser's time zone, so set it explicitly
  through the API/SDK and the fire time never drifts with locale.
- `description` — one-liner shown by `--list`.
- `enabled` — whether the routine fires on its schedule
  (default true).

`routines.py` is the loader (`load_routines`) plus the cron check
(`validate_cron`): five fields, a non-empty time zone, and no
schedule firing more often than every five minutes (it catches
the obvious cases — every minute, or a minute step under five).
A full cron parser is out of scope; the service is the final judge.

## The API — `routines.py`, `RoutineManager`

The Python SDK is `azure-ai-projects` (>= 2.4.0); every call below
goes through `client.beta.routines`. (In Python there is no
standalone `RoutinesClient` class — that name is the .NET/Java
surface. Python exposes the same operations as
`client.beta.routines`.) Source: Microsoft Learn, "Automate agents
with routines".

**Create or update** — one trigger entry (the docs' convention
names it `"default"`), one action. `create_or_update` replaces the
stored definition, so re-running it is how you change a routine:

```python
routine = client.beta.routines.create_or_update(
    routine_name="daily-summary",
    description="Runs at 09:00 in New York.",
    enabled=True,
    triggers={"default": {
        "type": "schedule",
        "cron_expression": "0 9 * * *",
        "time_zone": "America/New_York",
    }},
    action={"type": "invoke_agent_responses_api",
            "agent_name": "qa",
            "input": "Check the quote service is healthy."},
)
# routine.name, .enabled, .triggers, .action, .updated_at
```

**List / get / delete:**

```python
for r in client.beta.routines.list():
    print(r.name, r.enabled, list(r.triggers.keys()))
routine = client.beta.routines.get("daily-summary")
client.beta.routines.delete("daily-summary")
```

**Dispatch (manual test run)** — queue a one-off run without
waiting for the trigger. Omit the payload to run with the
configured prompt; pass one to override it for this dispatch only:

```python
result = client.beta.routines.dispatch(
    routine_name="daily-summary",
    payload={"type": "invoke_agent_responses_api",
             "input": "Run the daily summary for testing."})
# result.dispatch_id, .task_id, .action_correlation_id
```

Dispatch is asynchronous: the result is an acknowledgment that
the run was enqueued, not a completed run. Use the dispatch id to
find it in the run history.

**Run history:**

```python
runs = client.beta.routines.list_runs("daily-summary")
for run in runs:
    print(run.id, run.phase, run.attempt_source,
          run.started_at, run.ended_at)
    if run.phase == "failed":
        print(run.error_type, "-", run.error_message)
# run.status, .trigger_type, .dispatch_id, .response_id
```

`connect_from_env()` builds the real manager from
`FOUNDRY_PROJECT_ENDPOINT`, with `DefaultAzureCredential`
(Entra). It raises a clear error when the endpoint is missing,
before importing the SDK.

## Trigger and action types

| Trigger | When it fires |
|---|---|
| `schedule` | Recurring, on a cron expression. Minimum interval five minutes. |
| `timer` | Once, at a specific future date/time or after a duration. |
| `github_issue` | When an issue is opened or closed in a watched repository. |
| `custom` | From an external provider — the `teams` provider fires on a new message in a watched Teams channel. |

| Action | What runs |
|---|---|
| `invoke_agent_responses_api` | The agent, through the Responses API. Agent name or endpoint id. |
| `invoke_agent_invocations_api` | The agent, through the Invocations API. Agent name or endpoint id. |

For `github_issue` and `custom` (Teams) triggers, the event
payload overwrites `action.input` when the event fires — the
configured input applies only to manual test dispatches.

Identity: every routine uses the **agent identity** by default.
If the agent has tools that need delegated user access, opt in to
creator identity at creation time only —
`RoutineAuthorization(identity="creator")` in Python. It always
represents the principal that created the routine, not another
user, and it cannot be changed by updating; delete and recreate
to switch.

## Commands

```bash
cd ~/workspace/foundry-team-tutorial/03-scheduled-work
python3 tests/test_routines.py   # 46 tests, plain script, no pytest
python3 demo.py                  # fake service, creates + dispatches + shows runs
python3 routines.py --list                       # routines in the project
python3 routines.py --create-all                # create every routine in routines.json
python3 routines.py --dispatch quote-service-check   # queue a one-off run now
python3 routines.py --dispatch quote-service-check --input "Run it now, please."
python3 routines.py --runs quote-service-check  # run history
python3 routines.py --get quote-service-check   # one routine's definition
python3 routines.py --delete research-roundup    # delete one routine
```

The demo and the tests run against the fake. The CLI uses the real
service unless you pass a fake client in code — it calls
`connect_from_env()`, so set `FOUNDRY_PROJECT_ENDPOINT` first.

## Creating the routines (real run)

```bash
export FOUNDRY_PROJECT_ENDPOINT="https://<account>.services.ai.azure.com/api/projects/<project>"
pip install "azure-ai-projects>=2.4.0" azure-identity
python3 routines.py --create-all
python3 routines.py --list
```

Or in the Foundry portal: open the project → **Routines** → **+
New routine** → name, agent, prompt, trigger type **Recurring
schedule**, frequency and time → **Create & start**. The portal
uses the browser's time zone; prefer the API/SDK when the zone
matters.

## The fake — `fake_routines.py`

`FakeRoutines` mimics `client.beta.routines` with the same method
names and result shapes (`.name`, `.enabled`, `.triggers`,
`.action`, `.dispatch_id`, `.task_id`, `.id`, `.phase`,
`.attempt_source`, `.started_at`, `.ended_at`). What it
simplifies, stated plainly:

- **Dispatch:** the real dispatch is asynchronous. The fake
  completes the run immediately so the demo can show the run
  record in one pass.
- **Run history:** the real service paginates (`data` +
  `next_link`) and supports `limit`/`order`/`after`. The fake
  applies `limit` only and returns newest first.
- **attempt_source:** the fake records `"manual_dispatch"` for
  test dispatches. The service's value for a schedule-fired run
  is `"schedule_delivery"` (the only one the docs show); the
  fake's label is its own.
- **Identity:** the fake records the authorization value if
  given but enforces nothing.

Every fake call is recorded in `.calls`, so the tests assert the
exact shapes `RoutineManager` sends the service.

## What it does not do (limits worth knowing)

- **No Teams / email reporting.** The run history lives in
  Foundry; nothing posts it anywhere. The next step is a reporter
  that reads the latest run and posts it — same shape as the
  looper's Teams webhook (private channel, 28 KB limit), or an
  email draft.
- **No retries or alerting of its own.** A failed run is a run
  record with `phase == "failed"` and an error; nothing pages
  anyone. The service retries the downstream dispatch per its
  own policy (each attempt has a 30-second downstream timeout).
- **No CMK.** Routines do not support customer-managed key
  encryption. Do not use them for workloads that require CMK
  protection.
- **Not in every region.** Routines are unavailable in UK West,
  Switzerland West, Japan West, UAE North, and Norway East.
  Confirm the project is not in one of those before creating a
  routine.
- **One agent per routine.** See the limit at the top: to run
  the whole team on a schedule, the routine must point at a
  coordinator agent.
