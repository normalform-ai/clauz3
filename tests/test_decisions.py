"""Issue #62: the approval decision vocabulary is closed.

An integrator posted ``{"decision": "approved"}`` to the decision endpoint. The
service stored it, the runner saw a string outside the approved set, and the run
was denied with a transcript that read ``approval: approved``. No boundary may
map an unknown decision to either outcome: the enum, the run client, the mock
service and its CLI, and the policy loader must each refuse it with an error
naming the valid strings. The service endpoint and the profile store are
covered in ``test_approval_service.py`` and ``test_approval_profiles.py``.
"""

from __future__ import annotations

import doctest
import io
import json
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

import clauz3.approval
import clauz3.approval_policy
from clauz3.approvals import client as approval_client
from clauz3.approvals import decisions
from clauz3.approvals.client import (
    ApprovalResponse,
    ApprovalServiceError,
    MockApprovalServer,
    approval_response_from_config,
    submit_approval_request,
)
from clauz3.approvals.decisions import (
    APPROVED_DECISIONS,
    AUTO_DECISIONS,
    REMEMBERABLE_DECISIONS,
    USER_DECISIONS,
    Decision,
    UnknownDecisionError,
    is_approved,
    parse_decision,
)
from clauz3.approvals.policy import (
    ApprovalPolicyError,
    PolicyDecision,
    Rule,
    approval_policy_from_dict,
)
from clauz3.cli import main


@pytest.mark.parametrize("module", [decisions, approval_client])
def test_doctests(module: ModuleType) -> None:
    result = doctest.testmod(module, optionflags=doctest.ELLIPSIS)
    assert result.attempted > 0
    assert result.failed == 0


#: Near-misses an integrator might plausibly send, plus non-strings.
UNKNOWN: list[object] = [
    "approved",
    "approve",
    "aprove",
    "Approved_Once",
    "APPROVED_ONCE",
    " approved_once",
    "approved_once ",
    "reject",
    "denied",
    "",
    None,
    1,
    ["approved_once"],
]


# -- the enum and parser -------------------------------------------------------


@pytest.mark.parametrize("value", UNKNOWN)
def test_parse_decision_refuses_unknown_values(value: object) -> None:
    with pytest.raises(UnknownDecisionError, match="is not one of"):
        parse_decision(value)
    assert not is_approved(value)


@pytest.mark.parametrize("decision", list(Decision))
def test_parse_decision_accepts_exact_spelling_only(decision: Decision) -> None:
    assert parse_decision(decision.value) is decision
    with pytest.raises(UnknownDecisionError):
        parse_decision(decision.value.upper())


def test_error_names_the_value_and_lists_only_the_allowed_strings() -> None:
    with pytest.raises(UnknownDecisionError) as info:
        parse_decision("approved", allowed=USER_DECISIONS)
    message = str(info.value)
    assert "'approved'" in message
    for decision in USER_DECISIONS:
        assert decision.value in message
    for decision in AUTO_DECISIONS:
        assert decision.value not in message


@pytest.mark.parametrize("decision", sorted(AUTO_DECISIONS))
def test_policy_decisions_are_not_user_decisions(decision: Decision) -> None:
    with pytest.raises(UnknownDecisionError):
        parse_decision(decision.value, allowed=USER_DECISIONS)


def test_partitions_cover_the_vocabulary() -> None:
    assert {decision for decision in Decision if decision.approved} == set(
        APPROVED_DECISIONS
    )
    assert {
        Decision.APPROVED_ONCE,
        Decision.APPROVED_REMEMBER,
        Decision.AUTO_APPROVED,
    } == APPROVED_DECISIONS
    assert frozenset(Decision) == USER_DECISIONS | AUTO_DECISIONS
    assert not USER_DECISIONS & AUTO_DECISIONS
    assert REMEMBERABLE_DECISIONS <= USER_DECISIONS
    assert Decision.REQUEST_MORE not in REMEMBERABLE_DECISIONS


def test_compatibility_modules_keep_their_names() -> None:
    # AGENTS.md: the top-level approval* modules are stable embedding imports.
    assert {
        "approved_once",
        "approved_remember",
        "auto_approved",
    } == clauz3.approval.APPROVED_DECISIONS
    assert clauz3.approval.APPROVED_DECISIONS is APPROVED_DECISIONS
    assert {"auto_approved", "auto_rejected"} == clauz3.approval_policy.AUTO_DECISIONS


