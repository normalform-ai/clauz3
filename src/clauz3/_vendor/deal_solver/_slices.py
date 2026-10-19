"""Python's unit-step sequence bounds, shared by strings, lists and tuples."""

from __future__ import annotations

import z3


def sequence_index(sequence, index, *, ctx, type_name):
    from ._proxies import types

    if not isinstance(index, (types.int, types.bool)):
        ctx.add_exception(TypeError, 'sequence indices must be integers')
        return z3.IntVal(0, ctx=ctx.z3_ctx)
    raw = index.m_int(ctx=ctx).expr
    length = z3.Length(sequence)
    offset = z3.If(raw < 0, length + raw, raw)
    ctx.add_exception(
        IndexError, f'{type_name} index out of range',
        cond=types.bool(z3.Or(offset < 0, offset >= length)),
    )
    return offset


def sequence_slice(sequence, start, stop, *, ctx):
    from ._proxies import types
    from ._proxies._none import NoneSort

    length = z3.Length(sequence)

    def bound(value, default):
        if isinstance(value, NoneSort):
            return default
        if not isinstance(value, (types.int, types.bool)):
            ctx.add_exception(TypeError, 'slice indices must be integers or None')
            return default
        raw = value.m_int(ctx=ctx).expr
        offset = z3.If(raw < 0, length + raw, raw)
        return z3.If(offset < 0, 0, z3.If(offset > length, length, offset))

    lower = bound(start, z3.IntVal(0, ctx=ctx.z3_ctx))
    upper = bound(stop, length)
    return z3.SubSeq(sequence, lower, z3.If(upper > lower, upper - lower, 0))
