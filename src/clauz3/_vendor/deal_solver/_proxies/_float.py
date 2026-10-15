from __future__ import annotations

import math
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
    from ._int import IntSort


FP_HANDLERS = {
    operator.__gt__: z3.fpGT,
    operator.__ge__: z3.fpGEQ,
    operator.__lt__: z3.fpLT,
    operator.__le__: z3.fpLEQ,
}

# Every finite binary64 value fits in a signed 1025-bit integer after
# truncation: its magnitude is strictly less than 2**1024.  Using the native
# FP-to-BV operation avoids routing symbolic conversion through fpToReal,
# which is both harder for Z3 and undefined for non-finite values.
FLOAT_INT_BITS = 1025


def _compare_fp_to_real(
    left: z3.FPRef,
    right: z3.ArithRef,
    handler: typing.Callable,
) -> z3.BoolRef:
    """Compare a binary64 value with an exact real without rounding the real.

    Python compares floats and integers without first converting a large
    integer to float.  That matters immediately above 2**53 and also means the
    comparison itself never raises int-to-float OverflowError.
    """
    if handler in {operator.__gt__, operator.__ge__}:
        infinite_result = z3.Not(z3.fpIsNegative(left))
    elif handler in {operator.__lt__, operator.__le__}:
        infinite_result = z3.fpIsNegative(left)
    else:
        raise UnsupportedError('unsupported floating-point comparison handler')
    finite_result = handler(z3.fpToReal(left), right)
    return z3.And(
        z3.Not(z3.fpIsNaN(left)),
        z3.If(z3.fpIsInf(left), infinite_result, finite_result),
    )


def _truncate_to_int(expr: z3.ArithRef) -> z3.ArithRef:
    """Convert a real expression with Python ``int(float)`` semantics."""
    zero = z3.RealVal(0, ctx=expr.ctx)
    return z3.If(
        expr >= zero,
        z3.ToInt(expr),
        -z3.ToInt(-expr),
        ctx=expr.ctx,
    )


def _python_fmod(left: z3.FPRef, right: z3.FPRef) -> z3.FPRef:
    """Return the truncating remainder used by C and Python ``fmod``.

    SMT-LIB ``fp.rem`` uses the IEEE nearest-integer quotient instead.  When
    that chooses the integer on the other side of truncation, its remainder
    has the opposite sign from the dividend and differs from ``fmod`` by
    exactly one divisor in the dividend's direction.  Both remainders are
    exactly representable for binary floating-point operands, so this
    one-divisor correction is exact as well.
    """
    remainder = z3.fpRem(left, right)
    signed_zero = z3.If(
        z3.fpIsNegative(left),
        z3.fpMinusZero(left.sort()),
        z3.fpPlusZero(left.sort()),
    )
    divisor_toward_dividend = z3.If(
        z3.fpIsNegative(left) == z3.fpIsNegative(right),
        right,
        -right,
    )
    same_sign = z3.fpIsNegative(remainder) == z3.fpIsNegative(left)
    return z3.If(
        z3.fpIsZero(remainder),
        signed_zero,
        z3.If(
            same_sign,
            remainder,
            remainder + divisor_toward_dividend,
        ),
    )


