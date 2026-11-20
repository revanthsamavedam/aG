"""FoundryAgentClient — stdlib-only client for the Azure AI Foundry Agent Service.

The Agent Service is a managed loop: you create an agent once (in the
Foundry portal), then per turn you create a thread, add a user message,
create a run, poll the run, and read the assistant's reply off the
thread. This client is one thin wrapper around that flow — no SDK, no
extra dependency, same stdlib-only style as the looper's provider.

Flow (REST, api-version 2025-05-15-preview):
  POST {endpoint}/threads?api-version=...            -> {"id": "thread_..."}
  POST {endpoint}/threads/{tid}/messages?...         -> {"role": "user", ...}
  POST {endpoint}/threads/{tid}/runs?...             -> {"id": "run_...", "status": "queued"}
  GET  {endpoint}/threads/{tid}/runs/{rid}?...       -> {"status": "completed" | ...}
  GET  {endpoint}/threads/{tid}/messages?...         -> {"data": [messages]}

Auth: Authorization: Bearer <token>. In production that token is an
Entra ID access token (the production path); v1 also accepts a project
API key from the environment as the Bearer value. Never paste a key in
chat — pass it via the FOUNDRY_API_KEY env var.

Runs expire ~10 minutes after creation if they are left waiting on
tool output, so the poll loop is bounded by run_timeout as well.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

DEFAULT_API_VERSION = "2025-05-15-preview"

# Terminal run states. Anything else (queued, in_progress, cancelling)
# means keep polling. requires_action is handled separately: it means
# the agent wants a function-tool output, which v1 does not support.
_TERMINAL = {"completed", "failed", "cancelled", "expired"}


class FoundryError(RuntimeError):
    """A Foundry call failed. The message always says which step failed
    and what the service answered, so a bad endpoint/key/agent id is
    diagnosable from the error alone."""


class FoundryAgentClient:
    """One agent call = thread + message + run + poll + read the reply.

    endpoint: the Foundry project endpoint, e.g.
        https://<ResourceName>.services.ai.azure.com/api/projects/<ProjectName>
    api_key: Bearer token (Entra ID in production, API key in v1).
    """

    def __init__(self, endpoint: str, api_key: str,
                 api_version: str = DEFAULT_API_VERSION,
                 timeout: float = 30.0, poll_interval: float = 2.0,
                 run_timeout: float = 600.0):
        if not endpoint:
            raise ValueError("FoundryAgentClient needs an endpoint: "
                             "https://<Resource>.services.ai.azure.com/api/projects/<Project>")
        if not api_key:
            raise ValueError("FoundryAgentClient needs a key/token: set "
                             "FOUNDRY_API_KEY (Entra ID token in production).")
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.api_version = api_version
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.run_timeout = run_timeout

    # -- low-level ------------------------------------------------------
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
                f"Foundry HTTP {exc.code} on {method} {path}: {body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise FoundryError(
                f"Foundry request failed on {method} {path}: {exc}"
            ) from exc

    # -- the five steps -------------------------------------------------
    def create_thread(self) -> str:
        return self._request("POST", "/threads", {})["id"]

    def add_message(self, thread_id: str, content: str) -> dict:
        return self._request("POST", f"/threads/{thread_id}/messages",
                             {"role": "user", "content": content})

    def create_run(self, thread_id: str, agent_id: str) -> dict:
        # The run body names the agent. (The older Assistants API used
        # "assistant_id" here; the Agent Service uses "agent_id".)
        return self._request("POST", f"/threads/{thread_id}/runs",
                             {"agent_id": agent_id})

    def get_run(self, thread_id: str, run_id: str) -> dict:
        return self._request("GET", f"/threads/{thread_id}/runs/{run_id}")

    def list_messages(self, thread_id: str) -> list[dict]:
        return self._request("GET", f"/threads/{thread_id}/messages").get("data", [])

    # -- one call, end to end -------------------------------------------
    def call_agent(self, agent_id: str, prompt: str,
                   thread_id: str | None = None) -> str:
        """Ask one Foundry agent a question, return its final text.

        Creates a thread when none is given (each delegation is a fresh
        thread — the Leader passes earlier results in the prompt, so
        specialists build on each other without sharing a thread).
        """
        thread_id = thread_id or self.create_thread()
        self.add_message(thread_id, prompt)
        run = self.create_run(thread_id, agent_id)
        run_id = run["id"]
        deadline = time.monotonic() + self.run_timeout
        while True:
            run = self.get_run(thread_id, run_id)
            status = run.get("status")
            if status == "completed":
                return self._last_assistant_text(thread_id)
            if status == "requires_action":
                # The agent wants a function-tool output. v1 has no
                # tool handler, so this is an error, not a hang.
                tools = (run.get("required_action", {})
                         .get("submit_tool_outputs", {})
                         .get("tool_calls", []))
                names = ", ".join(t.get("function", {}).get("name", "?")
                                  for t in tools) or "?"
                raise FoundryError(
                    f"run {run_id} needs tool outputs ({names}) — function "
                    "tools are not supported in v1. Give the agent no "
                    "function tools, or add a tool-output handler.")
            if status in _TERMINAL:
                err = run.get("last_error") or {}
                raise FoundryError(
                    f"run {run_id} ended {status}: "
                    f"{err.get('code', '')} {err.get('message', '')}".strip())
            if time.monotonic() >= deadline:
                raise FoundryError(
                    f"run {run_id} did not finish in {self.run_timeout:.0f}s "
                    "(runs also expire server-side after ~10 min).")
            time.sleep(self.poll_interval)

    def _last_assistant_text(self, thread_id: str) -> str:
        """The latest assistant message's text parts, joined."""
        for msg in reversed(self.list_messages(thread_id)):
            if msg.get("role") != "assistant":
                continue
            parts = []
            for item in msg.get("content") or []:
                if item.get("type") == "text":
                    parts.append(item.get("text", {}).get("value", ""))
            if parts:
                return "\n".join(p for p in parts if p).strip()
        return ""
