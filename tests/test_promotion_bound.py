"""Unit tests for the promotion bound on row-dependent loop sums.

The end-to-end proof behaviour is pinned by the ``sum_selector_*`` fixtures in
``test_quantified_relations.py``. These tests cover the pieces those fixtures
depend on silently: the literal ``len(result) <= K`` recognition that keeps
the pass cases inside the solver timeout, and the refusal of ``value()`` when
the total only exists alongside side axioms.
"""

from __future__ import annotations

import textwrap
from types import SimpleNamespace
from typing import Any, cast

import pytest
import z3
from z3 import z3util

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._context._quantifier import Quantifier
from clauz3._vendor.deal_solver._eval_stmt import eval_stmt
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3.prover import AgentTheorem
from clauz3.spec import (
    _literal_upper_bounds,
    _match_upper_bound,
    _promotion_bound,
    effect,
)

SOURCE = """
import deal
import clauz3


class InvoiceRow(clauz3.Row):
    amount: int


@deal.has("db_read")
@deal.post(lambda result: len(result) <= 100)
def db_query_invoices(table: str) -> list[InvoiceRow]: ...


@deal.has("billing")
def charge(amount: int) -> None: ...


@deal.has("check")
def enabled() -> bool: ...


@clauz3.guarantee(lambda: True)
def main() -> None:
    for inv in db_query_invoices("invoices"):
        if inv.amount <= 10:
            charge(inv.amount)
"""

# The same loop, but the query only runs under a branch, so its
# postcondition is recorded as a conditional given.
CONDITIONAL_SOURCE = SOURCE.replace(
    """    for inv in db_query_invoices("invoices"):
        if inv.amount <= 10:
            charge(inv.amount)
""",
    """    if enabled():
        for inv in db_query_invoices("invoices"):
            if inv.amount <= 10:
                charge(inv.amount)
""",
)


def _executed_context(source: str = SOURCE) -> Any:
    """Symbolically execute the source's main() and return its context."""
    theorems = list(AgentTheorem.from_text(textwrap.dedent(source)))
    main = next(t for t in theorems if t.name == "main")
    ctx = Context.make_empty(
        get_contracts=main.get_contracts,
        get_guarantees=main.get_guarantees,
    )
    for stmt in main._func.body:
        eval_stmt(stmt, ctx=ctx)
    return ctx


def test_literal_upper_bound_reads_the_trusted_postcondition() -> None:
    """The real `len(result) <= 100` given is recognised for the loop length."""
    ctx = _executed_context()
    (fact,) = [f for f in ctx.facts if f.name == "charge"]
    (quantifier,) = fact.quantifiers
    assert _literal_upper_bounds(quantifier.upper - quantifier.lower, ctx=ctx) == [100]


def test_literal_upper_bounds_reads_a_conditional_postcondition() -> None:
    """A query under a branch records `Or(inactive, len <= 100)`; the cap must
    still be found so the linear relaxation is emitted."""
    ctx = _executed_context(CONDITIONAL_SOURCE)
    assert CONDITIONAL_SOURCE != SOURCE
    (fact,) = [f for f in ctx.facts if f.name == "charge"]
    (quantifier,) = fact.quantifiers
    assert _literal_upper_bounds(quantifier.upper - quantifier.lower, ctx=ctx) == [100]


def test_value_refuses_a_total_that_needs_side_axioms() -> None:
    """`value()` has nowhere to put the promotion axioms, so it refuses."""
    ctx = _executed_context()
    spec = effect("charge").sum(lambda e: e.amount)
    with pytest.raises(UnsupportedError, match="side axioms"):
        spec.value(ctx=ctx)


N = z3.Int("n")
M = z3.Int("m")


@pytest.mark.parametrize(
    ("atom", "expected"),
    [
        (N <= 7, 7),
        (N < 7, 6),
        (z3.IntVal(7) >= N, 7),
        (z3.IntVal(7) > N, 6),
        (z3.Not(N > 7), 7),
        (z3.Not(N >= 7), 6),
        (z3.Not(z3.IntVal(7) < N), 7),
        # Lower bounds, other symbols and non-literals give no cap.
        (N >= 7, None),
        (z3.IntVal(7) <= N, None),
        (z3.Not(N <= 7), None),
        (M <= 7, None),
        (N <= M, None),
        (N == 7, None),
        # Quantified givens are not applications and must not crash.
        (z3.ForAll([M], M <= N), None),
        (z3.Not(z3.ForAll([M], M <= N)), None),
    ],
    ids=str,
)
def test_match_upper_bound(atom: z3.BoolRef, expected: int | None) -> None:
    assert _match_upper_bound(atom, N) == expected


