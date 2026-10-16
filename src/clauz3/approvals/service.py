"""FastAPI approval service for clauz3 run."""

import hmac
import secrets
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from hashlib import sha256
from html import escape
from json import dumps
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse

import uvicorn
from fastapi import Cookie, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from clauz3.approvals.authority import (
    ApprovalAuthorizationError,
    ApprovedLayer,
    authorize_layer,
)
from clauz3.approvals.client import (
    APPROVAL_SERVICE_ENV,
    request_id_from_payload,
    sign_approval_receipt,
)
from clauz3.approvals.decisions import (
    REMEMBERABLE_DECISIONS,
    USER_DECISIONS,
    Decision,
    UnknownDecisionError,
    is_approved,
    parse_decision,
)
from clauz3.approvals.descriptions import render_guarantees
from clauz3.approvals.policy import (
    ApprovalPolicy,
    auto_approval_response,
    evaluate_policy,
)
from clauz3.approvals.profiles.store import ProfileStore
from clauz3.approvals.profiles.verification import verify as verify_profile
from clauz3.approvals.receipts import signing_key
from clauz3.approvals.ui import SCRIPT as UI_SCRIPT
from clauz3.approvals.ui import STYLE as UI_STYLE
from clauz3.approvals.workspace import WEB as APPROVAL_WEB
from clauz3.approvals.workspace import profile_page as render_profile_page
from clauz3.libraries.mock_metadata import mock_declarations

if TYPE_CHECKING:
    from clauz3.sessions.chat import ChatConfig

#: Cookie carrying an approver browser session minted by ``/ui/login``.
APPROVER_SESSION_COOKIE = "clauz3_approver_session"

#: Default cap on how long ``POST /requests`` blocks a worker waiting for a
#: human decision before returning 503, so unanswered requests cannot pin the
#: server's small thread pool indefinitely.
DEFAULT_MAX_WAIT_SECONDS = 300.0


