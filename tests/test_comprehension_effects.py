"""Issue #67: trusted side effects inside list comprehensions.

The prover evaluates a comprehension body in a child context; the child's
facts/preconditions/exceptions must never be silently discarded, or every
effect guarantee can be defeated by wrapping the call in a comprehension.
v1 does not model per-iteration comprehension effects, so any trusted fact,
precondition, or exception produced inside a comprehension maps to
UnsupportedError (SKIP/PARTIAL -> blocked), never OK.
"""

from pathlib import Path

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProofResult, prove_text

ROOT = Path(__file__).resolve().parents[1]
HOME_ROOT = ROOT / "examples/home-automation"
TRUSTED_ROOT = HOME_ROOT / "tools/home/trusted"

HEADER = """\
import clauz3
from tools.home.trusted import contracts as home
from tools.home.trusted.effects import order_grocery, unlock_door
"""


def _prove(body: str) -> ProofResult:
    results = prove_text(
        HEADER + body,
        import_paths=[HOME_ROOT],
        trusted_roots=[TRUSTED_ROOT],
    )
    assert len(results) == 1
    return results[0]


def test_direct_effect_fails_guarantee() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    unlock_door("front")
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_effect_inside_list_comp_does_not_prove() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    [unlock_door("front") for _x in [1]]
""")
    assert result.proof.conclusion is Conclusion.SKIP


def test_effect_inside_list_comp_if_does_not_prove() -> None:
    # The `if` clause is evaluated in a separate body_ctx (_compr_apply_ifs)
    # with its own _compr_reject_effects guard — a distinct path from node.elt.
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    _ys = [x for x in [1, 2] if unlock_door("front")]
""")
    assert result.proof.conclusion is Conclusion.SKIP


def test_precondition_inside_list_comp_does_not_prove() -> None:
    result = _prove("""
def main() -> None:
    [order_grocery("milk", -5, -5) for _x in [1]]
""")
    assert result.proof.conclusion is Conclusion.SKIP


def test_pure_list_comp_still_proves() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    _ys = [x + 1 for x in [1, 2, 3]]
""")
    assert result.ok


def test_effect_inside_lambda_does_not_prove() -> None:
    # Same #67 unsoundness reached through a lambda: LambdaSort.m_call evaluates
    # the body in a child context whose facts are never merged back.
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    f = lambda: unlock_door("front")
    f()
""")
    assert result.proof.conclusion is Conclusion.SKIP


def test_precondition_inside_lambda_does_not_prove() -> None:
    result = _prove("""
def main() -> None:
    f = lambda: order_grocery("milk", -5, -5)
    f()
""")
    assert result.proof.conclusion is Conclusion.SKIP


def test_pure_lambda_still_proves() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    f = lambda x: x + 1
    _y = f(2)
""")
    assert result.ok
