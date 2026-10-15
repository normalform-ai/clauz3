"""Friendly error for using clauz3's MCP integration without the ``mcp`` SDK."""

from __future__ import annotations

_INSTALL_HINT = (
    "the 'mcp' Python SDK is required for clauz3's MCP integration. "
    "Install it with: pip install 'clauz3[mcp]'"
)


class MCPNotInstalledError(ImportError):
    """Raised when an MCP feature is used without the ``mcp`` extra."""

    def __init__(self, original: ImportError | None = None) -> None:
        super().__init__(_INSTALL_HINT)
        self.original = original
