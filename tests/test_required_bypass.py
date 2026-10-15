"""Issue #72: required-contract injection must not be bypassable.

``_inject_required_guarantees`` decided whether to conjoin a required (TLE-
mandated) contract by a syntactic scan of the *target function only*, matching
trusted function *names*. Two bypasses silently dropped the obligation while
the effect still executed:

1. Indirection — a trusted call reached through an agent-defined helper escaped
   ``calls_in(target)``.
2. Marker mismatch — a ``when_used`` that names a ``@deal.has(...)`` marker
   never matched a function name.

The injection must resolve calls transitively through helpers, and a
``when_used`` token that matches nothing must fail loudly rather than silently
drop enforcement.

The second bypass is closed from the other end now. An effect is addressed by
its function name (see :mod:`clauz3.effects`), markers are deal's and clauz3
does not read them, so a marker-named ``when_used`` is an unknown token — and
an unknown token is a config error. The property #72 asked for is unchanged:
the obligation is never quietly dropped.
"""

from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from clauz3._vendor.deal_solver import Conclusion
from clauz3.policy import DomainPolicy, transitive_calls
from clauz3.prover import ProofResult, ProverConfigError, prove_text

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
# when_used names a `@deal.has(...)` marker rather than the effect. It used to
# resolve to whichever functions carried the string; it is now refused.
REQUIRED_BY_MARKER = [
    DomainPolicy(
        when_used=("trusted",),
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


BOB_TWICE_VIA_HELPER = """
import clauz3
from tools.email.trusted.effects import send_email


def deliver() -> None:
    send_email("bob@example.com", "one")
    send_email("bob@example.com", "two")


def main() -> None:
    deliver()
"""

BOB_ONCE_VIA_HELPER = """
import clauz3
from tools.email.trusted.effects import send_email


def deliver() -> None:
    send_email("bob@example.com", "hi")


def main() -> None:
    deliver()
"""

BOB_TWICE_DIRECT = """
import clauz3
from tools.email.trusted.effects import send_email


def main() -> None:
    send_email("bob@example.com", "one")
    send_email("bob@example.com", "two")
"""

# Renamed import: the prover inlines the real send_email by inference and runs
# the effect, but trigger detection keys on the call name `se`.
BOB_TWICE_ALIASED = """
import clauz3
from tools.email.trusted.effects import send_email as se


def main() -> None:
    se("bob@example.com", "one")
    se("bob@example.com", "two")
"""

BOB_ONCE_ALIASED = """
import clauz3
from tools.email.trusted.effects import send_email as se


def main() -> None:
    se("bob@example.com", "hi")
"""

BOB_TWICE_ALIASED_VIA_HELPER = """
import clauz3
from tools.email.trusted.effects import send_email as se


def deliver() -> None:
    se("bob@example.com", "one")
    se("bob@example.com", "two")


def main() -> None:
    deliver()
"""


# ── unit: transitive call resolution ──────────────────────────────────────────


def test_transitive_calls_through_helper() -> None:
    used = transitive_calls(BOB_TWICE_VIA_HELPER, "main")
    assert "send_email" in used


def test_transitive_calls_resolves_import_alias() -> None:
    # `se` must resolve to the real trailing name `send_email`.
    used = transitive_calls(BOB_TWICE_ALIASED, "main")
    assert "send_email" in used


# ── Bypass 3: function-name import alias ──────────────────────────────────────


def test_aliased_import_still_enforces_required(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies", lambda **_: REQUIRED_UNIQUE
    )
    assert _conclusions(_prove(BOB_TWICE_ALIASED)) == [Conclusion.FAIL]


def test_aliased_import_via_helper_enforces_required(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies", lambda **_: REQUIRED_UNIQUE
    )
    assert _conclusions(_prove(BOB_TWICE_ALIASED_VIA_HELPER)) == [Conclusion.FAIL]


def test_aliased_import_marker_is_refused(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies", lambda **_: REQUIRED_BY_MARKER
    )
    with pytest.raises(ProverConfigError, match="name no trusted effect"):
        _prove(BOB_TWICE_ALIASED)


def test_aliased_import_passes_when_satisfied(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies", lambda **_: REQUIRED_UNIQUE
    )
    assert _conclusions(_prove(BOB_ONCE_ALIASED)) == [Conclusion.OK]


# ── Bypass 1: helper indirection ──────────────────────────────────────────────


def test_helper_indirection_still_enforces_required(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies", lambda **_: REQUIRED_UNIQUE
    )
    assert _conclusions(_prove(BOB_TWICE_VIA_HELPER)) == [Conclusion.FAIL]


def test_helper_indirection_passes_when_satisfied(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies", lambda **_: REQUIRED_UNIQUE
    )
    assert _conclusions(_prove(BOB_ONCE_VIA_HELPER)) == [Conclusion.OK]


# ── Bypass 2: when_used names a marker ────────────────────────────────────────


def test_marker_when_used_is_refused(monkeypatch: MonkeyPatch) -> None:
    """A marker is not a token clauz3 knows, and an unknown token fails closed.

    The enforcement #72 wanted is what matters, not the mechanism: the policy
    cannot silently stop enforcing. It now says so at configuration time, and
    names the effects it does know, instead of resolving the string against
    whatever markers a trusted layer happened to declare.
    """

    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies", lambda **_: REQUIRED_BY_MARKER
    )
    with pytest.raises(ProverConfigError, match="name no trusted effect"):
        _prove(BOB_TWICE_DIRECT)


# ── fail loudly on an unknown when_used token ─────────────────────────────────


def test_unknown_when_used_token_fails_loudly(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies",
        lambda **_: [
            DomainPolicy(
                when_used=("no_such_effect",),
                required=("unique_recipients",),
                domain_package="tools.email.trusted",
            )
        ],
    )
    with pytest.raises(ProverConfigError):
        _prove(BOB_TWICE_DIRECT)


# ── a when_used token that is also a marker is ambiguous ──────────────────────

# Two effects carrying one marker: what a pre-migration ``when_used="send"``
# reached, and what it no longer does.
AMBIGUOUS_EFFECTS = """
import clauz3
import deal


@clauz3.trusted
@deal.has("send")
def send(addr: str) -> None:
    pass


@clauz3.trusted
@deal.has("send")
def send_bulk(addr: str) -> None:
    pass
"""

AMBIGUOUS_CONTRACTS = """
from clauz3.spec import ContractSpec, contract, effect

Sends = effect("send", "send_bulk")


@contract
def only_bob() -> ContractSpec:
    return Sends.all(lambda e: e.addr == "bob@example.com")
"""

BULK_TO_EVE = """
import clauz3
from {pkg}.effects import send_bulk


def main() -> None:
    send_bulk("eve@example.com")
"""


def _ambiguous_layer(tmp_path: Path) -> tuple[str, Path]:
    pkg = tmp_path.name.replace("-", "_")
    package = tmp_path / pkg
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(AMBIGUOUS_EFFECTS)
    (package / "contracts.py").write_text(AMBIGUOUS_CONTRACTS)
    return pkg, package


def _prove_ambiguous(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    *,
    when_used: tuple[str, ...],
) -> list[ProofResult]:
    pkg, package = _ambiguous_layer(tmp_path)
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies",
        lambda **_: [
            DomainPolicy(
                when_used=when_used,
                required=("only_bob",),
                domain_package=pkg,
            )
        ],
    )
    return prove_text(
        BULK_TO_EVE.format(pkg=pkg),
        target="main",
        import_paths=[tmp_path],
        trusted_roots=[package],
    )


