"""Verify the Case Store MCP server end to end, over a real MCP client.

Checks (stdio by default, `--http <url>` for the HTTP transport):
1. `tools/list` shows get_case, list_cases, set_case_status — and
   get_case / list_cases carry the readOnlyHint annotation.
2. `list_cases` returns the 3 seeded cases.
3. `get_case` for CASE-101 returns status "open".
4. `set_case_status(CASE-101, "approved")` returns the case with
   status "approved".
5. `get_case` for CASE-101 AGAIN returns "approved" — the state
   change persisted across calls inside the server process.
6. `set_case_status` with a bad status returns a clean error string
   (no crash), and the case is unchanged afterwards.
7. `set_case_status` with an unknown case_id returns a clean error
   string (no crash).

Exit code 0 = all checks passed.

Usage:
    python test_client.py                  # stdio, spawns server.py
    python test_client.py --http http://localhost:8899/mcp
"""

import argparse
import json
import sys
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import TextContent

HERE = Path(__file__).parent


def check(cond: bool, label: str) -> None:
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        raise SystemExit(1)


def tool_text(result) -> str:
    texts = [b.text for b in result.content if isinstance(b, TextContent)]
    return "\n".join(texts)


async def verify(client: Client) -> None:
    tools = await client.list_tools()
    names = [t.name for t in tools.tools]
    check(
        names == ["get_case", "list_cases", "set_case_status"],
        f"tools/list shows all three tools (got {names})",
    )
    by_name = {t.name: t for t in tools.tools}
    for read_only_name in ("get_case", "list_cases"):
        ann = by_name[read_only_name].annotations
        check(
            ann is not None and ann.read_only_hint is True,
            f"{read_only_name} carries readOnlyHint=true",
        )
    check(
        by_name["set_case_status"].annotations is None
        or by_name["set_case_status"].annotations.read_only_hint is not True,
        "set_case_status does NOT carry readOnlyHint (it writes state)",
    )

    result = await client.call_tool("list_cases", {})
    cases = json.loads(tool_text(result))
    check(len(cases) == 3, f"list_cases returns 3 cases (got {len(cases)})")
    check(
        [c["id"] for c in cases] == ["CASE-101", "CASE-102", "CASE-103"],
        "list_cases returns CASE-101, CASE-102, CASE-103 in order",
    )

    result = await client.call_tool("get_case", {"case_id": "CASE-101"})
    case = json.loads(tool_text(result))
    check(case["status"] == "open", 'get_case CASE-101 starts "open"')

    result = await client.call_tool(
        "set_case_status", {"case_id": "CASE-101", "status": "approved"}
    )
    case = json.loads(tool_text(result))
    check(
        case["status"] == "approved" and case["id"] == "CASE-101",
        'set_case_status returns the updated case, status "approved"',
    )

    # The persistence proof: a fresh call, same server process.
    result = await client.call_tool("get_case", {"case_id": "CASE-101"})
    case = json.loads(tool_text(result))
    check(
        case["status"] == "approved",
        'get_case CASE-101 again still "approved" — state persisted',
    )

    result = await client.call_tool(
        "set_case_status", {"case_id": "CASE-101", "status": "bogus"}
    )
    text = tool_text(result)
    check(
        "invalid status" in text and "bogus" in text,
        f'bad status returns a clean error, not a crash ("{text[:80]}")',
    )
    result = await client.call_tool("get_case", {"case_id": "CASE-101"})
    check(
        json.loads(tool_text(result))["status"] == "approved",
        'failed set leaves the case unchanged (still "approved")',
    )

    result = await client.call_tool(
        "set_case_status", {"case_id": "CASE-999", "status": "approved"}
    )
    text = tool_text(result)
    check(
        "unknown case_id" in text and "CASE-999" in text,
        f'unknown case_id returns a clean error ("{text[:80]}")',
    )

    # A second, different mutation, to show the store keeps working.
    result = await client.call_tool(
        "set_case_status", {"case_id": "CASE-102", "status": "denied"}
    )
    check(
        json.loads(tool_text(result))["status"] == "denied",
        'set_case_status CASE-102 -> "denied" works too',
    )
    result = await client.call_tool("list_cases", {})
    statuses = {c["id"]: c["status"] for c in json.loads(tool_text(result))}
    check(
        statuses == {"CASE-101": "approved", "CASE-102": "denied", "CASE-103": "open"},
        f"final list_cases shows both mutations: {statuses}",
    )


async def main_async(args) -> None:
    if args.http:
        print(f"== HTTP mode: {args.http} ==")
        async with Client(args.http) as client:
            await verify(client)
    else:
        print("== stdio mode: spawning server.py ==")
        params = StdioServerParameters(
            command=sys.executable, args=[str(HERE / "server.py")], cwd=str(HERE)
        )
        async with Client(params) as client:
            await verify(client)
    print("ALL CHECKS PASSED")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the Case Store MCP server.")
    parser.add_argument("--http", metavar="URL",
                        help="Test a running HTTP server instead of stdio, "
                             "e.g. http://localhost:8899/mcp")
    args = parser.parse_args()
    anyio.run(main_async, args)


if __name__ == "__main__":
    main()
