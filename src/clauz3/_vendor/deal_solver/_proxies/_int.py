from __future__ import annotations

import operator
import typing

import z3

from .._exceptions import UnsupportedError
from ._proxy import ProxySort
from ._registry import types
from ._type_factory import TypeFactory

if typing.TYPE_CHECKING:
    from .._context import Context
    from ._bool import BoolSort
    from ._float import FloatSort, FPSort, RealSort
    from ._str import StrSort


# Round-to-nearest-even converts values at or beyond this midpoint to infinity.
# CPython raises OverflowError instead of returning that infinity for int-to-
# float conversion and for an overflowing int true-division result.
FLOAT_OVERFLOW_THRESHOLD = 2**1024 - 2**970


def _abs_int_expr(expr: z3.ArithRef) -> z3.ArithRef:
    return z3.If(expr >= 0, expr, -expr)


# Native Python bitwise evaluation is exact, but must not turn a compact source
# expression (especially a left shift) into an unbounded allocation.
MAX_CONCRETE_BITWISE_BITS = 4096
MAX_CONCRETE_EVAL_NODES = 1024
MAX_CONCRETE_EVAL_DEPTH = 128
MAX_INTEGER_LITERAL_BITS = 100_000
_MAX_CONCRETE_BITWISE_ABS = (1 << MAX_CONCRETE_BITWISE_BITS) - 1
# 0.30103 is a slight overestimate of log10(2), so this bounds the decimal
# digits without depending on Python's process-global int-to-string limit.
_MAX_CONCRETE_BITWISE_DECIMAL_DIGITS = (
    MAX_CONCRETE_BITWISE_BITS * 30103 // 100000 + 1
)
_Z3_LITERAL_CHUNK_BITS = 1024
_Z3_LITERAL_CHUNK_BASE = 1 << _Z3_LITERAL_CHUNK_BITS
_Z3_LITERAL_CHUNK_MASK = _Z3_LITERAL_CHUNK_BASE - 1


def _int_val_without_decimal_expansion(
    value: int,
    *,
    ctx: z3.Context | None,
) -> z3.ArithRef:
    """Construct a large Z3 integer without decimal-string conversion."""
    if value.bit_length() > MAX_INTEGER_LITERAL_BITS:
        raise UnsupportedError(
            f'integer literal exceeds the {MAX_INTEGER_LITERAL_BITS}-bit '
            'safety limit',
        )
    if value.bit_length() <= MAX_CONCRETE_BITWISE_BITS:
        return z3.IntVal(value, ctx=ctx)

    magnitude = abs(value)
    chunks: list[int] = []
    while magnitude:
        chunks.append(magnitude & _Z3_LITERAL_CHUNK_MASK)
        magnitude >>= _Z3_LITERAL_CHUNK_BITS

    expr = z3.IntVal(chunks.pop(), ctx=ctx)
    base = z3.IntVal(_Z3_LITERAL_CHUNK_BASE, ctx=ctx)
    while chunks:
        expr = expr * base + z3.IntVal(chunks.pop(), ctx=ctx)
    if value < 0:
        return -expr
    return expr


def _raise_bitwise_width(*, op: str, role: str) -> typing.NoReturn:
    raise UnsupportedError(
        f'{role} for concrete integer bitwise operation {op!r} exceeds '
        f'the {MAX_CONCRETE_BITWISE_BITS}-bit safety limit',
    )


def _check_bitwise_width(value: int, *, op: str, role: str) -> None:
    if value.bit_length() > MAX_CONCRETE_BITWISE_BITS:
        _raise_bitwise_width(op=op, role=role)