def create_approval_app(
    *,
    policy: ApprovalPolicy | None = None,
    authorized_layers: Sequence[ApprovedLayer] = (),
    auth_token: str | None = None,
    decision_token: str | None = None,
    allowed_hosts: Sequence[str] | None = None,
    max_wait_seconds: float | None = DEFAULT_MAX_WAIT_SECONDS,
    profile: ProfileStore | None = None,
    chat_config: "ChatConfig | None" = None,
) -> FastAPI:
    """Create a simple localhost approval-service app.

    When ``policy`` is given, each incoming request is first evaluated against
    the policy-admin rules. A matching rule resolves the request immediately
    (``auto_approved`` or ``auto_rejected``) without waiting for a human;
    otherwise the request stays pending for a user decision.

    Security controls:

    - ``authorized_layers`` — operator-owned root configurations that policy
      requests may select. With a policy or profile, unconfigured selections
      return 403
      before importing code or reusing a prior approval. The default grants no
      loading authority. A manual-only service does not load requested layers.
    - ``auth_token`` — the *submission* credential held by ``clauz3 run``. When
      set, ``POST /requests`` requires ``Authorization: Bearer <token>``.
    - ``decision_token`` — the *approver* credential. When set, recording a
      decision requires it, either as ``Authorization: Bearer <token>`` or as a
      browser session minted by ``GET /ui/login?key=<token>``, and the HTML UI
      requires that session. The submission token does not authorize
      decisions, so a process that holds only what the runner holds cannot
      approve its own request. No page ever contains either token. When
      ``decision_token`` is omitted, ``auth_token`` doubles as the approver
      credential (the weaker, shared-token configuration); the two must differ
      when both are given.
    - ``allowed_hosts`` — when set, a ``TrustedHostMiddleware`` rejects requests
      whose ``Host`` header is not allow-listed, defeating DNS-rebinding. The
      same set also gates the ``Origin`` header on state-changing POSTs, so a
      cross-origin page cannot drive a decision even within an allow-listed Host.
      (The approver session cookie is ``SameSite=Strict``, so a cross-site page
      cannot send it; the Origin check is defense-in-depth.)
    - ``max_wait_seconds`` — bounds the blocking wait on ``POST /requests`` so an
      unanswered (or flood of) request(s) cannot wedge the worker pool.
    - ``profile`` — operator-owned persistent preferences and history for one
      project. Requires distinct submission/approver credentials and authorized
      layers. Matching independently verifies consent in a bounded subprocess;
      unsupported requests remain manual. Explicit policies and profiles cannot
      yet be composed. Protect the store just like an operator's policy file.
    """

    # Freeze the operator's registry; later mutations of a caller-owned list
    # must not change this running service's loading authority.
    authorized_layers = tuple(authorized_layers)
    if chat_config is not None and (not auth_token or not decision_token):
        raise ValueError(
            "workspace requires separate submission and approver credentials"
        )
    if profile is not None and policy is not None:
        raise ValueError(
            "remembered profiles and explicit policies cannot yet be combined"
        )
    if profile is not None and (not auth_token or not decision_token):
        raise ValueError(
            "profiles require separate submission and approver credentials"
        )
    if (
        decision_token is not None
        and auth_token is not None
        and _same_secret(decision_token, auth_token)
    ):
        raise ValueError(
            "decision_token must differ from auth_token: the runner's submission "
            "credential must not also authorize decisions"
        )
    approver_token = decision_token if decision_token is not None else auth_token
    approver_sessions: set[str] = set()
    app = FastAPI(title="clauz3 approval service")
    if allowed_hosts is not None:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    records: dict[str, dict[str, object]] = {}
    record_layers: dict[str, ApprovedLayer] = {}
    order: list[str] = []
    condition = threading.Condition()

    @app.get("/ui/libraries", response_class=HTMLResponse)
    @app.get("/ui/profile", response_class=HTMLResponse)
    def profile_page(
        session: str | None = Cookie(default=None, alias=APPROVER_SESSION_COOKIE),
    ) -> HTMLResponse:
        if not _is_approver(None, session):
            return _approver_login_required()
        if chat_config is not None:
            from clauz3.sessions.chat import render_workspace

            return HTMLResponse(render_workspace())
        return HTMLResponse(render_profile_page())

    @app.get("/ui/assets/{name}")
    def workspace_asset(name: str) -> Response:
        if name not in {
            "workspace.js",
            "workspace.css",
            "files.js",
            "libraries.js",
            "markdown.js",
            "logo.svg",
            "favicon.svg",
        }:
            raise HTTPException(404)
        directory = Path(__file__).parents[1] / "sessions" / "web"
        media_types = {".css": "text/css", ".svg": "image/svg+xml"}
        return Response(
            (directory / name).read_text(),
            media_type=media_types.get(Path(name).suffix, "text/javascript"),
        )

    @app.get("/ui/approval-assets/{name}")
    def approval_asset(name: str) -> Response:
        if name not in {"profile.js", "profile.css", "guarantees.js"}:
            raise HTTPException(404)
        return Response(
            (APPROVAL_WEB / name).read_text(),
            media_type="text/css" if name.endswith(".css") else "text/javascript",
        )

    @app.get("/api/profile")
    def get_profile(
        authorization: str | None = Header(default=None),
        session: str | None = Cookie(default=None, alias=APPROVER_SESSION_COOKIE),
    ) -> dict[str, object]:
        _check_approver(authorization, session)
        if profile is None:
            raise HTTPException(404, "profiles are not enabled")
        return profile.snapshot()

    @app.post("/api/profile/{action}")
    def change_profile(
        action: str,
        payload: dict[str, object],
        authorization: str | None = Header(default=None),
        origin: str | None = Header(default=None),
        session: str | None = Cookie(default=None, alias=APPROVER_SESSION_COOKIE),
    ) -> dict[str, object]:
        _check_origin(origin)
        _check_approver(authorization, session)
        if profile is None:
            raise HTTPException(404, "profiles are not enabled")
        try:
            if action == "remember":
                rule = profile.remember(str(payload.get("id", "")))
                return {"rule_id": rule}
            if action == "disable":
                profile.disable(str(payload.get("id", "")))
                return {"ok": True}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        raise HTTPException(404, "unknown profile action")

    def _check_auth(authorization: str | None) -> None:
        if auth_token is None:
            return
        expected = f"Bearer {auth_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=401, detail="missing or invalid bearer token"
            )

    def _is_approver(authorization: str | None, session: str | None) -> bool:
        if approver_token is None:
            return True
        if authorization is not None and _same_secret(
            authorization, f"Bearer {approver_token}"
        ):
            return True
        if session is None:
            return False
        with condition:
            known_sessions = tuple(approver_sessions)
        return any(_same_secret(session, known) for known in known_sessions)

    def _check_approver(authorization: str | None, session: str | None) -> None:
        if not _is_approver(authorization, session):
            raise HTTPException(
                status_code=401, detail="missing or invalid approver credential"
            )

    def _check_origin(origin: str | None) -> None:
        # Non-browser clients (the runner) send no Origin and are unaffected.
        # A same-origin browser POST sends Origin: http://<host>:<port>; reject
        # one whose hostname isn't allow-listed, so a drive-by page on another
        # origin cannot POST a decision even within the same Host. (The Host
        # check defeats DNS-rebinding; this is the complementary CSRF guard.)
        if origin is None or allowed_hosts is None:
            return
        hostname = urlparse(origin).hostname
        if hostname is None or hostname not in set(allowed_hosts):
            raise HTTPException(status_code=403, detail="origin not allowed")

    # The workspace pauses automatic approvals (remembered allow rules and
    # policy allows) for a conversation that gained libraries after it
    # started; automatic rejections still apply.
    approvals_paused = False
    if chat_config is not None:
        from clauz3.libraries.layout import LayoutError, expand_import_roots
        from clauz3.sessions.chat import install_chat

        def activate_workspace_libraries(roots: list[Path], paused: bool) -> None:
            nonlocal authorized_layers, approvals_paused
            # Only the operator-owned workspace manager supplies this selection;
            # incoming approval requests still select, never authorize, roots.
            try:
                import_roots = tuple(expand_import_roots([Path(chat_config.cwd)]))
            except LayoutError:
                # An unusable project layout authorizes nothing. Report it for a
                # selection; the empty fallback selection must not raise again.
                with condition:
                    authorized_layers = ()
                    approvals_paused = paused
                if roots:
                    raise
                return
            with condition:
                authorized_layers = (ApprovedLayer(tuple(roots), import_roots),)
                approvals_paused = paused

        install_chat(
            app,
            chat_config,
            lambda session: _is_approver(None, session),
            activate_workspace_libraries,
        )

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True}

    @app.get("/ui/login", response_model=None)
    def login(key: str = "") -> RedirectResponse | HTMLResponse:
        # Exchange the approver token (from the operator's terminal) for an
        # HttpOnly session cookie, then drop the key from the address bar.
        if approver_token is None:
            return RedirectResponse("/ui", status_code=303)
        if not key or not _same_secret(key, approver_token):
            return _approver_login_required()
        session = secrets.token_urlsafe(32)
        with condition:
            approver_sessions.add(session)
        response = RedirectResponse("/ui", status_code=303)
        response.set_cookie(
            APPROVER_SESSION_COOKIE,
            session,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    @app.get("/ui", response_class=HTMLResponse)
    def index(
        session: str | None = Cookie(default=None, alias=APPROVER_SESSION_COOKIE),
    ) -> HTMLResponse:
        if not _is_approver(None, session):
            return _approver_login_required()
        if chat_config is not None:
            from clauz3.sessions.chat import render_workspace

            return HTMLResponse(render_workspace())
        with condition:
            return HTMLResponse(_render_index(records=records, order=order))

    @app.post("/requests")
    @app.post("/api/requests")
    def create_request(
        payload: dict[str, object],
        authorization: str | None = Header(default=None),
        origin: str | None = Header(default=None),
    ) -> dict[str, object]:
        _check_origin(origin)
        _check_auth(authorization)
        selected_layer: ApprovedLayer | None = None
        if policy is not None or profile is not None:
            try:
                selected_layer = authorize_layer(
                    payload, authorized_layers=authorized_layers
                )
            except ApprovalAuthorizationError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
        request_id = request_id_from_payload(payload)
        # Only operator-authorized paths may supply UI metadata. A manual-only
        # service must not read arbitrary paths from a submitted request.
        metadata_layer = selected_layer
        if metadata_layer is None and authorized_layers:
            with suppress(ApprovalAuthorizationError):
                metadata_layer = authorize_layer(
                    payload, authorized_layers=authorized_layers
                )
        declarations = None
        if metadata_layer is not None and metadata_layer.trusted_roots:
            roots = list(metadata_layer.trusted_roots)
            # Prefer project-relative labels; unrelated/cross-drive roots retain
            # absolute labels without requiring a common filesystem ancestor.
            project = next(
                (
                    base
                    for base in metadata_layer.import_roots
                    if all(root.is_relative_to(base) for root in roots)
                ),
                None,
            )
            declarations = mock_declarations(project, roots)
        basis = None
        profile_error = None
        if profile is not None:
            assert selected_layer is not None
            # Do not hold the UI/decision lock during proof. The worker has a
            # wall-clock deadline and no signing key or approver credential.
            try:
                basis = verify_profile(payload, selected_layer, project=profile.project)
            except Exception:
                profile_error = (
                    "Independent verification unavailable; "
                    "this request cannot be remembered."
                )
        with condition:
            previous = records.get(request_id)
            if profile is not None and previous is not None:
                fields = (
                    "program",
                    "target",
                    "program_sha256",
                    "guarantees",
                    "coverage",
                    "trusted_roots",
                    "import_roots",
                )
                if any(previous.get(k) != payload.get(k) for k in fields):
                    raise HTTPException(
                        409, "request ID already belongs to another submission"
                    )
                if previous.get("approval"):
                    # Each resubmission is a new execution: reconsult current
                    # rules and mint a new receipt, never reuse a past decision.
                    previous = None
                elif previous.get("profile_signature") != (
                    basis.get("signature") if basis is not None else None
                ):
                    raise HTTPException(
                        409, "verification context changed for a pending request"
                    )
            previous_approval = (
                previous.get("approval") if previous is not None else None
            )
            is_new = request_id not in records
            record = {
                **payload,
                "request_id": request_id,
                "status": "pending",
            }
            # Decision state belongs to the service, never to the submitter.
            record.pop("approval", None)
            record.pop("auto_decision", None)
            record.pop("library_mock_declarations", None)
            if declarations is not None:
                record["library_mock_declarations"] = declarations
            record.pop("contract_documentation", None)
            record.pop("contract_descriptions", None)
            for field in (
                "profile_eligible",
                "profile_error",
                "approvals_paused",
                "profile_signature",
                "history_id",
            ):
                record.pop(field, None)
            if profile is not None:
                if basis is not None:
                    record["contract_documentation"] = basis.get(
                        "contract_documentation", {}
                    )
                    record["contract_descriptions"] = basis.get(
                        "contract_descriptions", {}
                    )
                record["profile_eligible"] = basis is not None
                record["profile_signature"] = basis["signature"] if basis else None
                if profile_error:
                    record["profile_error"] = profile_error
                record["history_id"] = (
                    previous["history_id"]
                    if previous is not None
                    else profile.record(record, basis)
                )
            same_layer = (
                selected_layer is None
                or record_layers.get(request_id) == selected_layer
            )
            if isinstance(previous_approval, dict) and same_layer:
                record["approval"] = previous_approval
                record["status"] = _status_for_decision(previous_approval)
            elif policy is not None:
                assert selected_layer is not None
                # Freeze the authorized selection even if a request-controlled
                # path alias changes before evaluation. Keep the original
                # record for display and receipt signing.
                policy_request = {
                    **record,
                    "trusted_roots": [
                        str(path) for path in selected_layer.trusted_roots
                    ],
                    "import_roots": [str(path) for path in selected_layer.import_roots],
                }
                try:
                    decision = evaluate_policy(
                        policy, policy_request, authorized_layers=(selected_layer,)
                    )
                except ApprovalAuthorizationError as exc:
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
                if (
                    decision is not None
                    and decision.decision is Decision.AUTO_APPROVED
                    and approvals_paused
                ):
                    record["approvals_paused"] = True
                    decision = None
                if decision is not None:
                    auto_approval = auto_approval_response(
                        request_id=request_id,
                        decision=decision,
                    )
                    # Replace the placeholder receipt with a signed one so an
                    # auto-approved request survives the runner's verification.
                    key = signing_key()
                    if key is not None and "receipt" in auto_approval:
                        auto_approval["receipt"] = sign_approval_receipt(
                            signing_key_b64=key,
                            request_id=request_id,
                            request_payload=record,
                            decision=decision.decision,
                        )
                    record["approval"] = auto_approval
                    record["status"] = _status_for_decision(auto_approval)
                    record["auto_decision"] = {
                        "rule": decision.rule,
                        "reason": decision.reason,
                    }
            if profile is not None and basis is not None and "approval" not in record:
                rule = profile.match(str(basis["signature"]))
                if (
                    rule is not None
                    and rule["decision"] == "allow"
                    and approvals_paused
                ):
                    record["approvals_paused"] = True
                    rule = None
                if rule is not None:
                    action = (
                        Decision.AUTO_APPROVED
                        if rule["decision"] == "allow"
                        else Decision.AUTO_REJECTED
                    )
                    response = _approval_response(
                        request_id=request_id,
                        decision=action,
                        feedback=None,
                        receipt=None,
                        record=record,
                    )
                    profile.decide(str(record["history_id"]), action, str(rule["id"]))
                    record["approval"] = response
                    record["status"] = _status_for_decision(response)
                    record["auto_decision"] = {
                        "rule": rule["id"],
                        "reason": "Remembered exact contract in this project",
                    }
            if profile is not None and previous is not None:
                previous.clear()
                previous.update(record)
                record = previous
            records[request_id] = record
            if selected_layer is not None:
                record_layers[request_id] = selected_layer
            if is_new:
                order.append(request_id)
            condition.notify_all()

            deadline = (
                None
                if max_wait_seconds is None
                else time.monotonic() + max_wait_seconds
            )
            while True:
                if (
                    selected_layer is not None
                    and record_layers.get(request_id) != selected_layer
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="request superseded by a different authorized layer",
                    )
                approval = (record if profile is not None else records[request_id]).get(
                    "approval"
                )
                if isinstance(approval, dict):
                    return dict(cast(dict[str, object], approval))
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise HTTPException(
                        status_code=503,
                        detail="approval timed out awaiting a human decision",
                    )
                condition.wait(timeout=remaining)

    @app.get("/requests")
    @app.get("/api/requests")
    def list_requests() -> list[dict[str, object]]:
        with condition:
            return [dict(records[request_id]) for request_id in order]

    @app.get("/requests/{request_id}")
    @app.get("/api/requests/{request_id}")
    def get_request(request_id: str) -> dict[str, object]:
        with condition:
            try:
                return dict(records[request_id])
            except KeyError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="request not found",
                ) from exc

    @app.post("/requests/{request_id}/decision")
    @app.post("/api/requests/{request_id}/decision")
    def decide_request(
        request_id: str,
        payload: dict[str, object],
        authorization: str | None = Header(default=None),
        origin: str | None = Header(default=None),
        session: str | None = Cookie(default=None, alias=APPROVER_SESSION_COOKIE),
    ) -> dict[str, object]:
        _check_origin(origin)
        _check_approver(authorization, session)
        # Issue #62: an unknown string is a protocol error, not a denial. Refuse
        # it before touching the record or the profile, so nothing is stored
        # and the runner keeps waiting for a real decision.
        try:
            decision = parse_decision(payload.get("decision"), allowed=USER_DECISIONS)
        except UnknownDecisionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        feedback = _optional_text(payload.get("feedback"))
        receipt = _optional_text(payload.get("receipt"))

        with condition:
            try:
                record = records[request_id]
            except KeyError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="request not found",
                ) from exc
            response = _approval_response(
                request_id=request_id,
                decision=decision,
                feedback=feedback,
                receipt=receipt,
                record=record,
            )
            remember = payload.get("remember") is True or (
                profile is not None and decision is Decision.APPROVED_REMEMBER
            )
            if remember and (profile is None or not record.get("profile_eligible")):
                raise HTTPException(400, "this request cannot be remembered")
            if profile is not None:
                if "approval" in record:
                    raise HTTPException(409, "this request has already been decided")
                if remember and decision not in REMEMBERABLE_DECISIONS:
                    raise HTTPException(
                        400, "only approval or rejection can be remembered"
                    )
                profile.decide(str(record["history_id"]), decision, remember=remember)
            record["approval"] = response
            record["status"] = _status_for_decision(response)
            condition.notify_all()
            return dict(response)

    @app.get("/ui/rows", response_class=HTMLResponse)
    def index_rows(
        session: str | None = Cookie(default=None, alias=APPROVER_SESSION_COOKIE),
    ) -> HTMLResponse:
        if not _is_approver(None, session):
            return _approver_login_required()
        with condition:
            return HTMLResponse(_render_rows(records=records, order=order))

    @app.get("/ui/requests/{request_id}", response_class=HTMLResponse)
    def request_detail(
        request_id: str,
        session: str | None = Cookie(default=None, alias=APPROVER_SESSION_COOKIE),
    ) -> HTMLResponse:
        if not _is_approver(None, session):
            return _approver_login_required()
        with condition:
            try:
                record = dict(records[request_id])
            except KeyError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="request not found",
                ) from exc
        return HTMLResponse(_render_request(record))

    return app


