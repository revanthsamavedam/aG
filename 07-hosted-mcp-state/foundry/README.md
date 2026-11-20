# Foundry side — attach the Case Store MCP server to a hosted agent

Two ways to attach, same result. The SDK way is scripted in
`attach.py`; the portal way is clicks. Either way the agent ends up
with one MCP tool (`case-store`) exposing three tools, with
`set_case_status` always requiring approval and the two reads never
requiring it.

## 0. Host the MCP server first

Foundry's MCP tool connects to **remote HTTP MCP servers only** —
stdio does not work. Run the server on a machine with a public URL
(VM, Container Apps, etc.) and note the URL:

```bash
cd ../mcp-server
python server.py --transport streamable-http --port 8899
# endpoint: http://<host>:8899/mcp
```

## 1. Attach — SDK way

```bash
pip install -r requirements.txt   # azure-ai-projects==2.11.0

# Print the definition, change nothing (needs no credentials):
python attach.py --plan --mcp-url http://<host>:8899/mcp

# Create the agent version for real (needs FOUNDRY_PROJECT_ENDPOINT
# and `az login` — Entra ID):
export FOUNDRY_PROJECT_ENDPOINT=https://<Resource>.services.ai.azure.com/api/projects/<Project>
python attach.py --apply --mcp-url http://<host>:8899/mcp
```

`--apply` calls `client.agents.create_version(agent_name="case-manager",
definition={...})`. `create_version` IS the create/update mechanism:
every apply creates a new version and the latest version is what
runs. The definition is built from the real SDK models (`MCPTool`,
`MCPToolRequireApproval`, `MCPToolFilter`) and serialised with
`as_dict()`, so the field names are the SDK's, not hand-written.

The approval config in the definition:

```json
{
  "type": "mcp",
  "server_label": "case-store",
  "server_url": "http://<host>:8899/mcp",
  "allowed_tools": ["get_case", "list_cases", "set_case_status"],
  "require_approval": {
    "always": {"tool_names": ["set_case_status"]},
    "never": {"tool_names": ["get_case", "list_cases"]}
  }
}
```

## 2. Attach — portal way

1. Foundry portal → your project → Agents → `case-manager`
   (create it if it does not exist; model deployment `gpt-4o-mini`).
2. Tools → Add tool → MCP → fill in:
   - Server URL: `http://<host>:8899/mcp`
   - Server label: `case-store`
   - Allowed tools: `get_case`, `list_cases`, `set_case_status`
3. Approval: set `set_case_status` to **Always require approval**,
   `get_case` and `list_cases` to **Never require approval**.
   (In the SDK this is the `require_approval` object above.)
4. Paste the instructions from `attach.py` (`AGENT_INSTRUCTIONS`)
   into the agent's Instructions. The key line: call
   `set_case_status` ONLY with the case_id and status from the
   human's card response — never a guessed case or status.
5. Save. Test in the playground: "What cases are open?" should list
   the three demo cases; "Approve CASE-101" should pause for your
   approval instead of changing it immediately.

## 3. Drive it — driver.py (FastAPI sketch)

The driver is the server side of the two-way flow: the browser talks
to it, it talks to Foundry with its own credentials. It extends the
01-team-of-agents REST client (thread → message → run → poll) with
`submit_tool_outputs` for resuming a paused run.

```bash
pip install -r requirements.txt   # fastapi, uvicorn
export FOUNDRY_PROJECT_ENDPOINT=https://<Resource>.services.ai.azure.com/api/projects/<Project>
export FOUNDRY_API_KEY=<token>    # Entra ID in production; API key in v1
export FOUNDRY_AGENT_ID=<agent id from the portal, not the name>
python driver.py                  # port 8001
```

```bash
# Start a run. 200 + reply when it finishes outright.
curl -X POST localhost:8001/run -H 'Content-Type: application/json' \
  -d '{"prompt": "What cases are open?"}'

# When the agent calls set_case_status, /run returns 202 instead:
# {"status": "needs_approval", "thread_id": "...", "run_id": "...",
#  "tool_calls": [{"tool_call_id": "...", "name": "set_case_status",
#                  "arguments": "{...}"}]}
# That is the pause. Submit the human's decision to resume:
curl -X POST localhost:8001/approve -H 'Content-Type: application/json' \
  -d '{"thread_id": "...", "run_id": "...",
       "decisions": [{"tool_call_id": "...", "approve": true}]}'
```

## Honest limits

1. **The /approve path was NOT live-tested.** The thread/message/
   run/poll calls are copied from the verified 01 REST client, and
   `submit_tool_outputs` is the standard counterpart of the
   `required_action.submit_tool_outputs` structure that client
   already parses. But the exact `required_action` payload for an
   MCP-tool approval is not in the installed SDK (azure-ai-projects
   2.11.0 only carries MCP approval models for Realtime). The
   driver logs the full `required_action` JSON the first time a run
   pauses — check that log against what `/approve` submits before
   trusting it in production.
2. **No auth on the driver.** Same as the 06 scaffold: local
   learning only. Add auth before exposing it.
3. **Runs expire.** A run left waiting on approval expires
   server-side after ~10 minutes, and the driver's in-memory
   `PENDING` map dies with the process.
