"""Transfer a conditional context's obligations without its local bindings."""

from __future__ import annotations

import typing

from clauz3.diagnostics import proof as proof_monitor


if typing.TYPE_CHECKING:
    from ._context import Context
    from .._proxies import BoolSort


def merge_branch_layers(*, branch: Context, guard: BoolSort, ctx: Context) -> None:
    from .._proxies import and_expr, or_expr

    inactive = guard.m_not(ctx=ctx)
    for assumption in branch.given.layer:
        ctx.given.add(or_expr(inactive, assumption, ctx=ctx))
    for constraint in branch.expected.layer:
        expected = or_expr(inactive, constraint, ctx=ctx)
        proof_monitor.inherit(constraint.expr, expected.expr)
        ctx.expected.add(expected)
    for exc in branch.exceptions.layer:
        ctx.exceptions.add(exc._replace(cond=and_expr(guard, exc.cond, ctx=ctx)))
    for ret in branch.returns.layer:
        ctx.returns.add(ret._replace(cond=and_expr(guard, ret.cond, ctx=ctx)))
    for fact in branch.facts.layer:
        # In particular, keep the quantifiers active at the original call.
        ctx.facts.add(fact._replace(cond=and_expr(guard, fact.cond, ctx=ctx)))
