"""Plain-script tests for the Foundry team (no pytest).

Run:  python3 tests/test_foundry_team.py
Exit 0 when every test passes, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fake_client import FakeFoundryClient, fake_members
from foundry_client import FoundryAgentClient, FoundryError
from members import load_members
from team import Leader, Member, Team, _parse_plan

PASSED = []
FAILED = []


def check(name, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — record, keep running the rest
        FAILED.append((name, f"{type(exc).__name__}: {exc}"))
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    else:
        PASSED.append(name)
        print(f"PASS {name}")


# --- mocked urllib ----------------------------------------------------------
class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class MockUrlopen:
    """Scripted urlopen: (method, path-suffix) -> list of payloads, in order.
    Records every request (url, method, headers, json body) for assertions."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.requests = []

    def __call__(self, req, timeout=None):
        method = req.get_method()
        url = req.full_url
        body = json.loads(req.data.decode()) if req.data else None
        self.requests.append({"url": url, "method": method,
                              "headers": dict(req.header_items()), "body": body})
        path = url.split("?")[0]
        key = (method, path.split("/")[-1] if path.split("/")[-1] else path)
        # match on the last path segment: threads / messages / runs / <id>
        for (m, suffix), queue in self.script.items():
            if m == method and path.endswith(suffix):
                if not queue:
                    raise AssertionError(f"unexpected extra call to {method} {suffix}")
                return FakeResponse(queue.pop(0))
        raise AssertionError(f"unexpected call: {method} {url}")


def run_with_mock(script, fn):
    mock = MockUrlopen(script)
    real = urllib.request.urlopen
    urllib.request.urlopen = mock
    try:
        return fn(mock)
    finally:
        urllib.request.urlopen = real


def _thread_msg_run(poll_statuses, agent_text="Hello from the agent"):
    """Script for one full call_agent flow."""
    script = {
        ("POST", "/threads"): [{"id": "thread_1"}],
        ("POST", "/messages"): [{"id": "msg_1", "role": "user"}],
        ("POST", "/runs"): [{"id": "run_1", "status": "queued"}],
        ("GET", "/runs/run_1"): [{"id": "run_1", "status": s} for s in poll_statuses],
        ("GET", "/messages"): [{"data": [
            {"role": "user", "content": [{"type": "text", "text": {"value": "hi"}}]},
            {"role": "assistant", "content": [{"type": "text", "text": {"value": agent_text}}]},
        ]}],
    }
    return script


# --- client tests -----------------------------------------------------------
def test_client_full_flow():
    def fn(mock):
        client = FoundryAgentClient("https://res.services.ai.azure.com/api/projects/p",
                                    "key123", poll_interval=0)
        out = client.call_agent("asst_dev", "build the thing")
        assert out == "Hello from the agent", out
        urls = [r["url"] for r in mock.requests]
        assert urls[0].endswith("/threads?api-version=2025-05-15-preview"), urls[0]
        assert "/threads/thread_1/messages" in urls[1], urls[1]
        assert "/threads/thread_1/runs" in urls[2], urls[2]
        assert mock.requests[2]["body"] == {"agent_id": "asst_dev"}, mock.requests[2]["body"]
        assert mock.requests[1]["body"] == {"role": "user", "content": "build the thing"}
        for r in mock.requests:
            assert r["headers"].get("Authorization") == "Bearer key123", r["headers"]
        assert len([u for u in urls if "/runs/run_1" in u]) == 2  # polled twice
    run_with_mock(_thread_msg_run(["in_progress", "completed"]), fn)


def test_client_reuses_thread():
    def fn(mock):
        client = FoundryAgentClient("https://res.services.ai.azure.com/api/projects/p",
                                    "key123", poll_interval=0)
        out = client.call_agent("asst_dev", "follow up", thread_id="thread_9")
        assert out == "Hello from the agent"
        assert not any(r["url"].split("?")[0].endswith("/threads")
                       and r["method"] == "POST" for r in mock.requests), \
            "should not create a thread when one is given"
        assert "/threads/thread_9/messages" in mock.requests[0]["url"]
    run_with_mock(_thread_msg_run(["completed"]), fn)


