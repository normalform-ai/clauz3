from __future__ import annotations

from .._context import Context
from .._exceptions import UnsupportedError
from .._proxies import BoolSort, PatternSort, ProxySort
from .._strings import string_value
from ._registry import register


def _match_signature(pattern, string):
    pass


def _compile_signature(pattern):
    pass


@register('re.fullmatch', signature=_match_signature)
def re_fullmatch(
    pattern: ProxySort,
    string: ProxySort,
    *args: ProxySort,
    ctx: Context,
    **kwargs,
) -> BoolSort:
    if args or kwargs:
        raise UnsupportedError('regular-expression flags are not supported')
    pat_str = string_value(pattern.expr)
    pat = PatternSort.val(pat_str)
    return pat.fullmatch(string, ctx=ctx)


@register('re.match', signature=_match_signature)
def re_match(
    pattern: ProxySort,
    string: ProxySort,
    *args: ProxySort,
    ctx: Context,
    **kwargs,
) -> BoolSort:
    if args or kwargs:
        raise UnsupportedError('regular-expression flags are not supported')
    pat_str = string_value(pattern.expr)
    pat = PatternSort.val(pat_str)
    return pat.match(string, ctx=ctx)


@register('re.compile', signature=_compile_signature)
def re_compile(
    pattern: ProxySort,
    *args: ProxySort,
    ctx: Context,
    **kwargs,
) -> PatternSort:
    if args or kwargs:
        raise UnsupportedError('regular-expression flags are not supported')
    pat_str = string_value(pattern.expr)
    return PatternSort.val(pat_str)