# -- typed values --------------------------------------------------------------


def test_typed_values_refuse_unknown_strings_from_untyped_callers() -> None:
    with pytest.raises(UnknownDecisionError):
        ApprovalResponse(decision=cast(Decision, "approved"))
    with pytest.raises(UnknownDecisionError):
        Rule("allow", cast(Decision, "approved"), ())
    with pytest.raises(UnknownDecisionError):
        Rule("allow", Decision.APPROVED_ONCE, ())
    with pytest.raises(UnknownDecisionError):
        PolicyDecision(decision=Decision.REJECTED, rule="r", reason=None)
    coerced = ApprovalResponse(decision=cast(Decision, "approved_once"))
    assert coerced.decision is Decision.APPROVED_ONCE
    assert coerced.approved


# -- the policy loader ---------------------------------------------------------


@pytest.mark.parametrize("value", ["approved", "approved_once", "Auto_Approved", None])
def test_policy_rule_refuses_non_policy_decisions(value: object) -> None:
    data = {"rules": [{"name": "r", "decision": value, "when_proven": ["x.y()"]}]}
    with pytest.raises(ApprovalPolicyError, match="rule 'r': decision") as info:
        approval_policy_from_dict(data)
    assert "auto_approved" in str(info.value)
    assert "approved_once" not in str(info.value).split("is not one of")[1]


def test_policy_rule_decision_is_typed() -> None:
    data = {
        "rules": [{"name": "r", "decision": "auto_rejected", "unless_proven": ["x()"]}]
    }
    (rule,) = approval_policy_from_dict(data).rules
    assert rule.decision is Decision.AUTO_REJECTED


# -- the run client ------------------------------------------------------------


def _serve_payload(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode()
    monkeypatch.setattr(
        approval_client, "urlopen", lambda request, timeout=0.0: io.BytesIO(body)
    )


@pytest.mark.parametrize("value", ["approved", "Approved_Once", "", None])
def test_client_refuses_unknown_decision_instead_of_denying(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    _serve_payload(monkeypatch, {"decision": value, "request_id": "clr_x"})
    with pytest.raises(ApprovalServiceError, match="invalid approval response"):
        submit_approval_request("http://127.0.0.1:1", {"program_sha256": "abc"})


def test_client_returns_a_typed_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_payload(monkeypatch, {"decision": "request_more", "request_id": "clr_x"})
    response = submit_approval_request("http://127.0.0.1:1", {"program_sha256": "abc"})
    assert response.decision is Decision.REQUEST_MORE
    assert not response.approved


def test_run_exits_2_on_an_unknown_service_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The runner reports a protocol error, not "approval: approved" and exit 3.
    _serve_payload(monkeypatch, {"decision": "approved", "request_id": "clr_x"})
    monkeypatch.setenv("CLAUZ3_APPROVAL_SERVICE", "http://127.0.0.1:1")
    program = tmp_path / "program.py"
    program.write_text("def main() -> None:\n    pass\n")

    result = main(["run", str(program)])

    err = capsys.readouterr().err
    assert result == 2, err
    assert "invalid approval response: decision 'approved' is not one of" in err
    assert "approval: approved" not in err


# -- the mock service and its CLI ----------------------------------------------


@pytest.mark.parametrize("value", ["approved", "Approved_Once", "", 1])
def test_mock_server_refuses_unknown_scripted_decision_at_startup(
    value: object,
) -> None:
    with pytest.raises(UnknownDecisionError):
        MockApprovalServer(config={"decision": value})


def test_mock_config_defaults_to_approved_once() -> None:
    response = approval_response_from_config(
        config={}, request_payload={"program_sha256": "abc"}
    )
    assert response["decision"] == "approved_once"
    assert "receipt" in response


def test_mock_approval_service_cli_exits_2_on_unknown_decision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "approval.json"
    config.write_text('{"decision": "approved"}\n')

    result = main(["mock-approval-service", "--config", str(config), "--port", "0"])

    assert result == 2
    err = capsys.readouterr().err
    assert "decision 'approved' is not one of" in err
    assert "approved_once" in err
