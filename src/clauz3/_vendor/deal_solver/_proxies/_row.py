"""Z3 representations for clauz3.Row schemas.

Each clauz3.Row subclass maps to one Z3 datatype with a single constructor
and one accessor per declared field. The mapping is cached per class
identity so repeated lookups during a proof reuse the same Z3 sort.
"""

from __future__ import annotations

import typing
from typing import Any

import z3

from ._proxy import ProxySort
from ._registry import types


if typing.TYPE_CHECKING:
    from .._context import Context


_DATATYPE_CACHE: dict[type, z3.DatatypeSortRef] = {}


_TYPE_TO_SORT: dict[type, z3.SortRef] = {
    str: z3.StringSort(),
    int: z3.IntSort(),
    bool: z3.BoolSort(),
}


def z3_datatype_for_row(schema: type) -> z3.DatatypeSortRef:
    """Return (and cache) the Z3 datatype for a clauz3.Row subclass.

    >>> class Eg:
    ...     __annotations__ = {'x': str, 'y': int}
    >>> dt = z3_datatype_for_row(Eg)
    >>> dt.num_constructors()
    1
    """
    cached = _DATATYPE_CACHE.get(schema)
    if cached is not None:
        return cached

    annotations = schema.__annotations__
    if not annotations:
        raise TypeError(f"{schema.__name__} has no annotated fields")

    dt = z3.Datatype(schema.__name__)
    fields: list[tuple[str, z3.SortRef]] = []
    for fname, ftype in annotations.items():
        sort = _TYPE_TO_SORT.get(ftype)
        if sort is None:
            raise TypeError(
                f"unsupported field type {ftype!r} on {schema.__name__}.{fname}"
            )
        fields.append((fname, sort))
    dt.declare(f"mk_{schema.__name__.lower()}", *fields)
    created = dt.create()

    _DATATYPE_CACHE[schema] = created
    return created


def _accessor_for_field(dt: z3.DatatypeSortRef, name: str) -> z3.FuncDeclRef:
    """Return the Z3 accessor function for *name* on the single constructor."""
    ctor = dt.constructor(0)
    for i in range(ctor.arity()):
        acc = dt.accessor(0, i)
        if acc.name() == name:
            return acc
    raise KeyError(name)


@types.add
class RowSort(ProxySort):
    """Symbolic row wrapping a Z3 datatype value for a known schema.

    >>> class Eg:
    ...     __annotations__ = {'x': str}
    >>> import z3
    >>> dt = z3_datatype_for_row(Eg)
    >>> val = dt.constructor(0)(z3.StringVal("hi"))
    >>> r = RowSort(schema=Eg, expr=val)
    >>> r.schema is Eg
    True
    """

    type_name = "row"
    methods = ProxySort.methods.copy()

    def __init__(self, *, schema: type, expr: z3.ExprRef) -> None:
        self.schema = schema
        self.expr = expr

    @methods.add(name="__getattr__")
    def m_getattr(self, name: str, ctx: Context) -> ProxySort:
        """self.name — delegates to field() for declared schema fields."""
        annotations = self.schema.__annotations__
        if name in annotations:
            return self.field(name, ctx=ctx)
        # Fall back to ProxySort's m_getattr for methods and unknown attrs
        return super().m_getattr(name, ctx=ctx)

    def field(self, name: str, *, ctx: Context) -> ProxySort:
        """Return a typed proxy for the named field.

        Raises AttributeError for unknown field names.
        """
        annotations = self.schema.__annotations__
        if name not in annotations:
            raise AttributeError(f"unknown field {name!r} on {self.schema.__name__}")
        dt = z3_datatype_for_row(self.schema)
        acc = _accessor_for_field(dt, name)
        z3_value = acc(self.expr)
        ftype = annotations[name]
        if ftype is str:
            return types.str(expr=z3_value)
        if ftype is int:
            return types.int(expr=z3_value)
        if ftype is bool:
            return types.bool(expr=z3_value)
        raise TypeError(f"unexpected field type {ftype!r}")


@types.add
class RowClassSort(ProxySort):
    """Sentinel proxy wrapping an actual Row class for contract arguments.

    Used when a contract function argument is a Row subclass (e.g. UserRow in
    ``contracts.addresses_from(UserRow, "email")``). The proof evaluator can't
    evaluate class names symbolically, so we pass them through as sentinels and
    unwrap them in _solve_with before calling the contract function.
    """

    type_name = "row_class"
    methods = ProxySort.methods.copy()

    def __init__(self, *, schema: type) -> None:
        self.schema = schema
        # ProxySort requires .expr; use a dummy string expr as a placeholder
        import z3

        self.expr = z3.StringVal(f"<class {schema.__name__}>")


@types.add
class QueryResultSort(ProxySort):
    """Symbolic return of a trusted call typed list[Row].

    Wraps a Z3 Array(Int, RowDatatype) plus a symbolic length and source metadata.
    """

    type_name = "query_result"

    def __init__(
        self,
        *,
        row_schema: type,
        array_expr: z3.ArrayRef,
        length_expr: z3.ArithRef,
        source: tuple[str, dict[str, Any]],
    ) -> None:
        self.row_schema = row_schema
        self.array_expr = array_expr
        self.length_expr = length_expr
        self.source = source
        # ProxySort requires .expr; treat array_expr as the canonical expr
        self.expr = array_expr

    methods = ProxySort.methods.copy()

    @methods.add(name="__getitem__")
    def m_getitem(self, index: ProxySort, ctx: Context) -> RowSort:
        """self[index] — return the symbolic row at the given index."""
        return self.at(index.expr, ctx=ctx)

    @methods.add(name="__len__")
    def m_len(self, ctx: Context) -> "IntSort":
        """len(self) — return the symbolic length expression."""
        from ._int import IntSort

        return IntSort(expr=self.length_expr)

    def at(self, i: z3.ArithRef, *, ctx: Any) -> RowSort:
        """Return a RowSort for the element at index i."""
        return RowSort(schema=self.row_schema, expr=z3.Select(self.array_expr, i))
