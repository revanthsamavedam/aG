# MCP App demo — `show_risk_card`

A working Python example of an **MCP App**: an MCP tool with a face.
Companion to the `06-chat-ui` scaffold in this repo; same RiskCard
theme, same demo data shape. Local only, no keys, no network.

## What an MCP App is

Two parts, always two parts:

1. **A tool** that does the work and returns data, like any other
   tool. Here: `show_risk_card` takes a company name and returns the
   risk JSON (`company`, `risk_level`, `summary`, `risks[]`,
   `sources[]`).
2. **A `ui://` resource** holding the HTML the host shows for it.
   Here: `ui://risk-card/app.html`, served as
   `text/html;profile=mcp-app`.

The tool carries `_meta.ui.resourceUri` pointing at the resource.
The host fetches it with `resources/read`, renders it in a
**sandboxed iframe**, and pushes the tool's result into the iframe
via `postMessage`. The server never handles any `ui/*` messages —
that traffic is between the host and the iframe. You serve a tool
and an HTML document; the host does the theater.

## The files

```
mcp-app/
├── server.py          # MCP server (Apps extension, stdio + HTTP)
├── risk_card.html     # the RiskCard UI, plain HTML/JS, no build step
├── risk_data.py       # demo data (Anthropic High/3, OpenAI Medium/2)
├── test_client.py     # verification: tools/list, resources/read, calls
├── requirements.txt   # pinned: mcp==2.3.0
└── README.md
```

## Run it

```bash
cd mcp-app
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# stdio (default) — for local hosts, Claude Desktop, the looper, etc.
.venv/bin/python server.py

# HTTP — for hosts that need a REMOTE endpoint.
# Foundry's MCP tool connects to remote HTTP MCP servers only;
# stdio does not work for Foundry. Run this on a machine with a
# public URL (VM, Container Apps, etc.) and point Foundry at it.
.venv/bin/python server.py --transport streamable-http --port 8000
# endpoint: http://<host>:8000/mcp
```

## Test it

```bash
.venv/bin/python test_client.py                 # stdio, spawns server.py
.venv/bin/python test_client.py --http http://localhost:8000/mcp
```

What it checks: `tools/list` shows `show_risk_card` with
`_meta.ui.resourceUri` set; `resources/read` returns the HTML with
MIME type `text/html;profile=mcp-app`; calling the tool for
Anthropic returns the JSON (High, 3 risks); OpenAI returns Medium /
2 risks; an unknown company returns the generic Medium / 0 risks;
and a client that does **not** advertise Apps support gets a human
sentence instead of JSON.

## How it plugs into the 06 chat UI / CopilotKit

CopilotKit renders MCP Apps through its MCP Apps entry point
(`@copilotkit/react-core/mcp-apps`): when the agent calls a tool
that carries a `ui://` resource, the frontend fetches the HTML and
renders it in the chat instead of (or next to) the text. The 06
scaffold's own RiskCard works differently — the *agent* calls a
frontend tool and CopilotKit renders a React component. The MCP App
path is the alternative: the UI ships *with the tool*, from the MCP
server, so any Apps-capable host can render it without the frontend
knowing about the component in advance.

## The graceful-degradation rule (from the spec)

> Tools **MUST** return a meaningful `content` array even when UI is
> available. The model reads `content`; the iframe is for humans.

This server follows the documented pattern: it checks
`client_supports_apps(ctx)` and returns the raw JSON to UI-capable
hosts (the iframe parses it; the model reads the same JSON), and a
real sentence to text-only clients. Never a placeholder like
`"[Rendered UI]"` — a text-only client would get nothing useful.

## About the HTML: why plain postMessage, not the ext-apps SDK

The official `@modelcontextprotocol/ext-apps` browser SDK gives you
`ontoolresult`, `callServerTool`, and `getHostContext` instead of raw
message events. It is an npm package, so using it needs a bundler or
a CDN fetch at render time. This demo keeps the HTML
dependency-free — no build step, no network — so it uses the same
handshake the SDK speaks, in ~40 lines: `ui/initialize` →
`ui/notifications/initialized` → `ui/notifications/tool-result`.
For a real app, use the SDK. The HTML loads no external resources,
so it requests no CSP domains or browser permissions.

## Honest limits

1. **No auth.** Neither transport authenticates callers. The HTTP
   mode accepts any request. **Add auth before exposing it** — put
   it behind a reverse proxy with a token check, or add middleware
   in `server.py`. The stdio mode is safe by construction (only the
   process that spawned it can talk to it).
2. **The host must be Apps-capable.** If the host does not declare
   the `io.modelcontextprotocol/ui` extension, the user only gets
   the text sentence — the card never renders. That is by design
   (see the degradation rule above), but it means the UI is only
   as good as the host.
3. **Host rendering was NOT verified here.** The checks above prove
   the server side: the tool binding, the resource, the MIME type,
   and both answer shapes. Whether a real host renders the HTML in
   an iframe was not tested offline — that needs an Apps-capable
   host (CopilotKit MCP Apps, Claude Desktop, etc.).
4. **Demo data is fake.** Same numbers as the 06 scaffold, from
   public Anthropic S-1 reporting. Not live research.
5. **One tool, one card.** The `ui://` resource is bound to
   `show_risk_card` only. A new card means a new tool + resource
   pair (same 4-move pattern as in `server.py`).
