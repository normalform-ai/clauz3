import threading
import time

from fastapi.testclient import TestClient

from clauz3.approval_service import create_approval_app


def test_approval_service_waits_for_user_approval() -> None:
    app = create_approval_app()
    client = TestClient(app)
    responses: list[dict[str, object]] = []

    def submit() -> None:
        response = client.post(
            "/requests",
            json={
                "request_id": "clr_test",
                "program_sha256": "abc123",
                "guarantees": ["emails.only(['bob@example.com'])"],
            },
        )
        responses.append(response.json())

    thread = threading.Thread(target=submit)
    thread.start()
    _wait_for_record(client, "clr_test")

    pending = client.get("/requests/clr_test").json()
    assert pending["status"] == "pending"
    assert "approval" not in pending

    decision = client.post(
        "/api/requests/clr_test/decision",
        json={"decision": "approved_once", "receipt": "receipt-from-service"},
    )

    assert decision.status_code == 200
    assert decision.json() == {
        "decision": "approved_once",
        "receipt": "receipt-from-service",
        "request_id": "clr_test",
    }
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert responses == [decision.json()]
    assert client.get("/requests/clr_test").json()["approval"] == decision.json()
    assert len(client.get("/requests").json()) == 1


def test_approval_service_can_request_more() -> None:
    app = create_approval_app()
    client = TestClient(app)
    responses: list[dict[str, object]] = []

    def submit() -> None:
        response = client.post("/requests", json={"program_sha256": "abc123"})
        responses.append(response.json())

    thread = threading.Thread(target=submit)
    thread.start()
    _wait_for_record(client, "clr_abc123")

    response = client.post(
        "/requests/clr_abc123/decision",
        json={"decision": "request_more", "feedback": "prove nobody twice"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "decision": "request_more",
        "feedback": "prove nobody twice",
        "request_id": "clr_abc123",
    }
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert responses == [response.json()]
    assert client.get("/requests/clr_abc123").json()["request_id"] == "clr_abc123"


def test_approval_service_ui_lists_and_shows_requests() -> None:
    app = create_approval_app()
    client = TestClient(app)

    empty = client.get("/")
    assert empty.status_code == 200
    assert "No approval requests yet." in empty.text
    assert "POST /requests" in empty.text

    thread = threading.Thread(
        target=lambda: client.post(
            "/api/requests",
            json={
                "request_id": "clr_ui",
                "target": "main",
                "program": "def main() -> None:\n    pass\n",
                "guarantees": ["emails.none()"],
                "proofs": [
                    {
                        "name": "main",
                        "conclusion": "proved!",
                        "description": "guarantee",
                    }
                ],
            },
        )
    )
    thread.start()
    _wait_for_record(client, "clr_ui")

    index = client.get("/ui")
    assert index.status_code == 200
    assert "clr_ui" in index.text
    assert "pending" in index.text

    detail = client.get("/ui/requests/clr_ui")
    assert detail.status_code == 200
    assert "emails.none()" in detail.text
    assert "main: proved! guarantee" in detail.text
    assert "Pending user decision." in detail.text
    assert "Reject contract" in detail.text
    assert "Show source" in detail.text

    decision = client.post(
        "/api/requests/clr_ui/decision",
        json={"decision": "approved_once"},
    )
    assert decision.status_code == 200
    thread.join(timeout=5)
    assert not thread.is_alive()

    decided = client.get("/ui/requests/clr_ui")
    assert "approved_once" in decided.text
    assert "local-clr_ui" in decided.text


def test_approval_service_ui_rows_endpoint_reflects_live_requests() -> None:
    app = create_approval_app()
    client = TestClient(app)

    empty = client.get("/ui/rows")
    assert empty.status_code == 200
    assert "No approval requests yet." in empty.text

    index = client.get("/ui")
    # The index polls the rows fragment to stay live without a reload.
    assert 'id="requests-body"' in index.text
    assert "/ui/rows" in index.text

    thread = threading.Thread(
        target=lambda: client.post(
            "/api/requests",
            json={"request_id": "clr_rows", "target": "main"},
        )
    )
    thread.start()
    _wait_for_record(client, "clr_rows")

    rows = client.get("/ui/rows")
    assert rows.status_code == 200
    assert "clr_rows" in rows.text
    assert "No approval requests yet." not in rows.text

    client.post(
        "/api/requests/clr_rows/decision",
        json={"decision": "approved_once"},
    )
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_approval_service_ui_renders_coverage_flags() -> None:
    app = create_approval_app()
    client = TestClient(app)

    thread = threading.Thread(
        target=lambda: client.post(
            "/api/requests",
            json={
                "request_id": "clr_cov",
                "target": "main",
                "program": "def main() -> None:\n    pass\n",
                "guarantees": ["emails.only(['bob@example.com'])"],
                "coverage": [
                    {
                        "domain": "email",
                        "status": "recommended_gap",
                        "missing_required": [],
                        "missing_recommended": ["unique_recipients"],
                    },
                    {
                        "domain": "db",
                        "status": "silent_gap",
                        "missing_required": [],
                        "missing_recommended": [],
                    },
                ],
            },
        )
    )
    thread.start()
    _wait_for_record(client, "clr_cov")

    index = client.get("/ui")
    # The index badge surfaces the worst status (silent_gap).
    assert "no guarantees" in index.text

    detail = client.get("/ui/requests/clr_cov")
    assert detail.status_code == 200
    # Silent gap sorts above the recommended gap and is rendered loudly.
    silent_pos = detail.text.index("used, but the agent stated no guarantee")
    recommended_pos = detail.text.index("unique_recipients")
    assert silent_pos < recommended_pos
    assert "cov-silent_gap" in detail.text

    client.post(
        "/api/requests/clr_cov/decision",
        json={"decision": "approved_once"},
    )
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_approval_service_rest_api_aliases() -> None:
    app = create_approval_app()
    client = TestClient(app)

    thread = threading.Thread(
        target=lambda: client.post("/api/requests", json={"request_id": "clr_api"})
    )
    thread.start()
    _wait_for_record(client, "clr_api")
    response = client.post(
        "/api/requests/clr_api/decision",
        json={"decision": "approved_once"},
    )

    assert response.status_code == 200
    assert client.get("/api/requests").json()[0]["request_id"] == "clr_api"
    assert client.get("/api/requests/clr_api").json()["request_id"] == "clr_api"
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_approval_service_returns_existing_decision_for_retried_request_ids() -> None:
    app = create_approval_app()
    client = TestClient(app)
    responses: list[dict[str, object]] = []

    def submit(target: str) -> None:
        response = client.post(
            "/requests",
            json={"request_id": "clr_retry", "target": target},
        )
        responses.append(response.json())

    first_thread = threading.Thread(target=submit, args=("first",))
    first_thread.start()
    _wait_for_record(client, "clr_retry")
    decision = client.post(
        "/requests/clr_retry/decision",
        json={"decision": "approved_once"},
    )
    first_thread.join(timeout=5)

    second = client.post(
        "/requests",
        json={"request_id": "clr_retry", "target": "second"},
    )

    assert second.status_code == 200
    assert responses == [decision.json()]
    assert second.json() == decision.json()
    requests = client.get("/requests").json()
    assert len(requests) == 1
    assert requests[0]["request_id"] == "clr_retry"
    assert requests[0]["target"] == "second"
    assert requests[0]["approval"] == decision.json()


def _wait_for_record(client: TestClient, request_id: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/requests/{request_id}")
        if response.status_code == 200:
            return
        time.sleep(0.01)
    raise AssertionError(f"request {request_id} was not recorded")
