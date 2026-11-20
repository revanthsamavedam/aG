"""Foundry Routines — the team runs on a clock, managed by Foundry.

This replaces the hand-rolled cron-friendly runner that used to
live here (jobs.py, runner.py, state.json, runs.jsonl). That version
is in git history — commit c4dd80bd on revanthsamavedam/aG — if you
want to see what the service replaces: a script crontab woke up,
interval-based due-ness, a JSONL run log. The service now owns the
clock, the dispatch, and the run history. This module is a thin
wrapper that speaks its API.

A routine, in one paragraph (Microsoft Learn, "Automate agents
with routines"; Routines are generally available since Sep 24,
2026): a named automation rule with exactly one trigger (when to
fire) and one action (which agent to invoke). Foundry queues the
invocation, runs the agent, and stores a run record you can inspect
later. The Python SDK is azure-ai-projects (>= 2.4.0); every call
below goes through ``project_client.beta.routines``.

One honest limit, up front: a routine invokes ONE agent, not the
whole team. The team in 01 is five specialists plus a Leader, and
no routine fires all five as a unit. Each routine below points at
the specialist the job needs (QA for the health check, researcher
for the roundup). If you want the whole team on a schedule, make
the Leader a Foundry agent too — a coordinator agent — and point
the routine at it. That is a design decision, not something this
folder hides.

Trigger types (the service's): ``schedule`` (recurring cron,
minimum interval five minutes), ``timer`` (one-shot),
``github_issue`` and ``custom`` (Teams message) for events. This
folder uses ``schedule`` only; the others are the same shape with
a different trigger dict. Action types: ``invoke_agent_responses_api``
and ``invoke_agent_invocations_api``. This folder uses the Responses
API. Identity: the agent's identity by default; the routine
creator's identity is a create-time opt-in
(``RoutineAuthorization(identity="creator")``) for agents whose
tools need delegated user access.

Real run: FOUNDRY_PROJECT_ENDPOINT, then connect_from_env(). Auth
is DefaultAzureCredential (Entra). The module imports with no
Azure SDK installed; the SDK import happens inside
connect_from_env(), so tests and the demo (fake) never need it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROUTINES = os.path.join(_HERE, "routines.json")

# The service enforces a minimum interval of five minutes on
# schedule triggers. The wrapper catches the obvious cases here;
# the service rejects the rest.
_MIN_INTERVAL_MINUTES = 5


def validate_cron(cron_expression: str, time_zone: str) -> None:
    """Validate a schedule trigger the way the service expects it.

    Five fields (minute hour day-of-month month day-of-week), a
    non-empty IANA time zone, and no schedule firing more often
    than every five minutes. The interval check covers the common
    spellings — every minute, or a minute step under five. A full
    cron parser is out of scope; the service is the final judge.
    """
    if not time_zone or not str(time_zone).strip():
        raise ValueError("a schedule trigger needs a time_zone "
                         "(IANA, e.g. 'America/New_York')")
    fields = str(cron_expression or "").split()
    if len(fields) != 5:
        raise ValueError(
            f"cron_expression needs 5 fields, got {len(fields)}: "
            f"{cron_expression!r}")
    minute = fields[0]
    if minute == "*":
        raise ValueError(
            f"cron {cron_expression!r} fires every minute — the minimum "
            f"interval is {_MIN_INTERVAL_MINUTES} minutes")
    step = re.fullmatch(r"\*/(\d+)", minute)
    if step and int(step.group(1)) < _MIN_INTERVAL_MINUTES:
        raise ValueError(
            f"cron {cron_expression!r} fires every {step.group(1)} minutes "
            f"— the minimum interval is {_MIN_INTERVAL_MINUTES} minutes")


@dataclass
class Routine:
    """One routine: a trigger + one agent + the prompt it runs.

    name            unique id, used on the command line and in Foundry
    agent_name      the Foundry agent's name (the examples use the
                    specialist handle as a placeholder; a real
                    deployment uses the agent's name in Foundry)
    prompt          the input handed to the agent on each firing
    cron_expression five-field cron, e.g. "0 9 * * *" (daily 9am)
    time_zone       IANA time zone the cron is evaluated in
    description     optional one-liner, shown by --list
    enabled         whether the routine fires on its schedule
    """
    name: str
    agent_name: str
    prompt: str
    cron_expression: str
    time_zone: str
    description: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        validate_cron(self.cron_expression, self.time_zone)

    def to_create_kwargs(self) -> dict:
        """The kwargs for client.beta.routines.create_or_update.

        One trigger entry (the docs' convention names it "default"),
        one action. Dicts, exactly as the Learn doc's Python sample
        passes them — the SDK serializes dicts for these models.
        """
        return {
            "description": self.description,
            "enabled": self.enabled,
            "triggers": {
                "default": {
                    "type": "schedule",
                    "cron_expression": self.cron_expression,
                    "time_zone": self.time_zone,
                }
            },
            "action": {
                "type": "invoke_agent_responses_api",
                "agent_name": self.agent_name,
                "input": self.prompt,
            },
        }


def load_routines(path: str) -> list[Routine]:
    """Read routines.json -> [Routine, ...].

    A routine missing name/agent_name/prompt/cron_expression/
    time_zone raises a clear error. Unknown keys are ignored.
    """
    with open(path) as f:
        data = json.load(f)
    routines = []
    for entry in data.get("routines", []):
        for field in ("name", "agent_name", "prompt",
                      "cron_expression", "time_zone"):
            if field not in entry:
                raise ValueError(
                    f"routine in {path} is missing {field!r}: {entry}")
        routines.append(Routine(
            name=entry["name"],
            agent_name=entry["agent_name"],
            prompt=entry["prompt"],
            cron_expression=entry["cron_expression"],
            time_zone=entry["time_zone"],
            description=entry.get("description", ""),
            enabled=entry.get("enabled", True),
        ))
    return routines


# -- helpers ---------------------------------------------------------------

def _get(obj, key, default=None):
    """Attribute access that also works on dicts (SDK objects and fakes)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def format_routine(routine) -> str:
    """One routine as a short human-readable block."""
    triggers = _get(routine, "triggers", {}) or {}
    trigger = next(iter(triggers.values()), {}) if triggers else {}
    action = _get(routine, "action", {}) or {}
    lines = [
        f"Routine: {_get(routine, 'name')}",
        f"Enabled: {_get(routine, 'enabled')}",
        f"Trigger: {_get(trigger, 'type')} "
        f"{_get(trigger, 'cron_expression', '')} "
        f"({_get(trigger, 'time_zone', '')})",
        f"Agent: {_get(action, 'agent_name')} "
        f"({_get(action, 'type')})",
        f"Input: {_get(action, 'input', '')}",
    ]
    description = _get(routine, "description")
    if description:
        lines.append(f"About: {description}")
    return "\n".join(lines)


def format_run(run) -> str:
    """One routine run as a short human-readable block."""
    lines = [
        f"Run: {_get(run, 'id')}",
        f"Phase: {_get(run, 'phase')}  Status: {_get(run, 'status')}",
        f"Trigger: {_get(run, 'trigger_type')}  "
        f"Source: {_get(run, 'attempt_source')}",
        f"Started: {_get(run, 'started_at')}  "
        f"Ended: {_get(run, 'ended_at')}",
        f"Dispatch: {_get(run, 'dispatch_id')}  "
        f"Response: {_get(run, 'response_id')}",
    ]
    error_type = _get(run, "error_type")
    if error_type:
        lines.append(f"Error: {error_type} - {_get(run, 'error_message')}")
    return "\n".join(lines)


# -- manager -----------------------------------------------------------------

class RoutineManager:
    """Create, list, dispatch, and inspect routines.

    ``routines`` is the operations object, duck-typed so the fake
    can stand in: the real one is ``project_client.beta.routines``
    (azure-ai-projects), the fake is ``FakeRoutines`` from
    fake_routines.py.
    """

    def __init__(self, routines):
        self.routines = routines

    def create(self, routine: Routine):
        """Create or update one routine (create_or_update replaces
        the stored definition). Returns the saved routine."""
        return self.routines.create_or_update(
            routine_name=routine.name, **routine.to_create_kwargs())

    def create_all(self, routines: list[Routine]) -> list:
        return [self.create(r) for r in routines]

    def list(self) -> list:
        return list(self.routines.list())

    def get(self, name: str):
        return self.routines.get(name)

    def delete(self, name: str) -> None:
        self.routines.delete(name)

    def dispatch(self, name: str, input: str | None = None):
        """Queue a one-off run without waiting for the trigger.

        No input uses the routine's configured prompt. With input,
        the payload overrides it for this dispatch only. Dispatch is
        asynchronous: the result is an acknowledgment (dispatch_id),
        not a completed run — use list_runs() to confirm completion.
        """
        if input is None:
            return self.routines.dispatch(routine_name=name)
        return self.routines.dispatch(
            routine_name=name,
            payload={"type": "invoke_agent_responses_api", "input": input})

    def list_runs(self, name: str, limit: int | None = None) -> list:
        return list(self.routines.list_runs(name, limit=limit))


# -- real connection -----------------------------------------------------------

def connect_from_env() -> RoutineManager:
    """Build the real manager from the environment.

    Needs FOUNDRY_PROJECT_ENDPOINT. Auth is DefaultAzureCredential
    (Entra). Raises a clear error when the endpoint is missing,
    before importing the SDK.
    """
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "missing environment variable FOUNDRY_PROJECT_ENDPOINT "
            "for the real Routines API")
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    project_client = AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    )
    return RoutineManager(project_client.beta.routines)


