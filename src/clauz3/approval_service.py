"""FastAPI approval service for clauz3 run."""

import threading
from collections.abc import Mapping
from html import escape
from json import dumps
from typing import cast

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from clauz3.approval import (
    APPROVAL_SERVICE_ENV,
    APPROVED_DECISIONS,
    request_id_from_payload,
)
from clauz3.approval_policy import (
    ApprovalPolicy,
    auto_approval_response,
    evaluate_policy,
)


def create_approval_app(*, policy: ApprovalPolicy | None = None) -> FastAPI:
    """Create a simple localhost approval-service app.

    When ``policy`` is given, each incoming request is first evaluated against
    the policy-admin rules. A matching rule resolves the request immediately
    (``auto_approved`` or ``auto_rejected``) without waiting for a human;
    otherwise the request stays pending for a user decision.
    """

    app = FastAPI(title="clauz3 approval service")
    records: dict[str, dict[str, object]] = {}
    order: list[str] = []
    condition = threading.Condition()

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    @app.get("/ui", response_class=HTMLResponse)
    def index() -> str:
        with condition:
            return _render_index(records=records, order=order)

    @app.post("/requests")
    @app.post("/api/requests")
    def create_request(payload: dict[str, object]) -> dict[str, object]:
        request_id = request_id_from_payload(payload)
        with condition:
            previous = records.get(request_id)
            previous_approval = (
                previous.get("approval") if previous is not None else None
            )
            is_new = previous is None
            record = {
                **payload,
                "request_id": request_id,
                "status": "pending",
            }
            if isinstance(previous_approval, dict):
                record["approval"] = previous_approval
                record["status"] = _status_for_decision(previous_approval)
            elif policy is not None:
                decision = evaluate_policy(policy, record)
                if decision is not None:
                    auto_approval = auto_approval_response(
                        request_id=request_id,
                        decision=decision,
                    )
                    record["approval"] = auto_approval
                    record["status"] = _status_for_decision(auto_approval)
                    record["auto_decision"] = {
                        "rule": decision.rule,
                        "reason": decision.reason,
                    }
            records[request_id] = record
            if is_new:
                order.append(request_id)
            condition.notify_all()

            while True:
                approval = records[request_id].get("approval")
                if isinstance(approval, dict):
                    return dict(cast(dict[str, object], approval))
                condition.wait()

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
    ) -> dict[str, object]:
        decision = payload.get("decision")
        if not isinstance(decision, str) or not decision:
            raise HTTPException(status_code=400, detail="decision must be a string")

        feedback = _optional_text(payload.get("feedback"))
        receipt = _optional_text(payload.get("receipt"))
        response = _approval_response(
            request_id=request_id,
            decision=decision,
            feedback=feedback,
            receipt=receipt,
        )

        with condition:
            try:
                record = records[request_id]
            except KeyError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="request not found",
                ) from exc
            record["approval"] = response
            record["status"] = _status_for_decision(response)
            condition.notify_all()
            return dict(response)

    @app.get("/ui/rows", response_class=HTMLResponse)
    def index_rows() -> str:
        with condition:
            return _render_rows(records=records, order=order)

    @app.get("/ui/requests/{request_id}", response_class=HTMLResponse)
    def request_detail(request_id: str) -> str:
        with condition:
            try:
                record = dict(records[request_id])
            except KeyError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="request not found",
                ) from exc
        return _render_request(record)

    return app


def _approval_response(
    *,
    request_id: str,
    decision: str,
    feedback: str | None,
    receipt: str | None,
) -> dict[str, object]:
    response: dict[str, object] = {
        "decision": decision,
        "request_id": request_id,
    }
    if feedback:
        response["feedback"] = feedback
    if decision in APPROVED_DECISIONS:
        response["receipt"] = receipt or f"local-{request_id}"
    return response


def _optional_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _status_for_decision(approval: Mapping[str, object]) -> str:
    decision = approval.get("decision")
    if decision in APPROVED_DECISIONS:
        return "approved"
    return "denied"


def _render_rows(*, records: Mapping[str, dict[str, object]], order: list[str]) -> str:
    rows = []
    for request_id in reversed(order):
        record = records[request_id]
        decision = _decision_label(record)
        guarantees = _list(record.get("guarantees"))
        escaped_id = escape(request_id)
        rows.append(
            "<tr>"
            f"<td><a href='/ui/requests/{escaped_id}'>{escaped_id}</a></td>"
            f"<td>{escape(decision)}</td>"
            f"<td>{escape(str(record.get('target', 'main')))}</td>"
            f"<td>{len(guarantees)}</td>"
            f"<td>{_coverage_badge(record.get('coverage'))}</td>"
            "</tr>"
        )
    return "\n".join(rows) or (
        "<tr><td colspan='5' class='empty'>No approval requests yet.</td></tr>"
    )


