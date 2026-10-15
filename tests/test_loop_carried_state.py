"""Loop-carried state in a quantified for-loop must not prove.

The quantified path in ``deal_solver._eval_stmt.eval_for`` binds the loop
variable to ``rows[i]`` for a single symbolic ``i`` and evaluates the body
once. That is sound for a body whose every statement is a function of the
current row, and unsound the moment the body remembers anything: a counter is
always ``1``, an accumulator holds one row's contribution, and a name the body
rebinds keeps its first-iteration value after the loop -- including on the
zero-row path, where at runtime the loop body never runs at all.

Each ``*_fail`` case below is one the prover reported ``proved!`` for while the
interpreter, running the same source with real rows, violated the very
guarantee that was proved (issue #123). They must now come back SKIP, which is
the fail-closed answer: the prover says it cannot model the program rather than
vouching for it. The ``*_pass`` cases pin what the refusal must not swallow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProofResult, prove_path

FIXTURES = Path(__file__).resolve().parent / "fixtures/loop_email"
TRUSTED = FIXTURES / "qtrusted"

#: Programs whose guarantee the single-iteration model used to discharge.
UNSOUND = [
    # A counter guards the effect: `n` is symbolically always 1, so `n > 2` is
    # dead and `at_most(0)` holds. Five rows send three emails.
    "counter_over_loop_fail.py",
    # After the loop `target` is unconditionally `rows[i].email`. Zero rows
    # leave it at the attacker-controlled literal.
    "post_loop_rebind_fail.py",
    # `total` is one row's contribution, not the sum over up to 100 rows.
    "accumulator_over_loop_fail.py",
    # The body rebinds the loop variable, so it no longer talks about the row
    # the quantifier ranges over.
    "loop_var_rebind_fail.py",
    # Bound inside the body, read after the loop: no value on the zero-row
    # path, and no particular iteration on any other.
    "post_loop_body_local_fail.py",
    # Same, for the loop variable Python leaks and the prover cannot.
    "post_loop_loop_var_fail.py",
    # The accumulator one loop deeper: a property of the body, not its top
    # level.
    "nested_accumulator_fail.py",
]


def _prove(case_name: str) -> ProofResult:
    results = prove_path(
        FIXTURES / "cases" / case_name,
        trusted_roots=[TRUSTED],
        import_roots=[FIXTURES],
    )
    (result,) = results
    return result


@pytest.mark.parametrize("case_name", UNSOUND)
def test_loop_carried_state_is_refused_not_proved(case_name: str) -> None:
    """Every one of these reported ``proved!`` before issue #123."""
    result = _prove(case_name)
    assert result.proof.conclusion is Conclusion.SKIP, (
        f"{case_name}: expected a skip, got {result.proof.conclusion}"
    )
    assert not result.ok


def test_refusal_names_the_variable_that_carries_the_state() -> None:
    """The skip has to be actionable, so it names the offending binding."""
    result = _prove("accumulator_over_loop_fail.py")
    assert "'total'" in str(result.proof.error)


def test_body_local_temporary_is_still_allowed() -> None:
    """A per-row temporary read only inside the body is a function of the row.

    Refusing this too would cost the quantified path most of its usefulness,
    so the check is about *carried* state, not about assignment.
    """
    assert _prove("body_local_temp_pass.py").proof.conclusion is Conclusion.OK


def test_unrolled_loops_keep_their_accumulators() -> None:
    """``range(N)`` and literal sequences are unrolled, not quantified.

    Concrete unrolling re-evaluates the body per iteration, so loop-carried
    state is modelled exactly there and must keep working -- and must still
    report a real FAIL, not a skip, when the bound is exceeded. The fix is
    scoped to the quantified path.
    """
    assert _prove("unrolled_accumulator_pass.py").proof.conclusion is Conclusion.OK
    assert _prove("unrolled_accumulator_fail.py").proof.conclusion is Conclusion.FAIL
