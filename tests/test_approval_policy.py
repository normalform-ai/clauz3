import json
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clauz3 import interp, layer_state
from clauz3.approvals.authority import ApprovedLayer
from clauz3.approvals.policy import (
    ApprovalPolicy,
    ApprovalPolicyError,
    Rule,
    approval_policy_from_dict,
    evaluate_policy,
    load_approval_policy,
)
from clauz3.approvals.service import create_approval_app
from clauz3.imports_policy import ImportPolicy

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"
APPROVED_LAYERS = (ApprovedLayer((TRUSTED_ROOT,), (EMAIL_ROOT,)),)

_POLICY_DATA = {
    "version": 1,
    "imports": {"pol": "tools.email.trusted.contracts"},
    "rules": [
        {
            "name": "deny-blocked-recipients",
            "decision": "auto_rejected",
            "reason": "cannot prove a blocked address is never emailed",
            "unless_proven": ["pol.recipient_at_most('ceo@example.com', 0)"],
        },
        {
            "name": "auto-pass-internal",
            "decision": "auto_approved",
            "reason": "bounded, unique, internal-only email",
            "when_proven": [
                "pol.only(['bob@example.com', 'ann@example.com'])",
                "pol.unique_recipients()",
                "pol.at_most(10)",
            ],
        },
    ],
}


def _policy() -> ApprovalPolicy:
    return approval_policy_from_dict(_POLICY_DATA)


def _empty_clause_policy() -> ApprovalPolicy:
    # The JSON loader refuses empty rules, but callers can construct a policy
    # directly. Its empty-conjunction shortcut must still validate the source.
    return ApprovalPolicy(
        imports=(),
        rules=(Rule(name="empty-clauses", decision="auto_approved", clauses=()),),
    )


def _request(program: str) -> dict[str, object]:
    return {
        "program": program,
        "target": "main",
        "trusted_roots": [str(TRUSTED_ROOT)],
        "import_roots": [str(EMAIL_ROOT)],
    }


def _program(body: str) -> str:
    return (
        "from tools.email.trusted.effects import send_email\n"
        "def main() -> None:\n" + body
    )


@pytest.mark.parametrize("empty_clauses", [False, True], ids=["nonempty", "empty"])
def test_module_level_effect_cannot_be_auto_approved(
    capsys: pytest.CaptureFixture[str], empty_clauses: bool
) -> None:
    """Issue #100 on the autoapproval path.

    ``_entails_all`` reaches ``prove_text`` directly, not ``run_source``, so
    ``runner._preflight_structure`` never sees this program. Before
    ``_entails_all`` gated module scope the deny rule saw a body that emails only Bob,
    the allow rule entailed all three clauses, and a program that emails the
    CEO at module load was ``auto_approved``. Refusing to prove it leaves the
    decision to a human.
    """
    program = (
        "from tools.email.trusted.effects import send_email\n"
        "SENT = send_email('ceo@example.com', 'exfil')\n"
        "def main() -> None:\n"
        "    send_email('bob@example.com', 'hi')\n"
    )
    policy = _empty_clause_policy() if empty_clauses else _policy()

    assert (
        evaluate_policy(policy, _request(program), authorized_layers=APPROVED_LAYERS)
        is None
    )
    assert "module-level assignment value must be a constant expression" in (
        capsys.readouterr().err
    )


def test_empty_clause_policy_can_approve_a_valid_module(
    capsys: pytest.CaptureFixture[str],
) -> None:
    decision = evaluate_policy(
        _empty_clause_policy(),
        _request(_program('    send_email("bob@example.com", "hi")\n')),
        authorized_layers=APPROVED_LAYERS,
    )

    assert decision is not None
    assert decision.decision == "auto_approved"
    assert decision.rule == "empty-clauses"
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("empty_clauses", [False, True], ids=["nonempty", "empty"])
def test_duplicate_entry_cannot_reach_an_autoapproval_shortcut(
    capsys: pytest.CaptureFixture[str], empty_clauses: bool
) -> None:
    program = _program('    send_email("bob@example.com", "hi")\n') + (
        "def main() -> None:\n    pass\n"
    )
    policy = _empty_clause_policy() if empty_clauses else _policy()

    assert (
        evaluate_policy(policy, _request(program), authorized_layers=APPROVED_LAYERS)
        is None
    )
    assert "target 'main' is bound more than once at module level" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize("empty_clauses", [False, True], ids=["nonempty", "empty"])
