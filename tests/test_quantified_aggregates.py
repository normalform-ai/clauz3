"""How ``count()`` and ``sum()`` encode facts emitted inside a symbolic loop.

A trusted call made inside ``for row in db_query(...)`` is recorded once, with a
``Quantifier`` frame naming the loop's fresh ``z3.Int`` index. ``SumSpec.value``
turns such a fact into ``If(cond, base * product(q.upper), 0)`` and leaves the
loop index **free**. Because every obligation is discharged by checking
``given and not guarantee`` unsatisfiable, a free index reads universally, which
makes upper bounds over-approximate and lower bounds under-approximate.

These tests pin the resulting behaviour: which bounds prove, which do not, and
where the encoding is exact rather than loose. Each case records the count the
program can really reach, so the pass/fail column reads as conservative rather
than arbitrary. ``docs/explanation/symbolic-iteration.md`` states the same rules
in prose for contract authors.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import z3
from z3 import z3util

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._eval_stmt import eval_stmt
from clauz3.prover import AgentTheorem, prove_text
from clauz3.spec import effect

FIXTURES = Path(__file__).resolve().parent / "fixtures/loop_email"
FIXTURE_TRUSTED = FIXTURES / "qtrusted"

PREAMBLE = """
import clauz3
from qtrusted import contracts
from qtrusted.effects import db_query, db_query_users100, send_email
"""

# Two loop sources with different trusted length bounds:
#   db_query          -> @deal.post(lambda result: len(result) <= 5)
#   db_query_users100 -> @deal.post(lambda result: len(result) <= 100)

BRANCH_100 = """
    for row in db_query_users100("users"):
        if row.consented:
            send_email(row.email, "hi")
"""

UNCONDITIONAL_100 = """
    for row in db_query_users100("users"):
        send_email(row.email, "hi")
"""

BRANCH_5 = """
    for row in db_query("users"):
        if row.consented:
            send_email(row.email, "hi")
"""

IMPOSSIBLE_BRANCH_100 = """
    for row in db_query_users100("users"):
        if row.consented and not row.consented:
            send_email(row.email, "hi")
"""

# One email per row: the two arms cannot both fire for the same row.
EXCLUSIVE_ARMS_100 = """
    for row in db_query_users100("users"):
        if row.consented:
            send_email(row.email, "a")
        else:
            send_email(row.email, "b")
"""

# Up to two emails per row, and only on the else arm.
UNEVEN_ARMS_100 = """
    for row in db_query_users100("users"):
        if row.consented:
            send_email(row.email, "a")
        else:
            send_email(row.email, "b")
            send_email(row.email, "c")
"""

# Two conditions that can hold for the same row: up to two emails per row.
INDEPENDENT_CONDITIONS_100 = """
    for row in db_query_users100("users"):
        if row.consented:
            send_email(row.email, "a")
        if row.name == "x":
            send_email(row.email, "b")
"""

# One email per row overall, but split across two loops over the same rows.
TWO_LOOPS_100 = """
    rows = db_query_users100("users")
    for row in rows:
        if row.consented:
            send_email(row.email, "a")
    for other in rows:
        if not other.consented:
            send_email(other.email, "b")
"""

NESTED_5_BY_100 = """
    for outer in db_query("users"):
        for inner in db_query_users100("more"):
            if inner.consented:
                send_email(inner.email, "hi")
"""

LITERAL_THEN_BRANCH_100 = """
    send_email("bob@example.com", "hi")
    for row in db_query_users100("users"):
        if row.consented:
            send_email(row.email, "hi")
"""

NON_MATCHING_MESSAGE_100 = """
    for row in db_query_users100("users"):
        if row.consented:
            send_email(row.email, "bye")