def _python_float_divmod(
    left: z3.FPRef,
    right: z3.FPRef,
) -> tuple[z3.FPRef, z3.FPRef]:
    """Return Python-compatible ``(left // right, left % right)`` terms.

    CPython derives both results together.  In particular, ``//`` is not the
    floor of the already-rounded IEEE division: ``1.0 // 0.1`` is ``9.0`` even
    though ``1.0 / 0.1`` rounds to ``10.0``.  CPython first derives the
    quotient from the original ``fmod`` remainder, then adjusts both values
    together when the remainder and divisor have different signs.

    Division by zero is reported by the caller.  The returned terms for that
    branch are therefore intentionally irrelevant.
    """
    remainder = _python_fmod(left, right)
    division = (left - remainder) / right
    signed_zero = z3.If(
        z3.fpIsNegative(right),
        z3.fpMinusZero(left.sort()),
        z3.fpPlusZero(left.sort()),
    )
    same_sign = z3.fpIsNegative(remainder) == z3.fpIsNegative(right)
    adjust_sign = z3.And(z3.Not(z3.fpIsZero(remainder)), z3.Not(same_sign))
    modulo = z3.If(
        z3.fpIsZero(remainder),
        signed_zero,
        z3.If(adjust_sign, remainder + right, remainder),
    )

    one = z3.FPVal(1.0, left.sort())
    division = z3.If(adjust_sign, division - one, division)

    # This mirrors CPython's correction for roundoff around an integer
    # boundary after the coupled sign adjustment above.
    floored = z3.fpRoundToIntegral(z3.RTN(), division)
    half = z3.FPVal(0.5, left.sort())
    quotient = z3.If(
        z3.fpGT(division - floored, half),
        floored + one,
        floored,
    )

    # CPython preserves the sign of the original division when the quotient is
    # zero.  Reconstructing it from ``left - modulo`` would lose that sign when
    # two equal negative operands cancel to +0.
    negative_quotient = z3.fpIsNegative(left) != z3.fpIsNegative(right)
    quotient_zero = z3.If(
        negative_quotient,
        z3.fpMinusZero(left.sort()),
        z3.fpPlusZero(left.sort()),
    )
    quotient = z3.If(z3.fpIsZero(division), quotient_zero, quotient)
    return quotient, modulo


def _add_fp_zero_division(
    right: z3.FPRef,
    *,
    ctx: Context,
    message: str,
) -> None:
    """Record Python's exception for either sign of floating-point zero."""
    ctx.add_exception(
        ZeroDivisionError,
        message,
        cond=types.bool(expr=z3.fpIsZero(right)),
    )


