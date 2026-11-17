"""Tests for the sync MCPClient wrapper.

Spawns the FastMCP echo server in tests/fixtures/mcp_echo_server.py over
stdio and exercises the client end-to-end.
"""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Iterator
from contextlib import suppress
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


def running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# Each server records its PID, then never completes ``initialize`` and ignores
# its closed stdin, so only the client's own shutdown can stop it.
REJECTS_INITIALIZE = """
import json, os, sys, time
open(sys.argv[1], "w").write(str(os.getpid()))
request = json.loads(sys.stdin.readline())
error = {"code": -32600, "message": "nope"}
print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": error}), flush=True)
time.sleep(120)
"""
NEVER_ANSWERS = """
import os, sys, time
open(sys.argv[1], "w").write(str(os.getpid()))
time.sleep(120)
"""


@pytest.mark.parametrize(
    ("server", "error", "deadline"),
    [(REJECTS_INITIALIZE, "nope", None), (NEVER_ANSWERS, "^$", 1.0)],
    ids=["initialize-rejected", "initialize-timeout"],
)
def test_mcp_client_stops_a_server_that_fails_to_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server: str,
    error: str,
    deadline: float | None,
) -> None:
    if deadline is not None:
        monkeypatch.setattr("clauz3.mcp.client._START_TIMEOUT", deadline)
    record = tmp_path / "pid"
    client = MCPClient(command=sys.executable, args=["-c", server, str(record)])
    try:
        with pytest.raises(Exception, match=error) as failure:
            client.call("echo", text="hello")
        assert isinstance(failure.value, TimeoutError) == (deadline is not None)
        pid = int(record.read_text())
        try:
            # The failed start has already shut the server down.
            assert not running(pid)
        finally:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        # A failed start is not a closed client: the next call tries again.
        record.unlink()
        with pytest.raises(Exception, match=error):
            client.call("echo", text="hello")
        retry = int(record.read_text())
        try:
            assert retry != pid and not running(retry)
        finally:
            with suppress(ProcessLookupError):
                os.kill(retry, signal.SIGKILL)
    finally:
        client.close()


def test_mcp_client_call_after_close_fails() -> None:
    client = MCPClient(command=sys.executable, args=[str(FIXTURE)])
    client.call("echo", text="warmup")
    client.close()
    with pytest.raises(MCPClientError):
        client.call("echo", text="too late")
