"""The ``mcp`` SDK is an optional extra; these tests run without it installed."""

from __future__ import annotations

import sys

import pytest
from _pytest.monkeypatch import MonkeyPatch

from clauz3.mcp import MCPClient, MCPNotInstalledError


def test_not_installed_hint_names_the_extra_not_the_removed_subcommand() -> None:
    message = str(MCPNotInstalledError())
    assert "clauz3[mcp]" in message
    # The in-process ``clauz3 mcp`` server moved to the separate clauz3-mcp
    # package; the hint must not send users to a subcommand that no longer exists.
    assert "clauz3 mcp" not in message


def test_client_raises_not_installed_when_sdk_is_missing(
    monkeypatch: MonkeyPatch,
) -> None:
    for name in [m for m in sys.modules if m == "mcp" or m.startswith("mcp.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "mcp", None)

    client = MCPClient(command=sys.executable, args=["-c", "pass"])
    with pytest.raises(MCPNotInstalledError):
        client.call("echo", text="x")
