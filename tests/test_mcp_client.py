"""Tests for the sync MCPClient wrapper.

Spawns the FastMCP echo server in tests/fixtures/mcp_echo_server.py over
stdio and exercises the client end-to-end.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from clauz3.mcp import MCPClient, MCPClientError  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


@pytest.fixture
def echo_client() -> Iterator[MCPClient]:
    client = MCPClient(command=sys.executable, args=[str(FIXTURE)])
    try:
        yield client
    finally:
        client.close()


def test_mcp_client_calls_text_tool(echo_client: MCPClient) -> None:
    result = echo_client.call("echo", text="hello")
    assert result == "hello"


def test_mcp_client_returns_structured_content(echo_client: MCPClient) -> None:
    # FastMCP wraps primitive returns in structuredContent={"result": 5};
    # the client unwraps so a shim function with `-> int` gets a bare int.
    assert echo_client.call("add", a=2, b=3) == 5


def test_mcp_client_raises_on_tool_error(echo_client: MCPClient) -> None:
    with pytest.raises(MCPClientError) as excinfo:
        echo_client.call("boom", reason="nope")
    assert "nope" in str(excinfo.value)


def test_mcp_client_list_tools(echo_client: MCPClient) -> None:
    names = {tool["name"] for tool in echo_client.list_tools()}
    assert {"echo", "add", "boom"}.issubset(names)


def test_mcp_client_context_manager() -> None:
    with MCPClient(command=sys.executable, args=[str(FIXTURE)]) as client:
        assert client.call("echo", text="ctx") == "ctx"


def test_mcp_client_call_after_close_fails() -> None:
    client = MCPClient(command=sys.executable, args=[str(FIXTURE)])
    client.call("echo", text="warmup")
    client.close()
    with pytest.raises(MCPClientError):
        client.call("echo", text="too late")