def _same_secret(given: str, expected: str) -> bool:
    # Compare as bytes: ``hmac.compare_digest`` raises on non-ASCII ``str``,
    # which would turn a crafted header, cookie, or key into a 500.
    return hmac.compare_digest(given.encode(), expected.encode())


def _approver_login_required() -> HTMLResponse:
    return HTMLResponse(
        _page(
            "approver sign-in required",
            """
            <p>This approval console needs the approver credential. Open the
            <code>/ui/login?key=...</code> URL that <code>clauz3
            approval-service</code> printed in the operator's terminal.</p>
            """,
        ),
        status_code=401,
    )


def _approval_response(
    *,
    request_id: str,
    decision: Decision,
    feedback: str | None,
    receipt: str | None,
    record: Mapping[str, object],
) -> dict[str, object]:
    response: dict[str, object] = {
        "decision": decision.value,
        "request_id": request_id,
    }
    if feedback:
        response["feedback"] = feedback
    if decision.approved:
        response["receipt"] = _decision_receipt(request_id, decision, receipt, record)
    return response


def _decision_receipt(
    request_id: str,
    decision: Decision,
    explicit: str | None,
    record: Mapping[str, object],
) -> str:
    """Sign an Ed25519 receipt from the stored record, or fall back legacy.

    A signed receipt binds the run the approver actually saw (program +
    guarantees + coverage); the runner refuses anything it can't verify, so the
    legacy ``local-<id>`` form is only useful in keyless demos.
    """

    if explicit is not None:
        return explicit
    key = signing_key()
    if key is None:
        return f"local-{request_id}"
    return sign_approval_receipt(
        signing_key_b64=key,
        request_id=request_id,
        request_payload=record,
        decision=decision,
    )


