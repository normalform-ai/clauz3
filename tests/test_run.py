import io
import sys
from pathlib import Path

from _pytest.capture import CaptureFixture
from _pytest.monkeypatch import MonkeyPatch

from clauz3.approval import APPROVAL_SERVICE_ENV, MockApprovalServer
from clauz3.cli import main

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

FAILING_SOURCE = """
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    send_email("ann@example.com", "Report is ready")
"""


def test_clauz3_run_from_stdin_approved(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    with MockApprovalServer(
        config={"decision": "approved_once", "receipt": "receipt-test"},
    ) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(PASSING_SOURCE))

        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    assert result == 0
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["kind"] == "clauz3.run"
    assert request["guarantees"] == [
        "emails.only(['bob@example.com'])",
        "emails.unique_recipients()",
    ]
    assert request["coverage"] == [
        {
            "domain": "email",
            "status": "covered",
            "missing_required": [],
            "missing_recommended": [],
        }
    ]
    assert "main: proved!" in capsys.readouterr().out


def test_clauz3_run_discovers_trusted_roots_from_repo(
    monkeypatch: MonkeyPatch,
) -> None:
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(PASSING_SOURCE))
        monkeypatch.chdir(EMAIL_ROOT)

        result = main(["run"])

    assert result == 0
    assert len(server.requests) == 1


def test_clauz3_run_requires_approval_service(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.delenv(APPROVAL_SERVICE_ENV, raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSING_SOURCE))

    result = main(
        [
            "run",
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
        ]
    )

    assert result == 2
    assert "no approval service configured" in capsys.readouterr().err


def test_clauz3_run_rejected_by_approval_service(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    with MockApprovalServer(
        config={"decision": "request_more", "feedback": "prove nobody twice"},
    ) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(PASSING_SOURCE))

        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    assert result == 3
    err = capsys.readouterr().err
    assert "approval: request_more" in err
    assert "prove nobody twice" in err


def test_clauz3_run_does_not_submit_failed_proof(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    with MockApprovalServer(config={"decision": "approved_once"}) as server:
        monkeypatch.setenv(APPROVAL_SERVICE_ENV, server.url)
        monkeypatch.setattr(sys, "stdin", io.StringIO(FAILING_SOURCE))

        result = main(
            [
                "run",
                "--trusted-root",
                str(TRUSTED_ROOT),
                "--import-root",
                str(EMAIL_ROOT),
            ]
        )

    assert result == 1
    assert server.requests == []
    assert "main: failed guarantee" in capsys.readouterr().out


def test_clauz3_run_rejects_direct_side_effect_import(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO("import subprocess\n\ndef main() -> None:\n    pass\n"),
    )

    result = main(
        [
            "run",
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
        ]
    )

    assert result == 2
    assert "import of subprocess is not allowed" in capsys.readouterr().err


def test_clauz3_run_rejects_builtins_import(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO("import builtins\n\ndef main() -> None:\n    pass\n"),
    )

    result = main(
        [
            "run",
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
        ]
    )

    assert result == 2
    assert "import of builtins is not allowed" in capsys.readouterr().err


def test_clauz3_run_rejects_reflective_builtin_access(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO("def main() -> None:\n    getattr(object, '__class__')\n"),
    )

    result = main(
        [
            "run",
            "--trusted-root",
            str(TRUSTED_ROOT),
            "--import-root",
            str(EMAIL_ROOT),
        ]
    )

    assert result == 2
    assert "getattr is not allowed in run" in capsys.readouterr().err
