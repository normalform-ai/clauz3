from __future__ import annotations

import typing
from random import choices
from string import ascii_letters

import z3

from .._context import Context
from .._exceptions import UnsupportedError
from ._none import NoneSort
from ._registry import types


if typing.TYPE_CHECKING:
    from ._bool import BoolSort
    from ._proxy import ProxySort


T = typing.TypeVar('T', bound='ProxySort')


def if_expr(
    test: ProxySort,
    val_then: T,
    val_else: T,
    ctx: Context,
) -> T:
    test_expr = test.m_bool(ctx=ctx).expr
    if isinstance(val_then, NoneSort) or isinstance(val_else, NoneSort):
        if isinstance(val_then, NoneSort) and isinstance(val_else, NoneSort):
            return val_then
        raise UnsupportedError('cannot merge None with a non-None value')
    # FuncSort and LambdaSort wrap an astroid node rather than a z3 term, so a
    # name bound to a function in both branches has nothing to merge.
    for val in (val_then, val_else):
        if not hasattr(val, 'expr'):
            raise UnsupportedError(
                'cannot merge branches binding', val.type_name,
            )
    containers = (types.list, types.dict, types.set, types.tuple)
    if isinstance(val_then, containers) or isinstance(val_else, containers):
        return _merge_collection(test, val_then, val_else, ctx=ctx)
    # Branches can still carry different z3 sorts -- `1.0 if c else 0` merges a
    # float arm with an int arm. Report the unsupported merge directly rather
    # than relying on Theorem.prove's generic Z3 failure classification.
    try:
        expr = z3.If(
            test.m_bool(ctx=ctx).expr,
            val_then.expr,
            val_else.expr,
            ctx=ctx.z3_ctx,
        )
    except z3.Z3Exception as exc:
        raise UnsupportedError(
            'cannot merge branches of different types',
            f'{val_then.type_name} and {val_else.type_name}',
        ) from exc
    return type(val_then)(expr)


def _merge_collection(test, left, right, *, ctx: Context):
    from ._dict import UntypedDictSort
    from ._list import UntypedListSort
    from ._set import UntypedSetSort
    from ._var_tuple import UntypedVarTupleSort

    empty = (UntypedListSort, UntypedDictSort, UntypedSetSort, UntypedVarTupleSort)
    if left.type_name != right.type_name:
        raise UnsupportedError('cannot merge incompatible collection values')
    if isinstance(left, empty) and isinstance(right, empty):
        result = type(left)()
    else:
        prototype = right if isinstance(left, empty) else left
        if (
            not isinstance(left, empty)
            and not isinstance(right, empty)
            and not left.factory.match(right.factory)
        ):
            raise UnsupportedError('cannot merge incompatible collection values')
        lhs = prototype.factory.default.expr if isinstance(left, empty) else left.expr
        rhs = prototype.factory.default.expr if isinstance(right, empty) else right.expr
        result = prototype.evolve(expr=z3.If(test.m_bool(ctx=ctx).expr, lhs, rhs))
    ctx.ownership.merge(result, left, right)
    return result


def random_name(prefix: str = 'v') -> str:
    suffix = ''.join(choices(ascii_letters, k=20))
    return prefix + '__' + suffix


def switch(*cases: tuple[ProxySort, T], default, ctx: Context) -> T:
    result = default
    for cond, then in reversed(cases):
        result = if_expr(cond, then, result, ctx=ctx)
    return result


def and_expr(*args: ProxySort, ctx: Context) -> BoolSort:
    return types.bool(z3.And(*[arg.m_bool(ctx=ctx).expr for arg in args]))


def or_expr(*args: ProxySort, ctx: Context) -> BoolSort:
    return types.bool(z3.Or(*[arg.m_bool(ctx=ctx).expr for arg in args]))