def _optional_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _status_for_decision(approval: Mapping[str, object]) -> str:
    if is_approved(approval.get("decision")):
        return "approved"
    return "denied"


def _render_rows(*, records: Mapping[str, dict[str, object]], order: list[str]) -> str:
    return "\n".join(_render_card(records[key]) for key in reversed(order)) or (
        "<p class='empty'>No approval requests yet.</p>"
    )


def _render_index(*, records: Mapping[str, dict[str, object]], order: list[str]) -> str:
    cards = _render_rows(records=records, order=order) if order else ""
    return _page(
        "clauz3 approvals",
        f"""
        <p><a href="/ui/profile">Profile and history</a></p>
        <nav class="tabs" role="tablist" aria-label="Approval status">
          <button role="tab" id="tab-pending" data-filter="pending" aria-selected="true"
                  aria-controls="requests-body">Awaiting approval
                  <span class="count">0</span></button>
          <button role="tab" id="tab-approved" data-filter="approved"
                  aria-selected="false"
                  aria-controls="requests-body" tabindex="-1">Approved
                  <span class="count">0</span></button>
          <button role="tab" id="tab-denied" data-filter="denied" aria-selected="false"
                  aria-controls="requests-body" tabindex="-1">Rejected
                  <span class="count">0</span></button>
        </nav>
        <p id="connection-status" role="status"></p>
        <p id="announcement" class="sr-only" role="status"></p>
        <div id="requests-body">{cards}</div>
        <p id="empty-state" class="empty">No approval requests yet.</p>
        {UI_SCRIPT}
        """,
    )


