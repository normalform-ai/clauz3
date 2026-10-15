"""Issue #74: the run client must send the bearer token to an auth-enabled
approval service. Auth-on-by-default is useless if ``submit_approval_request``
never presents the token (the prior tests only exercised the server side with a
hand-crafted header).
"""

from __future__ import annotations

import json

import pytest

from clauz3.approvals import client as appr
from clauz3.approvals.client import APPROVAL_TOKEN_ENV, submit_approval_request


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _capture_request(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    captured: list[object] = []

    def fake_urlopen(request: object, timeout: float = 0.0) -> _FakeResponse:
        captured.append(request)
        return _FakeResponse({"decision": "approved_once", "request_id": "clr_x"})

    monkeypatch.setattr(appr, "urlopen", fake_urlopen)
    return captured


def test_client_sends_bearer_token_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(APPROVAL_TOKEN_ENV, "s3kret")
    captured = _capture_request(monkeypatch)

    submit_approval_request("http://127.0.0.1:8765", {"program_sha256": "abc"})

    request = captured[0]
    # urllib normalizes header names to title-case.
    assert request.get_header("Authorization") == "Bearer s3kret"  # type: ignore[attr-defined]


def test_client_omits_authorization_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(APPROVAL_TOKEN_ENV, raising=False)
    captured = _capture_request(monkeypatch)

    submit_approval_request("http://127.0.0.1:8765", {"program_sha256": "abc"})

    request = captured[0]
    assert request.get_header("Authorization") is None  # type: ignore[attr-defined]
