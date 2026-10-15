"""Regressions for function declaration and call control flow."""

from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProofResult, prove_text

ROOT = Path(__file__).resolve().parents[1]
HOME_ROOT = ROOT / "examples/home-automation"
TRUSTED_ROOT = HOME_ROOT / "tools/home/trusted"

HEADER = """\
import clauz3
from tools.home.trusted import contracts as home
from tools.home.trusted.effects import unlock_door
"""

DB_ROOT = ROOT / "examples/email-from-db"
DB_TRUSTED_ROOT = DB_ROOT / "tools/db/trusted"

DB_HEADER = """\
import clauz3
from tools.db.trusted import contracts as db
from tools.db.trusted.effects import db_query, send_email
"""


def _prove(body: str) -> ProofResult:
    results = prove_text(
        HEADER + body,
        import_paths=[HOME_ROOT],
        trusted_roots=[TRUSTED_ROOT],
    )
    assert len(results) == 1
    return results[0]


def _prove_bare(source: str) -> ProofResult:
    results = prove_text(source)
    assert len(results) == 1
    return results[0]


def _prove_db(body: str) -> ProofResult:
    results = prove_text(
        DB_HEADER + body,
        import_paths=[DB_ROOT],
        trusted_roots=[DB_TRUSTED_ROOT],
    )
    assert len(results) == 1
    return results[0]


def test_nested_function_return_does_not_interrupt_enclosing_function() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    def unused() -> int:
        return 0

    unlock_door("front")
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_unused_nested_function_is_inert() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    def unused() -> int:
        return 0
""")
    assert result.ok


def test_called_nested_function_records_effects() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    def helper() -> int:
        unlock_door("front")
        return 0

    helper()
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_nested_function_declaration_rebinds_its_name() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    helper = lambda: None

    def helper() -> None:
        unlock_door("front")

    helper()
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_called_nested_function_return_does_not_interrupt_caller() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    def helper() -> int:
        return 0

    helper()
    unlock_door("front")
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_direct_recursion_fails_closed() -> None:
    result = _prove("""
def helper(n: int) -> int:
    if n > 0:
        return helper(n - 1)
    unlock_door("front")
    return 0


@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    helper(1)
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert str(result.proof.error) == (
        "recursive calls are not supported at line 7: helper(n - 1)"
    )


def test_mutual_recursion_fails_closed() -> None:
    result = _prove("""
def first(n: int) -> int:
    if n > 0:
        return second(n - 1)
    unlock_door("front")
    return 0


def second(n: int) -> int:
    return first(n)


@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    first(1)
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert str(result.proof.error) == (
        "recursive calls are not supported at line 13: first(n)"
    )


def test_callee_trusted_precondition_is_required() -> None:
    """A trusted precondition violated inside a callee still fails the proof.

    ``unlock_door`` requires ``len(door) > 0``. Calling it through a helper must
    not launder that obligation away.
    """
    result = _prove("""
@clauz3.guarantee(home.no_guarantees())
def main() -> None:
    def notify() -> None:
        unlock_door("")

    notify()
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_callee_assertion_is_required() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_guarantees())
def main(n: int) -> None:
    def helper(k: int) -> int:
        assert k > 0
        return k

    helper(n)
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_callee_exception_is_reported() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_guarantees())
def main() -> None:
    def boom() -> int:
        raise ValueError("boom")

    boom()
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_effect_in_callee_keeps_enclosing_loop_quantifier() -> None:
    """An effect emitted inside a callee is quantified over the enclosing loop.

    ``db_query`` may return up to 100 rows, so routing the send through a helper
    must not collapse the count to a single occurrence.
    """
    result = _prove_db("""
@clauz3.guarantee(db.count_at_most(1))
def main() -> None:
    def notify(addr: str) -> None:
        send_email(addr, "hi")

    for row in db_query("users"):
        notify(row.email)
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_nested_def_bound_in_both_branches_fails_closed() -> None:
    """A name bound to a function in both branches has no z3 representation.

    Merging it must fail closed rather than raise ``AttributeError`` out of the
    prover.
    """
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main(flag: bool) -> None:
    if flag:
        def helper() -> None:
            unlock_door("front")
    else:
        def helper() -> None:
            pass

    helper()
""")
    assert result.proof.conclusion is Conclusion.SKIP


def test_function_value_assigned_in_both_branches_fails_closed() -> None:
    result = _prove("""
def unlocker() -> None:
    unlock_door("front")


def inert() -> None:
    pass


@clauz3.guarantee(home.no_unlocks())
def main(flag: bool) -> None:
    if flag:
        action = unlocker
    else:
        action = inert

    action()
""")
    assert result.proof.conclusion is Conclusion.SKIP


def test_callee_assumption_does_not_leak_past_a_caller_return() -> None:
    """A skipped call's postcondition must not become a global assumption.

    With ``flag=True`` execution stops at the assert and ``wrapper`` never
    runs, so ``inner``'s ``not flag`` guarantee says nothing about this path.
    Copying it unguarded lets it discharge the very assertion that fails.
    """
    result = _prove_bare("""\
import deal


@deal.pre(lambda flag: not flag)
@deal.ensure(lambda flag, result: not flag)
def inner(flag: bool) -> int:
    return 1


def wrapper(flag: bool) -> None:
    inner(flag)


def main(flag: bool) -> int:
    assert not flag
    if flag:
        return 0
    wrapper(flag)
    return 1
""")
    assert result.proof.conclusion is Conclusion.FAIL


_EMAIL_ROOT = ROOT / "examples/email"

_REACHABILITY_BODY = """
@clauz3.guarantee(emails.same_content("a@example.com", "b@example.com"))
def main(stop: bool) -> int:
{body}
"""

