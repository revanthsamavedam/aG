# MCP state — hosted MCP server + hosted Foundry agent + two-way UI

A detailed, working example of the full loop: a **hosted MCP server**
with tools that **set state**, attached to a **hosted Foundry agent**,
driving **two-way communication** with the chat UI. Follow-on to the
`06-chat-ui` scaffold (local Agent Framework agent + RiskCard) and
the `mcp-app` example (an MCP App: a tool with a face).

The difference from those two: here the state lives on the MCP
server, the agent lives in Foundry, and the human's button click
travels all the way back to change that state.

## The pieces

```
mcp-state/
├── mcp-server/
│   ├── server.py         # Case Store MCP server (3 tools, HTTP + stdio)
│   ├── requirements.txt  # pinned: mcp==2.3.0
│   └── test_client.py    # verification: tools/list + state changes
├── foundry/
│   ├── attach.py         # create the hosted agent with the MCP tool
│   ├── driver.py         # FastAPI sketch: run -> pause -> approve -> resume
│   ├── requirements.txt  # azure-ai-projects==2.11.0, fastapi
│   └── README.md         # portal steps, SDK attach, driver usage
├── frontend/
│   ├── CaseCard.tsx          # the card: case + 4 buttons
│   └── registry-snippet.tsx  # useHumanInTheLoop registration
└── README.md             # this file
```

## The two-way flow, step by step

Numbered message path for "Approve CASE-101":

1. **User asks in chat:** "Approve CASE-101."
2. **Agent (hosted in Foundry) reads first:** it calls the MCP
   `get_case` tool with `case_id="CASE-101"`. The MCP server returns
   the case JSON (status `open`). Read tools never need approval.
3. **Agent asks the human:** it calls the frontend tool
   `ask_case_decision` with `{case_id: "CASE-101", title: "...",
   current_status: "open"}`. The run pauses.
4. **CopilotKit renders the CaseCard** in the chat (registered with
   `useHumanInTheLoop` — the hook that pauses a tool call and hands
   the card a `respond` function).
5. **User clicks Approve.** The card calls
   `respond({action: "set_status", case_id: "CASE-101",
   status: "approved"})`.
6. **CopilotKit sends the decision back** as the tool call's result,
   over the same AG-UI run input the chat already uses. The run
   resumes. The case_id travels in the decision, so the agent knows
   exactly which case; the thread is the conversation itself, so the
   decision lands in the same thread — never a different one.
7. **Agent acts on the decision:** per its instructions it calls the
   MCP `set_case_status` tool with EXACTLY the case_id and status
   from the card's response.
8. **The MCP tool requires approval** (configured in Foundry:
   `require_approval.always = ["set_case_status"]`), so the run
   pauses AGAIN — this time on the server side. The driver
   (`foundry/driver.py`) holds the paused run and returns the pending
   tool call; the human's approval is submitted through
   `POST /approve`, which calls Foundry's `submit_tool_outputs` and
   resumes the run.
9. **The MCP server executes the tool:** it mutates the in-memory
   store (`CASES["CASE-101"]["status"] = "approved"`) and returns the
   updated case JSON.
10. **Agent confirms in chat:** "CASE-101: open → approved." A later
    `get_case("CASE-101")` returns `approved` — the state change
    persisted.

Note the two pauses are different mechanisms for the same idea:
step 4 is the FRONTEND pause (CopilotKit human-in-the-loop), step 8
is the FOUNDRY pause (MCP `require_approval`). In this example both
guard the same write. If you only need one, drop the other — the
card alone is enough when the agent runs locally (06 scaffold), and
the Foundry approval alone is enough when there is no card.

## Run it

```bash
# 1. The MCP server (HTTP — required for Foundry; stdio alone is not
#    enough, Foundry's MCP tool only connects to remote HTTP endpoints).
cd mcp-server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py --transport streamable-http --port 8899
# endpoint: http://localhost:8899/mcp

# 2. Test it (in a second terminal, same venv):
.venv/bin/python test_client.py --http http://localhost:8899/mcp
# or stdio: .venv/bin/python test_client.py

# 3. Attach it to a hosted Foundry agent (needs your project endpoint
#    + `az login`; --plan first, it needs no credentials):
cd ../foundry
pip install -r requirements.txt
python attach.py --plan --mcp-url http://<host>:8899/mcp
python attach.py --apply --mcp-url http://<host>:8899/mcp

# 4. Drive it (the pause/resume sketch):
export FOUNDRY_PROJECT_ENDPOINT=https://<Resource>.services.ai.azure.com/api/projects/<Project>
export FOUNDRY_API_KEY=<token>   # Entra ID in production; API key in v1
export FOUNDRY_AGENT_ID=<agent id from the portal, not the name>
python driver.py                 # port 8001

# 5. The card: copy frontend/CaseCard.tsx into the 06 scaffold's
#    frontend/src/components/, and paste frontend/registry-snippet.tsx
#    into useCustomComponents() in registry.tsx.
```

## How to test

- **MCP server:** `test_client.py` (stdio and HTTP) checks
  `tools/list` (all three tools, readOnlyHint on the two reads),
  `list_cases` (3 cases), `get_case` CASE-101 (`open`),
  `set_case_status` CASE-101 → `approved`, `get_case` again
  (still `approved` — persistence across calls), a bad status and
  an unknown case_id (clean error strings, no crash, case unchanged),
  and a second mutation (CASE-102 → `denied`).
- **Attach:** `attach.py --plan` builds the definition from the real
  SDK models and prints it — no credentials needed.
- **Driver + card + Foundry attach:** NOT live-tested (see limits).

## Honest limits

1. **In-memory state is demo-only.** The case store is a dict in
   the server process. Restart the server and the statuses reset.
   A real deployment needs a database (Postgres, Cosmos DB, whatever
   the team already runs) behind the three tools.
2. **No auth on the MCP HTTP endpoint.** Anyone who can reach
   `http://<host>:8899/mcp` can call `set_case_status`. Do not
   expose it beyond localhost without auth in front of it — a
   reverse proxy with a token check, or the MCP server's OAuth
   support. Same for `driver.py` (no auth, local learning only).
3. **Foundry attach and the driver were NOT live-tested against a
   real Foundry project.** The SDK call names are verified against
   the installed azure-ai-projects 2.11.0 (`MCPTool`,
   `MCPToolRequireApproval`, `MCPToolFilter`,
   `client.agents.create_version`), and the thread/message/run/poll
   calls are copied from the verified 01 REST client. But going live
   needs his Foundry project endpoint + agent id + credentials via
   secure entry. The one piece with no offline proof at all is the
   exact `required_action` payload for an MCP-tool approval — the
   driver logs it on the first live pause so it can be checked
   against what `/approve` submits.
4. **The card render was NOT live-tested in a browser.** The
   `useHumanInTheLoop` + `respond` mechanism is verified against the
   installed @copilotkit/react-core 1.89.0 types, and the snippet
   follows the 06 registry conventions, but no `npm run build` or
   live chat run was done for this card.
5. **Demo data is fake.** Three made-up cases, no real system
   behind them.
