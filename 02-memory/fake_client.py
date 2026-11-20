"""FakeFoundryClient — the same call_agent interface as
FoundryAgentClient, with canned replies per role, so the team runs
end to end with no Azure. Used by demo.py and the tests.

Agent ids are matched by handle substring ("fake-dev" -> Dev), so any
id containing the handle works. Every call is recorded in .calls, in
order, so tests can check delegation order.

Extension vs the vendored copy in 01-team-of-agents/ (and in
~/workspace/foundry-team/fake_client.py): each canned reply ends
with Decision: / Learned: / Best practice: lines. memory.py's
extraction only looks for those prefixes, so the demo needs them
here — that is the ONLY change to the vendored file.
"""
from __future__ import annotations

from team import Member

_CANNED = {
    "researcher": (
        "Research notes: the best free quote source is a public JSON quotes "
        "API (no key needed) — it returns a random quote per request. "
        "FastAPI serves it with one GET route; the random pick is "
        "random.choice over the fetched list. No auth, no database.\n"
        "Decision: use the public JSON quotes API, not a paid quotes API.\n"
        "Learned: the free API returns one random quote per request, "
        "no key required."),
    "dev": (
        "Built it: FastAPI app with GET /quote returning "
        '{"quote": ..., "author": ...} plus GET /quote/nvidia filtering to '
        "Nvidia quotes. main.py is ~25 lines (FastAPI, httpx, random). "
        "Run: uvicorn main:app --reload.\n"
        "Decision: serve quotes with FastAPI, one GET route per endpoint.\n"
        "Best practice: keep main.py small — one route per endpoint, "
        "no business logic in the route handler."),
    "qa": (
        "Tested it: GET /quote returns 200 with quote+author keys; "
        "GET /quote/nvidia returns only Nvidia quotes; empty upstream list "
        "returns 503 with a clear error, not a crash. Edge case: the "
        "upstream API down -> serve a small local fallback list.\n"
        "Learned: an empty upstream list must return a 503, not crash.\n"
        "Best practice: always test the upstream-down case — serve a "
        "local fallback list instead of failing the request."),
    "writer": (
        "README written: what it does, how to run it (uvicorn main:app), "
        "the two endpoints with example responses, and the fallback "
        "behaviour when the quotes API is down.\n"
        "Decision: document the fallback behaviour in the README, "
        "not just the happy path."),
    "reviewer": (
        "Review: looks good. Two risks — no caching, so every request hits "
        "the upstream API (add a 60s cache), and no rate limiting on the "
        "endpoint. Otherwise the shape is right.\n"
        "Learned: every request hits the upstream API with no cache.\n"
        "Best practice: add a 60s cache so repeated requests do not "
        "hammer the upstream API."),
}


def fake_members() -> list[Member]:
    """Five members with fake agent ids, for the demo and tests."""
    return [
        Member("Researcher", "researcher",
               "Researches, compares options, finds facts and sources.",
               "fake-researcher"),
        Member("Dev", "dev", "Builds code, APIs, scripts and fixes bugs.",
               "fake-dev"),
        Member("QA", "qa", "Tests, verifies, finds edge cases and regressions.",
               "fake-qa"),
        Member("Writer", "writer", "Writes docs, READMEs and clear explanations.",
               "fake-writer"),
        Member("Reviewer", "reviewer",
               "Reviews work for quality, risks and correctness.",
               "fake-reviewer"),
    ]


class FakeFoundryClient:
    def __init__(self, replies: dict[str, str] | None = None):
        self.replies = dict(_CANNED)
        if replies:
            self.replies.update(replies)
        self.calls: list[tuple[str, str]] = []  # (agent_id, prompt), in order

    def call_agent(self, agent_id: str, prompt: str,
                   thread_id: str | None = None) -> str:
        self.calls.append((agent_id, prompt))
        for handle, reply in self.replies.items():
            if handle in agent_id:
                return reply
        return f"(fake agent {agent_id} — no canned reply for this role)"
