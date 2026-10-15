from __future__ import annotations

import typing

import z3


if typing.TYPE_CHECKING:
    from .._context import Context
    from .._proxies import BoolSort, ProxySort


T = typing.TypeVar('T')

# Simplification walks the whole condition DAG, so bound it and treat anything
# larger as possible. Real raise conditions are small closed comparisons such
# as `2 == 0`; a large one is not worth an unbounded rewrite to rule out.
MAX_IMPOSSIBILITY_CHECK_NODES = 1_000


def _is_small(expr: z3.ExprRef) -> bool:
    pending = [expr]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        node_id = current.get_id()
        if node_id in seen:
            continue
        seen.add(node_id)
        if len(seen) > MAX_IMPOSSIBILITY_CHECK_NODES:
            return False
        pending.extend(current.children())
    return True


class ExceptionInfo(typing.NamedTuple):
    name: str               # exception name
    names: set[str]  # exception name and names of all its bases
    cond: BoolSort        # indicates if the exception is raised
    message: str = ''

    @property
    def is_impossible(self) -> bool:
        """True when this exception's raise condition is statically false.

        Every operation that *can* raise records an entry, including ones whose
        condition is a closed comparison that never holds -- ``x // 2`` records
        ZeroDivisionError under ``2 == 0``. Such an entry cannot fire, but it
        still makes the exception layer non-empty, and the comprehension and
        lambda gates read a non-empty layer as "this body has effects" and fail
        closed. Ruling the entry out here keeps ``/``, ``//``, ``%`` and
        int/float mixing usable inside those constructs.

        Fails closed: an oversized or merely satisfiable-looking condition is
        reported as possible.
        """
        return _is_small(self.cond.expr) and z3.is_false(z3.simplify(self.cond.expr))


class ReturnInfo(typing.NamedTuple):
    value: ProxySort
    cond: BoolSort

    def merge(self, other: ReturnInfo, ctx: Context) -> ReturnInfo:
        from .._proxies import if_expr, or_expr

        cls = type(self)
        return cls(
            cond=or_expr(self.cond, other.cond, ctx=ctx),
            value=if_expr(self.cond, self.value, other.value, ctx=ctx),
        )


class FactInfo(typing.NamedTuple):
    name: str
    markers: tuple[str, ...]
    args: dict[str, ProxySort]
    cond: BoolSort
    quantifiers: tuple = ()
    # Preserve the value handed back by the trusted boundary, so a concrete
    # differential run can use the same model of the proof assumptions.
    # None denotes a -> None annotation, not an unknown symbolic value.
    result: ProxySort | None = None
    qualified_name: str = ''


class Layer(typing.Generic[T]):
    __slots__ = ['layer', 'parent']

    layer: list[T]
    parent: Layer[T] | None

    def __init__(self, parent=None) -> None:
        self.layer = []
        self.parent = parent

    def add(self, item: T) -> None:
        self.layer.append(item)

    def make_child(self) -> Layer[T]:
        cls = type(self)
        return cls(parent=self)

    def __iter__(self) -> typing.Iterator[T]:
        yield from self.layer
        if self.parent:
            yield from self.parent

    def __repr__(self) -> str:
        return '{n}({r})'.format(
            n=type(self).__name__,
            r=repr(self.layer),
        )
