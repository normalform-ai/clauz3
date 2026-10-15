"""Relation primitives under quantified facts."""

from __future__ import annotations

from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_path

EXAMPLE = Path(__file__).parent.parent / "examples" / "email-from-db"
DB_TRUSTED = EXAMPLE / "tools" / "db" / "trusted"

FIXTURES = Path(__file__).resolve().parent / "fixtures/loop_email"
FIXTURE_TRUSTED = FIXTURES / "qtrusted"


def _prove(case_name: str) -> list[Conclusion]:
    """Run prove_path on an email-from-db case; return each target's conclusion."""
    case = EXAMPLE / "cases" / case_name
    results = prove_path(
        case,
        trusted_roots=[DB_TRUSTED],
        import_roots=[EXAMPLE],
    )
    return [r.proof.conclusion for r in results]


def _prove_fixture(case_name: str) -> list[Conclusion]:
    """Run prove_path on a fixture case (for cases without example equivalents)."""
    case = FIXTURES / "cases" / case_name
    results = prove_path(
        case,
        trusted_roots=[FIXTURE_TRUSTED],
        import_roots=[FIXTURES],
    )
    return [r.proof.conclusion for r in results]


def test_empty_relation_over_loop_fails_when_call_reachable() -> None:
    """emails.none() over a for-loop that DOES send an email should fail."""
    assert _prove("email_loop_fail.py") == [Conclusion.FAIL]


def test_empty_relation_passes_when_no_calls() -> None:
    """emails.none() when send_email is never called should pass."""
    assert _prove("no_emails_with_loop_pass.py") == [Conclusion.OK]


def test_all_relation_over_loop_passes_when_predicate_holds_trivially() -> None:
    """at_most(100) over a loop of at most 100 rows should pass."""
    assert _prove("all_trivial_loop_pass.py") == [Conclusion.OK]


def test_count_under_pass() -> None:
    """at_most(100) with up-to-100-row loop: count*1 <= 100 should pass."""
    assert _prove("count_pass.py") == [Conclusion.OK]


def test_count_too_tight_fail() -> None:
    """at_most(10) with up-to-100-row loop: 100 possible sends violate <= 10."""
    assert _prove("count_too_tight_fail.py") == [Conclusion.FAIL]


def test_sum_selector_depending_on_row_raises_unsupported() -> None:
    """sum(selector) depending on bound-var field raises UnsupportedError -> not OK."""
    assert _prove_fixture("sum_selector_depends_on_row_skip.py") == [Conclusion.SKIP]


def test_distinct_over_loop_fails_without_uniqueness_postcondition() -> None:
    """distinct(addr) over a loop fails when the trusted layer doesn't
    guarantee per-row email uniqueness."""
    assert _prove("unique_recipients_loop_fail.py") == [Conclusion.FAIL]


def test_shares_value_with_quantified_relation_raises() -> None:
    """shares_value over quantified relations raises UnsupportedError in v1."""
    assert _prove_fixture("shares_value_over_loop_skip.py") == [Conclusion.SKIP]


def test_precondition_over_loop_must_fail() -> None:
    """Soundness: a precondition requiring addr == 'bob@example.com'
    must NOT prove for an arbitrary row.email argument."""
    assert _prove_fixture("precondition_over_loop_fail.py") == [Conclusion.FAIL]


def test_count_under_branch_in_loop_must_fail() -> None:
    """Soundness: a count(1) bound must NOT prove when send_email
    could fire on every consented row (up to 100)."""
    assert _prove_fixture("count_under_branch_fail.py") == [Conclusion.FAIL]


def test_substituted_proxy_preserves_rowsort_schema() -> None:
    """Regression: _substituted_proxy must preserve schema when rewrapping
    a RowSort. Previously raised TypeError because RowSort(expr=...) omits
    the required `schema` keyword argument, breaking DistinctSpec's
    same-fact path whenever a fact arg or key proxy was a RowSort."""
    import z3

    from clauz3._vendor.deal_solver._proxies._row import (
        RowSort,
        z3_datatype_for_row,
    )
    from clauz3.row import Row
    from clauz3.spec import _substituted_proxy

    class _SchemaCheckRow(Row):
        email: str

    dt = z3_datatype_for_row(_SchemaCheckRow)
    arr = z3.Const("rows", z3.ArraySort(z3.IntSort(), dt))
    i = z3.Int("i")
    j = z3.Int("j")
    original = RowSort(schema=_SchemaCheckRow, expr=z3.Select(arr, i))

    substituted = _substituted_proxy(original, [(i, j)])

    assert isinstance(substituted, RowSort)
    assert substituted.schema is _SchemaCheckRow
    # Substitution actually rewrote the index — i ↦ j inside the expression.
    assert "j" in str(substituted.expr)


@pytest.mark.parametrize("cross_fact", [False, True])
@pytest.mark.parametrize("dependent_lower", [False, True])
@pytest.mark.parametrize("duplicates", [False, True])
def test_distinct_renames_dependent_bounds(
    cross_fact: bool, dependent_lower: bool, duplicates: bool
) -> None:
    """Only outer iteration zero runs; unseen iterations must not add duplicates."""
    import ast

    import z3
    from z3.z3util import get_vars  # type: ignore[import-untyped]

    from clauz3._vendor.deal_solver._context import Context
    from clauz3._vendor.deal_solver._context._layer import FactInfo
    from clauz3._vendor.deal_solver._context._quantifier import Quantifier
    from clauz3._vendor.deal_solver._proxies import BoolSort, IntSort
    from clauz3._vendor.deal_solver._proxies._row import QueryResultSort
    from clauz3.row import Row
    from clauz3.spec import (
        LambdaSpec,
        _distinct_pair_clause,
        _distinct_same_fact_clause,
    )

    class Item(Row):
        value: int

    query = QueryResultSort(
        row_schema=Item,
        array_expr=z3.Array("unused_rows", z3.IntSort(), z3.IntSort()),
        length_expr=z3.IntVal(2),
        source=("query", {}),
    )
    outer, inner = z3.Ints("outer_original inner_original")
    size = 2 if duplicates and not cross_fact else 1
    lower = z3.If(outer == 0, 0, size) if dependent_lower else z3.IntVal(0)
    upper = z3.IntVal(size) if dependent_lower else z3.If(outer == 0, size, 0)
    frames = (
        Quantifier(outer, query, z3.IntVal(0), z3.IntVal(2)),
        Quantifier(inner, query, lower, upper),
    )
    fact = FactInfo(
        name="emit",
        markers=(),
        args={"value": IntSort(z3.IntVal(0))},
        cond=BoolSort(z3.BoolVal(True)),
        quantifiers=frames,
    )
    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    key = LambdaSpec("call", ast.parse("call.value", mode="eval").body, {})
    if cross_fact:
        clause = _distinct_pair_clause(
            left=fact,
            right=fact,
            left_cond=BoolSort(outer == 0),
            right_cond=BoolSort(outer == (0 if duplicates else 1)),
            key=key,
            ctx=ctx,
        )
    else:
        clause = _distinct_same_fact_clause(fact=fact, cond=fact.cond, key=key, ctx=ctx)
    # No original index may remain free after either side is renamed.
    assert get_vars(clause.expr) == []
    solver = z3.Solver()
    solver.add(z3.Not(clause.expr))
    assert solver.check() == (z3.sat if duplicates else z3.unsat)