class _BoundedConcreteIntEvaluator:
    """Reduce a concrete Z3 integer DAG without invoking its simplifier."""

    def __init__(self, *, op: str, role: str) -> None:
        self._op = op
        self._role = role
        self._seen: set[int] = set()
        self._ints: dict[int, int | None] = {}
        self._bools: dict[int, bool | None] = {}

    def evaluate(self, expr: z3.ArithRef) -> int | None:
        return self._eval_int(expr, depth=1)

    def _enter(self, expr: z3.ExprRef, *, depth: int) -> None:
        if depth > MAX_CONCRETE_EVAL_DEPTH:
            self._raise_budget_limit()
        node_id = expr.get_id()
        if node_id in self._seen:
            return
        self._seen.add(node_id)
        if len(self._seen) > MAX_CONCRETE_EVAL_NODES:
            self._raise_budget_limit()

    def _raise_budget_limit(self) -> typing.NoReturn:
        raise UnsupportedError(
            f'{self._role} expression for concrete integer bitwise operation '
            f'{self._op!r} exceeds the bounded evaluation budget of '
            f'{MAX_CONCRETE_EVAL_NODES} nodes or '
            f'{MAX_CONCRETE_EVAL_DEPTH} levels',
        )

    def _bounded(self, value: int) -> int:
        _check_bitwise_width(value, op=self._op, role=self._role)
        return value

    def _eval_int(self, expr: z3.ArithRef, *, depth: int) -> int | None:
        node_id = expr.get_id()
        if node_id in self._ints:
            return self._ints[node_id]
        self._enter(expr, depth=depth)

        if z3.is_int_value(expr):
            text = expr.as_string()
            digits = text[1:] if text.startswith('-') else text
            if len(digits) > _MAX_CONCRETE_BITWISE_DECIMAL_DIGITS:
                _raise_bitwise_width(op=self._op, role=self._role)
            result = self._bounded(expr.as_long())
            self._ints[node_id] = result
            return result

        if not z3.is_app(expr):
            self._ints[node_id] = None
            return None

        kind = expr.decl().kind()
        result: int | None
        if kind == z3.Z3_OP_ADD:
            result = self._eval_add(expr, depth=depth)
        elif kind == z3.Z3_OP_SUB:
            result = self._eval_sub(expr, depth=depth)
        elif kind == z3.Z3_OP_MUL:
            result = self._eval_mul(expr, depth=depth)
        elif kind == z3.Z3_OP_UMINUS:
            value = self._eval_int(expr.arg(0), depth=depth + 1)
            result = None if value is None else self._bounded(-value)
        elif kind in {z3.Z3_OP_IDIV, z3.Z3_OP_MOD}:
            result = self._eval_division(expr, kind=kind, depth=depth)
        elif kind == z3.Z3_OP_ITE:
            condition = self._eval_bool(expr.arg(0), depth=depth + 1)
            if condition is None:
                result = None
            else:
                branch = expr.arg(1 if condition else 2)
                result = self._eval_int(branch, depth=depth + 1)
        else:
            result = None

        self._ints[node_id] = result
        return result

    def _eval_add(self, expr: z3.ArithRef, *, depth: int) -> int | None:
        result = 0
        for child in expr.children():
            value = self._eval_int(child, depth=depth + 1)
            if value is None:
                return None
            result = self._bounded(result + value)
        return result

    def _eval_sub(self, expr: z3.ArithRef, *, depth: int) -> int | None:
        children = expr.children()
        if not children:
            return None
        result = self._eval_int(children[0], depth=depth + 1)
        if result is None:
            return None
        for child in children[1:]:
            value = self._eval_int(child, depth=depth + 1)
            if value is None:
                return None
            result = self._bounded(result - value)
        return result

    def _eval_mul(self, expr: z3.ArithRef, *, depth: int) -> int | None:
        result = 1
        for child in expr.children():
            value = self._eval_int(child, depth=depth + 1)
            if value is None:
                return None
            if (
                result != 0
                and value != 0
                and abs(result) > _MAX_CONCRETE_BITWISE_ABS // abs(value)
            ):
                _raise_bitwise_width(op=self._op, role=self._role)
            result *= value
        return result

    def _eval_division(
        self,
        expr: z3.ArithRef,
        *,
        kind: int,
        depth: int,
    ) -> int | None:
        left = self._eval_int(expr.arg(0), depth=depth + 1)
        right = self._eval_int(expr.arg(1), depth=depth + 1)
        if left is None or right in {None, 0}:
            return None
        assert right is not None
        if kind == z3.Z3_OP_MOD:
            return self._bounded(left % abs(right))
        if right > 0:
            return self._bounded(left // right)
        return self._bounded(-(left // -right))

    def _eval_bool(self, expr: z3.BoolRef, *, depth: int) -> bool | None:
        node_id = expr.get_id()
        if node_id in self._bools:
            return self._bools[node_id]
        self._enter(expr, depth=depth)

        if z3.is_true(expr):
            result: bool | None = True
        elif z3.is_false(expr):
            result = False
        elif not z3.is_app(expr):
            result = None
        else:
            kind = expr.decl().kind()
            if kind == z3.Z3_OP_NOT:
                value = self._eval_bool(expr.arg(0), depth=depth + 1)
                result = None if value is None else not value
            elif kind in {z3.Z3_OP_AND, z3.Z3_OP_OR}:
                result = self._eval_bool_combination(
                    expr,
                    is_and=kind == z3.Z3_OP_AND,
                    depth=depth,
                )
            elif kind in {
                z3.Z3_OP_EQ,
                z3.Z3_OP_DISTINCT,
                z3.Z3_OP_LE,
                z3.Z3_OP_LT,
                z3.Z3_OP_GE,
                z3.Z3_OP_GT,
            }:
                result = self._eval_comparison(expr, kind=kind, depth=depth)
            elif kind == z3.Z3_OP_ITE:
                condition = self._eval_bool(expr.arg(0), depth=depth + 1)
                if condition is None:
                    result = None
                else:
                    branch = expr.arg(1 if condition else 2)
                    result = self._eval_bool(branch, depth=depth + 1)
            else:
                result = None

        self._bools[node_id] = result
        return result

    def _eval_bool_combination(
        self,
        expr: z3.BoolRef,
        *,
        is_and: bool,
        depth: int,
    ) -> bool | None:
        saw_unknown = False
        for child in expr.children():
            value = self._eval_bool(child, depth=depth + 1)
            if value is None:
                saw_unknown = True
                continue
            if is_and and not value:
                return False
            if not is_and and value:
                return True
        if saw_unknown:
            return None
        return is_and

    def _eval_comparison(
        self,
        expr: z3.BoolRef,
        *,
        kind: int,
        depth: int,
    ) -> bool | None:
        if expr.num_args() != 2:
            return None
        left_expr = expr.arg(0)
        right_expr = expr.arg(1)
        if z3.is_int(left_expr) and z3.is_int(right_expr):
            left = self._eval_int(left_expr, depth=depth + 1)
            right = self._eval_int(right_expr, depth=depth + 1)
        elif z3.is_bool(left_expr) and z3.is_bool(right_expr):
            left = self._eval_bool(left_expr, depth=depth + 1)
            right = self._eval_bool(right_expr, depth=depth + 1)
        else:
            return None
        if left is None or right is None:
            return None
        if kind == z3.Z3_OP_EQ:
            return left == right
        if kind == z3.Z3_OP_DISTINCT:
            return left != right
        if kind == z3.Z3_OP_LE:
            return left <= right
        if kind == z3.Z3_OP_LT:
            return left < right
        if kind == z3.Z3_OP_GE:
            return left >= right
        if kind == z3.Z3_OP_GT:
            return left > right
        raise RuntimeError('unreachable')


@types.add
class IntSort(ProxySort):
    type_name = 'int'
    methods = ProxySort.methods.copy()

    def __init__(self, expr) -> None:
        assert z3.is_int(expr), f'expected int, given {type(expr)}'
        self.expr = expr

    @classmethod
    def var(cls, *, name: str, ctx: z3.Context) -> IntSort:
        expr = z3.Const(name=name, sort=z3.IntSort(ctx=ctx))
        return cls(expr=expr)

    @staticmethod
    def val(x: int, ctx: Context) -> IntSort:
        expr = _int_val_without_decimal_expansion(x, ctx=ctx.z3_ctx)
        return types.int(expr=expr)

    @property
    def factory(self) -> TypeFactory:
        cls = type(self)
        return TypeFactory(
            type=cls,
            default=cls(expr=z3.IntVal(0, ctx=self.expr.ctx)),
            subtypes=(),
        )

    @methods.add(name='__int__')
    @methods.add(name='conjugate')
    @methods.add(name='numerator', prop=True)
    @methods.add(name='real', prop=True)
    def m_int(self, ctx: Context) -> IntSort:
        return self

    @methods.add(name='__float__')
    def m_float(self, ctx: Context) -> FPSort:
        """Convert an integer to Python's binary64 float, failing on overflow."""
        result = self.m_fp(ctx=ctx)
        ctx.add_exception(
            OverflowError,
            'int too large to convert to float',
            cond=types.bool(
                expr=_abs_int_expr(self.expr) >= FLOAT_OVERFLOW_THRESHOLD,
            ),
        )
        # If execution gets past the exception above, this conversion is
        # finite.  Preserve that fact for a subsequent ``int(float_value)`` so
        # Z3 does not need to prove the same floating-point bound again.
        return type(result)(expr=result.expr, finite_when_reached=True)

    def m_real(self, ctx: Context) -> RealSort:
        from ._float import RealSort

        return RealSort(z3.ToReal(self.expr)).m_real(ctx=ctx)

    def m_fp(self, ctx: Context):
        from ._float import FPSort

        expr = z3.ToReal(self.expr)
        return FPSort(
            expr=z3.fpRealToFP(
                z3.RNE(ctx=ctx.z3_ctx),
                expr,
                FPSort.sort(ctx=ctx.z3_ctx),
            )
        )

    @methods.add(name='__str__')
    def m_str(self, ctx: Context) -> StrSort:
        from .._strings import string_domain

        concrete = z3.simplify(self.expr)
        if z3.is_int_value(concrete):
            try:
                return types.str.val(str(concrete.as_long()), ctx=ctx)
            except ValueError as exc:
                raise UnsupportedError('cannot convert integer literal to str') from exc
        # Z3's decimal conversion produces only ASCII digits, so its narrower
        # native string alphabet is safe as an internal intermediate. Python
        # includes a sign for negative integers; Z3's IntToStr does not.
        text = z3.If(
            self.expr >= 0,
            z3.IntToStr(self.expr),
            z3.Concat(z3.StringVal('-', ctx=self.expr.ctx), z3.IntToStr(-self.expr)),
        )
        char = z3.FreshConst(z3.CharSort(ctx=self.expr.ctx), prefix='decimal_character')
        encode = z3.Lambda(char, z3.Int2BV(z3.CharToInt(char), 21))
        expr = z3.SeqMap(encode, text)
        ctx.given.add(types.bool(expr=string_domain(expr)))
        return types.str(expr=expr)

    @methods.add(name='__bool__')
    def m_bool(self, ctx: Context) -> BoolSort:
        return types.bool(expr=self.expr != z3.IntVal(0))

    @methods.add(name='__abs__')
    def m_abs(self, ctx: Context) -> ProxySort:
        expr = z3.If(self.expr >= z3.IntVal(0, ctx=ctx.z3_ctx), self.expr, -self.expr)
        return types.int(expr=expr)

    def _math_op(
        self, other: ProxySort, handler: typing.Callable, ctx: Context
    ) -> ProxySort:
        if not isinstance(other, types.int):
            raise UnsupportedError('integer math helper requires an integer operand')
        expr = self._binary_op(other=other, handler=handler, ctx=ctx)
        if not z3.is_int(expr):
            return types.float(expr=expr)
        return types.int(expr=expr)

    @methods.add(name='__add__')
    def m_add(self, other: ProxySort, ctx: Context) -> ProxySort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if isinstance(other, types.float):
            return self.m_float(ctx=ctx).m_add(other, ctx=ctx)
        if not isinstance(other, (types.int, types.float)):
            return self._bad_bin_op(other, op='+', ctx=ctx)
        return self._math_op(other=other, handler=operator.__add__, ctx=ctx)

    @methods.add(name='__sub__')
    def m_sub(self, other: ProxySort, ctx: Context) -> ProxySort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if isinstance(other, types.float):
            return self.m_float(ctx=ctx).m_sub(other, ctx=ctx)
        if not isinstance(other, (types.int, types.float)):
            return self._bad_bin_op(other, op='-', ctx=ctx)
        return self._math_op(other=other, handler=operator.__sub__, ctx=ctx)

    @methods.add(name='__mul__')
    def m_mul(self, other: ProxySort, ctx: Context) -> ProxySort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if isinstance(other, (types.str, types.tuple)):
            return other.m_mul(self, ctx=ctx)
        if isinstance(other, types.float):
            return self.m_float(ctx=ctx).m_mul(other, ctx=ctx)
        if isinstance(other, types.int):
            return self._math_op(other=other, handler=operator.__mul__, ctx=ctx)
        return self._bad_bin_op(other, op='*', ctx=ctx)

    @methods.add(name='__pow__')
    def m_pow(self, other: ProxySort, ctx: Context) -> ProxySort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if isinstance(other, types.float):
            return self.m_float(ctx=ctx).m_pow(other, ctx=ctx)
        if not isinstance(other, (types.int, types.float)):
            return self._bad_bin_op(other, op='** or pow()', ctx=ctx)
        return self._math_op(other=other, handler=operator.__pow__, ctx=ctx)

    @methods.add(name='__truediv__')
    def m_truediv(self, other: ProxySort, ctx: Context) -> FloatSort:
        from ._float import FPSort

        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if isinstance(other, types.int):
            divisor_is_zero = other.expr == z3.IntVal(0, ctx=ctx.z3_ctx)
            ctx.add_exception(
                ZeroDivisionError,
                'division by zero',
                cond=types.bool(expr=divisor_is_zero),
            )
            ratio = z3.ToReal(self.expr) / z3.ToReal(other.expr)
            rounded = z3.fpRealToFP(z3.RNE(), ratio, FPSort.sort(ctx=ctx.z3_ctx))
            negative = (self.expr < 0) != (other.expr < 0)
            rounded = z3.If(
                z3.And(z3.fpIsZero(rounded), negative),
                z3.fpMinusZero(FPSort.sort(ctx=ctx.z3_ctx)),
                rounded,
            )
            ctx.add_exception(
                OverflowError,
                'integer division result too large for a float',
                cond=types.bool(
                    expr=z3.And(
                        z3.Not(divisor_is_zero),
                        _abs_int_expr(self.expr)
                        >= FLOAT_OVERFLOW_THRESHOLD * _abs_int_expr(other.expr),
                    ),
                ),
            )
            return FPSort(expr=rounded, finite_when_reached=True)
        if not isinstance(other, types.float):
            self._bad_bin_op(other, op='/', ctx=ctx)
            return self.m_float(ctx=ctx)
        return self.m_float(ctx=ctx).m_truediv(other, ctx=ctx)

    @methods.add(name='__floordiv__')
    def m_floordiv(self, other: ProxySort, ctx: Context) -> ProxySort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if not isinstance(other, (types.int, types.float)):
            return self._bad_bin_op(other, op='//', ctx=ctx)
        if isinstance(other, types.float):
            return self.m_float(ctx=ctx).m_floordiv(other, ctx=ctx)
        zero = self.val(0, ctx=ctx).expr
        ctx.add_exception(
            ZeroDivisionError,
            'integer division or modulo by zero',
            cond=types.bool(expr=other.expr == zero),
        )
        result = types.int(
            z3.If(
                other.expr >= zero,
                self.expr / other.expr,
                -self.expr / -other.expr,
                ctx=ctx.z3_ctx,
            )
        )
        return result

    @methods.add(name='__mod__')
    def m_mod(self, other: ProxySort, ctx: Context) -> ProxySort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if not isinstance(other, (types.int, types.float)):
            return self._bad_bin_op(other, op='%', ctx=ctx)
        if isinstance(other, types.float):
            return self.m_float(ctx=ctx).m_mod(other, ctx=ctx)
        zero = self.val(0, ctx=ctx).expr
        ctx.add_exception(
            ZeroDivisionError,
            'integer modulo by zero',
            cond=types.bool(expr=other.expr == zero),
        )
        result = types.int(
            z3.If(
                other.expr >= zero,
                self.expr % other.expr,
                -(-self.expr % -other.expr),
                ctx=ctx.z3_ctx,
            )
        )
        return result

    @methods.add(name='__inv__')
    def m_inv(self, ctx: Context) -> IntSort:
        value = self._concrete_int(self.expr, op='~', role='operand')
        if value is None:
            raise UnsupportedError(
                'symbolic integer bitwise operation is unsupported',
                '~',
            )
        result = ~value
        _check_bitwise_width(result, op='~', role='result')
        return self.val(result, ctx=ctx)

    @staticmethod
    def _concrete_int(
        expr: z3.ArithRef,
        *,
        op: str,
        role: str,
    ) -> int | None:
        evaluator = _BoundedConcreteIntEvaluator(op=op, role=role)
        return evaluator.evaluate(expr)

    def _bitwise_op(
        self,
        other: ProxySort,
        handler: typing.Callable[[int, int], int],
        op: str,
        ctx: Context,
    ) -> IntSort:
        left = self._concrete_int(self.expr, op=op, role='left operand')
        right = self._concrete_int(other.expr, op=op, role='right operand')
        if left is None or right is None:
            raise UnsupportedError(
                'symbolic integer bitwise operation is unsupported',
                op,
            )
        result = handler(left, right)
        _check_bitwise_width(result, op=op, role='result')
        return self.val(result, ctx=ctx)

    def _shift_op(
        self,
        other: ProxySort,
        handler: typing.Callable[[int, int], int],
        op: str,
        ctx: Context,
    ) -> IntSort:
        left = self._concrete_int(self.expr, op=op, role='left operand')
        shift = self._concrete_int(other.expr, op=op, role='shift count')
        if left is None or shift is None:
            raise UnsupportedError(
                'symbolic integer bitwise operation is unsupported',
                op,
            )
        if shift < 0:
            raise UnsupportedError(
                'negative shift count is unsupported in concrete integer '
                'bitwise evaluation',
                op,
            )
        if shift > MAX_CONCRETE_BITWISE_BITS:
            raise UnsupportedError(
                f'shift count for concrete integer bitwise operation {op!r} '
                f'exceeds the safety limit of '
                f'{MAX_CONCRETE_BITWISE_BITS} positions',
            )
        if op == '<<' and left != 0:
            result_width = left.bit_length() + shift
            if result_width > MAX_CONCRETE_BITWISE_BITS:
                raise UnsupportedError(
                    f'result for concrete integer bitwise operation {op!r} '
                    f'exceeds the {MAX_CONCRETE_BITWISE_BITS}-bit safety limit',
                )
        result = handler(left, shift)
        _check_bitwise_width(result, op=op, role='result')
        return self.val(result, ctx=ctx)

    @methods.add(name='__and__')
    def m_and(self, other: ProxySort, ctx: Context):
        """self & other
        """
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if not isinstance(other, types.int):
            return self._bad_bin_op(other, op='&', ctx=ctx)
        return self._bitwise_op(
            other=other,
            handler=operator.__and__,
            op='&',
            ctx=ctx,
        )

    @methods.add(name='__or__')
    def m_or(self, other: ProxySort, ctx: Context):
        """self | other
        """
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if not isinstance(other, types.int):
            return self._bad_bin_op(other, op='|', ctx=ctx)
        return self._bitwise_op(
            other=other,
            handler=operator.__or__,
            op='|',
            ctx=ctx,
        )

    @methods.add(name='__xor__')
    def m_xor(self, other: ProxySort, ctx: Context):
        """self ^ other
        """
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if not isinstance(other, types.int):
            return self._bad_bin_op(other, op='^', ctx=ctx)
        return self._bitwise_op(
            other=other,
            handler=operator.__xor__,
            op='^',
            ctx=ctx,
        )

    @methods.add(name='__lshift__')
    def m_lshift(self, other: ProxySort, ctx: Context):
        """self << other
        """
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if not isinstance(other, types.int):
            return self._bad_bin_op(other, op='<<', ctx=ctx)
        return self._shift_op(
            other=other,
            handler=operator.__lshift__,
            op='<<',
            ctx=ctx,
        )

    @methods.add(name='__rshift__')
    def m_rshift(self, other: ProxySort, ctx: Context):
        """self >> other
        """
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if not isinstance(other, types.int):
            return self._bad_bin_op(other, op='>>', ctx=ctx)
        return self._shift_op(
            other=other,
            handler=operator.__rshift__,
            op='>>',
            ctx=ctx,
        )

    def _comp_op(
        self, other: ProxySort, handler: typing.Callable, ctx: Context
    ) -> BoolSort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if isinstance(other, types.float):
            reverse_handler = {
                operator.__lt__: operator.__gt__,
                operator.__le__: operator.__ge__,
                operator.__gt__: operator.__lt__,
                operator.__ge__: operator.__le__,
            }.get(handler)
            if reverse_handler is None:
                raise UnsupportedError('unsupported floating-point comparison handler')
            return other._comp_op(
                other=self,
                handler=reverse_handler,
                ctx=ctx,
            )
        return super()._comp_op(other=other, handler=handler, ctx=ctx)

    @methods.add(name='denominator', prop=True)
    def m_denominator(self, ctx: Context) -> IntSort:
        return self.val(1, ctx=ctx)

    @methods.add(name='imag', prop=True)
    def m_imag(self, ctx: Context) -> IntSort:
        return self.val(0, ctx=ctx)

    @methods.add(name='__eq__')
    def m_eq(self, other: ProxySort, ctx: Context) -> BoolSort:
        if isinstance(other, types.int):
            return types.bool(self.expr == other.expr)
        if isinstance(other, types.bool):
            return types.bool(self.expr == other.m_int(ctx=ctx).expr)
        if isinstance(other, types.float):
            return other.m_eq(self, ctx=ctx)
        return types.bool.val(False, ctx=ctx)

    @methods.add(name='as_integer_ratio')
    @methods.add(name='bit_length')
    @methods.add(name='from_bytes')
    @methods.add(name='to_bytes')
    def unsupported(self, *args, **kwargs):
        msg = f'unsupported attribute for type {self.type_name}'
        raise UnsupportedError(msg)