def test_module_import_validation_excludes_other_layer_operations(
    monkeypatch: pytest.MonkeyPatch, empty_clauses: bool
) -> None:
    observed: list[str] = []
    original = interp.check_module_level
    monkeypatch.setattr(layer_state, "LOCK_TIMEOUT", 0.02)

    def check_module_level(
        source: str,
        *,
        source_name: str,
        policy: ImportPolicy,
        import_roots: Iterable[Path] = (),
    ) -> None:
        roots = tuple(import_roots)

        def competing_operation() -> str:
            try:
                with layer_state.selected_layer(
                    trusted_roots=policy.trusted_roots, import_paths=roots
                ):
                    return "entered"
            except layer_state.LayerStateError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=1) as pool:
            observed.append(pool.submit(competing_operation).result(timeout=2))
        original(
            source,
            source_name=source_name,
            policy=policy,
            import_roots=roots,
        )

    monkeypatch.setattr(interp, "check_module_level", check_module_level)
    policy = _empty_clause_policy() if empty_clauses else _policy()

    decision = evaluate_policy(
        policy,
        _request(_program('    send_email("bob@example.com", "hi")\n')),
        authorized_layers=APPROVED_LAYERS,
    )

    assert decision is not None
    assert decision.decision == "auto_approved"
    assert observed and all("selection is busy" in message for message in observed)


def test_auto_pass_when_program_entails_the_conjunction() -> None:
    decision = evaluate_policy(
        _policy(),
        _request(_program('    send_email("bob@example.com", "hi")\n')),
        authorized_layers=APPROVED_LAYERS,
    )
    assert decision is not None
    assert decision.decision == "auto_approved"
    assert decision.rule == "auto-pass-internal"


def test_auto_pass_uses_entailment_not_contract_text() -> None:
    # The program states no guarantee of its own, yet a single literal send to an
    # allow-listed address entails only/unique/at_most. Structural name matching
    # would have missed this; entailment catches it.
    decision = evaluate_policy(
        _policy(),
        _request(_program('    send_email("ann@example.com", "hi")\n')),
        authorized_layers=APPROVED_LAYERS,
    )
    assert decision is not None
    assert decision.decision == "auto_approved"


def test_no_auto_pass_when_recipient_outside_allowlist() -> None:
    decision = evaluate_policy(
        _policy(),
        _request(_program('    send_email("stranger@other.com", "hi")\n')),
        authorized_layers=APPROVED_LAYERS,
    )
    # stranger is not blocked, so no deny; not allow-listed, so no auto-pass.
    assert decision is None


def test_deny_fires_when_avoidance_cannot_be_proven() -> None:
    decision = evaluate_policy(
        _policy(),
        _request(_program('    send_email("ceo@example.com", "hi")\n')),
        authorized_layers=APPROVED_LAYERS,
    )
    assert decision is not None
    assert decision.decision == "auto_rejected"
    assert decision.rule == "deny-blocked-recipients"


def test_deny_beats_allow_and_catches_a_reachable_block() -> None:
    body = (
        '    for addr in ["bob@example.com", "ceo@example.com"]:\n'
        '        send_email(addr, "hi")\n'
    )
    decision = evaluate_policy(
        _policy(), _request(_program(body)), authorized_layers=APPROVED_LAYERS
    )
    assert decision is not None
    assert decision.decision == "auto_rejected"


def test_non_dict_request_returns_none() -> None:
    assert evaluate_policy(_policy(), None, authorized_layers=APPROVED_LAYERS) is None
    assert (
        evaluate_policy(_policy(), {"program": ""}, authorized_layers=APPROVED_LAYERS)
        is None
    )


def test_load_approval_policy_from_file(tmp_path: Path) -> None:
    path = tmp_path / "approval-policy.json"
    path.write_text(json.dumps(_POLICY_DATA))
    policy = load_approval_policy(path)
    assert [rule.name for rule in policy.rules] == [
        "deny-blocked-recipients",
        "auto-pass-internal",
    ]
    assert policy.imports == (("pol", "tools.email.trusted.contracts"),)


