"""The default team: five specialists, each a Foundry agent.

Agent ids come from the environment (FOUNDRY_AGENT_DEV, ...) or from a
JSON config file: {"members": [{"handle": "dev", "agent_id": "asst_..."}]}.
A member with no agent id is skipped with a note — the team still runs
with whoever is wired up.
"""
from __future__ import annotations

import json
import os

from team import Member

# handle, name, role, env var
_DEFAULT_SPECS = [
    ("researcher", "Researcher",
     "Researches, compares options, finds facts and sources.",
     "FOUNDRY_AGENT_RESEARCHER"),
    ("dev", "Dev",
     "Builds code, APIs, scripts and fixes bugs.",
     "FOUNDRY_AGENT_DEV"),
    ("qa", "QA",
     "Tests, verifies, finds edge cases and regressions.",
     "FOUNDRY_AGENT_QA"),
    ("writer", "Writer",
     "Writes docs, READMEs and clear explanations.",
     "FOUNDRY_AGENT_WRITER"),
    ("reviewer", "Reviewer",
     "Reviews work for quality, risks and correctness.",
     "FOUNDRY_AGENT_REVIEWER"),
]


def _spec_by_handle(handle: str):
    for spec in _DEFAULT_SPECS:
        if spec[0] == handle:
            return spec
    return None


def load_members(config_path: str | None = None,
                 env: dict | None = None) -> tuple[list[Member], list[str]]:
    """Returns (members, skipped handles).

    With a config path, agent ids come from the JSON file (entries may
    add their own name/role; unknown handles are kept as-is). Without
    one, ids come from the FOUNDRY_AGENT_* env vars. A member with no
    agent id lands in `skipped`, not in `members`.
    """
    env = os.environ if env is None else env
    members: list[Member] = []
    skipped: list[str] = []
    if config_path:
        with open(config_path) as f:
            data = json.load(f)
        for entry in data.get("members", []):
            handle = (entry.get("handle") or "").lower()
            spec = _spec_by_handle(handle)
            name = entry.get("name") or (spec[1] if spec else handle.title())
            role = entry.get("role") or (spec[2] if spec else "")
            agent_id = entry.get("agent_id") or ""
            if agent_id:
                members.append(Member(name, handle, role, agent_id))
            else:
                skipped.append(handle)
        return members, skipped
    for handle, name, role, var in _DEFAULT_SPECS:
        agent_id = env.get(var) or ""
        if agent_id:
            members.append(Member(name, handle, role, agent_id))
        else:
            skipped.append(handle)
    return members, skipped
