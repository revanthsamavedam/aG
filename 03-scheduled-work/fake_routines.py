"""FakeRoutines — a stand-in for ``project_client.beta.routines``.

Same method names and result shapes as the real SDK, so
RoutineManager runs unchanged against it. No Azure, no key, no
network. Used by demo.py and the tests.

What the fake models, and what it simplifies:
  * Create/update: stores the routine definition; create_or_update
    replaces it, like the service. The fake validates the schedule
    trigger with the same validate_cron() the real path uses.
  * Dispatch: queues a run immediately and records it as completed
    (the real dispatch is asynchronous — the result is only an
    acknowledgment, and completion is confirmed via list_runs).
    The fake's run carries attempt_source "manual_dispatch"; the
    service's value for a schedule-fired run is "schedule_delivery"
    (the only one the docs show), so the fake's label is its own.
  * Run history: list_runs returns the runs recorded for that
    routine, newest first. The real service paginates (data +
    next_link) and supports limit/order/after; the fake applies
    limit only.
  * Identity: the fake records the authorization value if given
    but enforces nothing. The real service dispatches as the agent
    by default, or as the creator when authorization is set.

Every call is recorded in .calls (method name + kwargs), so tests
can check the exact shapes RoutineManager sends the service.
"""
from __future__ import annotations

from datetime import datetime, timezone

from routines import validate_cron


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _FakeTrigger:
    def __init__(self, trigger_type: str, cron_expression: str, time_zone: str):
        self.type = trigger_type
        self.cron_expression = cron_expression
        self.time_zone = time_zone


class _FakeAction:
    def __init__(self, action_type: str, agent_name: str, input: str | None):
        self.type = action_type
        self.agent_name = agent_name
        self.input = input


class _FakeRoutine:
    """Shape of the SDK's Routine: .name, .enabled, .triggers, .action."""
    def __init__(self, name: str, description: str, enabled: bool,
                 triggers: dict, action: dict, authorization=None):
        self.name = name
        self.description = description
        self.enabled = enabled
        self.triggers = {}
        for key, value in (triggers or {}).items():
            if isinstance(value, dict):
                self.triggers[key] = _FakeTrigger(
                    value.get("type"), value.get("cron_expression"),
                    value.get("time_zone"))
            else:
                self.triggers[key] = value
        if isinstance(action, dict):
            self.action = _FakeAction(
                action.get("type"), action.get("agent_name"),
                action.get("input"))
        else:
            self.action = action
        self.authorization = authorization
        self.updated_at = _iso_now()


class _FakeDispatchResult:
    """Shape of the SDK's DispatchRoutineResult."""
    def __init__(self, dispatch_id: str, task_id: str, action_correlation_id: str):
        self.dispatch_id = dispatch_id
        self.task_id = task_id
        self.action_correlation_id = action_correlation_id


class _FakeRoutineRun:
    """Shape of the SDK's RoutineRun."""
    def __init__(self, run_id: str, trigger_type: str, attempt_source: str,
                 started_at: str, ended_at: str, dispatch_id: str,
                 response_id: str):
        self.id = run_id
        self.status = "FINISHED"
        self.phase = "completed"
        self.trigger_type = trigger_type
        self.attempt_source = attempt_source
        self.started_at = started_at
        self.ended_at = ended_at
        self.dispatch_id = dispatch_id
        self.response_id = response_id
        self.error_type = None
        self.error_message = None


class FakeRoutines:
    """In-memory mimic of client.beta.routines."""

    def __init__(self):
        self._routines: dict[str, _FakeRoutine] = {}
        self._runs: dict[str, list[_FakeRoutineRun]] = {}
        self._next_dispatch = 1
        self._next_run = 1
        self.calls: list[tuple[str, dict]] = []

    def create_or_update(self, routine_name: str, *, description=None,
                         enabled=None, triggers=None, action=None,
                         authorization=None):
        self.calls.append(("create_or_update", {
            "routine_name": routine_name, "description": description,
            "enabled": enabled, "triggers": triggers, "action": action,
            "authorization": authorization}))
        for value in (triggers or {}).values():
            if isinstance(value, dict) and value.get("type") == "schedule":
                validate_cron(value.get("cron_expression", ""),
                              value.get("time_zone", ""))
        routine = _FakeRoutine(
            routine_name, description or "",
            True if enabled is None else enabled,
            triggers or {}, action or {}, authorization)
        self._routines[routine_name] = routine
        return routine

    def get(self, routine_name: str):
        self.calls.append(("get", {"routine_name": routine_name}))
        if routine_name not in self._routines:
            raise KeyError(f"no routine named {routine_name!r}")
        return self._routines[routine_name]

    def list(self, *, limit=None, after=None, order=None):
        self.calls.append(("list", {"limit": limit, "after": after,
                                    "order": order}))
        routines = list(self._routines.values())
        if limit is not None:
            routines = routines[:limit]
        return routines

    def delete(self, routine_name: str):
        self.calls.append(("delete", {"routine_name": routine_name}))
        if routine_name not in self._routines:
            raise KeyError(f"no routine named {routine_name!r}")
        del self._routines[routine_name]
        self._runs.pop(routine_name, None)

    def dispatch(self, routine_name: str, *, payload=None):
        self.calls.append(("dispatch", {"routine_name": routine_name,
                                        "payload": payload}))
        if routine_name not in self._routines:
            raise KeyError(f"no routine named {routine_name!r}")
        dispatch_id = f"disp-{self._next_dispatch}"
        task_id = f"task-{self._next_dispatch}"
        self._next_dispatch += 1
        run_id = f"run-{self._next_run}"
        self._next_run += 1
        routine = self._routines[routine_name]
        trigger = next(iter(routine.triggers.values()), None)
        trigger_type = getattr(trigger, "type", "schedule")
        now = _iso_now()
        run = _FakeRoutineRun(
            run_id, trigger_type, "manual_dispatch", now, now,
            dispatch_id, f"resp-{run_id}")
        self._runs.setdefault(routine_name, []).insert(0, run)
        return _FakeDispatchResult(dispatch_id, task_id, f"resp-{run_id}")

    def list_runs(self, routine_name: str, *, filter=None, limit=None,
                  after=None, order=None):
        self.calls.append(("list_runs", {
            "routine_name": routine_name, "filter": filter,
            "limit": limit, "after": after, "order": order}))
        if routine_name not in self._routines:
            raise KeyError(f"no routine named {routine_name!r}")
        runs = list(self._runs.get(routine_name, []))
        if limit is not None:
            runs = runs[:limit]
        return runs