def _render_index(*, records: Mapping[str, dict[str, object]], order: list[str]) -> str:
    table_body = _render_rows(records=records, order=order)
    return _page(
        "clauz3 approvals",
        f"""
        <section>
          <h2>Requests</h2>
          <table>
            <thead>
              <tr><th>Request</th><th>Decision</th><th>Target</th><th>Guarantees</th><th>Coverage</th></tr>
            </thead>
            <tbody id="requests-body">{table_body}</tbody>
          </table>
        </section>
        <script>
        // Poll for new/updated requests so the table stays live without a reload.
        async function refreshRows() {{
          try {{
            const response = await fetch("/ui/rows", {{cache: "no-store"}});
            if (response.ok) {{
              document.getElementById("requests-body").innerHTML =
                await response.text();
            }}
          }} catch (err) {{
            /* transient network error; try again on the next tick */
          }}
        }}
        setInterval(refreshRows, 2000);
        </script>
        """,
    )


def _render_request(record: Mapping[str, object]) -> str:
    request_id = str(record.get("request_id", "unknown"))
    approval = _mapping(record.get("approval"))
    proofs = _list(record.get("proofs"))
    guarantees = _list(record.get("guarantees"))
    proof_items = (
        "\n".join(f"<li>{escape(_summary(proof))}</li>" for proof in proofs)
        or "<li>None recorded.</li>"
    )
    guarantee_items = (
        "\n".join(
            f"<li><code>{escape(str(guarantee))}</code></li>"
            for guarantee in guarantees
        )
        or "<li>None declared.</li>"
    )
    approval_block = _render_approval_block(request_id=request_id, approval=approval)
    return _page(
        f"request {request_id}",
        f"""
        <p><a href="/ui">Back to requests</a></p>
        <section>
          <h2>Guarantees</h2>
          <ul>{guarantee_items}</ul>
        </section>
        <section>
          <h2>User decision</h2>
          {_render_auto_decision(record.get("auto_decision"))}
          {approval_block}
        </section>
        <section>
          <h2>Coverage</h2>
          {_render_coverage(record.get("coverage"))}
        </section>
        <section>
          <h2>Proofs</h2>
          <ul>{proof_items}</ul>
        </section>
        <section>
          <h2>Program</h2>
          <details>
            <summary>Show source</summary>
            <pre>{escape(str(record.get("program", "")))}</pre>
          </details>
        </section>
        <section>
          <h2>Raw Record</h2>
          <details>
            <summary>Show JSON</summary>
            <pre>{escape(dumps(dict(record), indent=2, sort_keys=True))}</pre>
          </details>
        </section>
        """,
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
    request_id: str,
    approval: Mapping[str, object],
) -> str:
    if approval:
        return f"""
        <dl>
          <dt>Request</dt><dd>{escape(request_id)}</dd>
          <dt>Decision</dt><dd>{escape(str(approval.get("decision", "unknown")))}</dd>
          <dt>Receipt</dt><dd>{escape(str(approval.get("receipt", "")))}</dd>
          <dt>Reason</dt><dd>{escape(str(approval.get("feedback", "")))}</dd>
        </dl>
        """

    request_id_json = dumps(request_id)
    return f"""
    <p class="pending">Pending user decision.</p>
    <label for="feedback">Optional reason or note</label>
    <textarea id="feedback" spellcheck="true"></textarea>
    <div class="actions">
      <button type="button" class="approve"
              onclick="decide('approved_once')">Approve</button>
      <button type="button"
              onclick="decide('rejected_contract')">Reject contract</button>
      <button type="button" onclick="decide('request_more')">Need more info</button>
      <button type="button" onclick="decide('rejected')">Reject</button>
    </div>
    <pre id="decision-result"></pre>
    <script>
    async function decide(decision) {{
      const feedback = document.getElementById("feedback").value;
      const result = document.getElementById("decision-result");
      const response = await fetch(`/api/requests/${{requestId}}/decision`, {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{decision, feedback}})
      }});
      result.textContent = await response.text();
      if (response.ok) {{
        window.location.reload();
      }}
    }}
    const requestId = {request_id_json};
    // Reload once a decision lands, even if it was made from another window.
    async function pollDecision() {{
      try {{
        const response = await fetch(`/api/requests/${{requestId}}`,
          {{cache: "no-store"}});
        if (response.ok && (await response.json()).approval) {{
          window.location.reload();
        }}
      }} catch (err) {{
        /* transient network error; try again on the next tick */
      }}
    }}
    setInterval(pollDecision, 2000);
    </script>
    """


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
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
    <p>REST API: <code>POST /requests</code>, <code>GET /requests</code>.</p>
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
        return "<p class='empty'>No domain policies applied.</p>"
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


def _coverage_badge(value: object) -> str:
    entries = [_mapping(item) for item in _list(value)]
    if not entries:
        return "<span class='empty'>&mdash;</span>"
    worst = max(
        entries,
        key=lambda entry: _COVERAGE_SEVERITY.get(str(entry.get("status")), 0),
    )
    status = str(worst.get("status", "covered"))
    label = _COVERAGE_LABEL.get(status, status)
    return f"<span class='badge cov-{escape(status)}'>{escape(label)}</span>"


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


def _decision_label(record: Mapping[str, object]) -> str:
    approval = _mapping(record.get("approval"))
    decision = approval.get("decision")
    if isinstance(decision, str) and decision:
        return decision
    status = record.get("status")
    if isinstance(status, str) and status:
        return status
    return "pending"


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
) -> None:
    """Start a blocking localhost FastAPI approval service."""

    app = create_approval_app(policy=policy)
    print(f"{APPROVAL_SERVICE_ENV}=http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port)
