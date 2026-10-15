from __future__ import annotations

import typing

from .._exceptions import UnsupportedError
from ._proxy import ProxySort
from ._registry import types


if typing.TYPE_CHECKING:
    from .._context import Context
    from ._bool import BoolSort


class NoneSort(ProxySort):
    """Python's None, without an invented scalar or container representation."""

    type_name = 'NoneType'
    methods = ProxySort.methods.copy()

    def __init__(self) -> None:
        pass

    @property
    def expr(self):
        raise UnsupportedError('unsupported operation on None')

    @property
    def factory(self):
        raise UnsupportedError('unsupported operation on None')

    def m_getattr(self, name: str, ctx: Context) -> ProxySort:
        # These names serve the evaluator's internal operator dispatch. Source
        # attribute access is refused separately, like the interpreter does.
        if name not in {'__bool__', '__eq__', '__ne__', 'not'}:
            raise UnsupportedError('unsupported operation on None', name)
        return super().m_getattr(name, ctx=ctx)

    @methods.add(name='__bool__')
    def m_bool(self, ctx: Context) -> BoolSort:
        return types.bool.val(False, ctx=ctx)

    @methods.add(name='__eq__')
    def m_eq(self, other: ProxySort, ctx: Context) -> BoolSort:
        return types.bool.val(isinstance(other, NoneSort), ctx=ctx)

    @methods.add(name='__ne__')
    def m_ne(self, other: ProxySort, ctx: Context) -> BoolSort:
        return types.bool.val(not isinstance(other, NoneSort), ctx=ctx)

    def _unsupported(self, *args, **kwargs) -> typing.NoReturn:
        # An evaluated unsupported operation must refuse proof rather than
        # become a suppressible modeled exception. Statically skipped operands
        # are handled by the guarded-expression evaluators before dispatch.
        raise UnsupportedError('unsupported operation on None')

    m_abs = _unsupported
    m_call = _unsupported
    m_len = _unsupported
    m_getitem = _unsupported
    get_slice = _unsupported
    m_setitem = _unsupported
    m_contains = _unsupported
    m_int = _unsupported
    m_float = _unsupported
    m_str = _unsupported
    m_real = _unsupported
    m_fp = _unsupported
    m_pos = _unsupported
    m_neg = _unsupported
    m_inv = _unsupported
    _bad_bin_op = _unsupported
    _bad_un_op = _unsupported
