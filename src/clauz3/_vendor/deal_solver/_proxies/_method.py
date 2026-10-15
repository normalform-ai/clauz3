from __future__ import annotations

import typing

from .._context._trace import (
    COMPREHENSION_BODY, LAMBDA_BODY, QUANTIFIED_LOOP_BODY,
)
from .._exceptions import UnsupportedError
from ._none import NoneSort
from .._context._trace import BOOLEAN_OPERAND, GUARDED_EXPRESSION
from ._proxy import ProxySort


if typing.TYPE_CHECKING:
    from .._context import Context


class Mutation(typing.NamedTuple):
    new_value: ProxySort
    result: ProxySort


class Method(ProxySort):
    type_name = 'method'
    methods = ProxySort.methods.copy()

    name: str
    impl: typing.Callable
    pure: bool
    prop: bool
    obj: ProxySort | None

    def __init__(self, name, impl, pure, prop, obj=None) -> None:
        self.name = name
        self.impl = impl
        self.pure = pure
        self.prop = prop
        self.obj = obj

    def with_obj(self, obj: ProxySort) -> Method:
        return type(self)(
            name=self.name,
            impl=self.impl,
            pure=self.pure,
            prop=self.prop,
            obj=obj,
        )

    @methods.add(name='__call__')
    def m_call(self, *args, ctx: Context, var_name: str = '', **kwargs) -> ProxySort:
        assert self.obj is not None
        if not self.pure and QUANTIFIED_LOOP_BODY in ctx.trace:
            raise UnsupportedError(
                'unsupported container operation: mutating methods inside a quantified loop are not supported',
                self.name,
            )
        if not self.pure:
            from ._list import TrustedStringListSort

            if isinstance(self.obj, TrustedStringListSort) or any(ctx.quantifiers):
                raise UnsupportedError(
                    'unsupported container operation on a trusted string-list '
                    'return or inside a symbolic loop', self.name,
                )
        # Lambda bodies do not merge mutable state back into their callers.
        # Check at dispatch so aliases, __call__ wrappers, comprehensions and
        # helper calls cannot discard a mutation before the lambda sees it.
        if not self.pure and LAMBDA_BODY in ctx.trace:
            raise UnsupportedError('mutating methods inside a lambda are not supported')

        # Child scopes and detached helper frames share the active trace.
        # Refuse at dispatch before an alias or deeper call loses the write.
        if not self.pure and COMPREHENSION_BODY in ctx.trace:
            raise UnsupportedError(
                'mutating methods inside a comprehension are not supported',
            )
        if not self.pure:
            # append retains its argument. Other supported mutators consume
            # scalar keys/elements or shallow-copy an iterable's elements;
            # nested elements were already sealed when their container formed.
            if self.name == 'append' and args:
                ctx.ownership.share(args[0])
            ctx.ownership.require_write(self.obj, var_name, ctx=ctx)
        if self.name not in {'__eq__', '__ne__', '__call__'} and any(
            isinstance(value, NoneSort) for value in (*args, *kwargs.values())
        ):
            raise UnsupportedError('unsupported operation on None', self.name)
        if not self.pure and BOOLEAN_OPERAND in ctx.trace:
            raise UnsupportedError(
                'mutating methods inside a boolean operand are not supported', self.name,
            )
        if not self.pure and GUARDED_EXPRESSION in ctx.trace:
            raise UnsupportedError(
                'mutating methods inside a guarded expression are not supported', self.name,
            )
        result = self.impl(self.obj, *args, ctx=ctx, **kwargs)
        if isinstance(result, Mutation):
            assert not self.pure
            new_value = result.new_value
            result = result.result
        else:
            new_value = result
            if not self.pure:
                # The model returns a new receiver for scope writeback. Python's
                # void mutators return None; pop uses Mutation for its element.
                result = NoneSort()
        if not self.pure and var_name:
            if not var_name.isidentifier():
                raise UnsupportedError('cannot modify', var_name)
            ctx.ownership.replace(self.obj, new_value)
            ctx.scope.set(name=var_name, value=new_value)
        elif self.pure and _returns_fresh_collection(type(self.obj), self.name):
            result = ctx.ownership.fresh(result, ctx=ctx)
        elif self.pure:
            from .._ownership import is_mutable

            if is_mutable(result):
                # A lookup can return an input reference even when its Z3
                # wrapper is new (notably dict.get(..., mutable_default)).
                # Without reference provenance, keep every possible source
                # read-only rather than treating that wrapper as a copy.
                ctx.ownership.share(self.obj)
                for value in args:
                    ctx.ownership.share(value)
                for value in kwargs.values():
                    ctx.ownership.share(value)
                ctx.ownership.share(result)
        return result


def _returns_fresh_collection(receiver_type: type[ProxySort], name: str) -> bool:
    """Only the named concrete models have Python's independent-copy semantics.

    Exact classes deliberately exclude future aliasing models or subclasses
    that happen to expose a method with the same name.
    """
    from ._dict import DictSort, UntypedDictSort
    from ._list import ListSort, UntypedListSort
    from ._set import SetSort, UntypedSetSort

    if receiver_type in (ListSort, UntypedListSort):
        return name in {'copy', '__add__'}
    if receiver_type in (DictSort, UntypedDictSort):
        return name == 'copy'
    if receiver_type in (SetSort, UntypedSetSort):
        return name in {
            'copy', 'union', 'intersection', 'difference', 'symmetric_difference',
            '__or__', '__and__', '__xor__',
        }
    return False
