from __future__ import annotations

import re
import sre_constants
import sre_parse
import typing

import z3

from .._exceptions import UnsupportedError
from .._strings import string_sort, string_val
from ._proxy import ProxySort
from ._registry import types


if typing.TYPE_CHECKING:
    from .._context import Context
    from ._bool import BoolSort


@types.add
class PatternSort(ProxySort):
    module_name = 're'
    type_name = 'Pattern'
    methods = ProxySort.methods.copy()

    expr: z3.ReRef
    pattern: str | None

    def __init__(self, expr, pattern: str | None = None) -> None:
        assert z3.is_re(expr)
        self.expr = expr
        self.pattern = pattern

    @classmethod
    def var(cls, *, name: str, ctx: z3.Context) -> PatternSort:
        expr = z3.Const(
            name=name,
            sort=z3.ReSort(string_sort(ctx)),
        )
        return cls(expr=expr)

    @classmethod
    def val(cls, pattern: str, flags: int = 0) -> PatternSort:
        try:
            parsed = sre_parse.parse(pattern, flags=flags)
        except re.error as exc:
            raise UnsupportedError('cannot interpret regexp', str(exc)) from exc
        if parsed.state.flags & ~re.UNICODE:
            raise UnsupportedError('regular-expression flags are not supported')
        expr = cls._parse_pattern(parsed)
        return cls(expr=expr, pattern=pattern)

    @classmethod
    def _parse_pattern(cls, pattern: sre_parse.SubPattern) -> z3.ReRef:
        result = []
        for t_type, t_args in pattern.data:
            token = cls._parse_token(t_type, t_args)
            result.append(token)
        if not result:
            return z3.Re(string_val(''))
        if len(result) == 1:
            return result[0]
        return z3.Concat(*result)

    @classmethod
    def _parse_token(cls, t_type: int, t_args) -> PatternSort:
        re_all = z3.Range(string_val(chr(0)), string_val(chr(0x10FFFF)))

        if t_type == sre_constants.LITERAL:
            return z3.Re(string_val(chr(t_args)))
        if t_type == sre_constants.NOT_LITERAL:
            return z3.Intersect(re_all, z3.Complement(z3.Re(string_val(chr(t_args)))))
        if t_type == sre_constants.RANGE:
            lo, hi = t_args
            return z3.Range(string_val(chr(lo)), string_val(chr(hi)))
        if t_type == sre_constants.IN:
            subpatterns = []
            for st_type, st_args in t_args:
                subpatterns.append(cls._parse_token(st_type, st_args))
            return z3.Union(*subpatterns)
        if t_type == sre_constants.SUBPATTERN:
            if t_args[1] or t_args[2]:
                raise UnsupportedError('regular-expression flags are not supported')
            return cls._parse_pattern(t_args[-1])
        if t_type == sre_constants.ANY and t_args is None:
            return z3.Intersect(re_all, z3.Complement(z3.Re(string_val('\n'))))

        if t_type == sre_constants.MAX_REPEAT:
            start = t_args[0]
            end = t_args[1]
            sub_re = cls._parse_pattern(t_args[2])
            # Z3 uses an upper bound of zero to mean unbounded repetition.
            # Python's finite {0} / {0,0} instead matches only the empty string.
            if end == 0:
                return z3.Re(string_val(''))
            if end == sre_constants.MAXREPEAT:
                if start == 0:
                    return z3.Star(sub_re)
                if start == 1:
                    return z3.Plus(sub_re)
                return z3.Loop(sub_re, start)
            if start == 0 and end == 1:
                return z3.Option(sub_re)
            return z3.Loop(sub_re, start, end)

        if t_type == sre_constants.CATEGORY:
            # Python's Unicode categories are not ASCII character ranges.
            raise UnsupportedError('regular-expression categories are not supported')

        raise UnsupportedError('cannot interpret regexp')

    @methods.add(name='fullmatch')
    def fullmatch(self, string: ProxySort, ctx: Context) -> BoolSort:
        if not isinstance(string, types.str):
            raise UnsupportedError('regular-expression subject must be a string')
        return types.bool(expr=z3.InRe(string.expr, self.expr))

    @methods.add(name='match')
    def match(self, string: ProxySort, ctx: Context) -> BoolSort:
        if not isinstance(string, types.str):
            raise UnsupportedError('regular-expression subject must be a string')
        rex = z3.Concat(
            self.expr,
            z3.Star(
                z3.Range(string_val(chr(0)), string_val(chr(0x10FFFF))),
            ),
        )
        return types.bool(expr=z3.InRe(string.expr, rex))
