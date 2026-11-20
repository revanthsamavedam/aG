"""Plain-script tests for Foundry Routines — no pytest.

    python3 tests/test_routines.py

Run from the 03-scheduled-work/ folder. Each check prints
PASS/FAIL and the script exits non-zero if anything fails.
Everything runs against FakeRoutines — no Azure, no key.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fake_routines import FakeRoutines
from routines import Routine, RoutineManager, connect_from_env
from routines import load_routines, main as cli_main, validate_cron

RESULTS: list[tuple[str, bool, str]] = []
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}"
          + (f" — {detail}" if detail and not cond else ""))


def _routine(name="daily-check", agent="qa", cron="0 9 * * *"):
    return Routine(name=name, agent_name=agent,
                   prompt="Check the quote service is healthy.",
                   cron_expression=cron, time_zone="America/New_York",
                   description="test routine")


def _manager():
    return RoutineManager(FakeRoutines())


def test_load_routines_reads_json() -> None:
    routines = load_routines(os.path.join(_HERE, "routines.json"))
    check("load: reads 2 routines", len(routines) == 2,
          f"got {len(routines)}")
    check("load: names in order",
          [r.name for r in routines] == ["quote-service-check",
                                         "research-roundup"])
    first = routines[0]
    check("load: fields carried through",
          first.agent_name == "qa" and first.cron_expression == "0 9 * * *"
          and first.time_zone == "America/New_York"
          and "GET /quote" in first.prompt)


def test_load_routines_missing_field_raises() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".json",
                                     delete=False) as f:
        json.dump({"routines": [{"name": "x", "agent_name": "qa",
                                "prompt": "do it"}]}, f)
        path = f.name
    try:
        load_routines(path)
    except ValueError as exc:
        check("load: missing field raises ValueError", True)
        check("load: error names the missing field",
              "cron_expression" in str(exc), str(exc))
    else:
        check("load: missing field raises ValueError", False)
        check("load: error names the missing field", False)
    finally:
        os.unlink(path)


def test_cron_validation_valid() -> None:
    for cron in ("0 9 * * *", "0 9 * * 1", "*/5 * * * *", "30 8 * * 1-5"):
        try:
            validate_cron(cron, "America/New_York")
        except ValueError as exc:
            check(f"cron valid: {cron}", False, str(exc))
        else:
            check(f"cron valid: {cron}", True)


def test_cron_validation_rejects() -> None:
    bad = [
        ("0 9 * *", "America/New_York", "4 fields"),
        ("* * * * *", "America/New_York", "every minute"),
        ("*/2 * * * *", "America/New_York", "2-minute step"),
        ("*/4 * * * *", "America/New_York", "4-minute step"),
        ("0 9 * * *", "", "missing time zone"),
    ]
    for cron, tz, why in bad:
        try:
            validate_cron(cron, tz)
        except ValueError:
            check(f"cron rejects: {why}", True)
        else:
            check(f"cron rejects: {why}", False, f"{cron!r} passed")


def test_create_and_get_round_trip() -> None:
    manager = _manager()
    created = manager.create(_routine())
    check("create: returns the routine", created.name == "daily-check")
    got = manager.get("daily-check")
    check("create/get: agent carried through",
          got.action.agent_name == "qa")
    check("create/get: cron and time zone carried through",
          got.triggers["default"].cron_expression == "0 9 * * *"
          and got.triggers["default"].time_zone == "America/New_York")
    check("create/get: input is the prompt",
          got.action.input == "Check the quote service is healthy.")
    check("create/get: enabled by default", got.enabled is True)


def test_create_replaces() -> None:
    manager = _manager()
    manager.create(_routine())
    manager.create(_routine(cron="0 10 * * *"))
    got = manager.get("daily-check")
    check("create_or_update: second create replaces the trigger",
          got.triggers["default"].cron_expression == "0 10 * * *")


def test_list_shows_all() -> None:
    manager = _manager()
    manager.create(_routine("a"))
    manager.create(_routine("b"))
    names = [r.name for r in manager.list()]
    check("list: shows every routine", names == ["a", "b"], str(names))


def test_dispatch_records_a_run() -> None:
    manager = _manager()
    manager.create(_routine())
    result = manager.dispatch("daily-check")
    check("dispatch: returns a dispatch_id", bool(result.dispatch_id),
          repr(result.dispatch_id))
    check("dispatch: returns a task_id", bool(result.task_id))
    runs = manager.list_runs("daily-check")
    check("dispatch: one run recorded", len(runs) == 1,
          f"got {len(runs)}")
    check("dispatch: run completed", runs[0].phase == "completed",
          f"got {runs[0].phase}")
    check("dispatch: run carries the dispatch_id",
          runs[0].dispatch_id == result.dispatch_id)


def test_dispatch_with_input_override() -> None:
    fake = FakeRoutines()
    manager = RoutineManager(fake)
    manager.create(_routine())
    manager.dispatch("daily-check", input="Run the check now, please.")
    calls = [c for c in fake.calls if c[0] == "dispatch"]
    payload = calls[0][1]["payload"]
    check("dispatch override: payload type matches the action",
          payload["type"] == "invoke_agent_responses_api", str(payload))
    check("dispatch override: payload carries the input",
          payload["input"] == "Run the check now, please.", str(payload))


def test_delete_removes() -> None:
    manager = _manager()
    manager.create(_routine())
    manager.delete("daily-check")
    try:
        manager.get("daily-check")
    except KeyError:
        check("delete: get after delete raises", True)
    else:
        check("delete: get after delete raises", False)
    check("delete: list is empty afterwards", manager.list() == [])


def test_unknown_name_raises() -> None:
    manager = _manager()
    for op in ("get", "delete", "dispatch", "list_runs"):
        try:
            getattr(manager, op)("nope")
        except KeyError:
            check(f"unknown: {op} raises KeyError", True)
        else:
            check(f"unknown: {op} raises KeyError", False)


def test_create_sends_right_shapes() -> None:
    fake = FakeRoutines()
    manager = RoutineManager(fake)
    manager.create(_routine())
    calls = [c for c in fake.calls if c[0] == "create_or_update"]
    check("create: one create_or_update call", len(calls) == 1)
    kwargs = calls[0][1]
    trigger = kwargs["triggers"]["default"]
    check("create: trigger is a schedule with cron + time zone",
          trigger == {"type": "schedule", "cron_expression": "0 9 * * *",
                      "time_zone": "America/New_York"}, str(trigger))
    check("create: action invokes the agent via Responses API",
          kwargs["action"] == {
              "type": "invoke_agent_responses_api", "agent_name": "qa",
              "input": "Check the quote service is healthy."},
          str(kwargs["action"]))


def test_cli_end_to_end() -> None:
    fake = FakeRoutines()
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli_main(["--create-all"], routines_client=fake)
    check("cli: --create-all exits 0", code == 0)
    check("cli: --create-all prints both routines",
          "quote-service-check" in buf.getvalue()
          and "research-roundup" in buf.getvalue())
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli_main(["--list"], routines_client=fake)
    check("cli: --list exits 0 and shows routines",
          code == 0 and "quote-service-check" in buf.getvalue())
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli_main(["--dispatch", "quote-service-check"],
                    routines_client=fake)
    check("cli: --dispatch exits 0 with a dispatch_id",
          code == 0 and "dispatch_id:" in buf.getvalue(), buf.getvalue())
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli_main(["--runs", "quote-service-check"],
                    routines_client=fake)
    check("cli: --runs exits 0 and shows a completed run",
          code == 0 and "completed" in buf.getvalue(), buf.getvalue())
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli_main(["--delete", "research-roundup"],
                    routines_client=fake)
    check("cli: --delete exits 0", code == 0)


def test_cli_unknown_dispatch_exits_1() -> None:
    fake = FakeRoutines()
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli_main(["--dispatch", "nope"], routines_client=fake)
    check("cli: unknown --dispatch exits 1", code == 1)


def test_connect_from_env_missing_endpoint() -> None:
    saved = os.environ.pop("FOUNDRY_PROJECT_ENDPOINT", None)
    try:
        try:
            connect_from_env()
        except RuntimeError as exc:
            check("connect_from_env: missing endpoint raises RuntimeError",
                  True)
            check("connect_from_env: error names the variable",
                  "FOUNDRY_PROJECT_ENDPOINT" in str(exc), str(exc))
        else:
            check("connect_from_env: missing endpoint raises RuntimeError",
                  False, "no error raised")
            check("connect_from_env: error names the variable", False)
    finally:
        if saved is not None:
            os.environ["FOUNDRY_PROJECT_ENDPOINT"] = saved


def main() -> int:
    test_load_routines_reads_json()
    test_load_routines_missing_field_raises()
    test_cron_validation_valid()
    test_cron_validation_rejects()
    test_create_and_get_round_trip()
    test_create_replaces()
    test_list_shows_all()
    test_dispatch_records_a_run()
    test_dispatch_with_input_override()
    test_delete_removes()
    test_unknown_name_raises()
    test_create_sends_right_shapes()
    test_cli_end_to_end()
    test_cli_unknown_dispatch_exits_1()
    test_connect_from_env_missing_endpoint()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        for name, _, detail in failed:
            print(f"  FAILED: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