"""


def _prove(*, guarantee: str, body: str) -> Conclusion:
    """Prove one inline program and retain the exact outcome."""
    source = f"{PREAMBLE}\n\n@clauz3.guarantee({guarantee})\ndef main() -> None:{body}"
    results = prove_text(
        source,
        trusted_roots=[FIXTURE_TRUSTED],
        import_paths=[FIXTURES],
        source_name="<quantified-aggregate>",
    )
    assert len(results) == 1, results
    return results[0].proof.conclusion


# (case id, guarantee, program body, proves?) — the trailing comment on each
# entry is the largest count the program can really produce.
UPPER_BOUND_CASES: list[tuple[str, str, str, Conclusion]] = [
    # The row-dependent branch is ignored: the bound must cover every row, so
    # the loop's own length bound of 100 is the smallest bound that discharges.
    (
        "branch-at-length-bound",
        "contracts.at_most(100)",
        BRANCH_100,
        Conclusion.OK,
    ),  # <= 100
    (
        "branch-one-below-bound",
        "contracts.at_most(99)",
        BRANCH_100,
        Conclusion.FAIL,
    ),  # <= 100
    (
        "branch-far-below-bound",
        "contracts.at_most(1)",
        BRANCH_100,
        Conclusion.FAIL,
    ),  # <= 100
    # Without a branch the encoding is exact, and lands in the same place.
    (
        "plain-at-length-bound",
        "contracts.at_most(100)",
        UNCONDITIONAL_100,
        Conclusion.OK,
    ),  # 100
    (
        "plain-one-below-bound",
        "contracts.at_most(99)",
        UNCONDITIONAL_100,
        Conclusion.FAIL,
    ),  # 100
    # The bound that matters is the loop source's own length postcondition.
    (
        "smaller-source-at-bound",
        "contracts.at_most(5)",
        BRANCH_5,
        Conclusion.OK,
    ),  # <= 5
    (
        "smaller-source-below-bound",
        "contracts.at_most(4)",
        BRANCH_5,
        Conclusion.FAIL,
    ),  # <= 5
    # A condition no row can satisfy collapses the contribution to zero. This is
    # the one shape of row-dependent condition the encoding does exploit.
    (
        "impossible-branch",
        "contracts.at_most(0)",
        IMPOSSIBLE_BRANCH_100,
        Conclusion.OK,
    ),  # 0
    # One shared index per loop: exclusive arms cost one email per row, not two.
    (
        "exclusive-arms",
        "contracts.at_most(100)",
        EXCLUSIVE_ARMS_100,
        Conclusion.OK,
    ),  # 100
    # The heavier arm sets the per-row cost.
    (
        "uneven-arms-at-bound",
        "contracts.at_most(200)",
        UNEVEN_ARMS_100,
        Conclusion.OK,
    ),  # 200
    (
        "uneven-arms-below-bound",
        "contracts.at_most(199)",
        UNEVEN_ARMS_100,
        Conclusion.FAIL,
    ),  # 200
    # Conditions that can hold together cost two emails per row.
    (
        "independent-conditions-at-bound",
        "contracts.at_most(200)",
        INDEPENDENT_CONDITIONS_100,
        Conclusion.OK,
    ),  # 200
    (
        "independent-conditions-below-bound",
        "contracts.at_most(100)",
        INDEPENDENT_CONDITIONS_100,
        Conclusion.FAIL,
    ),  # 200
    # Separate loops get separate indices, so their exclusivity is invisible:
    # the true worst case is 100, and only 200 discharges.
    (
        "two-loops-at-doubled-bound",
        "contracts.at_most(200)",
        TWO_LOOPS_100,
        Conclusion.OK,
    ),  # 100
    (
        "two-loops-at-true-bound",
        "contracts.at_most(100)",
        TWO_LOOPS_100,
        Conclusion.FAIL,
    ),  # 100
    # Nested loops multiply their length bounds.
    (
        "nested-at-product",
        "contracts.at_most(500)",
        NESTED_5_BY_100,
        Conclusion.OK,
    ),  # <= 500
    (
        "nested-below-product",
        "contracts.at_most(499)",
        NESTED_5_BY_100,
        Conclusion.FAIL,
    ),  # <= 500
    # Strict `<` shifts the boundary by one, as it does for unquantified facts.
    (
        "strict-above-bound",
        "contracts.fewer_than(101)",
        BRANCH_100,
        Conclusion.OK,
    ),  # <= 100
    (
        "strict-at-bound",
        "contracts.fewer_than(100)",
        BRANCH_100,
        Conclusion.FAIL,
    ),  # <= 100
    # A `where` filter prunes when its predicate is impossible independently
    # of the row: here every call carries a different literal message.
    (
        "where-filter-excludes-every-fact",
        "contracts.greeting_at_most(0)",
        NON_MATCHING_MESSAGE_100,
        Conclusion.OK,
    ),  # 0
    (
        "where-filter-matches-every-fact",
        "contracts.greeting_at_most(99)",
        BRANCH_100,
        Conclusion.FAIL,
    ),  # <= 100
    # Filtering on a satisfiable row-derived address still charges all rows.
    (
        "row-derived-filter-at-bound",
        "contracts.bob_at_most(100)",
        UNCONDITIONAL_100,
        Conclusion.OK,
    ),
    (
        "row-derived-filter-below-bound",
        "contracts.bob_at_most(99)",
        UNCONDITIONAL_100,
        Conclusion.FAIL,
    ),
    # A constant selector scales the same product.
    (
        "const-sum-at-bound",
        "contracts.unit_cost_at_most(200)",
        BRANCH_100,
        Conclusion.OK,
    ),  # <= 200
    (
        "const-sum-below-bound",
        "contracts.unit_cost_at_most(199)",
        BRANCH_100,
        Conclusion.FAIL,
    ),  # <= 200
]

LOWER_BOUND_CASES: list[tuple[str, str, str, Conclusion]] = [
    # A lower bound cannot rest on a quantified fact: the free index may land
    # where the branch does not fire, collapsing the contribution to zero.
    ("branch-ge-one", "contracts.at_least(1)", BRANCH_100, Conclusion.FAIL),  # >= 0
    # Even without a branch — and here the refusal is exact, because the loop
    # source is allowed to be empty.
    (
        "plain-ge-one",
        "contracts.at_least(1)",
        UNCONDITIONAL_100,
        Conclusion.FAIL,
    ),  # >= 0
    (
        "plain-gt-zero",
        "contracts.more_than(0)",
        UNCONDITIONAL_100,
        Conclusion.FAIL,
    ),  # >= 0
    # Zero is the only lower bound a quantified fact discharges on its own.
    ("branch-ge-zero", "contracts.at_least(0)", BRANCH_100, Conclusion.OK),  # >= 0
    # An unquantified fact carries its own lower bound through unharmed.
    (
        "literal-carries-lower-bound",
        "contracts.at_least(1)",
        LITERAL_THEN_BRANCH_100,
        Conclusion.OK,
    ),  # >= 1
    (
        "literal-cannot-carry-two",
        "contracts.at_least(2)",
        LITERAL_THEN_BRANCH_100,
        Conclusion.FAIL,
    ),  # >= 1
    # Constant-selector sums behave the same way in the lower-bound direction.
    (
        "const-sum-ge-two",
        "contracts.unit_cost_at_least(2)",
        UNCONDITIONAL_100,
        Conclusion.FAIL,
    ),  # >= 0
]


@pytest.mark.parametrize(
    ("guarantee", "body", "expected"),
    [
        pytest.param(guarantee, body, expected, id=case_id)
        for case_id, guarantee, body, expected in UPPER_BOUND_CASES
    ],
)
def test_upper_bound_over_quantified_facts(
    guarantee: str,
    body: str,
    expected: Conclusion,
) -> None:
    """Upper bounds over-approximate: the bound must cover the loop's length."""
    assert _prove(guarantee=guarantee, body=body) is expected


