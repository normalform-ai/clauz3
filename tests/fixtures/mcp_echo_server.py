"""Tiny FastMCP server used by tests.test_mcp_client.

Exposes:
- echo(text)   -> str          (returns the text it was given)
- add(a, b)    -> int          (returns a + b as a structured int)
- boom(reason) -> ToolError    (deliberately raises to test error propagation)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

server = FastMCP("clauz3-test-echo")


@server.tool()
def echo(text: str) -> str:
    return text


@server.tool()
def add(a: int, b: int) -> int:
    return a + b


@server.tool()
def boom(reason: str) -> str:
    raise RuntimeError(reason)


if __name__ == "__main__":
    server.run()
