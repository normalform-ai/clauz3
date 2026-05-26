from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

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


def _ok(proofs: list[ProofResult]) -> bool:
    return all(proof.ok for proof in proofs)


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
    proofs = _prove(EMAILS_BOB_TWICE)
    assert proofs
    assert not _ok(proofs)  # required unique_recipients is violated


def test_required_contract_passes_when_satisfied(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies",
        lambda **_: REQUIRED_UNIQUE,
    )
    assert _ok(_prove(EMAILS_BOB_ONCE))


def test_required_contract_not_triggered_when_effect_unused(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies",
        lambda **_: REQUIRED_UNIQUE,
    )
    assert _ok(_prove(NO_EMAIL))


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
    assert _ok(_prove(EMAILS_BOB_TWICE))