def _render_card(record: Mapping[str, object], *, single: bool = False) -> str:
    request_id = str(record.get("request_id", "unknown"))
    approval = _mapping(record.get("approval"))
    status = _status_for_decision(approval) if approval else "pending"
    label = escape(_decision_label(approval))
    guarantees = render_guarantees(record)
    proofs = (
        "\n".join(
            f"<li>{escape(_summary(item))}</li>" for item in _list(record.get("proofs"))
        )
        or "<li>None recorded.</li>"
    )
    version = sha256(dumps(dict(record), sort_keys=True).encode()).hexdigest()
    policy_note = (
        "<p>No domain policies applied.</p>"
        if not _list(record.get("coverage"))
        else ""
    )
    hidden = " hidden" if not single and status != "pending" else ""
    return f"""
    <article class="request-card" data-request-id="{escape(request_id)}"
             data-status="{status}" data-version="{version}"{hidden}>
      <div class="card-heading">
        <h2>{escape(str(record.get("target", "main")))}</h2>
        <span class="status-badge">{label}</span>
      </div>
      <a class="request-link" href="/ui/requests/{escape(request_id)}">
        {escape(request_id)}</a>
      <h3>Guarantees</h3><ul>{guarantees}</ul>
      {_render_mock_declarations(record.get("library_mock_declarations"))}
      {_render_coverage(record.get("coverage"))}
      {_render_auto_decision(record.get("auto_decision"))}
      {_render_approval_block(approval=approval)}
      {_render_paused(record, approval)}{_render_remember(record, approval)}
      <details data-panel="source"><summary>Show source</summary>
        <pre>{escape(str(record.get("program", "")))}</pre></details>
      <details data-panel="proofs"><summary>Proofs</summary><ul>{proofs}</ul></details>
      <details data-panel="record"><summary>Request details</summary>
        {policy_note}
        <pre>{escape(dumps(dict(record), indent=2, sort_keys=True))}</pre></details>
    </article>
    """


