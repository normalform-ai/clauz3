"""ColumnRef matching in contracts — the headline test."""

from __future__ import annotations

from pathlib import Path

import z3

from clauz3.prover import prove_path

EXAMPLE = Path(__file__).parent.parent / "examples" / "email-from-db"
DB_TRUSTED = EXAMPLE / "tools" / "db" / "trusted"


def _prove_case(case_name: str) -> bool:
    results = prove_path(
        EXAMPLE / "cases" / case_name,
        trusted_roots=[DB_TRUSTED],
        import_roots=[EXAMPLE],
    )
    return all(r.ok for r in results)


def test_addresses_from_passes_when_addr_came_from_column() -> None:
    """The headline: for-loop sends from UserRow.email → contract holds."""
    assert _prove_case("newsletter_pass.py")


def test_addresses_from_fails_when_addr_is_literal() -> None:
    assert not _prove_case("literal_address_fail.py")


def test_addresses_from_fails_when_addr_from_wrong_column() -> None:
    assert not _prove_case("wrong_column_fail.py")


def test_addresses_from_fails_on_mixed_source() -> None:
    """Literal send + loop sends — fails because the literal fact violates."""
    assert not _prove_case("mixed_source_fail.py")


def test_compare_column_ref_returns_false_for_non_array_arg() -> None:
    """Regression: matcher must return False (not raise, not match) when the
    underlying Z3 expression is a field accessor applied to a Lambda-based
    select rather than a genuine array-backed QueryResult row.

    z3.Lambda creates an Array-sort expression for which z3.is_array() returns
    False.  Without the guard, the matcher would call .sort().range() and get a
    passing sort match — a false positive.  With the guard it returns False.
    """
    from clauz3._vendor.deal_solver._context import Context
    from clauz3._vendor.deal_solver._proxies import types
    from clauz3._vendor.deal_solver._proxies._row import z3_datatype_for_row
    from clauz3.row import ColumnRef, Row
    from clauz3.spec import _compare_column_ref

    class _LambdaRow(Row):
        email: str

    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    dt = z3_datatype_for_row(_LambdaRow)

    # Build email(Lambda(x, row_var)[0]) — this passes the Z3_OP_SELECT check
    # (inner.decl().kind() == Z3_OP_SELECT) but the "array" is a Lambda, not a
    # real array variable, so z3.is_array(array_expr) == False.
    x = z3.Int("_lam_x")
    row_var = z3.Const("_lam_row", dt)
    lam = z3.Lambda([x], row_var)
    assert not z3.is_array(lam), "precondition: Lambda is not is_array"
    lam_sel = z3.Select(lam, z3.IntVal(0))
    email_acc = dt.accessor(0, 0)
    direct_email = email_acc(lam_sel)
    arg_proxy = types.str(expr=direct_email)

    result = _compare_column_ref(
        arg_proxy, ColumnRef(schema=_LambdaRow, field="email"), ctx=ctx
    )

    # Must return a BoolSort (not raise) and simplify to False.
    underlying = result.expr if hasattr(result, "expr") else result
    assert z3.simplify(underlying) == z3.BoolVal(False), (
        f"Expected False but got {z3.simplify(underlying)}"
    )