@types.add
class FloatSort(ProxySort):
    type_name = 'float'
    prefer_real = False
    methods = ProxySort.methods.copy()

    def __new__(cls, expr=None, **_kwargs):
        if cls is not FloatSort or expr is None:
            return super().__new__(cls)
        if z3.is_real(expr):
            return RealSort.__new__(RealSort)
        return FPSort.__new__(FPSort)

    @classmethod
    def var(cls, *, name: str, ctx: z3.Context) -> FloatSort:
        expr = z3.Const(
            name=name,
            sort=cls.sort(ctx=ctx),
        )
        return cls(expr=expr)

    @property
    def factory(self) -> TypeFactory:
        cls = type(self)
        if self.is_real:
            default = z3.RealVal(0.0, ctx=self.expr.ctx)
        else:
            default = z3.FPVal(0.0, cls.sort(), ctx=self.expr.ctx)
        return TypeFactory(
            type=cls,
            default=type(self)(default),
            subtypes=(),
        )

    @classmethod
    def sort(cls, ctx: z3.Context | None = None) -> z3.SortRef:
        if cls.prefer_real:
            return RealSort.sort(ctx=ctx)
        return FPSort.sort(ctx=ctx)

    @classmethod
    def val(cls, x: float, ctx: Context | None = None) -> FloatSort:
        if ctx is not None:
            ctx = ctx.z3_ctx
        if not math.isfinite(x):
            return FPSort.val(x, ctx=ctx)
        if cls.prefer_real:
            return RealSort.val(x, ctx=ctx)
        return FPSort.val(x, ctx=ctx)

    @methods.add(name='__float__')
    @methods.add(name='conjugate')
    @methods.add(name='real', prop=True)
    def m_float(self, ctx: Context) -> FloatSort:
        return self

    @property
    def is_real(self) -> bool:
        return z3.is_real(self.expr)

    @property
    def is_fp(self) -> bool:
        return z3.is_fp(self.expr)

    @methods.add(name='__str__')
    def m_str(self, ctx: Context):
        raise UnsupportedError('cannot convert float to str')

    @property
    def is_nan(self) -> BoolSort:
        raise NotImplementedError

    @methods.add(name='__pow__')
    def m_pow(self, other: ProxySort, ctx: Context) -> ProxySort:
        if not isinstance(other, (types.bool, types.int, types.float)):
            return self._bad_bin_op(other, op='** or pow()', ctx=ctx)
        raise UnsupportedError('cannot raise float in a power')

    @methods.add(name='__floordiv__')
    def m_floordiv(self, other: ProxySort, ctx: Context) -> ProxySort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if isinstance(other, types.int):
            other = other.m_float(ctx=ctx)
        if not isinstance(other, types.float):
            return self._bad_bin_op(other, op='//', ctx=ctx)
        left_fp = self.m_fp(ctx=ctx)
        right_fp = other.m_fp(ctx=ctx)
        _add_fp_zero_division(
            right_fp.expr,
            ctx=ctx,
            message='float floor division by zero',
        )
        quotient, _ = _python_float_divmod(left_fp.expr, right_fp.expr)
        return FPSort(expr=quotient)

    @methods.add(name='__mul__')
    def m_mul(self, other: ProxySort, ctx: Context) -> FloatSort:
        if isinstance(other, (types.str, types.list)):
            msg = "can't multiply sequence by non-int of type '{}'"
            msg = msg.format(self.type_name)
            ctx.add_exception(TypeError, msg)
            return self
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if not isinstance(other, (types.float, types.int)):
            return self._bad_bin_op(other, op='*', ctx=ctx)
        expr = self._binary_op(other=other, handler=operator.__mul__, ctx=ctx)
        return types.float(expr)

    @methods.add(name='__add__')
    def m_add(self, other: ProxySort, ctx: Context) -> ProxySort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if not isinstance(other, (types.float, types.int)):
            return self._bad_bin_op(other, op='+', ctx=ctx)
        expr = self._binary_op(other=other, handler=operator.__add__, ctx=ctx)
        return types.float(expr)

    @methods.add(name='__sub__')
    def m_sub(self, other: ProxySort, ctx: Context) -> ProxySort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if not isinstance(other, (types.float, types.int)):
            return self._bad_bin_op(other, op='-', ctx=ctx)
        expr = self._binary_op(other=other, handler=operator.__sub__, ctx=ctx)
        return types.float(expr)

    @methods.add(name='imag', prop=True)
    def m_imag(self, ctx: Context) -> FloatSort:
        return self.val(0)

    @methods.add(name='is_integer')
    def m_is_integer(self, ctx: Context) -> BoolSort:
        if self.is_real:
            integer = z3.ToInt(self.expr)
            return types.bool(expr=self.expr == z3.ToReal(integer))
        finite = z3.And(
            z3.Not(z3.fpIsNaN(self.expr)),
            z3.Not(z3.fpIsInf(self.expr)),
        )
        as_real = z3.fpToReal(self.expr)
        return types.bool(
            expr=z3.And(finite, as_real == z3.ToReal(z3.ToInt(as_real))),
        )

    @methods.add(name='as_integer_ratio')
    @methods.add(name='from_hex')
    @methods.add(name='hex')
    def unsupported(self, *args, **kwargs):
        msg = f'unsupported attribute for type {self.type_name}'
        raise UnsupportedError(msg)

    @methods.add(name='__eq__')
    def m_eq(self, other: ProxySort, ctx: Context) -> BoolSort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if isinstance(other, types.int):
            if self.is_real:
                return types.bool(expr=self.expr == z3.ToReal(other.expr))
            finite = z3.And(
                z3.Not(z3.fpIsNaN(self.expr)),
                z3.Not(z3.fpIsInf(self.expr)),
            )
            return types.bool(
                expr=z3.And(
                    finite,
                    z3.fpToReal(self.expr) == z3.ToReal(other.expr),
                ),
            )
        if not isinstance(other, types.float):
            return types.bool.val(False, ctx=ctx)
        if self.is_real and other.is_real:
            expr = self.m_real(ctx=ctx).expr == other.m_real(ctx=ctx).expr
            return types.bool(expr=expr)
        expr = z3.fpEQ(
            self.m_fp(ctx=ctx).expr,
            other.m_fp(ctx=ctx).expr,
        )
        return types.bool(expr=expr)


