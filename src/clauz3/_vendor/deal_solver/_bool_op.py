"""Python scalar operand selection with guarded symbolic right-hand sides."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

import z3

from ._context import Context
from ._context._branch import merge_branch_layers
from ._context._trace import BOOLEAN_OPERAND
from ._exceptions import UnsupportedError
from ._proxies import ProxySort, and_expr, if_expr, types


Node = TypeVar('Node')
BOOL_OPERATIONS = frozenset({'and', 'or'})


def eval_boolean_operands(
    *,
    op: str,
    values: Sequence[Node],
    evaluate: Callable[[Node, Context], ProxySort],
    ctx: Context,
) -> ProxySort:
    assert op in BOOL_OPERATIONS, 'unsupported binary boolean operation'
    assert values
    value = evaluate(values[0], ctx)
    _require_boolean_operand(value)
    for subnode in values[1:]:
        truth = value.m_bool(ctx=ctx)
        selected = truth if op == 'and' else truth.m_not(ctx=ctx)
        guard = and_expr(selected, ctx.interrupted.m_not(ctx=ctx), ctx=ctx)
        # Contracts are also evaluated in contexts containing completed return
        # paths. Returns gate side effects, not the pure predicate's value.
        known = z3.simplify(selected.expr)
        if z3.is_false(known):
            # The remaining source expressions do not execute in Python.
            return value
        if z3.is_true(known) and z3.is_true(z3.simplify(guard.expr)):
            with ctx.trace.guard(BOOLEAN_OPERAND):
                value = evaluate(subnode, ctx)
            _require_boolean_operand(value)
            continue

        branch = ctx.make_child()
        with ctx.trace.guard(BOOLEAN_OPERAND):
            right = evaluate(subnode, branch)
        _require_boolean_operand(right)
        if branch.scope.layer:
            raise UnsupportedError('scope changes inside a boolean operand are not supported')
        if not z3.is_true(known) and type(value) is not type(right):
            raise UnsupportedError(
                'cannot merge boolean operands of different types',
                value.type_name, right.type_name,
            )
        merge_branch_layers(branch=branch, guard=guard, ctx=ctx)
        # Only matching immutable scalar models reach this constructor. Do not
        # coerce False to 0 or rebuild a mutable object's identity from its Z3
        # contents; both change the selected operand's Python meaning.
        value = right if z3.is_true(known) else if_expr(selected, right, value, ctx=ctx)
    return value


def _require_boolean_operand(value: ProxySort) -> None:
    if not isinstance(value, (types.bool, types.int, types.float, types.str)):
        raise UnsupportedError(
            'unsupported boolean operand type',
            getattr(value, 'type_name', type(value).__name__),
        )
