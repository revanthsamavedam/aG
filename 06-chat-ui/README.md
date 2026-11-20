# Foundry Chat UI — a minimal generic chat UI for Foundry agents

A chat UI you can point at any Foundry agent (or any OpenAI-compatible
model), with a way to register your own React components that the agent
can trigger. Local learning scaffold, not a production app.

## The pieces, in plain language

```
Browser (React + CopilotKit)
      │  AG-UI over HTTP (POST / + SSE events)
      ▼
FastAPI agent server (agent/server.py)
      │  Microsoft Agent Framework
      ▼
Chat client: Foundry agent  |  OpenAI-compatible model  |  demo (no key)
```

- **`frontend/`** — Vite + React + TypeScript + CopilotKit. The chat UI.
  It connects **directly** to the FastAPI server with CopilotKit's
  `selfManagedAgents` and an `HttpAgent` from `@ag-ui/client`. There is
  **no Node/CopilotKit runtime in between** — that was a deliberate
  change of plan (the user asked for the runtime to be FastAPI). The
  agent URL is the only config the browser needs (`VITE_AGENT_URL`,
  default `http://localhost:8000`). The browser never holds credentials.
- **`agent/`** — FastAPI + Microsoft Agent Framework (`agent-framework`
  + `agent-framework-ag-ui`). Exposes the agent as an AG-UI endpoint
  (`POST /`, Server-Sent Events back) via
  `add_agent_framework_fastapi_endpoint`. The agent has one tool,
  `show_risk_card`, which returns structured risk JSON for a company.

## Run it locally

Two terminals.

### 1. Agent server (demo mode — no keys, no network)

```bash
cd agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn server:app --port 8000
```

Check it: `curl http://localhost:8000/health` →
`{"status":"ok","mode":"demo"}`.

Demo mode is the default when no credentials are set. The demo client
(`agent/demo_client.py`) is a fake model: it has no model behind it, it
just pattern-matches the company name in your question and calls the
`show_risk_card` tool from built-in data (Anthropic and OpenAI).
Ask: **"What is the risk on Anthropic?"** — the tool runs and the
RiskCard renders in the chat.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

To point the frontend at a different agent URL:

```bash
VITE_AGENT_URL=http://localhost:8001 npm run dev
```

Build check: `npm run build` (runs `tsc -b && vite build`).

## Register a new custom component — 3 steps

A custom component is a React component the agent can trigger: the
agent calls a tool, and instead of plain text the chat shows your
component. The one place they live is
`frontend/src/components/registry.tsx`.

**Step 1 — Write the React component.**
E.g. `frontend/src/components/MyCard.tsx`. It takes the structured
data as props. See `RiskCard.tsx` for the example: it renders
`company`, `risk_level`, `summary`, `risks[]`, `sources[]`.

**Step 2 — Register it in the registry.**
In `registry.tsx`, inside `useCustomComponents()`, add a
`useRenderTool` call with the SAME name as the agent's tool, a zod
schema for the tool's arguments, and a `render` function:

```tsx
useRenderTool(
  {
    name: "my_tool",
    parameters: z.object({ my_arg: z.string() }),
    render: ({ status, result }) => {
      if (status !== "complete") return <div>Working…</div>;
      const data = parseMyData(result); // validate the tool's JSON
      return data ? <MyCard data={data} /> : <div>Could not read that.</div>;
    },
  },
  [],
);
```

`status` is `"inProgress"` (agent deciding), `"executing"` (tool
running), or `"complete"` (result is in). `result` is the tool's
return value as a string — parse and validate it, never trust it
blindly (`parseRiskCardData` in `RiskCard.tsx` shows the pattern:
JSON.parse, then check every field, return null on a bad shape).

**Step 3 — Give the agent a matching tool.**
In `agent/server.py`, add a function with the SAME name that returns
a JSON string, and add it to the agent's `tools=[...]`:

```python
def my_tool(my_arg: str) -> str:
    return json.dumps({"my_arg": my_arg, "...": "..."})
```

The name must match on both sides. If it doesn't, the agent calls a
tool the frontend doesn't know about and you get plain text instead
of your component.

## Point it at a real Foundry agent

The agent server picks its chat client from the environment, in this
order:

1. **Foundry** — set `FOUNDRY_PROJECT_ENDPOINT` (your Foundry project
   endpoint, e.g. `https://<resource>.services.ai.azure.com/api/projects/<project>`).
   Uses `FoundryChatClient` with `DefaultAzureCredential` — run
   `az login` first. Model from `FOUNDRY_MODEL` (default `gpt-4o-mini`).
   ```bash
   FOUNDRY_PROJECT_ENDPOINT=https://... .venv/bin/uvicorn server:app --port 8000
   ```
2. **OpenAI-compatible** — set `OPENAI_API_KEY`. Model from
   `OPENAI_MODEL` (default `gpt-4o-mini`), base URL from
   `OPENAI_BASE_URL` (for a Foundry Models v1 endpoint or any
   OpenAI-compatible server).
3. **Demo** — neither set. Fake client, no model, no network.

The frontend needs no change: it just talks to the same `VITE_AGENT_URL`.
If the agent server runs somewhere else, set `VITE_AGENT_URL` to that
URL and set `CORS_ORIGINS` on the server to the frontend's exact
origin (default allows `http://localhost:5173`).

