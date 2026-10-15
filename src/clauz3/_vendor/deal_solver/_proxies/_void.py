from __future__ import annotations

import typing

from .._exceptions import UnsupportedError
from ._proxy import ProxySort
from ._registry import types


if typing.TYPE_CHECKING:
    from .._context import Context
    from ._bool import BoolSort
    from ._type_factory import TypeFactory


class VoidSort(ProxySort):
    """The actual None returned by a void call, supported only as a condition.

    None is falsey but is neither False nor zero. Until the solver has a full
    None value sort, exposing a boolean or integer expression here would let
    equality, containers, or branch merges erase that distinction. Refuse such
    uses instead of inventing a value; bool() and not remain exact.
    """

    type_name = 'NoneType'
    methods = ProxySort.methods.copy()

    def __init__(self) -> None:
        pass

    @property
    def expr(self):
        raise UnsupportedError('None call results only support truthiness')

    @property
    def factory(self) -> TypeFactory:
        raise UnsupportedError('None call results cannot be container values')

    def m_getattr(self, name: str, ctx: Context) -> ProxySort:
        if name not in {'__bool__', 'not'}:
            raise UnsupportedError('unsupported operation on None call result', name)
        return super().m_getattr(name, ctx=ctx)

    @methods.add(name='__bool__')
    def m_bool(self, ctx: Context) -> BoolSort:
        return types.bool.val(False, ctx=ctx)


def reject_void_values(*values: ProxySort, operation: str) -> None:
    """Guard dispatch where another operand could hide a void result's type."""
    if any(isinstance(value, VoidSort) for value in values):
        raise UnsupportedError('unsupported None call result in', operation)