def _render_mock_declarations(value: object) -> str:
    metadata = _mapping(value)
    effects = _list(metadata.get("declared_mock"))
    errors = _list(metadata.get("errors"))
    if not effects and not errors:
        return ""
    label = "Includes simulated tools" if effects else "Source inspection details"
    names = "".join(f"<li><code>{escape(str(item))}</code></li>" for item in effects)
    problems = "".join(f"<li>{escape(str(item))}</li>" for item in errors)
    return (
        '<aside class="mock-declarations"><details data-panel="mock-effects">'
        f"<summary>{label}</summary>"
        + (
            "<p>The library author marks these tools as simulations. "
            "This is not enforced isolation; review the implementation for details.</p>"
            f"<ul>{names}</ul>"
            if effects
            else ""
        )
        + (
            f"<p>Could not inspect all sources:</p><ul>{problems}</ul>"
            if problems
            else ""
        )
        + "</details></aside>"
    )


def _render_paused(record: Mapping[str, object], approval: Mapping[str, object]) -> str:
    if not record.get("approvals_paused") or approval:
        return ""
    return (
        "<p>An automatic approval rule matches, but automatic approvals are "
        "paused in this conversation because libraries were added after it "
        "started.</p>"
    )


def _render_remember(
    record: Mapping[str, object], approval: Mapping[str, object]
) -> str:
    if record.get("profile_error"):
        return f"<p>{escape(str(record['profile_error']))}</p>"
    if not record.get("profile_eligible"):
        return ""
    if approval:
        return (
            '<p><a href="/ui/profile">'
            "Manage this decision in profile and history</a></p>"
        )
    return """<p>Remember this exact contract for this project and trusted code:</p>
    <button data-decision="approved_once" data-remember="true">
      Approve and remember</button>
    <button data-decision="rejected" data-remember="true">
      Reject and remember</button>"""


