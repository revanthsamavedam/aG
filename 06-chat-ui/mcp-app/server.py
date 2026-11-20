"""MCP App demo: `show_risk_card` — a tool with a face.

An MCP App is a tool plus a `ui://` HTML resource, two parts:
1. The tool does the work and returns data (the risk JSON).
2. The `ui://risk-card/app.html` resource holds the HTML the host
   renders in a sandboxed iframe for that tool's result.

The tool carries `_meta.ui.resourceUri` pointing at the resource.
The host fetches it with `resources/read`, renders it, and pushes the
tool's result into the iframe via postMessage. This server never
handles any `ui/*` messages — that traffic is between the host and
the iframe.

Run it:
    python server.py                        # stdio (default)
    python server.py --transport streamable-http --port 8000
                                            # HTTP, for hosts that need a
                                            # remote endpoint (e.g. Foundry's
                                            # MCP tool — stdio does not work
                                            # there; the server must be
                                            # reachable over HTTP).

No auth on either transport: do not expose the HTTP mode beyond
localhost without adding authentication in front of it.
"""

import argparse
import json
from pathlib import Path

from mcp.server.apps import Apps, client_supports_apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from risk_data import lookup, summarize

RESOURCE_URI = "ui://risk-card/app.html"
HTML_PATH = Path(__file__).parent / "risk_card.html"

apps = Apps()


@apps.tool(
    resource_uri=RESOURCE_URI,
    description=(
        "Show a risk card for a company: risk level, a short summary, "
        "the top risks, and sources."
    ),
)
def show_risk_card(ctx: Context, company: str) -> str:
    """Return the risk card for `company`.

    One tool, two answers (the graceful-degradation rule from the MCP
    Apps spec — a tool MUST return a meaningful `content` array even
    when UI is available):
    - UI-capable host: the raw risk JSON. The host pushes it into the
      iframe, which parses it and renders the card. The model reads
      the same JSON.
    - Text-only client: a human sentence. That is all such a client
      (and its model) ever sees, so it must stand on its own — never
      a placeholder like "[Rendered UI]".
    """
    card = lookup(company)
    if client_supports_apps(ctx):
        return json.dumps(card)
    return summarize(card)


# The HTML lives in its own file so it is editable without touching
# Python; the server reads it once at startup and serves the string.
# (The SDK's FileResource path would do the same job — this is the
# simpler documented `add_html_resource` shape, which guarantees the
# `text/html;profile=mcp-app` MIME type.)
apps.add_html_resource(RESOURCE_URI, HTML_PATH.read_text(encoding="utf-8"), title="Risk Card")

mcp = MCPServer("risk-card", extensions=[apps])


def main() -> None:
    parser = argparse.ArgumentParser(description="Risk Card MCP App server.")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="stdio (default) or streamable-http for remote hosts.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=8000, help="HTTP bind port.")
    args = parser.parse_args()
    if args.transport == "streamable-http":
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
