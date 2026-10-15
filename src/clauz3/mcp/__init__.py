"""MCP client integration for clauz3.

:class:`MCPClient` is a small sync wrapper around an upstream MCP server.
A trusted-layer engineer (TLE) imports it from their ``effects.py`` to
forward trusted calls into an existing, untrusted MCP server, after
attaching the ``@clauz3.trusted`` declarations and per-domain ``@contract``\\ s
that clauz3 will prove against. See ``docs/how-to/mcp.md``.

The ``mcp`` Python SDK is included in the base installation for ``clauz3 start``.
The ``[mcp]`` extra is retained for compatibility. ``start`` owns a restricted
session bridge; the standalone server lives in the separate ``clauz3-mcp``
package and is added to a harness as ``uvx clauz3-mcp``.
"""

from clauz3.mcp._sdk import MCPNotInstalledError
from clauz3.mcp.client import MCPClient, MCPClientError

__all__ = ["MCPClient", "MCPClientError", "MCPNotInstalledError"]