def test_a_when_used_that_is_also_a_marker_is_refused(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """``when_used="send"`` used to reach every effect carrying that marker.

    It resolves to the *effect* called ``send`` now, so the obligation quietly
    stopped covering ``send_bulk`` --- and this program, which bulk-mails eve
    under a "only bob" requirement, went from refuted to proved. The token
    means two different things depending on when the policy was written, so it
    is refused rather than read either way.
    """

    with pytest.raises(ProverConfigError, match="a @deal.has marker carried by"):
        _prove_ambiguous(tmp_path, monkeypatch, when_used=("send",))


def test_naming_both_effects_restores_the_obligation(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The fix the refusal asks for: say which effects the obligation covers."""

    proofs = _prove_ambiguous(tmp_path, monkeypatch, when_used=("send", "send_bulk"))
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]


def test_a_marker_matching_only_its_own_effect_is_not_ambiguous(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Nothing narrowed, so there is nothing to refuse."""

    pkg, package = _ambiguous_layer(tmp_path)
    (package / "effects.py").write_text(
        AMBIGUOUS_EFFECTS.replace('@deal.has("send")\ndef send_bulk', "def send_bulk")
    )
    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies",
        lambda **_: [
            DomainPolicy(
                when_used=("send",),
                required=("only_bob",),
                domain_package=pkg,
            )
        ],
    )
    proofs = prove_text(
        BULK_TO_EVE.format(pkg=pkg),
        target="main",
        import_paths=[tmp_path],
        trusted_roots=[package],
    )
    assert _conclusions(proofs) == [Conclusion.OK], (
        "send_bulk is outside the trigger, and always was"
    )


# ── a recommended-only policy is validated too ───────────────────────────────


def test_a_recommended_only_policy_with_an_unknown_trigger_is_refused(
    monkeypatch: MonkeyPatch,
) -> None:
    """Its failure is quieter than a required one's, not smaller.

    A ``recommended``-only policy keyed on a marker stops matching anything, so
    its domain drops out of the coverage report rather than appearing there as
    a ``silent_gap`` --- and the human approving the run is never told the
    program used it. The validation used to run only for policies carrying
    ``required``, which left exactly this case silent.
    """

    monkeypatch.setattr(
        "clauz3.prover.load_domain_policies",
        lambda **_: [
            DomainPolicy(
                when_used=("trusted",),  # a @deal.has marker, not an effect
                recommended=("unique_recipients",),
                domain_package="tools.email.trusted",
            )
        ],
    )
    with pytest.raises(ProverConfigError, match="name no trusted effect"):
        _prove(BOB_TWICE_DIRECT)


def test_a_recommended_policy_naming_an_effect_still_proves(
    monkeypatch: MonkeyPatch,
) -> None:
    """The control: a trigger that names an effect is not refused."""

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
    assert _conclusions(_prove(BOB_TWICE_DIRECT)) == [Conclusion.OK], (
        "recommended is advice, not an obligation"
    )