class RealSort(FloatSort):
    methods = FloatSort.methods.copy()
    expr: z3.RatNumRef

    def __init__(self, expr) -> None:
        assert z3.is_real(expr), f'expected real, given {type(expr)}'
        self.expr = expr

    @staticmethod
    def sort(ctx: z3.Context | None = None):
        return z3.RealSort(ctx=ctx)

    @classmethod
    def val(cls, x: float, ctx: z3.Context | None = None):
        return cls(expr=z3.RealVal(x, ctx=ctx))

    @classmethod
    def _as_fp(cls, x):
        return z3.fpRealToFP(z3.RNE(), x, FPSort.sort())

    def m_real(self, ctx: Context) -> RealSort:
        return self

    def m_fp(self, ctx: Context) -> FPSort:
        return FPSort(expr=self._as_fp(self.expr))

    @methods.add(name='__int__')
    def m_int(self, ctx: Context) -> IntSort:
        return types.int(expr=_truncate_to_int(self.expr))

    @methods.add(name='__bool__')
    def m_bool(self, ctx: Context) -> BoolSort:
        return types.bool(expr=self.expr != z3.RealVal(0))

    @methods.add(name='__abs__')
    def m_abs(self, ctx: Context) -> ProxySort:
        expr = z3.If(self.expr >= z3.RealVal(0), self.expr, -self.expr, ctx=ctx.z3_ctx)
        return type(self)(expr=expr)

    @property
    def is_nan(self) -> BoolSort:
        return types.bool.val(False)

    def _binary_op(self, other: ProxySort, handler: typing.Callable, ctx: Context):
        if isinstance(other, (types.int, types.bool)):
            return handler(self.expr, other.m_real(ctx=ctx).expr)
        if other.is_real:
            return handler(self.expr, other.expr)
        if self.prefer_real:
            return handler(self.expr, other.m_real(ctx=ctx).expr)
        return handler(self.m_fp(ctx=ctx).expr, other.expr)

    @methods.add(name='__mod__')
    def m_mod(self, other: ProxySort, ctx: Context) -> ProxySort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if isinstance(other, types.int):
            other = other.m_float(ctx=ctx)
        if not isinstance(other, types.float):
            return self._bad_bin_op(other, op='%', ctx=ctx)
        return self.m_fp(ctx=ctx).m_mod(other.m_fp(ctx=ctx), ctx=ctx)

    @methods.add(name='__truediv__')
    def m_truediv(self, other: ProxySort, ctx: Context) -> FloatSort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if isinstance(other, types.int):
            other = other.m_float(ctx=ctx)
        if not isinstance(other, types.float):
            return self._bad_bin_op(other, op='/', ctx=ctx)
        return self.m_fp(ctx=ctx).m_truediv(other.m_fp(ctx=ctx), ctx=ctx)


