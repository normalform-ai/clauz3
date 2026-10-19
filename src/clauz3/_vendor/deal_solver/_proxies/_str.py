from __future__ import annotations

import sys
import typing

import z3

from .._exceptions import UnsupportedError
from .._slices import sequence_index, sequence_slice
from .._strings import is_string_expr, string_sort, string_val, string_value
from ._proxy import ProxySort
from ._registry import types
from ._type_factory import TypeFactory


if typing.TYPE_CHECKING:
    from .._context import Context
    from ._bool import BoolSort
    from ._float import FloatSort
    from ._int import IntSort


@types.add
class StrSort(ProxySort):
    type_name = 'str'
    methods = ProxySort.methods.copy()
    expr: z3.SeqRef

    def __init__(self, expr) -> None:
        if not is_string_expr(expr):
            raise UnsupportedError('expected a Python string sequence; use string_val or string_sort')
        self.expr = expr

    @classmethod
    def var(cls, *, name: str, ctx: z3.Context) -> StrSort:
        expr = z3.Const(
            name=name,
            sort=string_sort(ctx),
        )
        return cls(expr=expr)

    @staticmethod
    def val(val: str, ctx: Context) -> StrSort:
        return types.str(expr=string_val(val, ctx=ctx.z3_ctx))

    @property
    def factory(self) -> TypeFactory:
        cls = type(self)
        return TypeFactory(
            type=cls,
            default=cls(expr=string_val('', ctx=self.expr.ctx)),
            subtypes=(),
        )

    @methods.add(name='__int__')
    def m_int(self, ctx: Context) -> IntSort:
        # StrToInt accepts a narrower grammar and returns -1 for invalid text;
        # neither behavior represents Python's conversion or its exceptions.
        text = string_value(self.expr)
        try:
            value = int(text)
        except ValueError as exc:
            raise UnsupportedError('cannot convert string literal to int') from exc
        return types.int.val(value, ctx=ctx)

    @methods.add(name='__str__')
    def m_str(self, ctx: Context) -> StrSort:
        return self

    @methods.add(name='__float__')
    def m_float(self, ctx: Context) -> FloatSort:
        try:
            text = string_value(self.expr)
        except UnsupportedError as exc:
            raise UnsupportedError('cannot convert str to float') from exc
        try:
            val = float(text)
        except ValueError as exc:
            raise UnsupportedError('cannot convert string literal to float') from exc
        return types.float.val(val)

    @methods.add(name='__bool__')
    def m_bool(self, ctx: Context) -> BoolSort:
        assert self.expr is not None
        expr = self.expr != z3.Empty(self.expr.sort())
        return types.bool(expr=expr)

    @methods.add(name='__getitem__')
    def m_getitem(self, index: ProxySort, ctx: Context) -> ProxySort:
        offset = sequence_index(self.expr, index, ctx=ctx, type_name='string')
        expr = z3.SubSeq(
            s=self.expr,
            offset=offset,
            length=z3.IntVal(1, ctx=ctx.z3_ctx),
        )
        return types.str(expr=expr)

    def get_slice(self, start: ProxySort, stop: ProxySort, ctx: Context) -> StrSort:
        return types.str(sequence_slice(self.expr, start, stop, ctx=ctx))

    @methods.add(name='__contains__')
    def m_contains(self, item: ProxySort, ctx: Context) -> BoolSort:
        if not isinstance(item, types.str):
            msg = "'in <string>' requires string as left operand, not {}"
            msg = msg.format(item.type_name)
            ctx.add_exception(TypeError, msg)
            return types.bool.val(True, ctx=ctx)
        assert self.expr is not None
        expr = z3.Contains(self.expr, item.expr)
        return types.bool(expr=expr)

    @methods.add(name='startswith')
    def r_startswith(self, prefix: ProxySort, ctx: Context) -> BoolSort:
        assert self.expr is not None
        expr = z3.PrefixOf(prefix.expr, self.expr)
        return types.bool(expr=expr)

    @methods.add(name='endswith')
    def r_endswith(self, suffix: ProxySort, ctx: Context) -> BoolSort:
        assert self.expr is not None
        expr = z3.SuffixOf(suffix.expr, self.expr)
        return types.bool(expr=expr)

    @methods.add(name='index')
    def r_index(self, *args: ProxySort, ctx: Context, **kwargs: ProxySort) -> IntSort:
        if kwargs or len(args) not in (1, 2):
            raise UnsupportedError('unsupported str.index argument binding')
        other = args[0]
        if not isinstance(other, types.str):
            raise UnsupportedError('str.index requires a string substring')
        start = args[1] if len(args) == 2 else types.int.val(0, ctx=ctx)
        if not isinstance(start, (types.int, types.bool)):
            raise UnsupportedError('str.index requires an integer start')
        offset = start.m_int(ctx=ctx).expr
        length = z3.Length(self.expr)
        zero = z3.IntVal(0, ctx=ctx.z3_ctx)
        offset = z3.If(
            offset < zero,
            z3.If(length + offset < zero, zero, length + offset),
            offset,
        )
        # Positive offsets must not be clamped: even an empty substring is
        # absent when start exceeds len(self), whereas it occurs at len(self).
        # Generic Unicode sequences do not simplify empty-needle IndexOf as
        # native Z3 strings did. Spell out Python's exact empty-match rule so
        # the local success check still recognizes it without assumptions.
        result = z3.IndexOf(self.expr, other.expr, offset)
        if z3.is_true(z3.simplify(z3.Length(other.expr) == zero)):
            result = z3.If(
                offset == zero, zero,
                z3.If(offset <= length, offset, z3.IntVal(-1, ctx=ctx.z3_ctx)),
            )
        # IndexOf uses -1 where Python raises ValueError. Refuse locally unless
        # success is established without caller assumptions; a condition
        # predicate must not turn the sentinel into a successful proposition.
        if not z3.is_true(z3.simplify(result >= zero)):
            raise UnsupportedError('str.index search may raise ValueError')
        return types.int(expr=result)

    @methods.add(name='find')
    def r_find(self, *args: ProxySort, ctx: Context, **kwargs: ProxySort) -> IntSort:
        if kwargs or len(args) not in (1, 2):
            raise UnsupportedError('unsupported str.find argument binding')
        other = args[0]
        if not isinstance(other, types.str):
            raise UnsupportedError('str.find requires a string substring')
        start = args[1] if len(args) == 2 else types.int.val(0, ctx=ctx)
        if not isinstance(start, (types.int, types.bool)):
            raise UnsupportedError('str.find requires an integer start')
        offset = start.m_int(ctx=ctx).expr
        length = z3.Length(self.expr)
        zero = z3.IntVal(0, ctx=ctx.z3_ctx)
        offset = z3.If(
            offset < zero,
            z3.If(length + offset < zero, zero, length + offset),
            offset,
        )
        # Preserve positive offsets beyond the end: an empty needle matches
        # at len(self), but not after it. Unlike str.index, a missing match is
        # the legitimate -1 result, including for an uncertain symbolic search.
        return types.int(expr=z3.IndexOf(self.expr, other.expr, offset))

    @methods.add(name='__len__')
    def m_len(self, ctx: Context) -> IntSort:
        assert self.expr is not None
        return types.int(expr=z3.Length(self.expr))

    @methods.add(name='count')
    def r_count(self, *args, ctx: Context, **kwargs) -> IntSort:
        if len(args) != 1 or kwargs:
            raise UnsupportedError('str.count() requires one positional substring; start/end are not supported')
        other = args[0]
        if not isinstance(other, types.str):
            raise UnsupportedError('str.count() requires a string substring')
        try:
            text = string_value(self.expr)
            needle = string_value(other.expr)
        except UnsupportedError:
            pass
        else:
            return types.int.val(text.count(needle), ctx=ctx)

        # A conservative model of Python's nonoverlapping count. An exact
        # recursive sequence definition can make even printing a count require
        # expensive solver induction. All actual counts satisfy these bounds;
        # content-dependent arithmetic may therefore be inconclusive, never
        # silently assigned an invented exact value. A function (not a fresh
        # integer per call) preserves determinism for equal inputs.
        count = z3.Function(
            '$clauz3_str_count', self.expr.sort(), other.expr.sort(),
            z3.IntSort(ctx=ctx.z3_ctx),
        )(self.expr, other.expr)
        size = z3.Length(self.expr)
        width = z3.Length(other.expr)
        ctx.given.add(types.bool(z3.And(
            count >= 0,
            z3.If(width == 0, count == size + 1, z3.And(
                count * width <= size,
                (count == 0) == z3.Not(z3.Contains(self.expr, other.expr)),
            )),
        )))
        return types.int(expr=count)

    @methods.add(name='split')
    def r_split(self, *args, ctx: Context, **kwargs):
        from .._domains import add_value_domain
        from ._none import NoneSort

        if len(args) > 2 or set(kwargs) - {'sep', 'maxsplit'}:
            raise UnsupportedError('unsupported str.split argument binding')
        bound = dict(zip(('sep', 'maxsplit'), args))
        if bound.keys() & kwargs.keys():
            raise UnsupportedError('duplicate str.split argument')
        bound.update(kwargs)
        separator = bound.get('sep', NoneSort())
        maximum = bound.get('maxsplit', types.int.val(-1, ctx=ctx))
        if not isinstance(separator, (types.str, NoneSort)):
            raise UnsupportedError('str.split separator must be str or None')
        if not isinstance(maximum, (types.int, types.bool)):
            raise UnsupportedError('str.split maxsplit must be an integer')
        limit = maximum.m_int(ctx=ctx).expr
        valid_limit = z3.And(limit >= -sys.maxsize - 1, limit <= sys.maxsize)
        ctx.add_exception(
            OverflowError, 'Python int too large to convert to C ssize_t',
            cond=types.bool(z3.Not(valid_limit)),
        )
        whitespace = isinstance(separator, NoneSort)
        valid_separator = (
            z3.BoolVal(True, ctx=ctx.z3_ctx) if whitespace
            else z3.Length(separator.expr) > 0
        )
        if not whitespace:
            ctx.add_exception(
                ValueError, 'empty separator',
                cond=types.bool(z3.Not(valid_separator)),
            )
        try:
            text = string_value(self.expr)
            sep = None if whitespace else string_value(separator.expr)
            concrete_limit = z3.simplify(limit)
            if not z3.is_int_value(concrete_limit):
                raise UnsupportedError('symbolic maxsplit')
            pieces = text.split(sep, concrete_limit.as_long())
        except UnsupportedError:
            pass
        except (ValueError, OverflowError):
            # The exception above must still be proved unreachable. Do not
            # constrain an impossible successful result on that path.
            return types.list.val([types.str.val('', ctx=ctx)], ctx=ctx)
        else:
            if not pieces:
                return types.list(
                    expr=z3.Empty(z3.SeqSort(self.expr.sort())),
                    subtypes=(self.factory,),
                )
            return types.list.val([types.str.val(item, ctx=ctx) for item in pieces], ctx=ctx)

        # A deterministic overapproximation, not a recursive split definition.
        # Actual Python split results always satisfy these constraints. Unknown
        # contents stay unknown, so content-dependent safety cannot be invented
        # merely to make a program that prints a prefix easy to prove.
        result_sort = z3.SeqSort(self.expr.sort())
        if whitespace:
            result_expr = z3.Function(
                '$clauz3_str_split_whitespace', self.expr.sort(), limit.sort(), result_sort,
            )(self.expr, limit)
        else:
            result_expr = z3.Function(
                '$clauz3_str_split', self.expr.sort(), separator.expr.sort(),
                limit.sort(), result_sort,
            )(self.expr, separator.expr, limit)
        result = types.list(expr=result_expr, subtypes=(self.factory,))
        size = z3.Length(self.expr)
        count = z3.Length(result_expr)
        constraints = [z3.Implies(limit >= 0, count <= limit + 1)]
        if whitespace:
            constraints.append(count <= size)
        else:
            constraints.extend([
                count >= 1,
                (count - 1) * z3.Length(separator.expr) <= size,
                z3.Implies(
                    z3.Or(limit == 0, z3.Not(z3.Contains(self.expr, separator.expr))),
                    result_expr == z3.Unit(self.expr),
                ),
            ])
        ctx.given.add(types.bool(z3.Implies(
            z3.And(valid_limit, valid_separator), z3.And(*constraints),
        )))
        add_value_domain(result, ctx=ctx)
        return result

    @methods.add(name='__add__')
    def m_add(self, other: ProxySort, ctx: Context) -> ProxySort:
        if not isinstance(other, types.str):
            msg = 'can only concatenate str (not "{}") to {}'
            msg = msg.format(other.type_name, self.type_name)
            ctx.add_exception(TypeError, msg)
            return self
        return types.str(self.expr + other.expr)

    @methods.add(name='__mul__')
    def m_mul(self, other: ProxySort, ctx: Context) -> ProxySort:
        if not isinstance(other, types.int):
            msg = "can't multiply sequence by non-int of type '{}'"
            msg = msg.format(other.type_name)
            ctx.add_exception(TypeError, msg)
            return self
        raise UnsupportedError('cannot multiply str')

    @methods.add(name='__mod__')
    def m_mod(self, other: ProxySort, ctx: Context) -> StrSort:
        msg = 'not all arguments converted during string formatting'
        ctx.add_exception(TypeError, msg)
        return self

    @methods.add(name='__sub__')
    def m_sub(self, other: ProxySort, ctx: Context) -> StrSort:
        return self._bad_bin_op(other, op='-', ctx=ctx)

    @methods.add(name='__pos__')
    def m_pos(self, ctx: Context) -> StrSort:
        return self._bad_un_op(op='+', ctx=ctx)

    @methods.add(name='__neg__')
    def m_neg(self, ctx: Context) -> StrSort:
        return self._bad_un_op(op='-', ctx=ctx)

    @methods.add(name='__eq__')
    def m_eq(self, other: ProxySort, ctx: Context) -> BoolSort:
        if not isinstance(other, types.str):
            return types.bool.val(False, ctx=ctx)
        return types.bool(self.expr == other.expr)

    def _order(self, other: ProxySort, *, reverse: bool, inclusive: bool) -> BoolSort:
        if not isinstance(other, types.str):
            raise UnsupportedError('string ordering requires a string operand')
        left, right = (other.expr, self.expr) if reverse else (self.expr, other.expr)
        index = z3.FreshInt('string_order_index', ctx=self.expr.ctx)
        different = z3.Exists(
            index,
            z3.And(
                index >= 0,
                index < z3.Length(left),
                index < z3.Length(right),
                z3.SubSeq(left, 0, index) == z3.SubSeq(right, 0, index),
                z3.ULT(left[index], right[index]),
            ),
        )
        prefix = z3.And(z3.PrefixOf(left, right), z3.Length(left) < z3.Length(right))
        expr = z3.Or(prefix, different)
        if inclusive:
            expr = z3.Or(left == right, expr)
        return types.bool(expr=expr)

    @methods.add(name='__lt__')
    def m_lt(self, other: ProxySort, ctx: Context) -> BoolSort:
        return self._order(other, reverse=False, inclusive=False)

    @methods.add(name='__le__')
    def m_le(self, other: ProxySort, ctx: Context) -> BoolSort:
        return self._order(other, reverse=False, inclusive=True)

    @methods.add(name='__gt__')
    def m_gt(self, other: ProxySort, ctx: Context) -> BoolSort:
        return self._order(other, reverse=True, inclusive=False)

    @methods.add(name='__ge__')
    def m_ge(self, other: ProxySort, ctx: Context) -> BoolSort:
        return self._order(other, reverse=True, inclusive=True)

    @methods.add(name='capitalize')
    @methods.add(name='casefold')
    @methods.add(name='center')
    @methods.add(name='encode')
    @methods.add(name='expandtabs')
    @methods.add(name='format')
    @methods.add(name='format_map')
    @methods.add(name='isalnum')
    @methods.add(name='isalpha')
    @methods.add(name='isascii')
    @methods.add(name='isdecimal')
    @methods.add(name='isdigit')
    @methods.add(name='isidentifier')
    @methods.add(name='islower')
    @methods.add(name='isnumeric')
    @methods.add(name='isprintable')
    @methods.add(name='isspace')
    @methods.add(name='istitle')
    @methods.add(name='isupper')
    @methods.add(name='join')
    @methods.add(name='ljust')
    @methods.add(name='lower')
    @methods.add(name='lstrip')
    @methods.add(name='maketrans')
    @methods.add(name='partition')
    @methods.add(name='removeprefix')
    @methods.add(name='removesuffix')
    @methods.add(name='replace')
    @methods.add(name='rfind')
    @methods.add(name='rindex')
    @methods.add(name='rjust')
    @methods.add(name='rpartition')
    @methods.add(name='rsplit')
    @methods.add(name='rstrip')
    @methods.add(name='splitlines')
    @methods.add(name='strip')
    @methods.add(name='swapcase')
    @methods.add(name='title')
    @methods.add(name='translate')
    @methods.add(name='upper')
    @methods.add(name='zfill')
    def unsupported(self, *args, **kwargs):
        msg = 'unsupported attribute for type {}'.format(self.type_name)
        raise UnsupportedError(msg)
