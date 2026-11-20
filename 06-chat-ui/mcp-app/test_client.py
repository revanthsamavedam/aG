"""Verify the Risk Card MCP App end to end, over a real MCP client.

Checks (stdio by default, `--http <url>` for the HTTP transport):
1. `tools/list` shows `show_risk_card` with `_meta.ui.resourceUri`
   set to the `ui://` resource.
2. `resources/read` on that URI returns the HTML with MIME type
   `text/html;profile=mcp-app`.
3. `tools/call` for Anthropic returns the risk JSON (High, 3 risks);
   OpenAI returns Medium / 2 risks; an unknown company returns the
   generic Medium / 0 risks.
4. A client that does NOT advertise Apps support gets the human
   sentence instead of the JSON (graceful degradation).

Exit code 0 = all checks passed. This verifies the server side only:
whether a host actually renders the HTML in an iframe is the host's
job and is NOT tested here.

Usage:
    python test_client.py                  # stdio, spawns server.py
    python test_client.py --http http://localhost:8000/mcp
"""

import argparse
import json
import sys
from pathlib import Path

import anyio
from mcp import Client
from mcp.client import advertise
from mcp.client.stdio import StdioServerParameters
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID
from mcp.types import TextContent

HERE = Path(__file__).parent
APPS_SUPPORT = advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})
RESOURCE_URI = "ui://risk-card/app.html"


def check(cond: bool, label: str) -> None:
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        raise SystemExit(1)


def tool_text(result) -> str:
    texts = [b.text for b in result.content if isinstance(b, TextContent)]
    return "\n".join(texts)


async def verify(client: Client, *, apps: bool) -> None:
    tools = await client.list_tools()
    names = [t.name for t in tools.tools]
    check("show_risk_card" in names, f"tools/list includes show_risk_card (got {names})")

    tool = next(t for t in tools.tools if t.name == "show_risk_card")
    meta = (tool.meta or {}).get("ui", {}) if hasattr(tool, "meta") else {}
    # The SDK exposes tool metadata as `meta` on the tool object.
    uri = meta.get("resourceUri")
    check(uri == RESOURCE_URI, f"_meta.ui.resourceUri == {RESOURCE_URI} (got {uri!r})")
    print(f"      inputSchema: {json.dumps(tool.input_schema)}")

    resource = await client.read_resource(RESOURCE_URI)
    contents = resource.contents
    check(len(contents) == 1, "resources/read returns exactly one content block")
    block = contents[0]
    mime = getattr(block, "mime_type", None) or getattr(block, "mimeType", None)
    check(
        mime == APP_MIME_TYPE,
        f"resource MIME type == {APP_MIME_TYPE} (got {mime!r})",
    )
    html = getattr(block, "text", "") or ""
    check("Risk Card" in html and "handleToolResult" in html,
          "resource body is the RiskCard HTML")
    print(f"      resource is {len(html)} chars of HTML")

    result = await client.call_tool("show_risk_card", {"company": "Anthropic"})
    text = tool_text(result)
    if apps:
        card = json.loads(text)
        check(card["company"] == "Anthropic", "Anthropic call returns Anthropic card")
        check(card["risk_level"] == "High", "Anthropic risk_level == High")
        check(len(card["risks"]) == 3, "Anthropic has 3 risks")
        check(len(card["sources"]) == 2, "Anthropic has 2 sources")
        print(f"      Anthropic JSON OK: High, {[r['title'] for r in card['risks']]}")
    else:
        check("Anthropic" in text and "High" in text,
              "text-only client gets a sentence, not JSON")
        try:
            json.loads(text)
            check(False, "text-only result must not be bare JSON")
        except json.JSONDecodeError:
            check(True, "text-only result is prose, not JSON")
        print(f"      fallback sentence: {text[:120]}...")

    result = await client.call_tool("show_risk_card", {"company": "OpenAI"})
    text = tool_text(result)
    if apps:
        card = json.loads(text)
        check(card["risk_level"] == "Medium" and len(card["risks"]) == 2,
              "OpenAI returns Medium / 2 risks")
    else:
        check("OpenAI" in text and "Medium" in text, "OpenAI sentence fallback OK")

    result = await client.call_tool("show_risk_card", {"company": "Acme Corp"})
    text = tool_text(result)
    if apps:
        card = json.loads(text)
        check(card["risk_level"] == "Medium" and card["risks"] == [],
              "unknown company returns generic Medium / 0 risks")


async def main_async(args) -> None:
    if args.http:
        print(f"== HTTP mode: {args.http} ==")
        async with Client(args.http, extensions=[APPS_SUPPORT]) as client:
            print("-- with Apps advertised --")
            await verify(client, apps=True)
        async with Client(args.http) as client:
            print("-- without Apps advertised --")
            await verify(client, apps=False)
    else:
        print("== stdio mode: spawning server.py ==")
        params = StdioServerParameters(
            command=sys.executable, args=[str(HERE / "server.py")], cwd=str(HERE)
        )
        async with Client(params, extensions=[APPS_SUPPORT]) as client:
            print("-- with Apps advertised --")
            await verify(client, apps=True)
        async with Client(params) as client:
            print("-- without Apps advertised --")
            await verify(client, apps=False)
    print("ALL CHECKS PASSED")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the Risk Card MCP App.")
    parser.add_argument("--http", metavar="URL",
                        help="Test a running HTTP server instead of stdio, "
                             "e.g. http://localhost:8000/mcp")
    args = parser.parse_args()
    anyio.run(main_async, args)


if __name__ == "__main__":
    main()