def test_client_requires_action():
    def fn(mock):
        client = FoundryAgentClient("https://res.services.ai.azure.com/api/projects/p",
                                    "key123", poll_interval=0)
        try:
            client.call_agent("asst_dev", "do it")
        except FoundryError as exc:
            assert "requires_action" in str(exc) or "tool outputs" in str(exc), str(exc)
            return
        raise AssertionError("expected FoundryError for requires_action")
    script = _thread_msg_run(["requires_action"])
    script[("GET", "/runs/run_1")] = [{"id": "run_1", "status": "requires_action",
                                        "required_action": {"submit_tool_outputs": {
                                            "tool_calls": [{"function": {"name": "get_weather"}}]}}}]
    run_with_mock(script, fn)


def test_client_run_failed():
    def fn(mock):
        client = FoundryAgentClient("https://res.services.ai.azure.com/api/projects/p",
                                    "key123", poll_interval=0)
        try:
            client.call_agent("asst_dev", "do it")
        except FoundryError as exc:
            assert "failed" in str(exc), str(exc)
            return
        raise AssertionError("expected FoundryError for a failed run")
    script = _thread_msg_run(["failed"])
    script[("GET", "/runs/run_1")] = [{"id": "run_1", "status": "failed",
                                        "last_error": {"code": "server_error",
                                                       "message": "boom"}}]
    run_with_mock(script, fn)


def test_client_http_error():
    real = urllib.request.urlopen

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    urllib.request.urlopen = boom
    try:
        client = FoundryAgentClient("https://res.services.ai.azure.com/api/projects/p",
                                    "key123")
        try:
            client.call_agent("asst_dev", "do it")
        except FoundryError as exc:
            assert "401" in str(exc), str(exc)
            return
        raise AssertionError("expected FoundryError for HTTP 401")
    finally:
        urllib.request.urlopen = real


# --- team tests ---------------------------------------------------------------
class FakePlanner:
    """Planner double: .call(messages, tools) -> {"content": ...}."""

    def __init__(self, plan_text="", integrate_text=""):
        self.plan_text = plan_text
        self.integrate_text = integrate_text
        self.calls = []

    def call(self, messages, tools):
        self.calls.append(messages)
        content = self.plan_text if "Plan how" in messages[-1]["content"] \
            else self.integrate_text
        return {"content": content, "tool_calls": []}


def _quiet_team(members=None, planner=None):
    log: list[str] = []
    team = Team(members if members is not None else fake_members(),
                FakeFoundryClient(), planner=planner, print_fn=log.append)
    return team, log


def test_team_run_fake():
    team, log = _quiet_team()
    final = team.run("Build a fastapi service with a random quote about nvidia")
    assert team.last_plan, "plan should not be empty"
    assert len(team.last_plan) <= 3, team.last_plan
    # Delegation happened in plan order, one call per step.
    called = [a for a, _ in team.client.calls]
    assert called == [m.agent_id for m, _ in team.last_plan], (called, team.last_plan)
    # The final answer carries the specialists' work.
    assert final.strip(), "final answer should not be empty"
    assert "FastAPI" in final or "fastapi" in final.lower(), final
    assert any("planning" in line for line in log), log
    assert any("integrating" in line for line in log), log


def test_team_keyword_plan_order():
    leader = Leader(planner=None)
    members = fake_members()
    steps = leader.plan("Research the best quotes API, then build the service", members)
    handles = [m.handle for m, _ in steps]
    assert handles.index("researcher") < handles.index("dev"), handles
    assert len(steps) <= 3


