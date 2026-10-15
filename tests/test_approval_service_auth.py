"""Issue #74: the approval service must authenticate state-changing endpoints,
defeat DNS-rebinding via host allow-listing, and bound the blocking wait.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from clauz3.approvals.service import create_approval_app


def _wait_for_record(
    client: TestClient, request_id: str, token: str | None = None
) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for _ in range(200):
        response = client.get(f"/requests/{request_id}", headers=headers)
        if response.status_code == 200 and "request_id" in response.json():
            return
        time.sleep(0.01)
    raise AssertionError(f"record {request_id} never appeared")


# ── bearer-token auth on state-changing endpoints ─────────────────────────────


def test_decision_rejected_without_token() -> None:
    app = create_approval_app(auth_token="sekret")
    client = TestClient(app)
    # Seed a pending record (create also requires the token).
    threading.Thread(
        target=lambda: client.post(
            "/requests",
            json={"program_sha256": "abc123"},
            headers={"Authorization": "Bearer sekret"},
        ),
        daemon=True,
    ).start()
    _wait_for_record(client, "clr_abc123", token="sekret")

    unauth = client.post(
        "/requests/clr_abc123/decision", json={"decision": "approved_once"}
    )
    assert unauth.status_code == 401

    wrong = client.post(
        "/requests/clr_abc123/decision",
        json={"decision": "approved_once"},
        headers={"Authorization": "Bearer nope"},
    )
    assert wrong.status_code == 401

    ok = client.post(
        "/requests/clr_abc123/decision",
        json={"decision": "approved_once"},
        headers={"Authorization": "Bearer sekret"},
    )
    assert ok.status_code == 200


def test_create_rejected_without_token() -> None:
    app = create_approval_app(auth_token="sekret")
    client = TestClient(app)
    resp = client.post("/requests", json={"program_sha256": "abc123"})
    assert resp.status_code == 401


def test_no_token_keeps_open_access() -> None:
    app = create_approval_app()
    client = TestClient(app)
    threading.Thread(
        target=lambda: client.post("/requests", json={"program_sha256": "abc123"}),
        daemon=True,
    ).start()
    _wait_for_record(client, "clr_abc123")
    resp = client.post(
        "/requests/clr_abc123/decision", json={"decision": "approved_once"}
    )
    assert resp.status_code == 200


# ── host allow-listing (DNS-rebinding / CSRF) ─────────────────────────────────


def test_foreign_host_header_rejected() -> None:
    app = create_approval_app(allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    client = TestClient(app)
    resp = client.get("/health", headers={"host": "evil.example.com"})
    assert resp.status_code == 400
    assert client.get("/health").status_code == 200


# ── cross-origin POST rejected (CSRF defense-in-depth) ────────────────────────


def test_cross_origin_post_rejected() -> None:
    app = create_approval_app(allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    client = TestClient(app)
    resp = client.post(
        "/requests",
        json={"program_sha256": "abc123"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403


def test_same_origin_post_allowed() -> None:
    # A same-origin (allow-listed hostname) Origin passes the origin check; the
    # request then times out waiting for a human (proving it got past the guard).
    app = create_approval_app(
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
        max_wait_seconds=0.2,
    )
    client = TestClient(app)
    resp = client.post(
        "/requests",
        json={"program_sha256": "abc123"},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert resp.status_code == 503  # past the origin check, into the bounded wait


# ── server-side max wait (DoS) ────────────────────────────────────────────────


def test_create_request_times_out_and_frees_worker() -> None:
    app = create_approval_app(max_wait_seconds=0.2)
    client = TestClient(app)
    start = time.monotonic()
    resp = client.post("/requests", json={"program_sha256": "abc123"})
    elapsed = time.monotonic() - start
    assert resp.status_code == 503
    assert elapsed < 5  # the worker was freed, not pinned indefinitely


# ── separate approver credential; no token in any page (issue #257) ───────────


def _seed(client: TestClient, request_id: str, token: str) -> None:
    sha = request_id.removeprefix("clr_")
    threading.Thread(
        target=lambda: client.post(
            "/requests",
            json={"program_sha256": sha},
            headers={"Authorization": f"Bearer {token}"},
        ),
        daemon=True,
    ).start()
    _wait_for_record(client, request_id)


def test_ui_is_closed_without_approver_session_and_never_leaks_tokens() -> None:
    app = create_approval_app(auth_token="submit-tok", decision_token="decide-tok")
    client = TestClient(app)
    _seed(client, "clr_leak1", "submit-tok")

    for path in ("/", "/ui", "/ui/rows", "/ui/requests/clr_leak1"):
        resp = client.get(path)
        assert resp.status_code == 401, path
        assert "submit-tok" not in resp.text
        assert "decide-tok" not in resp.text

    # Even a signed-in approver's page carries no credential.
    client.get("/ui/login", params={"key": "decide-tok"})
    detail = client.get("/ui/requests/clr_leak1")
    assert detail.status_code == 200
    assert "Awaiting approval" in detail.text
    assert "submit-tok" not in detail.text
    assert "decide-tok" not in detail.text


def test_submission_token_cannot_record_a_decision() -> None:
    app = create_approval_app(auth_token="submit-tok", decision_token="decide-tok")
    client = TestClient(app)
    _seed(client, "clr_self1", "submit-tok")

    selfie = client.post(
        "/requests/clr_self1/decision",
        json={"decision": "approved_once"},
        headers={"Authorization": "Bearer submit-tok"},
    )
    assert selfie.status_code == 401
    assert "approval" not in client.get("/requests/clr_self1").json()

    ok = client.post(
        "/requests/clr_self1/decision",
        json={"decision": "approved_once"},
        headers={"Authorization": "Bearer decide-tok"},
    )
    assert ok.status_code == 200


def test_login_mints_strict_httponly_session_that_can_decide() -> None:
    app = create_approval_app(auth_token="submit-tok", decision_token="decide-tok")
    client = TestClient(app)
    _seed(client, "clr_sess1", "submit-tok")

    bad = client.get("/ui/login", params={"key": "submit-tok"})
    assert bad.status_code == 401
    assert "clauz3_approver_session" not in client.cookies

    login = client.get(
        "/ui/login", params={"key": "decide-tok"}, follow_redirects=False
    )
    assert login.status_code == 303
    assert login.headers["location"] == "/ui"
    set_cookie = login.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert "decide-tok" not in set_cookie

    assert client.get("/ui").status_code == 200
    decided = client.post(
        "/requests/clr_sess1/decision", json={"decision": "approved_once"}
    )
    assert decided.status_code == 200


def test_forged_session_cookie_is_rejected() -> None:
    app = create_approval_app(auth_token="submit-tok", decision_token="decide-tok")
    client = TestClient(app)
    _seed(client, "clr_forge", "submit-tok")
    client.cookies.set("clauz3_approver_session", "made-up")
    assert client.get("/ui").status_code == 401
    resp = client.post(
        "/requests/clr_forge/decision", json={"decision": "approved_once"}
    )
    assert resp.status_code == 401


def test_shared_token_configuration_keeps_token_out_of_pages() -> None:
    # Legacy single-token configuration: the token still authorizes decisions,
    # but the UI requires a session and never embeds the token.
    app = create_approval_app(auth_token="sekret")
    client = TestClient(app)
    _seed(client, "clr_legacy", "sekret")
    unauth = client.get("/ui/requests/clr_legacy")
    assert unauth.status_code == 401
    assert "sekret" not in unauth.text
    client.get("/ui/login", params={"key": "sekret"})
    detail = client.get("/ui/requests/clr_legacy")
    assert detail.status_code == 200
    assert "sekret" not in detail.text


def test_decision_token_must_differ_from_submission_token() -> None:
    with pytest.raises(ValueError, match="must differ"):
        create_approval_app(auth_token="same", decision_token="same")


def test_non_ascii_credentials_are_rejected_not_crashed() -> None:
    app = create_approval_app(auth_token="submit-tok", decision_token="decide-tok")
    client = TestClient(app, raise_server_exceptions=False)
    _seed(client, "clr_utf8", "submit-tok")
    assert client.get("/ui/login", params={"key": "d\u00e9cide"}).status_code == 401
    client.cookies.set("clauz3_approver_session", "s%C3%A9ance")
    assert client.get("/ui").status_code == 401
