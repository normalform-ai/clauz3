from __future__ import annotations

import ntpath

import z3

from .._context import Context
from .._exceptions import UnsupportedError
from .._proxies import ProxySort, StrSort
from .._strings import string_val, string_value
from ._registry import register


def _join_signature(path, /, *paths):
    pass


def _string_paths(
    paths: tuple[ProxySort, ...], kwargs: dict[str, ProxySort], ctx: Context,
) -> tuple[StrSort, ...]:
    if kwargs:
        raise UnsupportedError('path join keyword arguments are not supported')
    if not paths:
        raise UnsupportedError('path join requires at least one argument')
    strings = []
    for path in paths:
        if not isinstance(path, StrSort):
            raise UnsupportedError('path join requires string arguments', path.type_name)
        strings.append(path)
    return tuple(strings)


@register('posixpath.join', signature=_join_signature)
def posix_path_join(*paths: ProxySort, ctx: Context, **kwargs) -> ProxySort:
    strings = _string_paths(paths, kwargs, ctx)
    empty = string_val('', ctx=ctx.z3_ctx)
    sep = string_val('/', ctx=ctx.z3_ctx)
    result = strings[0].expr
    for component in strings[1:]:
        # Python does not normalize joins. Absolute components discard the
        # previous path; empty components can add a trailing slash, while an
        # empty prefix or an existing separator needs no additional slash.
        result = z3.If(
            z3.PrefixOf(sep, component.expr),
            component.expr,
            z3.If(
                z3.Or(result == empty, z3.SuffixOf(sep, result)),
                z3.Concat(result, component.expr),
                z3.Concat(result, sep, component.expr),
            ),
        )
    return StrSort(result)


@register('ntpath.join', signature=_join_signature)
def nt_path_join(*paths: ProxySort, ctx: Context, **kwargs) -> ProxySort:
    strings = _string_paths(paths, kwargs, ctx)
    try:
        concrete = [string_value(path.expr) for path in strings]
    except UnsupportedError as exc:
        raise UnsupportedError('symbolic Windows path join (ntpath.join) is not supported') from exc
    return StrSort.val(ntpath.join(*concrete), ctx=ctx)


@register('os.path.join', signature=_join_signature)
def os_path_join(*paths: ProxySort, ctx: Context, **kwargs) -> ProxySort:
    # Astroid can build the host module under this alias, or infer both the
    # POSIX and Windows definitions. Neither spelling declares the platform
    # the program promises to use; require an explicit path flavour.
    raise UnsupportedError('platform-dependent os.path.join; use posixpath.join or ntpath.join')