def test_load_missing_policy_raises(tmp_path: Path) -> None:
    with pytest.raises(ApprovalPolicyError):
        load_approval_policy(tmp_path / "does-not-exist.json")


def test_invalid_decision_rejected() -> None:
    with pytest.raises(ApprovalPolicyError):
        approval_policy_from_dict(
            {
                "rules": [
                    {
                        "name": "bad",
                        "decision": "approved_once",
                        "when_proven": ["pol.only([])"],
                    }
                ]
            }
        )


def test_wrong_clause_key_for_decision_rejected() -> None:
    with pytest.raises(ApprovalPolicyError):
        approval_policy_from_dict(
            {
                "rules": [
                    {
                        "name": "deny",
                        "decision": "auto_rejected",
                        "when_proven": ["pol.none()"],
                    }
                ]
            }
        )


def test_malformed_clause_expression_rejected() -> None:
    with pytest.raises(ApprovalPolicyError):
        approval_policy_from_dict(
            {
                "imports": {"pol": "tools.email.trusted.contracts"},
                "rules": [
                    {
                        "name": "x",
                        "decision": "auto_approved",
                        "when_proven": ["pol.only(["],
                    }
                ],
            }
        )


def test_service_auto_approves_without_waiting() -> None:
    app = create_approval_app(policy=_policy(), authorized_layers=APPROVED_LAYERS)
    client = TestClient(app)
    response = client.post(
        "/requests",
        json={
            "request_id": "clr_pass",
            "program_sha256": "pass",
            "program": _program('    send_email("bob@example.com", "hi")\n'),
            "target": "main",
            "trusted_roots": [str(TRUSTED_ROOT)],
            "import_roots": [str(EMAIL_ROOT)],
        },
    )
    body = response.json()
    assert body["decision"] == "auto_approved"
    assert body["receipt"] == "auto-clr_pass"

    record = client.get("/requests/clr_pass").json()
    assert record["status"] == "approved"
    assert record["auto_decision"]["rule"] == "auto-pass-internal"

    detail = client.get("/ui/requests/clr_pass")
    assert "auto-pass-internal" in detail.text


def test_service_auto_rejects_without_waiting() -> None:
    app = create_approval_app(policy=_policy(), authorized_layers=APPROVED_LAYERS)
    client = TestClient(app)
    response = client.post(
        "/requests",
        json={
            "request_id": "clr_deny",
            "program_sha256": "deny",
            "program": _program('    send_email("ceo@example.com", "hi")\n'),
            "target": "main",
            "trusted_roots": [str(TRUSTED_ROOT)],
            "import_roots": [str(EMAIL_ROOT)],
        },
    )
    body = response.json()
    assert body["decision"] == "auto_rejected"
    assert "receipt" not in body
    assert client.get("/requests/clr_deny").json()["status"] == "denied"


def test_service_without_match_still_waits_for_user() -> None:
    app = create_approval_app(policy=_policy(), authorized_layers=APPROVED_LAYERS)
    client = TestClient(app)
    responses: list[dict[str, object]] = []

    def submit() -> None:
        responses.append(
            client.post(
                "/requests",
                json={
                    "request_id": "clr_ask",
                    "program_sha256": "ask",
                    "program": _program('    send_email("stranger@other.com", "hi")\n'),
                    "target": "main",
                    "trusted_roots": [str(TRUSTED_ROOT)],
                    "import_roots": [str(EMAIL_ROOT)],
                },
            ).json()
        )

    thread = threading.Thread(target=submit)
    thread.start()
    _wait_for_record(client, "clr_ask")

    assert client.get("/requests/clr_ask").json()["status"] == "pending"

    client.post(
        "/api/requests/clr_ask/decision",
        json={"decision": "approved_once", "receipt": "human"},
    )
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert responses[0]["decision"] == "approved_once"


def _wait_for_record(client: TestClient, request_id: str) -> None:
    for _ in range(50):
        if client.get(f"/requests/{request_id}").status_code == 200:
            return
        time.sleep(0.02)
    raise AssertionError(f"request {request_id} never appeared")
