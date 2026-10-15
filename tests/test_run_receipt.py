"""Issue #69: end-to-end fail-closed receipt verification in ``clauz3 run``.

The runner refuses to run unless it can verify the receipt with a provisioned
Ed25519 public key. A correctly signed receipt is accepted; a forged/unsigned
one and an absent verifying key are both refused; and a nonce is single-use.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture
from _pytest.monkeypatch import MonkeyPatch

from clauz3.approvals.client import APPROVAL_SERVICE_ENV, MockApprovalServer
from clauz3.approvals.receipts import (
    SIGNING_KEY_ENV,
    VERIFY_KEY_ENV,
    ReceiptError,
    generate_keypair,
)
from clauz3.cli import main
from clauz3.runner import _consume_nonce

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"

PASSING_SOURCE = """
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["bob@example.com"]))
@clauz3.guarantee(emails.unique_recipients())
def main() -> None:
    send_email("bob@example.com", "Report is ready")
"""


def _provision(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    signing, verify = generate_keypair()
    monkeypatch.setenv(SIGNING_KEY_ENV, signing)
    monkeypatch.setenv(VERIFY_KEY_ENV, verify)
    # Isolate the single-use nonce store so runs don't pollute the real cache.
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "cache"))


def _run(monkeypatch: MonkeyPatch, *, config: dict[str, object]) -> int:
    with MockApprovalServer(config=config) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(PASSING_SOURCE))
        return main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )


def test_signed_receipt_is_accepted(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _provision(monkeypatch, tmp_path)
    # No explicit receipt in config: the mock service signs with the private key.
    assert _run(monkeypatch, config={"decision": "approved_once"}) == 0


def test_forged_unsigned_receipt_is_rejected(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    _provision(monkeypatch, tmp_path)
    result = _run(
        monkeypatch,
        config={"decision": "approved_once", "receipt": "forged-anything"},
    )
    assert result == 2
    assert "receipt" in capsys.readouterr().err.lower()


def test_no_verifying_key_refuses_to_run(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    # Fail-closed: without a verifying key the runner must refuse, not fall back.
    monkeypatch.delenv(VERIFY_KEY_ENV, raising=False)
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "cache"))
    result = _run(monkeypatch, config={"decision": "approved_once"})
    assert result == 2
    assert "verifying key" in capsys.readouterr().err.lower()


def test_nonce_is_single_use(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    # The runner records consumed nonces; replaying one is refused.
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "cache"))
    _consume_nonce("nonce-xyz")
    with pytest.raises(ReceiptError):
        _consume_nonce("nonce-xyz")
