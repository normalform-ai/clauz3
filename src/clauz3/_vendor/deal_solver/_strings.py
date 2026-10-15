"""Python strings as sequences of full Unicode code points."""

from __future__ import annotations

import z3

from ._exceptions import UnsupportedError


def string_sort(ctx: z3.Context | None = None) -> z3.SeqSortRef:
    """One unsigned 21-bit element per Python character, including surrogates."""
    return z3.SeqSort(z3.BitVecSort(21, ctx=ctx))


def is_string_sort(sort: z3.SortRef) -> bool:
    return (
        isinstance(sort, z3.SeqSortRef)
        and z3.is_bv_sort(sort.basis())
        and sort.basis().size() == 21
    )


def is_string_expr(expr: z3.ExprRef) -> bool:
    return is_string_sort(expr.sort())


def string_domain(expr: z3.SeqRef) -> z3.BoolRef:
    """The complete Python alphabet, not a narrower runtime restriction."""
    index = z3.FreshInt('string_index', ctx=expr.ctx)
    return z3.ForAll(
        index,
        z3.Implies(
            z3.And(index >= 0, index < z3.Length(expr)),
            z3.ULE(expr[index], z3.BitVecVal(0x10FFFF, 21, ctx=expr.ctx)),
        ),
    )


def string_val(value: str, ctx: z3.Context | None = None) -> z3.SeqRef:
    """Construct exact code points without parsing display-text escapes."""
    parts = [z3.Unit(z3.BitVecVal(ord(char), 21, ctx=ctx)) for char in value]
    if not parts:
        return z3.Empty(string_sort(ctx))
    if len(parts) == 1:
        return parts[0]
    return z3.Concat(*parts)


def string_value(expr: z3.ExprRef) -> str:
    """Decode concrete sequence structure; refuse unknown or invalid elements."""
    if not is_string_expr(expr):
        raise UnsupportedError('expected a concrete string')
    pending = [z3.simplify(expr)]
    characters = []
    while pending:
        node = pending.pop()
        if z3.is_app_of(node, z3.Z3_OP_SEQ_EMPTY):
            continue
        if z3.is_app_of(node, z3.Z3_OP_SEQ_CONCAT):
            pending.extend(reversed(node.children()))
            continue
        if not z3.is_app_of(node, z3.Z3_OP_SEQ_UNIT) or not z3.is_bv_value(node.arg(0)):
            raise UnsupportedError('expected a concrete string')
        point = node.arg(0).as_long()
        if point > 0x10FFFF:
            raise UnsupportedError('string value exceeds Python Unicode range U+0000..U+10FFFF')
        characters.append(chr(point))
    return ''.join(characters)
