"""Unit tests for the promotion bound on row-dependent loop sums.

The end-to-end proof behaviour is pinned by the ``sum_selector_*`` fixtures in
``test_quantified_relations.py``. These tests cover the pieces those fixtures
depend on silently: the literal ``len(result) <= K`` recognition that keeps
the pass cases inside the solver timeout, and the refusal of ``value()`` when
the total only exists alongside side axioms.
"""

from __future__ import annotations

import textwrap
from typing import Any

import pytest
import z3

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._eval_stmt import eval_stmt
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3.prover import AgentTheorem
from clauz3.spec import _literal_upper_bound, _match_upper_bound, effect

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


@clauz3.guarantee(lambda: True)
def main() -> None:
    for inv in db_query_invoices("invoices"):
        if inv.amount <= 10:
            charge(inv.amount)
"""


def _executed_context() -> Any:
    """Symbolically execute SOURCE's main() and return its context."""
    theorems = list(AgentTheorem.from_text(textwrap.dedent(SOURCE)))
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
    assert _literal_upper_bound(quantifier.upper - quantifier.lower, ctx=ctx) == 100


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
    ],
    ids=str,
)
def test_match_upper_bound(atom: z3.BoolRef, expected: int | None) -> None:
    assert _match_upper_bound(atom, N) == expected