@pytest.mark.parametrize(
    ("guarantee", "body", "expected"),
    [
        pytest.param(guarantee, body, expected, id=case_id)
        for case_id, guarantee, body, expected in LOWER_BOUND_CASES
    ],
)
def test_lower_bound_over_quantified_facts(
    guarantee: str,
    body: str,
    expected: Conclusion,
) -> None:
    """Lower bounds under-approximate: a quantified fact contributes zero."""
    assert _prove(guarantee=guarantee, body=body) is expected


STRUCTURAL_SOURCE = """
import deal
import clauz3


class AggregateProbeRow(clauz3.Row):
    email: str
    consented: bool


@deal.has("db_read")
@deal.post(lambda result: len(result) <= 100)
def db_query(table: str) -> list[AggregateProbeRow]: ...


@deal.has("email")
def send_email(addr: str, msg: str) -> None: ...


@clauz3.guarantee(lambda: True)
def main() -> None:
    for row in db_query("users"):
        if row.consented:
            send_email(row.email, "hi")
"""


def _count_expr_and_context() -> tuple[z3.ExprRef, Context]:
    """Symbolically execute ``main`` and build ``count()`` over its facts."""
    theorems = list(AgentTheorem.from_text(textwrap.dedent(STRUCTURAL_SOURCE)))
    main_theorem = next(theorem for theorem in theorems if theorem.name == "main")
    ctx = Context.make_empty(
        get_contracts=main_theorem.get_contracts,
        get_guarantees=main_theorem.get_guarantees,
    )
    for stmt in main_theorem._func.body:
        eval_stmt(stmt, ctx=ctx)
    return effect("send_email").count().value(ctx=ctx).expr, ctx


def test_count_term_leaves_the_loop_index_free() -> None:
    """The emitted term must not bind the loop index.

    This is the property the soundness argument rests on. Wrapping the
    contribution in ``ForAll`` for tidiness would silently turn the
    over-approximating upper bound and the under-approximating lower bound into
    something else, and nothing else in the suite would notice.
    """
    expr, ctx = _count_expr_and_context()

    assert not z3.is_quantifier(expr)
    free_names = {str(var) for var in z3util.get_vars(expr)}
    loop_indices = {
        str(quantifier.bound_var)
        for fact in ctx.facts
        for quantifier in fact.quantifiers
    }
    assert loop_indices, "expected the send_email fact to carry a quantifier frame"
    assert loop_indices <= free_names


def test_count_term_multiplies_by_the_source_length() -> None:
    """The multiplier is the loop source's symbolic length, not a literal bound.

    The trusted ``len(result) <= 100`` postcondition reaches the solver as an
    assumption; the aggregate term itself scales by the symbolic length, which
    is what lets ``count() <= 100`` discharge without 100 appearing in the term.
    """
    expr, ctx = _count_expr_and_context()

    lengths = {
        str(quantifier.upper) for fact in ctx.facts for quantifier in fact.quantifiers
    }
    assert lengths
    free_names = {str(var) for var in z3util.get_vars(expr)}
    assert lengths <= free_names
    pending = [expr]
    while pending:
        term = pending.pop()
        assert not (z3.is_int_value(term) and term.as_long() == 100)
        pending.extend(term.children())