A real Foundry *agent* (not just a model) is a different wiring: the
scaffold's agent is a plain Agent Framework agent running in FastAPI.
To drive a hosted Foundry agent instead, swap the chat client for the
Foundry Agent Service client (`FoundryAgentClient`, thread → message →
run → poll) — see the `01-team-of-agents` folder of the
`revanthsamavedam/aG` tutorial repo for that client shape.

## Honest limits — read before exposing this

1. **No auth.** The scaffold has no authentication or authorization.
   The FastAPI endpoint accepts any request. **Add auth before
   exposing it** — e.g. Entra ID on the endpoint (the endpoint is
   declared `single_user=True` precisely because there is no per-user
   scoping here; for multiple users add real auth and an
   `authorization_scope_resolver`). The browser holds no credentials,
   but that only helps if the server checks who is calling.
2. **In-session history only.** The chat keeps history in the browser
   session. There is no saved conversation list and no thread history
   from Foundry. Full thread history (list threads, list messages)
   from the Foundry API is a next step, not built here.
3. **Runtime-only features are not available on the direct path.**
   With no CopilotKit runtime in front, features the runtime provides
   (e.g. threads) don't exist here. If you need them, you need a
   runtime — the TypeScript `@copilotkit/runtime` package is free and
   MIT licensed; the Python runtime requires the paid CopilotKit
   Intelligence plan.
4. **Demo data is fake.** The Anthropic/OpenAI risk data in
   `agent/risk_data.py` is small built-in demo data (Anthropic: High,
   3 risks, from public S-1 reporting). It is not live research.
5. **Not live-tested against a real Foundry project.** The demo path
   is verified end to end (see below). Foundry/OpenAI modes are wired
   from the SDK docs, not tested against a live endpoint here.

## `selfManagedAgents` vs `agents__unsafe_dev_only`

The frontend uses `selfManagedAgents` (the production shape): you
create the `HttpAgent` yourself and hand it to `<CopilotKit>`.
CopilotKit also offers `agents__unsafe_dev_only`, which is a shortcut
for local dev only — it lets the browser reach agents without the
production wiring. Prefer `selfManagedAgents`; the dev-only flag is
not for anything you ship.

## How the demo was verified

- `npm run build` passes (`tsc -b && vite build`).
- Agent server `/health` returns `{"status":"ok","mode":"demo"}`.
- A real AG-UI run (`POST /` with "What is the risk on Anthropic?")
  streams the full event sequence: `RUN_STARTED` →
  `TOOL_CALL_START` / `TOOL_CALL_ARGS` (`show_risk_card`,
  `{"company": "Anthropic"}`) → `TOOL_CALL_END` →
  `TOOL_CALL_RESULT` (the full JSON) → final text
  ("Here is the risk summary — see the card above.") →
  `MESSAGES_SNAPSHOT` → `RUN_FINISHED`. That is exactly what
  CopilotKit's `useRenderTool` needs to render the RiskCard.
- `agent/test_wiring.py` asserts the tool output matches the
  RiskCard contract (Anthropic High / 3 risks, OpenAI Medium /
  2 risks, unknown company → generic Medium / 0 risks).

Two things the build taught us, recorded in the code:

- The tool-calling loop lives in the **chat client**, not in the
  Agent. A bare demo client that only implements the response
  protocol gets one model turn and the tool never executes.
  `DemoChatClient` therefore composes `FunctionInvocationLayer` +
  `BaseChatClient` and implements `_inner_get_response` — the same
  shape the real OpenAI/Foundry clients use. Streaming must return a
  `ResponseStream` with a finalizer (`ChatResponse.from_updates`),
  not a bare async generator.
- `get_response` must be a plain `def`, not `async def` — a coroutine
  breaks the stream handling (`'coroutine' object has no attribute
  'map'`).

## Files

```
foundry-chat-ui/
├── README.md
├── frontend/
│   ├── package.json            # @copilotkit/react-core 1.89.0, @ag-ui/client 1.0.5, zod 3.25, React 19
│   ├── vite.config.ts
│   ├── index.html
│   ├── tsconfig.json / tsconfig.app.json / tsconfig.node.json
│   └── src/
│       ├── main.tsx            # imports @copilotkit/react-core/v2/styles.css
│       ├── App.tsx             # CopilotKit + selfManagedAgents + HttpAgent(VITE_AGENT_URL)
│       ├── index.css           # RiskCard styles
│       ├── vite-env.d.ts
│       └── components/
│           ├── registry.tsx    # THE registry: useRenderTool("show_risk_card", ...)
│           └── RiskCard.tsx    # RiskCard + parseRiskCardData (the contract)
└── agent/
    ├── server.py               # FastAPI + AG-UI endpoint + show_risk_card tool + mode picker
    ├── demo_client.py          # Fake model client (demo mode, no key, no network)
    ├── risk_data.py            # Built-in demo risk data (Anthropic, OpenAI)
    ├── requirements.txt        # Pinned: agent-framework 1.26.0, agent-framework-ag-ui 1.8.1, fastapi 0.162.7, azure-ai-projects 2.11.0
    └── test_wiring.py          # Tool output matches the RiskCard contract
```

Versions verified while building: `@copilotkit/react-core` 1.89.0,
`@ag-ui/client` 1.0.5, `zod` 3.25.0, React 19, `agent-framework`
1.26.0, `agent-framework-ag-ui` 1.8.1, `fastapi` 0.162.7,
`azure-ai-projects` 2.11.0. (The build emits a chunk-size warning —
CopilotKit bundles a lot, including mermaid — harmless for a
scaffold.)