def test_team_model_plan_parses():
    planner = FakePlanner(
        plan_text="STEP @researcher: find the best quotes API\n"
                  "STEP @dev: build the FastAPI service\n"
                  "STEP @qa: test the endpoints\n"
                  "STEP @writer: write the README\n",  # 4th step must be dropped
        integrate_text="INTEGRATED: service built and tested.")
    team, _ = _quiet_team(planner=planner)
    final = team.run("Build a fastapi service with a random quote about nvidia")
    assert [m.handle for m, _ in team.last_plan] == ["researcher", "dev", "qa"], team.last_plan
    assert final == "INTEGRATED: service built and tested.", final


def test_team_missing_agent_id_skipped():
    members = fake_members() + [Member("Ghost", "ghost", "Does nothing.", "")]
    log: list[str] = []
    team = Team(members, FakeFoundryClient(), planner=None, print_fn=log.append)
    team.run("Build a fastapi service")
    assert not any(a == "" for a, _ in team.client.calls), "empty agent id must never be called"
    assert any("skipping @ghost" in line for line in log), log


def test_team_member_failure_does_not_kill_run():
    class Flaky(FakeFoundryClient):
        def call_agent(self, agent_id, prompt, thread_id=None):
            if "fake-qa" in agent_id:
                raise RuntimeError("agent exploded")
            return super().call_agent(agent_id, prompt, thread_id)

    log: list[str] = []
    team = Team(fake_members(), Flaky(), planner=None, print_fn=log.append)
    final = team.run("Research the best quotes API, build the service, test it")
    assert "failed" in final.lower() or "⚠️" in final, final
    assert team.last_results, "results should still exist after a failure"


def test_members_env_loading():
    env = {"FOUNDRY_AGENT_DEV": "asst_dev", "FOUNDRY_AGENT_QA": "asst_qa"}
    members, skipped = load_members(env=env)
    assert [m.handle for m in members] == ["dev", "qa"], members
    assert skipped == ["researcher", "writer", "reviewer"], skipped


def test_members_json_config(tmp_path="/tmp/foundry-team-test-config.json"):
    with open(tmp_path, "w") as f:
        json.dump({"members": [
            {"handle": "dev", "agent_id": "asst_dev"},
            {"handle": "researcher", "agent_id": ""},
            {"handle": "custom", "name": "Custom", "role": "Does custom work.",
             "agent_id": "asst_custom"},
        ]}, f)
    members, skipped = load_members(config_path=tmp_path)
    assert [m.handle for m in members] == ["dev", "custom"], members
    assert skipped == ["researcher"], skipped
    os.remove(tmp_path)


def test_parse_plan_ignores_unknown_handles():
    members = fake_members()
    steps = _parse_plan("STEP @dev: build it\nSTEP @nobody: do the impossible\n"
                        "STEP @dev: build it again\n", members)
    assert [(m.handle, s) for m, s in steps] == [("dev", "build it")], steps


if __name__ == "__main__":
    check("client full flow (thread/message/run/poll/read)", test_client_full_flow)
    check("client reuses a given thread", test_client_reuses_thread)
    check("client requires_action -> clear error", test_client_requires_action)
    check("client failed run -> clear error", test_client_run_failed)
    check("client HTTP error -> FoundryError with status", test_client_http_error)
    check("team run with fake client", test_team_run_fake)
    check("team keyword plan orders research before dev", test_team_keyword_plan_order)
    check("team model plan parses and caps at 3", test_team_model_plan_parses)
    check("team skips members with no agent id", test_team_missing_agent_id_skipped)
    check("team survives a member failure", test_team_member_failure_does_not_kill_run)
    check("members load from env", test_members_env_loading)
    check("members load from JSON config", test_members_json_config)
    check("plan parser ignores unknown/duplicate handles", test_parse_plan_ignores_unknown_handles)
    print(f"\n{PASSED.__len__()} passed, {FAILED.__len__()} failed")
    if FAILED:
        for name, err in FAILED:
            print(f"  FAILED: {name}: {err}")
        raise SystemExit(1)
