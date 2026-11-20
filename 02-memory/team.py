"""Team + Leader — the Team Room's Leader pattern, with Foundry agents
as the specialists instead of looper members.

Shape (mirrors teamroom/orchestrator.py _run_leader, minus the DB):
  1. The Leader plans: the task becomes at most 3 ordered steps, each
     assigned to one specialist by @handle, dependencies first
     (research before building, building before testing).
  2. It delegates: each step goes to that specialist's Foundry agent
     via client.call_agent, in plan order, and each prompt carries the
     earlier specialists' results, so later steps build on earlier ones.
  3. It integrates: the Leader combines the replies into one final
     answer.

The Leader's planning/integration calls reuse the looper's provider
(a plain OpenAI-compatible model call), not a Foundry agent — the
Leader is orchestration, not a hosted agent. When no provider/key is
available the Leader falls back to a keyword plan (same idea as the
room's parallel-path routing), so the team still runs with no model
configured. A specialist that fails does not kill the run: its error
is recorded as that step's result and the team continues.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

MAX_LEADER_STEPS = 3

STEP_RE = re.compile(r"^\s*STEP\s+@([A-Za-z0-9][A-Za-z0-9_-]*)\s*:\s*(.+?)\s*$",
                     re.IGNORECASE | re.MULTILINE)

# Keyword routing, same shape as the room's KEYWORDS: each handle owns
# the words that pick it. PHASE_ORDER puts dependencies first.
KEYWORDS: dict[str, list[str]] = {
    "researcher": ["research", "find", "compare", "investigate", "what is",
                   "how does", "options", "best", "look up", "search"],
    "dev": ["build", "implement", "code", "fix", "bug", "refactor", "script",
            "function", "api", "deploy", "write code", "program", "service"],
    "qa": ["test", "qa", "verify", "check", "edge case", "regression",
           "passes", "fails", "broken"],
    "writer": ["write", "doc", "document", "draft", "readme", "explain",
               "summarise", "summarize", "summary"],
    "reviewer": ["review", "audit", "risk", "quality", "look over", "approve",
                 "is this good"],
}
PHASE_ORDER = ["researcher", "dev", "qa", "writer", "reviewer"]


@dataclass
class Member:
    """One specialist: a Foundry agent behind a handle."""
    name: str
    handle: str
    role: str          # one line, used by the Leader to pick the member
    agent_id: str = ""  # Foundry agent id (asst_...); "" = not wired up


def _score(text: str, words: list[str]) -> int:
    low = f" {text.lower()} "
    score = 0
    for w in words:
        if f" {w} " in low or low.startswith(f"{w} ") or low.endswith(f" {w}"):
            score += 1
        elif " " not in w and re.search(rf"\b{re.escape(w)}\w*\b", low):
            score += 1
    return score


def _default_planner():
    """A planner from the looper provider, or None.

    Same env vars as the looper (LOOPER_MODEL / LOOPER_API_KEY /
    LOOPER_BASE_URL, or azure/...). No key or no looper on disk -> None,
    and the Leader falls back to the keyword plan instead of failing.
    """
    looper_dir = os.path.expanduser("~/workspace/custom-looper")
    if looper_dir not in sys.path:
        sys.path.insert(0, looper_dir)
    try:
        from looper.provider import OpenAICompatibleClient, resolve_provider
    except Exception:
        return None
    try:
        return OpenAICompatibleClient(resolve_provider())
    except Exception:
        return None


class Leader:
    """Plans and integrates. `planner` is any object with
    .call(messages, tools) -> {"content": str, ...} — the looper's
    OpenAICompatibleClient, or a test double. None means keyword-only."""

    def __init__(self, planner=None):
        self.planner = planner

    def plan(self, task: str, members: list[Member]) -> list[tuple[Member, str]]:
        steps = self._plan_with_model(task, members) if self.planner else []
        return steps or self._plan_with_keywords(task, members)

    def _plan_with_model(self, task: str,
                         members: list[Member]) -> list[tuple[Member, str]]:
        handles = ", ".join(
            f"@{m.handle} ({m.name}: {m.role})" for m in members)
        system = ("You are the Leader of a team of specialist agents. "
                  "Reply with the plan only, one STEP line per step, at most "
                  f"{MAX_LEADER_STEPS} steps. Format: STEP @handle: subtask. "
                  "Order steps so dependencies come first (research before "
                  "building, building before testing).")
        try:
            out = self.planner.call(
                [{"role": "system", "content": system},
                 {"role": "user",
                  "content": f"Plan how to handle this task.\nTask: {task}\n"
                             f"Available specialists: {handles}"}],
                tools=[])
        except Exception:
            return []
        return _parse_plan(out.get("content", ""), members)

    def _plan_with_keywords(self, task: str,
                            members: list[Member]) -> list[tuple[Member, str]]:
        scored = [(m, _score(task, KEYWORDS.get(m.handle, [m.handle])))
                  for m in members]
        picked = [m for m, s in scored if s > 0]
        if not picked:
            return [(members[0], task)] if members else []
        order = {h: i for i, h in enumerate(PHASE_ORDER)}
        picked.sort(key=lambda m: order.get(m.handle, 99))
        return [(m, task) for m in picked[:MAX_LEADER_STEPS]]

    def integrate(self, task: str, results: list[tuple[Member, str]]) -> str:
        if self.planner:
            joined = "\n\n".join(f"## {m.name}\n{r}" for m, r in results)
            try:
                out = self.planner.call(
                    [{"role": "system",
                      "content": "You are the Leader. Integrate the "
                                 "specialists' results below into one final "
                                 "answer. Keep what each specialist found; "
                                 "do not repeat their full text."},
                     {"role": "user",
                      "content": f"Task: {task}\n\n{joined}"}],
                    tools=[])
                if (out.get("content") or "").strip():
                    return out["content"].strip()
            except Exception:
                pass
        # Template fallback: the work is all here, just joined.
        lines = [f"Done — {', '.join(m.name for m, _ in results)}."]
        for m, r in results:
            first = (r.splitlines() or [""])[0][:200]
            lines.append(f"- {m.name}: {first}" if first else f"- {m.name}: (no reply)")
        return "\n".join(lines)


def _parse_plan(text: str, members: list[Member]) -> list[tuple[Member, str]]:
    """One STEP line per step, handles in the pool only, deduped, capped."""
    by_handle = {m.handle: m for m in members}
    steps: list[tuple[Member, str]] = []
    seen: set[str] = set()
    for m in STEP_RE.finditer(text or ""):
        handle = m.group(1).lower()
        subtask = m.group(2).strip()
        if handle not in by_handle or handle in seen or not subtask:
            continue
        seen.add(handle)
        steps.append((by_handle[handle], subtask))
        if len(steps) >= MAX_LEADER_STEPS:
            break
    return steps


class Team:
    """A team of Foundry agents under one Leader.

    client is any object with call_agent(agent_id, prompt,
    thread_id=None) -> str — FoundryAgentClient or FakeFoundryClient.
    Members with no agent_id are skipped with a note, never run.
    """

    def __init__(self, members: list[Member], client,
                 planner=None, print_fn=print):
        self.members = members
        self.client = client
        self.leader = Leader(planner)
        self.print = print_fn
        self.last_plan: list[tuple[Member, str]] = []
        self.last_results: list[tuple[Member, str]] = []

    def run(self, task: str) -> str:
        available = [m for m in self.members if m.agent_id]
        skipped = [m for m in self.members if not m.agent_id]
        for m in skipped:
            self.print(f"(skipping @{m.handle} — no agent id set; "
                       f"see FOUNDRY_AGENT_{m.handle.upper()})")
        if not available:
            msg = ("No members have an agent id — set the FOUNDRY_AGENT_* "
                   "env vars (or a team JSON config) first.")
            self.print(msg)
            return msg

        self.print("🧭 Leader is planning how to handle this...")
        steps = self.leader.plan(task, available)
        self.last_plan = steps
        self.print("🧭 Leader's plan:")
        for i, (m, sub) in enumerate(steps, start=1):
            self.print(f"  {i}. @{m.handle}: {sub}")

        results: list[tuple[Member, str]] = []
        for i, (member, subtask) in enumerate(steps, start=1):
            self.print(f"➡️ Step {i} of {len(steps)}: delegating to @{member.handle}.")
            prompt = subtask + f"\n(Original task: {task})"
            if results:
                prompt += "\n\n[Results so far — build on these]\n" + "\n".join(
                    f"- {pm.name}: {pr[:400]}" for pm, pr in results)
            try:
                reply = self.client.call_agent(member.agent_id, prompt)
            except Exception as exc:  # noqa: BLE001 — one agent failing must not kill the run
                reply = f"⚠️ {member.name} failed: {type(exc).__name__}: {exc}"
            results.append((member, reply))
            self.print(f"@{member.handle}: {(reply.splitlines() or [''])[0][:200]}")
        self.last_results = results

        self.print(f"✅ All {len(steps)} steps done — integrating.")
        final = self.leader.integrate(task, results)
        self.print(final)
        return final
