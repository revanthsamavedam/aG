"""FastAPI driver sketch — drive the hosted case-manager agent, with the
approval pause in the middle.

This is the SERVER side of the two-way flow. The browser never talks
to Foundry directly; it talks to this server, and this server talks
to Foundry with its own credentials (same shape as the 06 scaffold's
FastAPI server, and the same thread -> message -> run -> poll flow as
01-team-of-agents/foundry_client.py).

Endpoints:
  POST /run      {prompt, thread_id?}  -> start a run. If the agent
                  calls set_case_status, the run pauses for approval
                  and /run returns 202 with the pending tool calls.
  POST /approve  {thread_id, run_id, decisions: [{tool_call_id,
                  approve: true|false}]} -> submit the human's
                  decision, resume the run, return the final text.
  GET  /health   -> {"status": "ok"}.

Needs (env, never in chat): FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_API_KEY
(Entra ID token in production, project API key in v1), FOUNDRY_AGENT_ID
(the agent id from the portal — NOT the agent name).

HONEST GAP — read this before trusting the /approve path: the thread /
message / run / poll calls below are copied from the verified 01 REST
client (api-version 2025-05-15-preview), and submit_tool_outputs is the
standard counterpart of the `required_action.submit_tool_outputs`
structure that client already parses. But the exact required_action
payload for an MCP-tool approval was NOT verified against a live
project — the installed SDK (azure-ai-projects 2.11.0) only carries
MCP approval models for Realtime, not for threads/runs. So the first
time a run pauses, this driver LOGS the full required_action JSON
(look for "PAUSED — required_action:" in the server log). Check that
shape against what /approve submits; if the service rejects the
output value, that log is what tells you the real shape.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from fastapi import FastAPI, HTTPException

DEFAULT_API_VERSION = "2025-05-15-preview"
_TERMINAL = {"completed", "failed", "cancelled", "expired"}

# Runs waiting on a decision expire server-side after ~10 minutes.
# Pending runs live here only while this process runs.
PENDING: dict[str, dict] = {}


class FoundryError(RuntimeError):
    pass


class FoundryDriver:
    """Thread -> message -> run -> poll, plus submit_tool_outputs.

    Same five REST steps as 01-team-of-agents/foundry_client.py, with
    one addition: submit_tool_outputs, for resuming a paused run.
    """

    def __init__(self, endpoint: str, api_key: str,
                 api_version: str = DEFAULT_API_VERSION,
                 timeout: float = 30.0, poll_interval: float = 2.0,
                 run_timeout: float = 600.0):
        if not endpoint or not api_key:
            raise ValueError(
                "driver needs FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_API_KEY")
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.api_version = api_version
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.run_timeout = run_timeout

    def _url(self, path: str) -> str:
        return f"{self.endpoint}{path}?api-version={self.api_version}"

    def _request(self, method: str, path: str,
                 payload: dict | None = None) -> dict:
        url = self._url(path)
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:2000]
            raise FoundryError(
                f"Foundry HTTP {exc.code} on {method} {path}: {body}") from exc
        except urllib.error.URLError as exc:
            raise FoundryError(
                f"Foundry request failed on {method} {path}: {exc}") from exc

    def create_thread(self) -> str:
        return self._request("POST", "/threads", {})["id"]

    def add_message(self, thread_id: str, content: str) -> dict:
        return self._request("POST", f"/threads/{thread_id}/messages",
                             {"role": "user", "content": content})

    def create_run(self, thread_id: str, agent_id: str) -> dict:
        return self._request("POST", f"/threads/{thread_id}/runs",
                             {"agent_id": agent_id})

    def get_run(self, thread_id: str, run_id: str) -> dict:
        return self._request("GET", f"/threads/{thread_id}/runs/{run_id}")

    def submit_tool_outputs(self, thread_id: str, run_id: str,
                            outputs: list[dict]) -> dict:
        """Resume a paused run with the human's decisions.

        Standard submit_tool_outputs shape: one entry per pending tool
        call, {tool_call_id, output}. For an approval the output is
        the decision string ("approved" / "denied") — see the module
        docstring: verify this against the logged required_action on
        your first live pause.
        """
        return self._request(
            "POST", f"/threads/{thread_id}/runs/{run_id}/submit_tool_outputs",
            {"tool_outputs": outputs})

    def list_messages(self, thread_id: str) -> list[dict]:
        return self._request("GET", f"/threads/{thread_id}/messages").get("data", [])

    def last_assistant_text(self, thread_id: str) -> str:
        for msg in reversed(self.list_messages(thread_id)):
            if msg.get("role") != "assistant":
                continue
            parts = [i.get("text", {}).get("value", "")
                     for i in msg.get("content") or []
                     if i.get("type") == "text"]
            if parts:
                return "\n".join(p for p in parts if p).strip()
        return ""

    def poll(self, thread_id: str, run_id: str) -> dict:
        """Poll until the run completes, pauses, or fails. Returns the
        final run dict; raises FoundryError on failure/timeout."""
        deadline = time.monotonic() + self.run_timeout
        while True:
            run = self.get_run(thread_id, run_id)
            status = run.get("status")
            if status in ("completed", "requires_action"):
                return run
            if status in _TERMINAL:
                err = run.get("last_error") or {}
                raise FoundryError(
                    f"run {run_id} ended {status}: "
                    f"{err.get('code', '')} {err.get('message', '')}".strip())
            if time.monotonic() >= deadline:
                raise FoundryError(f"run {run_id} did not finish in "
                                   f"{self.run_timeout:.0f}s")
            time.sleep(self.poll_interval)


def get_driver() -> FoundryDriver:
    return FoundryDriver(
        endpoint=os.environ.get("FOUNDRY_PROJECT_ENDPOINT", ""),
        api_key=os.environ.get("FOUNDRY_API_KEY", ""),
    )


AGENT_ID = os.environ.get("FOUNDRY_AGENT_ID", "")

app = FastAPI(title="case-manager driver (sketch)")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run")
def run(body: dict):
    """Start a run. 202 + pending tool calls when it pauses for approval."""
    if not AGENT_ID:
        raise HTTPException(500, "FOUNDRY_AGENT_ID is not set")
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(400, "prompt is required")
    driver = get_driver()
    try:
        thread_id = body.get("thread_id") or driver.create_thread()
        driver.add_message(thread_id, prompt)
        run = driver.create_run(thread_id, AGENT_ID)
        run_id = run["id"]
        run = driver.poll(thread_id, run_id)
    except FoundryError as exc:
        raise HTTPException(502, str(exc)) from exc
    if run.get("status") == "requires_action":
        # The pause. Log the raw payload — it is the ground truth for
        # what /approve must submit (see module docstring).
        required = run.get("required_action", {})
        print("PAUSED — required_action:", json.dumps(required, indent=2))
        tool_calls = (required.get("submit_tool_outputs", {})
                      .get("tool_calls", []))
        PENDING[run_id] = {"thread_id": thread_id, "run_id": run_id}
        return {
            "status": "needs_approval",
            "thread_id": thread_id,
            "run_id": run_id,
            "tool_calls": [
                {
                    "tool_call_id": t.get("id"),
                    "name": (t.get("function") or {}).get("name"),
                    "arguments": (t.get("function") or {}).get("arguments"),
                }
                for t in tool_calls
            ],
        }
    return {
        "status": "completed",
        "thread_id": thread_id,
        "run_id": run_id,
        "reply": driver.last_assistant_text(thread_id),
    }


@app.post("/approve")
def approve(body: dict):
    """Submit the human's decisions and resume the run.

    decisions: [{tool_call_id, approve: true|false}]. A denied call
    submits "denied" as its output, so the agent sees the refusal
    and can say so instead of changing the case.
    """
    thread_id, run_id = body.get("thread_id"), body.get("run_id")
    decisions = body.get("decisions") or []
    if not thread_id or not run_id or not decisions:
        raise HTTPException(400, "thread_id, run_id and decisions are required")
    if run_id not in PENDING:
        raise HTTPException(404, f"no pending run {run_id} (expired or unknown)")
    outputs = [
        {
            "tool_call_id": d["tool_call_id"],
            "output": "approved" if d.get("approve") else "denied",
        }
        for d in decisions
    ]
    driver = get_driver()
    try:
        driver.submit_tool_outputs(thread_id, run_id, outputs)
        run = driver.poll(thread_id, run_id)
        del PENDING[run_id]
    except FoundryError as exc:
        raise HTTPException(502, str(exc)) from exc
    if run.get("status") == "requires_action":
        # The agent asked for something else; pause again. Re-fetch the
        # new tool calls the same way /run does.
        required = run.get("required_action", {})
        print("PAUSED AGAIN — required_action:", json.dumps(required, indent=2))
        tool_calls = (required.get("submit_tool_outputs", {})
                      .get("tool_calls", []))
        PENDING[run_id] = {"thread_id": thread_id, "run_id": run_id}
        return {
            "status": "needs_approval",
            "thread_id": thread_id,
            "run_id": run_id,
            "tool_calls": [
                {
                    "tool_call_id": t.get("id"),
                    "name": (t.get("function") or {}).get("name"),
                    "arguments": (t.get("function") or {}).get("arguments"),
                }
                for t in tool_calls
            ],
        }
    return {
        "status": "completed",
        "thread_id": thread_id,
        "run_id": run_id,
        "reply": driver.last_assistant_text(thread_id),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8001")))
