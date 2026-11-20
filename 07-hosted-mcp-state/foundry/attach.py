"""Attach the Case Store MCP server to a HOSTED Foundry agent.

What this does: creates a new version of the `case-manager` agent in
your Foundry project, with the MCP tool pointing at your hosted
Case Store server. `create_version` IS the create/update mechanism —
there is no separate update call for the agent definition; every
apply creates a new version and the latest version is what runs.

    python attach.py --plan
        # Build the agent definition from the real SDK models and
        # print it. Needs NO credentials and touches nothing — this
        # is how the definition shape was verified offline.
    python attach.py --apply --mcp-url http://<host>:8899/mcp
        # Create the agent version for real. Needs
        # FOUNDRY_PROJECT_ENDPOINT set and `az login` (Entra ID).
        # Use --agent to pick a different agent name, --model to
        # pick a different model deployment.

Every call name here was verified against azure-ai-projects 2.11.0
(the version pinned in requirements.txt):
  AIProjectClient(endpoint=..., credential=...)
  client.agents.create_version(agent_name=..., definition={...})
  MCPTool(server_label=..., server_url=..., allowed_tools=[...],
          require_approval=MCPToolRequireApproval(
              always=MCPToolFilter(tool_names=[...]),
              never=MCPToolFilter(tool_names=[...])))

The approval config is the point of this example:
  set_case_status  -> always requires approval (it writes state)
  get_case, list_cases -> never (read-only)

When the agent calls set_case_status, the run pauses and waits for
the human's decision — that pause is what the CaseCard in
frontend/ answers (see frontend/registry-snippet.tsx and the driver
in driver.py, which is the server side of the same pause).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

AGENT_NAME = "case-manager"
SERVER_LABEL = "case-store"
ALLOWED_TOOLS = ["get_case", "list_cases", "set_case_status"]
ALWAYS_APPROVE = ["set_case_status"]
NEVER_APPROVE = ["get_case", "list_cases"]

# The agent's instructions. This is the matching half of the card:
# it tells the agent to use the read tools first, and to call
# set_case_status ONLY with the case_id and status from the human's
# card response — never a case_id it guessed, never a status the
# human did not pick.
AGENT_INSTRUCTIONS = """You are a case manager. You have three tools on the case-store
MCP server: get_case (read one case), list_cases (read all cases),
and set_case_status (change a case's status).

Rules:
1. When the user asks about a case, read it first with get_case or
   list_cases. Never guess a case's status.
2. Changing a status needs a human decision. When a status change is
   needed, call the ask_case_decision tool (the frontend shows the
   case as a card with Approve / Deny / Set in review / Escalate
   buttons) and WAIT for the human's answer.
3. When the card answers, call set_case_status with EXACTLY the
   case_id and status from the card's response. Do not substitute a
   different case or a different status.
4. After the status changes, confirm in one short sentence: the case
   id, the old status, and the new status.
5. If the card answers Deny, do nothing to the case and say so.
"""


def build_mcp_tool(mcp_url: str) -> dict:
    """The MCP tool as a wire dict, built from the real SDK models.

    Constructing MCPTool / MCPToolRequireApproval / MCPToolFilter and
    calling as_dict() guarantees the dict matches what the SDK sends
    — no hand-written field names.
    """
    from azure.ai.projects.models import (
        MCPTool,
        MCPToolFilter,
        MCPToolRequireApproval,
    )

    tool = MCPTool(
        server_label=SERVER_LABEL,
        server_url=mcp_url,
        allowed_tools=list(ALLOWED_TOOLS),
        require_approval=MCPToolRequireApproval(
            always=MCPToolFilter(tool_names=list(ALWAYS_APPROVE)),
            never=MCPToolFilter(tool_names=list(NEVER_APPROVE)),
        ),
    )
    return tool.as_dict()


def build_definition(model: str, mcp_url: str) -> dict:
    """The full agent definition for client.agents.create_version."""
    return {
        "kind": "prompt",
        "model": model,
        "instructions": AGENT_INSTRUCTIONS,
        "tools": [build_mcp_tool(mcp_url)],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Attach the Case Store MCP server to a hosted Foundry agent."
    )
    parser.add_argument("--mcp-url", default="http://localhost:8899/mcp",
                        help="Public URL of the hosted MCP server.")
    parser.add_argument("--agent", default=AGENT_NAME,
                        help="Agent name in the Foundry project.")
    parser.add_argument("--model", default=os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini"),
                        help="Model deployment for the agent.")
    parser.add_argument("--plan", action="store_true",
                        help="Print the definition, change nothing.")
    parser.add_argument("--apply", action="store_true",
                        help="Create the agent version for real.")
    args = parser.parse_args()

    if not args.plan and not args.apply:
        parser.error("pick one: --plan (print only) or --apply (create it)")

    definition = build_definition(args.model, args.mcp_url)
    print(f"agent: {args.agent}  model: {args.model}")
    print(f"MCP server: {args.mcp_url} (label {SERVER_LABEL})")
    print(f"allowed_tools: {ALLOWED_TOOLS}")
    print(f"require_approval: always={ALWAYS_APPROVE} never={NEVER_APPROVE}")
    print(json.dumps(definition, indent=2))

    if args.plan:
        print("\n--plan: definition printed, nothing created.")
        return

    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        print("ERROR: FOUNDRY_PROJECT_ENDPOINT is not set.", file=sys.stderr)
        print("Set it to your project endpoint, e.g.:", file=sys.stderr)
        print("  https://<Resource>.services.ai.azure.com/api/projects/<Project>",
              file=sys.stderr)
        raise SystemExit(1)

    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    result = client.agents.create_version(agent_name=args.agent, definition=definition)
    print(f"\nCreated agent version: {getattr(result, 'version', '?')}")
    print("The agent now has the case-store MCP tools. "
          "Talk to it in the Foundry playground or through driver.py.")


if __name__ == "__main__":
    main()