class FPSort(FloatSort):
    methods = FloatSort.methods.copy()

    def __init__(self, expr, *, finite_when_reached: bool = False) -> None:
        assert z3.is_fp(expr), f'expected FPSort, given {type(expr)}'
        self.expr = expr
        # Some encodings can produce infinity only on a path for which their
        # caller already recorded an exception. Remember that provenance so a
        # subsequent operation does not ask Z3 to rediscover the same hard FP
        # fact from scratch.
        self.finite_when_reached = finite_when_reached

    @staticmethod
    def sort(ctx: z3.Context | None = None):
        # Python's built-in float is an IEEE-754 binary64 value on the
        # supported CPython platforms.  ``sys.float_info.dig`` is a decimal
        # precision count, not the exponent width; using it as ``ebits`` made a
        # nonstandard 68-bit format that failed to overflow/underflow like
        # Python.
        return z3.Float64(ctx=ctx)

    @classmethod
    def val(cls, x, ctx: z3.Context | None = None):
        return FPSort(
            expr=z3.FPVal(x, cls.sort(), ctx=ctx),
            finite_when_reached=math.isfinite(x),
        )

    @classmethod
    def _as_real(cls, x):
        return z3.fpToReal(x)

    def m_real(self, ctx: Context) -> RealSort:
        return RealSort(expr=self._as_real(self.expr))

    def m_fp(self, ctx: Context) -> FPSort:
        return self

    @methods.add(name='__int__')
    def m_int(self, ctx: Context) -> IntSort:
        if not self.finite_when_reached:
            ctx.add_exception(
                OverflowError,
                'cannot convert float infinity to integer',
                cond=types.bool(expr=z3.fpIsInf(self.expr)),
            )
            ctx.add_exception(
                ValueError,
                'cannot convert float NaN to integer',
                cond=types.bool(expr=z3.fpIsNaN(self.expr)),
            )
        bit_vector = z3.fpToSBV(
            z3.RTZ(ctx=ctx.z3_ctx),
            self.expr,
            z3.BitVecSort(FLOAT_INT_BITS, ctx=ctx.z3_ctx),
        )
        return types.int(expr=z3.BV2Int(bit_vector, is_signed=True))

    @methods.add(name='__bool__')
    def m_bool(self, ctx: Context) -> BoolSort:
        # SMT equality distinguishes -0.0 from +0.0, while Python truthiness
        # treats both as false.  NaN remains truthy because it is not zero.
        expr = z3.Not(z3.fpIsZero(self.expr))
        return types.bool(expr=expr)

    @property
    def is_nan(self) -> BoolSort:
        return types.bool(expr=z3.fpIsNaN(self.expr))

    @methods.add(name='__abs__')
    def m_abs(self, ctx: Context) -> ProxySort:
        return FPSort(expr=z3.fpAbs(self.expr, ctx=ctx.z3_ctx))

    def _binary_op(self, other: ProxySort, handler: typing.Callable, ctx: Context):
        real_handler = handler
        fp_handler = FP_HANDLERS.get(handler, handler)
        if isinstance(other, (types.int, types.bool)):
            if isinstance(other, types.bool):
                other = other.m_int(ctx=ctx)
            if handler in FP_HANDLERS:
                return _compare_fp_to_real(
                    self.expr,
                    z3.ToReal(other.expr),
                    handler,
                )
            return fp_handler(self.expr, other.m_float(ctx=ctx).expr)

        if other.is_fp:
            return fp_handler(self.expr, other.expr)
        if self.prefer_real:
            return real_handler(self.m_real(ctx=ctx).expr, other.expr)
        return fp_handler(self.expr, other.m_fp(ctx=ctx).expr)

    @methods.add(name='__truediv__')
    def m_truediv(self, other: ProxySort, ctx: Context) -> FloatSort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if isinstance(other, types.int):
            other = other.m_float(ctx=ctx)
        if not isinstance(other, types.float):
            return self._bad_bin_op(other, op='/', ctx=ctx)
        right_fp = other.m_fp(ctx=ctx)
        _add_fp_zero_division(
            right_fp.expr,
            ctx=ctx,
            message='float division by zero',
        )
        return type(self)(expr=self.expr / right_fp.expr)

    @methods.add(name='__mod__')
    def m_mod(self, other: ProxySort, ctx: Context) -> ProxySort:
        if isinstance(other, types.bool):
            other = other.m_int(ctx=ctx)
        if isinstance(other, types.int):
            other = other.m_float(ctx=ctx)
        if not isinstance(other, types.float):
            return self._bad_bin_op(other, op='%', ctx=ctx)
        right_fp = other.m_fp(ctx=ctx)
        _add_fp_zero_division(
            right_fp.expr,
            ctx=ctx,
            message='float modulo',
        )
        _, modulo = _python_float_divmod(self.expr, right_fp.expr)
        return type(self)(expr=modulo)
