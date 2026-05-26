"""Quantifier frame for symbolic for-loops over trusted query returns."""
from __future__ import annotations

import typing
from dataclasses import dataclass

import z3

if typing.TYPE_CHECKING:
    from .._proxies._row import QueryResultSort


@dataclass(frozen=True)
class Quantifier:
    """One for-loop frame's binding."""

    bound_var: z3.ArithRef
    source: "QueryResultSort"
    lower: z3.ArithRef
    upper: z3.ArithRef

    def bounds_expr(self) -> z3.BoolRef:
        return z3.And(self.lower <= self.bound_var, self.bound_var < self.upper)