def _render_request(record: Mapping[str, object]) -> str:
    request_id = str(record.get("request_id", "unknown"))
    return _page(
        "clauz3 approvals",
        f'<p><a href="/ui">Back to requests</a></p>'
        '<p id="connection-status" role="status"></p>'
        '<p id="announcement" class="sr-only" role="status"></p>'
        f'<div id="requests-body" data-single="{escape(request_id)}">'
        + _render_card(record, single=True)
        + "</div>"
        + UI_SCRIPT,
    )


def _render_auto_decision(value: object) -> str:
    auto = _mapping(value)
    if not auto:
        return ""
    rule = str(auto.get("rule", ""))
    reason = str(auto.get("reason") or "")
    detail = f" — {escape(reason)}" if reason else ""
    return (
        "<p class='auto'>Decided automatically by policy rule "
        f"<code>{escape(rule)}</code>{detail}</p>"
    )


def _render_approval_block(
    *,
    approval: Mapping[str, object],
) -> str:
    if approval:
        feedback = str(approval.get("feedback") or "No note provided.")
        receipt = approval.get("receipt")
        receipt_panel = (
            '<details data-panel="receipt"><summary>Approval receipt</summary>'
            f"<pre>{escape(str(receipt))}</pre></details>"
            if receipt
            else ""
        )
        return (
            '<div class="decision-summary">'
            f"<p><strong>{escape(_decision_label(approval))}</strong></p>"
            f'<p class="decision-note">{escape(feedback)}</p>'
            f"{receipt_panel}</div>"
        )

    return """
    <label>Optional reason or note
      <textarea spellcheck="true"></textarea>
    </label>
    <div class="actions">
      <button type="button" class="approve" data-decision="approved_once">
        Approve</button>
      <button type="button" data-decision="rejected_contract">Reject contract</button>
      <button type="button" data-decision="request_more">Need more info</button>
      <button type="button" data-decision="rejected">Reject</button>
    </div>
    <p class="decision-result" role="status"></p>
    """


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    {UI_STYLE}
    body {{
      color: #202124;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
      margin: 2rem auto;
      max-width: 960px;
      padding: 0 1rem;
    }}
    header {{
      border-bottom: 1px solid #d5d7db;
      margin-bottom: 1.5rem;
    }}
    section {{
      margin: 1.5rem 0;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
    }}
    th, td {{
      border-bottom: 1px solid #e5e7eb;
      padding: 0.5rem;
      text-align: left;
    }}
    textarea, pre {{
      background: #f7f8fa;
      border: 1px solid #d5d7db;
      border-radius: 4px;
      box-sizing: border-box;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow: auto;
      padding: 0.75rem;
      width: 100%;
    }}
    textarea {{
      min-height: 6rem;
    }}
    button {{
      margin: 0.5rem 0.4rem 0 0;
      padding: 0.4rem 0.7rem;
    }}
    details summary {{
      cursor: pointer;
    }}
    label {{
      display: block;
      font-weight: 600;
      margin-bottom: 0.35rem;
    }}
    .approve {{
      font-weight: 700;
    }}
    .empty, .pending {{
      color: #6b7280;
    }}
    ul.coverage {{
      list-style: none;
      padding-left: 0;
    }}
    .cov {{
      border-left: 4px solid #d5d7db;
      border-radius: 3px;
      margin: 0.3rem 0;
      padding: 0.35rem 0.6rem;
    }}
    .badge {{
      border-radius: 999px;
      font-size: 0.8rem;
      padding: 0.1rem 0.55rem;
    }}
    .cov-covered {{
      background: #f2fbf4;
      border-left-color: #1a7f37;
    }}
    .cov-recommended_gap {{
      background: #fdf7e6;
      border-left-color: #bf8700;
    }}
    .cov-silent_gap, .cov-required_gap {{
      background: #fdeff0;
      border-left-color: #cf222e;
    }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(title)}</h1>
    <p>Review what the agent wants to do, then approve or reject.</p>
  </header>
  {body}