# `_promotion_bound` never reads a quantifier's source, so these hand-built
# frames carry a placeholder instead of a real query result.
_NO_SOURCE = cast(Any, None)


def _ground_axioms(
    amounts: list[int], *, cap: int | None, total: int
) -> tuple[list[z3.BoolRef], dict[str, z3.ArithRef]]:
    """Emit the promotion axioms for `charge(a)` under `a <= 10` over a loop
    whose rows are `amounts`, then pin every free symbol to a concrete value.

    Returns the axioms plus the ground equalities, so a check needs no search:
    the only unknowns left are the ones the caller asks about.
    """
    rows = z3.Array("rows", z3.IntSort(), z3.IntSort())
    length = z3.Int("rows_len")
    i = z3.Int("i")
    quantifier = Quantifier(
        bound_var=i, source=_NO_SOURCE, lower=z3.IntVal(0), upper=length
    )
    given = [length <= cap] if cap is not None else []
    s, axioms = _promotion_bound(
        quantifiers=(quantifier,),
        cond=rows[i] <= 10,
        base=rows[i],
        ctx=SimpleNamespace(given=given),
    )
    free = {
        str(v): v for v in z3util.get_vars(z3.And(*axioms)) if v.sort().eq(z3.IntSort())
    }
    terms = [a if a <= 10 else 0 for a in amounts]
    ground: list[z3.BoolRef] = [length == len(amounts), s.expr == total]
    for index, amount in enumerate(amounts):
        ground.append(rows[index] == amount)
    if terms:
        lo_name = next(name for name in free if name.startswith("sum_lo"))
        hi_name = next(name for name in free if name.startswith("sum_hi"))
        ground.append(free[lo_name] == terms.index(min(terms)))
        ground.append(free[hi_name] == terms.index(max(terms)))
    # Rows past the end are unconstrained by the program; pin them too.
    ground.append(
        z3.ForAll([i], z3.Implies(z3.Or(i < 0, i >= len(amounts)), rows[i] == 0))
    )
    return axioms + ground, free


@pytest.mark.parametrize(
    ("amounts", "cap"),
    [
        ([], None),
        ([], 3),
        ([4], None),
        ([3, 11, 7], 3),
        ([10, 10, 10], 3),
        ([-5, 2, 10, 50], 100),
        ([-1, -2], 2),
    ],
)
def test_promotion_axioms_hold_for_the_real_sum(
    amounts: list[int], cap: int | None
) -> None:
    """Soundness: the real total, argmin and argmax satisfy every axiom, so a
    proof under the axioms is a proof about the program."""
    real = sum(a for a in amounts if a <= 10)
    formula, _ = _ground_axioms(amounts, cap=cap, total=real)
    solver = z3.Solver()
    solver.set("timeout", 2000)
    solver.add(*formula)
    assert solver.check() == z3.sat


@pytest.mark.parametrize(
    ("amounts", "cap", "too_big"),
    [
        ([10, 10, 10], 3, 31),
        ([3, 11, 7], 3, 22),
    ],
)
def test_promotion_axioms_reject_a_total_past_n_times_max(
    amounts: list[int], cap: int, too_big: int
) -> None:
    """Tightness: a total above n * max(term) contradicts the axioms."""
    formula, _ = _ground_axioms(amounts, cap=cap, total=too_big)
    solver = z3.Solver()
    solver.set("timeout", 2000)
    solver.add(*formula)
    assert solver.check() == z3.unsat


def test_promotion_refuses_a_non_int_selector() -> None:
    """A Real-sorted term could make the Int total unsatisfiable: refuse."""
    i = z3.Int("i")
    rows = z3.Array("real_rows", z3.IntSort(), z3.RealSort())
    quantifier = Quantifier(
        bound_var=i, source=_NO_SOURCE, lower=z3.IntVal(0), upper=z3.Int("n")
    )
    with pytest.raises(UnsupportedError, match="int selector"):
        _promotion_bound(
            quantifiers=(quantifier,),
            cond=z3.BoolVal(True),
            base=rows[i],
            ctx=SimpleNamespace(given=[]),
        )