_VIA_HELPER = """\
    def notify() -> None:
        send_email("a@example.com", "hi")
        send_email("b@example.com", "hi")

    if stop:
        return 0
    notify()
    return 1"""

_INLINE = """\
    if stop:
        return 0
    send_email("a@example.com", "hi")
    send_email("b@example.com", "hi")
    return 1"""


def _prove_email(body: str) -> ProofResult:
    source = (
        "import clauz3\n"
        "from tools.email.trusted import contracts as emails\n"
        "from tools.email.trusted.effects import send_email\n"
        + _REACHABILITY_BODY.format(body=body)
    )
    results = prove_text(
        source,
        import_paths=[_EMAIL_ROOT],
        trusted_roots=[_EMAIL_ROOT / "tools/email/trusted"],
    )
    assert len(results) == 1
    return results[0]


def test_callee_effect_is_not_emitted_after_a_caller_return() -> None:
    """A helper called after an early return emits nothing on the taken path.

    ``same_content`` requires both addresses to receive an identical message.
    With ``stop=True`` neither send happens, so the guarantee is violated —
    which the inline form reports. Routing the sends through a helper must not
    invent effects on a path where the helper never runs.
    """
    assert _prove_email(_INLINE).proof.conclusion is Conclusion.FAIL
    assert _prove_email(_VIA_HELPER).proof.conclusion is Conclusion.FAIL


_BRANCH_QUERY_THEN = """
@clauz3.guarantee(db.count_at_most(100))
def main(flag: bool) -> None:
    if flag:
        for row in db_query("users"):
            send_email(row.email, "hi")
"""

_BRANCH_QUERY_ELSE = """
@clauz3.guarantee(db.count_at_most(100))
def main(flag: bool) -> None:
    if flag:
        send_email("a@example.com", "hi")
    else:
        for row in db_query("users"):
            send_email(row.email, "hi")
"""

_BRANCH_QUERY_BOTH = """
@clauz3.guarantee(db.count_at_most(200))
def main(flag: bool) -> None:
    if flag:
        for row in db_query("users"):
            send_email(row.email, "hi")
    else:
        for admin in db_query("admins"):
            send_email(admin.email, "hi")
"""


@pytest.mark.parametrize(
    "body",
    [_BRANCH_QUERY_THEN, _BRANCH_QUERY_ELSE, _BRANCH_QUERY_BOTH],
    ids=["then", "else", "both"],
)
def test_branch_keeps_a_trusted_postcondition_from_that_branch(body: str) -> None:
    """A query inside a branch keeps its ``len(result) <= 100`` postcondition.

    ``eval_if_else`` evaluates each branch in a child context and folds the
    child's obligations back into the parent. Assumptions have to be folded
    back too: without them the query length is unconstrained and the prover
    reports a 101-row counterexample, even though the identical loop proves
    when it is not wrapped in an ``if``.
    """
    assert _prove_db(body).proof.conclusion is Conclusion.OK


def test_unbranched_query_loop_still_proves() -> None:
    """Control for the parametrized case above: same loop, no ``if``."""
    result = _prove_db("""
@clauz3.guarantee(db.count_at_most(100))
def main() -> None:
    for row in db_query("users"):
        send_email(row.email, "hi")
""")
    assert result.proof.conclusion is Conclusion.OK


def test_branch_query_bound_still_binds() -> None:
    """The recovered assumption is the real bound, not a blanket amnesty.

    Same program as the ``then`` case with a guarantee one send too tight, so
    a merge that assumed something stronger than ``len(result) <= 100`` would
    show up here as a spurious pass.
    """
    result = _prove_db("""
@clauz3.guarantee(db.count_at_most(99))
def main(flag: bool) -> None:
    if flag:
        for row in db_query("users"):
            send_email(row.email, "hi")
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_branch_assumption_is_guarded_by_the_branch_condition() -> None:
    """A branch's assumption must not hold on the path that skips the branch.

    ``bump``'s postcondition constrains its *argument*, so folding it back
    unguarded would assert ``n > 10`` for every ``n`` and discharge the assert
    on the ``flag=False`` path, where ``bump`` never runs.
    """
    result = _prove_bare("""\
import deal


@deal.ensure(lambda n, result: n > 10)
def bump(n: int) -> int:
    return n


def main(flag: bool, n: int) -> int:
    if flag:
        bump(n)
    assert n > 10
    return n
""")
    assert result.proof.conclusion is Conclusion.FAIL


_LEAK_THEN_INTO_ELSE = """\
import deal


@deal.ensure(lambda n, result: n > 10)
def bump(n: int) -> int:
    return n


def main(flag: bool, n: int) -> int:
    if flag:
        bump(n)
    else:
        assert n > 10
    return n
"""

_LEAK_ELSE_INTO_THEN = """\
import deal


@deal.ensure(lambda n, result: n > 10)
def bump(n: int) -> int:
    return n


def main(flag: bool, n: int) -> int:
    if flag:
        assert n > 10
    else:
        bump(n)
    return n
"""


@pytest.mark.parametrize(
    "body",
    [_LEAK_THEN_INTO_ELSE, _LEAK_ELSE_INTO_THEN],
    ids=["then-into-else", "else-into-then"],
)
def test_branch_assumption_does_not_leak_into_the_other_arm(body: str) -> None:
    """One arm's assumption must not discharge an assertion in the other arm.

    Obligations are discharged at theorem level against the parent's final
    ``given``, so an unguarded merge would let ``bump``'s postcondition from
    one arm satisfy the ``assert n > 10`` in the arm where ``bump`` never ran.
    Together with the post-branch case above these pin all three ways the
    guard is load-bearing; removing it flips each to a false ``OK``.
    """
    assert _prove_bare(body).proof.conclusion is Conclusion.FAIL
