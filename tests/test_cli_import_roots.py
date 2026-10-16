"""CLI proof and execution use the same ordered trusted-layer selection."""

import hashlib
import importlib
import sys
import time
from collections.abc import Mapping, Sequence
from functools import wraps
from pathlib import Path
from typing import cast
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3.approvals.client import ApprovalResponse
from clauz3.approvals.decisions import Decision
from clauz3.approvals.receipts import (
    VERIFY_KEY_ENV,
    consent_digest,
    generate_keypair,
    sign_receipt,
)
from clauz3.cli import main
from clauz3.libraries.layout import expand_import_roots


def _project(tmp_path: Path, *, under_entry: bool = False) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    cases = project / "cases"
    cases.mkdir(parents=True)
    (project / "lib").mkdir()
    layer = (cases if under_entry else project) / f"cli_layer_{uuid4().hex}"
    layer.mkdir()
    (layer / "__init__.py").write_text("")
    (layer / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (layer / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def once() -> ContractSpec:\n"
        "    return effect('record').count() <= 1\n"
    )
    entry = cases / "entry.py"
    entry.write_text(
        "import clauz3\n"
        f"from {layer.name}.effects import record\n"
        f"from {layer.name}.contracts import once\n"
        "@clauz3.guarantee(once())\n"
        "def main() -> None:\n"
        "    record('one')\n"
    )
    return project, layer, entry


@pytest.mark.parametrize("roots_option", ["repeated", "plural", "default"])
def test_cli_prove_then_run_retains_native_provider_and_ordered_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    roots_option: str,
) -> None:
    project, layer, entry = _project(tmp_path)
    monkeypatch.chdir(project)
    if roots_option == "repeated":
        roots = [project / "lib", project]
        flags = [part for root in roots for part in ("--import-root", str(root))]
    elif roots_option == "plural":
        roots = [project / "lib", project]
        flags = ["--import-roots", *(str(root) for root in roots)]
    else:
        # Only the implicit default gains each directory's .clauz3 root.
        roots = expand_import_roots([project, entry.parent])
        flags = []
    arguments = [str(entry), "--trusted-root", str(layer), *flags]
    assert main(["prove", *arguments]) == 0
    assert "main: proved!" in capsys.readouterr().out

    effects = importlib.import_module(layer.name + ".effects")
    original = effects.record
    observed: list[str] = []

    @wraps(original)
    def patched(value: str) -> None:
        observed.append(value)
        original(value)

    monkeypatch.setattr(effects, "record", patched)
    signing, verify = generate_keypair()
    monkeypatch.setenv(VERIFY_KEY_ENV, verify)
    monkeypatch.setenv("CLAUZ3_APPROVAL_SERVICE", "http://approval.invalid")
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "receipts"))
    requests: list[Mapping[str, object]] = []

    def approve(
        url: str, request: Mapping[str, object], *, timeout: float | None = None
    ) -> ApprovalResponse:
        requests.append(request)
        digest = hashlib.sha256(entry.read_bytes()).hexdigest()
        return ApprovalResponse(
            decision=Decision.APPROVED_ONCE,
            receipt=sign_receipt(
                signing_key_b64=signing,
                request_id=str(request["request_id"]),
                program_sha256=digest,
                decision="approved_once",
                expires_at=time.time() + 60,
                nonce=uuid4().hex,
                consent_sha256=consent_digest(
                    program_sha256=digest,
                    guarantees=cast(Sequence[str], request["guarantees"]),
                    coverage=request.get("coverage"),
                ),
            ),
        )

    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    assert main(["run", *arguments]) == 0
    assert "executed: main" in capsys.readouterr().out
    assert len(requests) == 1
    assert observed == ["one"]
    assert effects.EVENTS == ["one"]
    assert sys.modules[layer.name + ".effects"] is effects
    assert effects.record is patched
    assert requests[0]["import_roots"] == [str(root) for root in roots]


@pytest.mark.parametrize("under_entry", [False, True], ids=["cwd", "entry-parent"])
def test_explicit_roots_do_not_gain_implicit_trusted_locations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    under_entry: bool,
) -> None:
    project, layer, entry = _project(tmp_path, under_entry=under_entry)
    monkeypatch.chdir(project)
    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    executor = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setenv("CLAUZ3_APPROVAL_SERVICE", "http://approval.invalid")
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", executor)
    arguments = [
        str(entry),
        "--trusted-root",
        str(layer),
        "--import-root",
        str(project / "lib"),
    ]
    for command in ("prove", "run"):
        assert main([command, *arguments]) == 2
        expected = (
            "trusted source is not below an import root"
            if command == "prove"
            else "is not in the allowlist"
        )
        assert expected in capsys.readouterr().err
    approve.assert_not_called()
    executor.assert_not_called()
    assert layer.name + ".effects" not in sys.modules
