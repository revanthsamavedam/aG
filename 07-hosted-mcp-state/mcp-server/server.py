"""Case Store MCP server — a HOSTED MCP server with state-setting tools.

Three tools over one in-memory case store:
  get_case(case_id)                 read-only, returns the case JSON.
  list_cases()                      read-only, returns all cases.
  set_case_status(case_id, status)  WRITES. Mutates the store and returns
                                    the updated case JSON.

The store is a plain dict that lives for the life of this process, so
state persists across calls (set a status, get the same case, see the
new status) but resets when the server restarts. That is demo-only:
a real deployment needs a database (see README).

Run it:
    python server.py                        # stdio (default)
    python server.py --transport streamable-http --port 8899
                                            # HTTP — REQUIRED for Foundry.
                                            # Foundry's MCP tool connects
                                            # to remote HTTP MCP servers
                                            # only; stdio does not work
                                            # there. Point Foundry at
                                            # http://<host>:8899/mcp

No auth on either transport: do not expose the HTTP mode beyond
localhost without adding authentication in front of it.
"""

import argparse
import json

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

VALID_STATUSES = ["open", "in_review", "approved", "denied", "escalated"]

# The demo store. Three cases, seeded at import. `set_case_status`
# mutates these dicts in place — that is the whole point of the demo.
CASES: dict[str, dict] = {
    "CASE-101": {
        "id": "CASE-101",
        "title": "Anthropic S-1 risk review",
        "status": "open",
        "notes": "Filed Oct 26, 2026. Compute commitments vs revenue.",
    },
    "CASE-102": {
        "id": "CASE-102",
        "title": "OpenAI vendor onboarding",
        "status": "in_review",
        "notes": "Security review passed; legal still reading the DPA.",
    },
    "CASE-103": {
        "id": "CASE-103",
        "title": "Vendor contract renewal",
        "status": "open",
        "notes": "Renews Dec 1. Needs a decision before Nov 25.",
    },
}

READ_ONLY = ToolAnnotations(read_only_hint=True)

mcp = MCPServer("case-store")


def _known_ids() -> str:
    return ", ".join(sorted(CASES))


@mcp.tool(
    description="Read one case by id. Read-only, never changes state.",
    annotations=READ_ONLY,
)
def get_case(case_id: str) -> str:
    """Return the case as JSON, or a clear error when the id is unknown."""
    case = CASES.get(case_id)
    if case is None:
        return f'Error: unknown case_id "{case_id}". Known cases: {_known_ids()}.'
    return json.dumps(case)


@mcp.tool(
    description="List all cases with their current status. Read-only.",
    annotations=READ_ONLY,
)
def list_cases() -> str:
    """Return all cases as a JSON list, in id order."""
    return json.dumps([CASES[k] for k in sorted(CASES)])


@mcp.tool(
    description=(
        "Set a case's status. THIS WRITES STATE: it changes the stored "
        "case and returns the updated case JSON. status must be one of: "
        "open, in_review, approved, denied, escalated."
    ),
)
def set_case_status(case_id: str, status: str) -> str:
    """Change the case's status and return the updated case as JSON.

    A bad case_id or a bad status returns a plain error string, not a
    crash — the tool call still succeeds, and the model (and the human
    reading the result) can see what went wrong and retry.
    """
    case = CASES.get(case_id)
    if case is None:
        return f'Error: unknown case_id "{case_id}". Known cases: {_known_ids()}.'
    if status not in VALID_STATUSES:
        return (
            f'Error: invalid status "{status}". '
            f"Must be one of: {', '.join(VALID_STATUSES)}."
        )
    case["status"] = status
    return json.dumps(case)


def main() -> None:
    parser = argparse.ArgumentParser(description="Case Store MCP server.")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="stdio (default) or streamable-http for remote hosts.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=8899, help="HTTP bind port.")
    args = parser.parse_args()
    if args.transport == "streamable-http":
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
