from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from clauz3._vendor.deal_solver import Conclusion
from clauz3.policy import DomainPolicy
from clauz3.prover import ProofResult, prove_text

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"

REQUIRED_UNIQUE = [
    DomainPolicy(
        when_used=("send_email",),
        required=("unique_recipients",),
        domain_package="tools.email.trusted",
    )
]


def _prove(source: str) -> list[ProofResult]:
    return prove_text(
        source,
        target="main",
        import_paths=[EMAIL_ROOT],
        trusted_roots=[TRUSTED_ROOT],
    )


def _conclusions(proofs: list[ProofResult]) -> list[Conclusion]:
    return [proof.proof.conclusion for proof in proofs]


EMAILS_BOB_TWICE = """
import clauz3
from tools.email.trusted.effects import send_email


def main() -> None:
    send_email("bob@example.com", "one")
    send_email("bob@example.com", "two")
"""

EMAILS_BOB_ONCE = """
import clauz3
from tools.email.trusted.effects import send_email


def main() -> None:
    send_email("bob@example.com", "hi")
"""

NO_EMAIL = """
def main() -> None:
    pass
"""


def test_required_contract_rejects_unstated_violation(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies",
        lambda **_: REQUIRED_UNIQUE,
    )
    # required unique_recipients is violated
    assert _conclusions(_prove(EMAILS_BOB_TWICE)) == [Conclusion.FAIL]


def test_required_contract_passes_when_satisfied(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies",
        lambda **_: REQUIRED_UNIQUE,
    )
    assert _conclusions(_prove(EMAILS_BOB_ONCE)) == [Conclusion.OK]


def test_required_contract_not_triggered_when_effect_unused(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies",
        lambda **_: REQUIRED_UNIQUE,
    )
    assert _conclusions(_prove(NO_EMAIL)) == [Conclusion.OK]


def test_without_required_policy_unstated_violation_proves(
    monkeypatch: MonkeyPatch,
) -> None:
    # Same violating program, but the policy only recommends — no enforcement.
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies",
        lambda **_: [
            DomainPolicy(
                when_used=("send_email",),
                recommended=("unique_recipients",),
                domain_package="tools.email.trusted",
            )
        ],
    )
    assert _conclusions(_prove(EMAILS_BOB_TWICE)) == [Conclusion.OK]


def test_monitored_required_policy_reports_submitted_locations(
    monkeypatch: MonkeyPatch,
) -> None:
    import ast
    import time

    from clauz3.diagnostics import proof

    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies", lambda **_: REQUIRED_UNIQUE
    )
    source = '''"""A submitted module docstring."""
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
        """Preserve this indentation and the comment below in source positions."""
        # The proved copy will discard this comment and normalize whitespace.
        send_email("bob@example.com", "hello")
        assert True
'''
    tree = ast.parse(source)
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "send_email"
    )
    assertion = next(node for node in ast.walk(tree) if isinstance(node, ast.Assert))
    guarantee = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "only"
    )
    events: list[dict[str, object]] = []
    with proof.monitoring(proof.Monitor(time.monotonic() + 30, events.append)):
        assert _conclusions(_prove(source)) == [Conclusion.OK]
    locations = [event for event in events if "description" in event]
    effect = next(event for event in locations if "call" in event)
    assert effect["source_kind"] == "submitted"
    assert effect["line"] == call.lineno == 11
    assert effect["column"] == call.col_offset + 1 == 9
    explicit_assert = next(
        event for event in locations if event.get("predicate") == "True"
    )
    assert explicit_assert["line"] == assertion.lineno
    assert explicit_assert["column"] == assertion.col_offset + 1
    explicit_guarantee = next(
        event
        for event in locations
        if str(event.get("predicate", "")).startswith("emails.only")
    )
    assert explicit_guarantee["line"] == guarantee.lineno
    assert explicit_guarantee["column"] == guarantee.col_offset + 1
    required = next(
        event for event in locations if event.get("source_kind") == "required_policy"
    )
    assert "unique_recipients" in str(required["predicate"])
    assert "line" not in required and "column" not in required
    # The context must not leak into a subsequent ordinary proof.
    events.clear()
    monkeypatch.setattr("clauz3.prover.load_domain_policies", lambda **_: [])
    with proof.monitoring(proof.Monitor(time.monotonic() + 30, events.append)):
        assert _conclusions(_prove(source)) == [Conclusion.OK]
    assert next(event for event in events if "call" in event)["line"] == 11


def test_unmonitored_required_policy_body_error_uses_submitted_location(
    monkeypatch: MonkeyPatch,
) -> None:
    from clauz3.diagnostics import proof

    assert not proof.active()
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies", lambda **_: REQUIRED_UNIQUE
    )
    source = """import clauz3
from tools.email.trusted.effects import send_email


# These comments and blank lines disappear from the injected proof copy.
# An unmonitored CLI proof must still point to the submitted call below.


def main() -> None:
    send_email("bob@example.com", "hello")

    # Unsupported body operation, not a bad guarantee.
    print("hello".splitlines())
"""
    result = _prove(source)[0].proof
    assert result.conclusion == Conclusion.SKIP
    assert result.description == "unsupported program body"
    assert "at line 13:" in str(result.error)
    assert "attribute is outside the interpreter surface" in str(result.error)
    assert "'hello'.splitlines" in str(result.error)


def test_unmonitored_required_policy_marks_unmapped_body_error_as_generated(
    monkeypatch: MonkeyPatch,
) -> None:
    from clauz3.diagnostics import proof

    assert not proof.active()
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies", lambda **_: REQUIRED_UNIQUE
    )
    source = EMAILS_BOB_ONCE + "    while False:\n        pass\n"
    result = _prove(source)[0].proof
    assert result.conclusion == Conclusion.SKIP
    assert result.description == "unsupported program body"
    assert "in generated_proof:" in str(result.error)
    assert "at line" not in str(result.error)