# -- CLI -----------------------------------------------------------------------

def main(argv: list[str] | None = None,
         routines_client=None) -> int:
    """Manage routines: create, list, dispatch, inspect, delete.

    ``routines_client`` is the operations object (real or fake);
    when None, connect_from_env() builds the real one. Tests pass
    the fake.
    """
    ap = argparse.ArgumentParser(description="Manage Foundry routines.")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--create-all", action="store_true",
                       help="create every routine in routines.json")
    group.add_argument("--list", action="store_true",
                       help="list routines in the project")
    group.add_argument("--get", metavar="NAME",
                       help="show one routine")
    group.add_argument("--dispatch", metavar="NAME",
                       help="queue a one-off run now")
    group.add_argument("--runs", metavar="NAME",
                       help="list runs for one routine")
    group.add_argument("--delete", metavar="NAME",
                       help="delete one routine")
    ap.add_argument("--input", metavar="TEXT",
                    help="override input for --dispatch (this run only)")
    ap.add_argument("--routines", default=DEFAULT_ROUTINES,
                    help="routines.json path")
    args = ap.parse_args(argv)

    manager = (RoutineManager(routines_client) if routines_client is not None
               else connect_from_env())

    if args.create_all:
        routines = load_routines(args.routines)
        for routine in manager.create_all(routines):
            print(format_routine(routine))
            print()
        return 0

    if args.list:
        routines = manager.list()
        if not routines:
            print("(no routines in this project)")
            return 0
        for routine in routines:
            print(format_routine(routine))
            print()
        return 0

    if args.get:
        try:
            print(format_routine(manager.get(args.get)))
        except Exception as exc:  # noqa: BLE001 — unknown name is a CLI error
            print(f"unknown routine: {args.get} ({exc})")
            return 1
        return 0

    if args.dispatch:
        try:
            result = manager.dispatch(args.dispatch, input=args.input)
        except Exception as exc:  # noqa: BLE001 — unknown name is a CLI error
            print(f"unknown routine: {args.dispatch} ({exc})")
            return 1
        print(f"dispatch_id: {result.dispatch_id}")
        print(f"task_id:     {result.task_id}")
        print("(queued — use --runs to confirm it completed)")
        return 0

    if args.runs:
        try:
            runs = manager.list_runs(args.runs)
        except Exception as exc:  # noqa: BLE001 — unknown name is a CLI error
            print(f"unknown routine: {args.runs} ({exc})")
            return 1
        if not runs:
            print(f"(no runs for {args.runs} yet)")
            return 0
        for run in runs:
            print(format_run(run))
            print()
        return 0

    if args.delete:
        try:
            manager.delete(args.delete)
        except Exception as exc:  # noqa: BLE001 — unknown name is a CLI error
            print(f"unknown routine: {args.delete} ({exc})")
            return 1
        print(f"deleted routine {args.delete}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
