"""Sync MCP client wrapper for trusted-layer engineers (TLEs).

A trusted-layer engineer who wants to "restore access" to an existing,
untrusted MCP server writes a tiny shim ``effects.py`` whose functions
forward into that MCP server but carry clauz3's static-contract markers
(``@deal.pre``, ``@clauz3.trusted``) — so an agent's program can still be
statically proved before any side effect runs.

Example shim::

    # tools/files/trusted/effects.py
    import clauz3
    import deal
    from clauz3.mcp import MCPClient

    _files = MCPClient(command="uvx", args=["mcp-server-filesystem", "/srv"])


    @deal.pre(lambda path: len(path) > 0, message="path must be non-empty")
    @deal.has("read", "network")
    @clauz3.trusted
    def read_file(path: str) -> str:
        return _files.call("read_file", path=path)


    @deal.pre(lambda path, content: len(path) > 0)
    @deal.has("write", "network")
    @clauz3.trusted
    def write_file(path: str, content: str) -> None:
        _files.call("write_file", path=path, content=content)

The contracts and ``@clauz3.trusted`` declarations are the trusted layer; the client
just dispatches. Always include the ``"network"`` marker on shim functions
— ``MCPClient`` opens sockets internally for the asyncio event loop, and
``deal``'s runtime checker will raise ``OfflineContractError`` otherwise.

The underlying ``mcp`` Python SDK is included with clauz3 — see :mod:`clauz3.mcp`.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import threading
import weakref
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from typing import Any

from clauz3.mcp._sdk import MCPNotInstalledError

_active_clients: weakref.WeakSet[MCPClient] = weakref.WeakSet()
_atexit_registered = False


class MCPClientError(RuntimeError):
    """An upstream MCP server returned an error or a malformed response."""


class MCPClient:
    """Sync wrapper around an upstream MCP server over stdio.

    Parameters
    ----------
    command:
        Executable that launches the MCP server (for example ``"uvx"`` or
        ``"node"``).
    args:
        Arguments passed to ``command``.
    env:
        Optional environment-variable overrides for the spawned server. When
        ``None``, the server inherits the parent process environment.
    cwd:
        Optional working directory for the spawned server.

    The connection is established lazily on the first call. Use ``close()``
    or a ``with`` block to shut down explicitly; otherwise the connection is
    torn down at interpreter exit.
    """

    def __init__(
        self,
        *,
        command: str,
        args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self._command = command
        self._args = list(args)
        self._env = dict(env) if env is not None else None
        self._cwd = cwd
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: Any | None = None
        self._stack: AsyncExitStack | None = None
        self._closed = False

    def call(self, tool: str, /, **arguments: Any) -> Any:
        """Call ``tool`` on the upstream server and return its result.

        Returns the tool's ``structuredContent`` when present; otherwise the
        text of the first text content block; otherwise ``None``. Raises
        :class:`MCPClientError` if the tool reports an error.
        """
        self._ensure_started()
        assert self._loop is not None
        assert self._session is not None
        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(tool, arguments=dict(arguments)),
            self._loop,
        )
        result = future.result()
        return _unwrap_result(tool, result)

    def list_tools(self) -> list[dict[str, Any]]:
        """List the tools advertised by the upstream server."""
        self._ensure_started()
        assert self._loop is not None
        assert self._session is not None
        future = asyncio.run_coroutine_threadsafe(
            self._session.list_tools(),
            self._loop,
        )
        result = future.result()
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema,
            }
            for tool in result.tools
        ]

    def close(self) -> None:
        """Shut down the upstream server and background event loop."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop
            stack = self._stack
            thread = self._thread
            self._loop = None
            self._stack = None
            self._thread = None
            self._session = None

        if loop is not None and stack is not None:
            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(stack.aclose(), loop).result(timeout=5)
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)

    def __enter__(self) -> MCPClient:
        self._ensure_started()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _ensure_started(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            if self._closed:
                raise MCPClientError("MCPClient is closed")
            try:
                from mcp import ClientSession  # noqa: PLC0415
                from mcp.client.stdio import (  # noqa: PLC0415
                    StdioServerParameters,
                    stdio_client,
                )
            except ImportError as exc:
                raise MCPNotInstalledError(exc) from exc

            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _run_loop() -> None:
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()

            thread = threading.Thread(
                target=_run_loop,
                name=f"clauz3-mcp-client-{self._command}",
                daemon=True,
            )
            thread.start()
            ready.wait()

            params = StdioServerParameters(
                command=self._command,
                args=self._args,
                env=self._env,
                cwd=self._cwd,
            )

            async def _open() -> tuple[Any, AsyncExitStack]:
                stack = AsyncExitStack()
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                return session, stack

            future = asyncio.run_coroutine_threadsafe(_open(), loop)
            try:
                session, stack = future.result(timeout=30)
            except Exception:
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=5)
                raise

            self._loop = loop
            self._thread = thread
            self._session = session
            self._stack = stack
            _active_clients.add(self)
            _ensure_atexit()


def _unwrap_result(tool: str, result: Any) -> Any:
    if getattr(result, "isError", False):
        message = _first_text(result) or f"MCP tool {tool!r} returned an error"
        raise MCPClientError(message)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        if isinstance(structured, str):
            try:
                return json.loads(structured)
            except json.JSONDecodeError:
                return structured
        # FastMCP wraps scalar returns in {"result": value}; unwrap so a shim
        # `-> str` function sees a plain string. Multi-key structured payloads
        # pass through.
        if isinstance(structured, dict) and list(structured.keys()) == ["result"]:
            return structured["result"]
        return structured
    return _first_text(result)


def _first_text(result: Any) -> str | None:
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    return None


def _ensure_atexit() -> None:
    global _atexit_registered
    if _atexit_registered:
        return
    atexit.register(_close_all)
    _atexit_registered = True


def _close_all() -> None:
    for client in list(_active_clients):
        with contextlib.suppress(Exception):
            client.close()