</body>
</html>
"""


# Severity rank: gaps sort above covered; silent/required are loudest.
_COVERAGE_SEVERITY = {
    "required_gap": 3,
    "silent_gap": 2,
    "recommended_gap": 1,
    "covered": 0,
}
_COVERAGE_LABEL = {
    "covered": "covered",
    "recommended_gap": "recommended gap",
    "silent_gap": "no guarantees",
    "required_gap": "required missing",
}


def _render_coverage(value: object) -> str:
    entries = [_mapping(item) for item in _list(value)]
    if not entries:
        return ""
    entries.sort(
        key=lambda entry: _COVERAGE_SEVERITY.get(str(entry.get("status")), 0),
        reverse=True,
    )
    items = []
    for entry in entries:
        status = str(entry.get("status", "covered"))
        domain = str(entry.get("domain", "?"))
        label = _COVERAGE_LABEL.get(status, status)
        items.append(
            f"<li class='cov cov-{escape(status)}'>"
            f"<strong>{escape(domain)}</strong> — {escape(label)}"
            f"{_coverage_detail(entry)}</li>"
        )
    return f"<ul class='coverage'>{''.join(items)}</ul>"


def _coverage_detail(entry: Mapping[str, object]) -> str:
    status = str(entry.get("status"))
    if status == "silent_gap":
        return escape(": used, but the agent stated no guarantee about it")
    if status == "required_gap":
        missing = _str_list(entry.get("missing_required"))
        return escape(": " + ", ".join(missing) + " not proved") if missing else ""
    if status == "recommended_gap":
        missing = _str_list(entry.get("missing_recommended"))
        return escape(": " + ", ".join(missing) + " not stated") if missing else ""
    return ""


def _str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, dict):
        return value
    return {}


def _list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []


def _decision_label(approval: Mapping[str, object]) -> str:
    if not approval:
        return "Awaiting approval"
    decision = str(approval.get("decision", "unknown"))
    return {
        "approved_once": "Approved once",
        "approved_remember": "Approved and remembered",
        "auto_approved": "Approved automatically",
        "auto_rejected": "Rejected automatically",
        "rejected_contract": "Contract rejected",
        "request_more": "More information requested",
        "rejected": "Rejected",
    }.get(decision, f"Decision: {decision}")


def _summary(value: object) -> str:
    if isinstance(value, dict):
        name = value.get("name", "unknown")
        conclusion = value.get("conclusion", "unknown")
        description = value.get("description", "")
        return f"{name}: {conclusion} {description}".strip()
    return str(value)


def serve_approval_service(
    *,
    host: str,
    port: int,
    policy: ApprovalPolicy | None = None,
    authorized_layers: Sequence[ApprovedLayer] = (),
    auth_token: str | None = None,
    decision_token: str | None = None,
) -> None:
    """Start a blocking localhost FastAPI approval service.

    Authenticates submissions with ``auth_token`` (the runner's credential) and
    decisions with ``decision_token`` (the approver's credential), and rejects
    requests whose ``Host`` header is not the bound host or a localhost alias,
    to defeat DNS-rebinding. The approver sign-in URL is printed for the
    operator; give it only to the person or tool that decides requests.
    """

    allowed_hosts = sorted({host, "127.0.0.1", "localhost", "::1"})
    app = create_approval_app(
        policy=policy,
        authorized_layers=authorized_layers,
        auth_token=auth_token,
        decision_token=decision_token,
        allowed_hosts=allowed_hosts,
    )
    print(f"{APPROVAL_SERVICE_ENV}=http://{host}:{port}", flush=True)
    if auth_token:
        print(f"CLAUZ3_APPROVAL_TOKEN={auth_token}", flush=True)
    approver_token = decision_token if decision_token is not None else auth_token
    if approver_token:
        print(
            f"approver UI (keep private): "
            f"http://{host}:{port}/ui/login?key={approver_token}",
            flush=True,
        )
    uvicorn.run(app, host=host, port=port)
