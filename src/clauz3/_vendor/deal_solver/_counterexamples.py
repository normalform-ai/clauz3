"""Recover small nested-sequence counterexamples without weakening a proof."""

from __future__ import annotations

import itertools
import time

import z3


# Search is only a SAT fallback. Exhausting these sizes never establishes UNSAT.
_SIZES = (1, 0, 2, 3, 4)
_MAX_SCAN_NODES = 10_000
_MAX_CONSTRUCTOR_NODES = 256


def sequence_counterexample(
    assertions: list[z3.BoolRef], *, timeout_ms: int,
) -> z3.ModelRef | None:
    """Return a model of the original assertions, or leave unknown unresolved.

    Z3 can refuse SAT for Seq(Seq(BitVec(21))) even without domain axioms.
    Fixing finite constructor shapes lets simplification eliminate sequence
    accesses and bounded domain quantifiers. Every original assertion remains
    in the query; only SAT is useful. UNSAT here says nothing about larger or
    differently shaped sequences and must never discharge an obligation.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    pending = list(assertions)
    seen: set[int] = set()
    variables: list[z3.ExprRef] = []
    while pending:
        expr = pending.pop()
        identity = expr.get_id()
        if identity in seen:
            continue
        seen.add(identity)
        if len(seen) > _MAX_SCAN_NODES or time.monotonic() >= deadline:
            return None
        sort = expr.sort()
        if (
            z3.is_const(expr)
            and expr.decl().kind() == z3.Z3_OP_UNINTERPRETED
            and isinstance(sort, z3.SeqSortRef)
            and isinstance(sort.basis(), z3.SeqSortRef)
        ):
            variables.append(expr)
        pending.extend(expr.children())
    if not variables:
        return None

    ctx = variables[0].ctx
    for outer_size, leaf_size in itertools.product(_SIZES, repeat=2):
        # A completely concrete alphabet hint also avoids residual seq.nth
        # incompleteness for deeper nesting. It only narrows a SAT search.
        for point in (None, 0x30000, 0x10FFFF, 0, ord('a'), 0xD800):
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                return None
            budget = [_MAX_CONSTRUCTOR_NODES]
            shapes = [
                _shape(var.sort(), outer_size, leaf_size, budget, point)
                for var in variables
            ]
            if any(shape is None for shape in shapes):
                break
            solver = z3.Then(
                'simplify', 'propagate-values', 'solve-eqs', 'simplify', 'smt', ctx=ctx,
            ).solver()
            solver.set(timeout=min(remaining, 250))
            solver.add(*assertions)
            solver.add(*[
                var == shape
                for var, shape in zip(variables, shapes, strict=True)
            ])
            result = solver.check()
            if result == z3.sat:
                return solver.model()
            if point is None and result == z3.unsat:
                break
    return None


def _shape(
    sort: z3.SortRef, outer_size: int, leaf_size: int, budget: list[int],
    point: int | None = None,
) -> z3.ExprRef | None:
    budget[0] -= 1
    if budget[0] < 0:
        return None
    if not isinstance(sort, z3.SeqSortRef):
        if point is not None and z3.is_bv_sort(sort) and sort.size() == 21:
            return z3.BitVecVal(point, 21, ctx=sort.ctx)
        return z3.FreshConst(sort, prefix='sequence_witness')
    size = outer_size if isinstance(sort.basis(), z3.SeqSortRef) else leaf_size
    if size == 0:
        return z3.Empty(sort)
    units = []
    for _ in range(size):
        item = _shape(sort.basis(), outer_size, leaf_size, budget, point)
        if item is None:
            return None
        units.append(z3.Unit(item))
    return units[0] if len(units) == 1 else z3.Concat(*units)
