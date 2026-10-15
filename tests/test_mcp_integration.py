"""End-to-end: `clauz3 run` → trusted shim that forwards via MCPClient.

Exercises the TLE-facing half of the MCP integration: a small trusted
``effects.py`` shim uses :class:`clauz3.mcp.MCPClient` to forward into an
upstream MCP server, with the usual ``@deal.has`` markers and a
``@contract`` so the program is provable. The observable side effect — a
line appended to a log file by the upstream server — is what proves the
round-trip happened.
"""

from __future__ import annotations

import importlib
import io
import sys
import textwrap
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

pytest.importorskip("mcp")

from clauz3.approvals.client import (  # noqa: E402
    APPROVAL_SERVICE_ENV,
    MockApprovalServer,
)
from clauz3.approvals.receipts import (  # noqa: E402
    SIGNING_KEY_ENV,
    VERIFY_KEY_ENV,
    generate_keypair,
)
from clauz3.cli import main  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


def test_run_drives_trusted_shim_that_forwards_to_upstream_mcp(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # The trusted layer convention uses the `tools` top-level namespace
    # package. Other tests cache `tools.<other-domain>` in sys.modules with
    # their own __path__; clear those entries so this tmp project's
    # `tools.say.trusted...` resolves against the right directory.
    for cached in [name for name in sys.modules if name.split(".")[0] == "tools"]:
        monkeypatch.delitem(sys.modules, cached, raising=False)
    importlib.invalidate_caches()

    project = tmp_path / "project"
    trusted = project / "tools" / "say" / "trusted"
    trusted.mkdir(parents=True)

    log = tmp_path / "calls.log"

    (trusted / "effects.py").write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path

            import deal
            from clauz3.mcp import MCPClient

            _LOG = Path({str(log)!r})
            _client = MCPClient(command={sys.executable!r}, args=[{str(FIXTURE)!r}])


            @deal.pre(lambda text: len(text) > 0, message="text must be non-empty")
            @deal.has("say", "trusted", "network")
            def say(text: str) -> str:
                reply = _client.call("echo", text=text)
                with _LOG.open("a") as f:
                    f.write(reply + "\\n")
                return reply
            """
        )
    )
    (trusted / "contracts.py").write_text(
        textwrap.dedent(
            """
            from clauz3.spec import ContractSpec, contract, effect

            Say = effect("say")


            @contract
            def says_at_most(n: int) -> ContractSpec:
                return Say.count() <= n
            """
        )
    )

    program = textwrap.dedent(
        """
        import clauz3
        from tools.say.trusted import contracts as say_specs
        from tools.say.trusted.effects import say


        @clauz3.guarantee(say_specs.says_at_most(2))
        def main() -> None:
            say("hello-from-clauz3")
        """
    )

    signing, verify = generate_keypair()
    monkeypatch.setenv(SIGNING_KEY_ENV, signing)
    monkeypatch.setenv(VERIFY_KEY_ENV, verify)
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "receipt-cache"))
    with MockApprovalServer(config={"decision": "approved_once"}) as approval:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, approval.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(program))
        result = main(
            [
                "run",
                "--trusted-root",
                str(trusted),
                "--import-root",
                str(project),
            ]
        )

    assert result == 0
    assert log.exists(), "trusted shim should have logged the upstream reply"
    assert log.read_text().strip() == "hello-from-clauz3"
