"""The run client reports every service failure as ``ApprovalServiceError``.

``clauz3 run`` maps that error to exit 2. Exit 1 with ``runtime error:`` is
reserved for an approved program that raised, so a failure to reach a decision
must never surface that way.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from clauz3.approvals import client as approval_client
from clauz3.approvals.client import ApprovalServiceError, submit_approval_request
from clauz3.cli import main


def _fail_with(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def fake_urlopen(request: object, timeout: float = 0.0) -> object:
        raise error

    monkeypatch.setattr(approval_client, "urlopen", fake_urlopen)


def test_client_reports_a_silent_service_as_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A listening socket that never answers: the connection and the request
    # succeed, and the read times out outside urllib's own error wrapping.
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    with socket.create_server(("127.0.0.1", 0)) as silent:
        port = silent.getsockname()[1]
        with pytest.raises(ApprovalServiceError, match="did not answer within 0.2 s"):
            submit_approval_request(
                f"http://127.0.0.1:{port}", {"request_id": "clr_x"}, timeout=0.2
            )


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (TimeoutError("timed out"), "did not answer within 300 s"),
        (ConnectionResetError(104, "Connection reset by peer"), "is unavailable"),
    ],
)
def test_client_wraps_errors_raised_while_reading_the_response(
    monkeypatch: pytest.MonkeyPatch, error: Exception, message: str
) -> None:
    _fail_with(monkeypatch, error)
    with pytest.raises(ApprovalServiceError, match=message):
        submit_approval_request("http://127.0.0.1:1", {"request_id": "clr_x"})


def test_run_exits_2_when_the_service_does_not_answer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _fail_with(monkeypatch, TimeoutError("timed out"))
    monkeypatch.setenv("CLAUZ3_APPROVAL_SERVICE", "http://127.0.0.1:1")
    program = tmp_path / "program.py"
    program.write_text("def main() -> None:\n    pass\n")

    result = main(["run", "--approval-timeout", "1", str(program)])

    err = capsys.readouterr().err
    assert result == 2, err
    assert "error: approval service did not answer within 1 s" in err
    assert "runtime error" not in err
