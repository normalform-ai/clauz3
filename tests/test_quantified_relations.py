"""Relation primitives under quantified facts."""

from __future__ import annotations

from pathlib import Path

from clauz3.prover import prove_path

EXAMPLE = Path(__file__).parent.parent / "examples" / "email-from-db"
DB_TRUSTED = EXAMPLE / "tools" / "db" / "trusted"

FIXTURES = Path(__file__).resolve().parent / "fixtures/loop_email"
FIXTURE_TRUSTED = FIXTURES / "qtrusted"


def _prove(case_name: str) -> bool:
    """Run prove_path on an email-from-db case; return True if all targets are OK."""
    case = EXAMPLE / "cases" / case_name
    results = prove_path(
        case,
        trusted_roots=[DB_TRUSTED],
        import_roots=[EXAMPLE],
    )
    return all(r.ok for r in results)


def _prove_fixture(case_name: str) -> bool:
    """Run prove_path on a fixture case (for cases without example equivalents)."""
    case = FIXTURES / "cases" / case_name
    results = prove_path(
        case,
        trusted_roots=[FIXTURE_TRUSTED],
        import_roots=[FIXTURES],
    )
    return all(r.ok for r in results)


def test_empty_relation_over_loop_fails_when_call_reachable() -> None:
    """emails.none() over a for-loop that DOES send an email should fail."""
    assert not _prove("email_loop_fail.py")


def test_empty_relation_passes_when_no_calls() -> None:
    """emails.none() when send_email is never called should pass."""
    assert _prove("no_emails_with_loop_pass.py")


def test_all_relation_over_loop_passes_when_predicate_holds_trivially() -> None:
    """at_most(100) over a loop of at most 100 rows should pass."""
    assert _prove("all_trivial_loop_pass.py")


def test_count_under_pass() -> None:
    """at_most(100) with up-to-100-row loop: count*1 <= 100 should pass."""
    assert _prove("count_pass.py")


def test_count_too_tight_fail() -> None:
    """at_most(10) with up-to-100-row loop: 100 possible sends violate <= 10."""
    assert not _prove("count_too_tight_fail.py")


def test_sum_selector_depending_on_row_raises_unsupported() -> None:
    """sum(selector) depending on bound-var field raises UnsupportedError -> not OK."""
    assert not _prove_fixture("sum_selector_depends_on_row_fail.py")


def test_distinct_over_loop_fails_without_uniqueness_postcondition() -> None:
    """distinct(addr) over a loop fails when the trusted layer doesn't
    guarantee per-row email uniqueness."""
    assert not _prove("unique_recipients_loop_fail.py")


def test_shares_value_with_quantified_relation_raises() -> None:
    """shares_value over quantified relations raises UnsupportedError in v1."""
    assert not _prove_fixture("shares_value_over_loop_fail.py")


def test_precondition_over_loop_must_fail() -> None:
    """Soundness: a precondition requiring addr == 'bob@example.com'
    must NOT prove for an arbitrary row.email argument."""
    assert not _prove_fixture("precondition_over_loop_fail.py")


def test_count_under_branch_in_loop_must_fail() -> None:
    """Soundness: a count(1) bound must NOT prove when send_email
    could fire on every consented row (up to 100)."""
    assert not _prove_fixture("count_under_branch_fail.py")


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
