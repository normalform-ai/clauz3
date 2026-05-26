"""Approval-service protocol for clauz3 run."""

import json
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

APPROVAL_SERVICE_ENV = "CLAUZ3_APPROVAL_SERVICE"
APPROVAL_URL_ENV = "CLAUZ3_APPROVAL_URL"
APPROVAL_CONFIG = Path(".clauz3/approval-service.json")
APPROVED_DECISIONS = {"approved_once", "approved_remember", "auto_approved"}
DEFAULT_APPROVAL_TIMEOUT = 300.0


class ApprovalServiceError(Exception):
    """The approval service could not be reached or returned invalid data."""


@dataclass(frozen=True)
class ApprovalResponse:
    decision: str
    request_id: str | None = None
    receipt: str | None = None
    feedback: str | None = None

    @property
    def approved(self) -> bool:
        return self.decision in APPROVED_DECISIONS


def configured_service_url(*, cwd: Path | None = None) -> str | None:
    """Return the externally configured approval-service URL, if any."""

    for env_name in (APPROVAL_SERVICE_ENV, APPROVAL_URL_ENV):
        value = os.environ.get(env_name)
        if value:
            return value.rstrip("/")

    root = Path.cwd() if cwd is None else cwd
    config_path = root / APPROVAL_CONFIG
    if not config_path.exists():
        return None
    try:
        payload = json.loads(config_path.read_text())
    except json.JSONDecodeError as exc:
        msg = f"invalid approval config {config_path}: {exc}"
        raise ApprovalServiceError(msg) from exc
    url = payload.get("url")
    if not isinstance(url, str) or not url:
        raise ApprovalServiceError(f"approval config {config_path} must contain a URL")
    return url.rstrip("/")


def submit_approval_request(
    service_url: str,
    payload: Mapping[str, object],
    *,
    timeout: float = DEFAULT_APPROVAL_TIMEOUT,
) -> ApprovalResponse:
    """Submit a run request to the approval service."""

    data = json.dumps(payload, sort_keys=True).encode()
    request = Request(
        f"{service_url.rstrip('/')}/requests",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            response_payload = _load_response(response.read())
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise ApprovalServiceError(
            f"approval service returned HTTP {exc.code}: {detail}",
        ) from exc
    except URLError as exc:
        raise ApprovalServiceError(f"approval service is unavailable: {exc}") from exc

    decision = response_payload.get("decision")
    if not isinstance(decision, str) or not decision:
        raise ApprovalServiceError("approval response must contain a decision")
    request_id = _optional_str(response_payload.get("request_id"))
    receipt = _optional_str(response_payload.get("receipt"))
    feedback = _optional_str(response_payload.get("feedback"))
    return ApprovalResponse(
        decision=decision,
        request_id=request_id,
        receipt=receipt,
        feedback=feedback,
    )


def load_mock_config(path: Path) -> dict[str, object]:
    """Load a mock approval-service config from JSON."""

    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ApprovalServiceError("mock approval config must be a JSON object")
    return dict(payload)


def serve_mock_approval_service(
    config: Mapping[str, object],
    *,
    host: str,
    port: int,
) -> None:
    """Run a blocking mock approval service for local testing."""

    with MockApprovalServer(config=config, host=host, port=port) as server:
        print(f"{APPROVAL_SERVICE_ENV}={server.url}", flush=True)
        server.serve_forever()


class MockApprovalServer:
    """Thread-friendly mock approval service for tests and demos."""

    def __init__(
        self,
        *,
        config: Mapping[str, object],
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.requests: list[dict[str, object]] = []
        handler = _make_handler(config=dict(config), records=self.requests)
        self._server = ThreadingHTTPServer((host, port), handler)
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = cast(tuple[str, int], self._server.server_address)
        return f"http://{host}:{port}"

    def __enter__(self) -> "MockApprovalServer":
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def serve_forever(self) -> None:
        self._server.serve_forever()


def _load_response(raw: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw.decode())
    except json.JSONDecodeError as exc:
        raise ApprovalServiceError(f"invalid approval response JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ApprovalServiceError("approval response must be a JSON object")
    return dict(payload)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ApprovalServiceError("approval response contains a non-string field")


def _make_handler(
    *,
    config: dict[str, object],
    records: list[dict[str, object]],
) -> type[BaseHTTPRequestHandler]:
    class MockApprovalHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/requests":
                self.send_error(404)
                return

            length = int(self.headers.get("Content-Length", "0"))
            try:
                request_payload = json.loads(self.rfile.read(length).decode())
            except json.JSONDecodeError:
                self.send_error(400, "invalid JSON")
                return
            if not isinstance(request_payload, dict):
                self.send_error(400, "request must be a JSON object")
                return

            request_record = dict(request_payload)
            records.append(request_record)
            response = approval_response_from_config(
                config=config,
                request_payload=request_record,
            )
            body = json.dumps(response, sort_keys=True).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return MockApprovalHandler


def approval_response_from_config(
    *,
    config: Mapping[str, object],
    request_payload: Mapping[str, object],
) -> dict[str, object]:
    request_id = request_id_from_payload(request_payload)
    required_hash = config.get("require_program_sha256")
    actual_hash = request_payload.get("program_sha256")
    if required_hash is not None and actual_hash != required_hash:
        return {
            "decision": "rejected",
            "request_id": request_id,
            "feedback": "program hash did not match mock approval config",
        }

    decision = config.get("decision", "approved_once")
    if not isinstance(decision, str):
        decision = "rejected"

    response: dict[str, object] = {
        "decision": decision,
        "request_id": request_id,
    }
    feedback = config.get("feedback")
    if isinstance(feedback, str):
        response["feedback"] = feedback
    if decision in APPROVED_DECISIONS:
        receipt = config.get("receipt")
        response["receipt"] = (
            receipt if isinstance(receipt, str) else f"mock-{request_id}"
        )
    return response


def request_id_from_payload(request_payload: Mapping[str, object]) -> str:
    """Return the stable clauz3 request id for an approval request."""

    request_id = request_payload.get("request_id")
    if isinstance(request_id, str) and request_id:
        return request_id
    program_hash = request_payload.get("program_sha256")
    if isinstance(program_hash, str) and program_hash:
        return f"clr_{program_hash[:12]}"
    return "clr_unknown"
