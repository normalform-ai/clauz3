"""Z3 representations for clauz3.Row schemas.

Each clauz3.Row subclass maps to one Z3 datatype with a single constructor
and one accessor per declared field. The mapping is cached per class
identity so repeated lookups during a proof reuse the same Z3 sort.
"""

from __future__ import annotations

import importlib
import itertools
import sys
import typing
from typing import Any

import astroid
import z3

from .._exceptions import UnsupportedError
from .._strings import string_sort
from ._proxy import ProxySort
from ._registry import types


if typing.TYPE_CHECKING:
    from .._context import Context
    from ._bool import BoolSort


_DATATYPE_CACHE: dict[type, z3.DatatypeSortRef] = {}

# Z3 interns datatype sorts by name within a context, so every sort name must
# be unique to one schema class for the lifetime of the process.
_SORT_SERIAL = itertools.count()


_TYPE_TO_SORT: dict[type, z3.SortRef] = {
    str: string_sort(),
    int: z3.IntSort(),
    bool: z3.BoolSort(),
}

_ANNOTATION_NAME_TO_TYPE: dict[str, type] = {
    "str": str,
    "builtins.str": str,
    "int": int,
    "builtins.int": int,
    "bool": bool,
    "builtins.bool": bool,
}

_SYNTHETIC_ROW_CACHE: dict[
    tuple[str, str, str, tuple[tuple[str, type], ...]], type
] = {}


def row_schema_from_class_def(class_def: astroid.ClassDef) -> type | None:
    """Resolve an astroid Row subclass to the runtime schema class.

    Imported trusted schemas can be resolved to their real Python class.
    Inline schemas from the proved source are represented as cached synthetic
    ``clauz3.Row`` subclasses so the same schema identity is reused by effect
    calls and contract arguments during one proof.
    """
    from clauz3.row import Row

    real = _resolve_real_class(class_def)
    if isinstance(real, type) and issubclass(real, Row) and real is not Row:
        return real

    if not _syntactically_extends_row(class_def):
        return None

    annotations = _annotations_from_class_def(class_def)
    if not annotations:
        return None

    root = class_def.root()
    module_name = getattr(root, "name", "") or "__clauz3_synthetic__"
    module_file = getattr(root, "file", "") or ""
    key = (
        module_name,
        module_file,
        class_def.qname(),
        tuple(annotations.items()),
    )
    cached = _SYNTHETIC_ROW_CACHE.get(key)
    if cached is not None:
        return cached

    schema = type(
        class_def.name,
        (Row,),
        {
            "__annotations__": annotations,
            "__module__": module_name,
        },
    )
    _SYNTHETIC_ROW_CACHE[key] = schema
    return schema


def _resolve_real_class(class_def: astroid.ClassDef) -> type | None:
    module_name = class_def.root().name
    if not module_name:
        return None
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            return None
        module = importlib.import_module(module_name)
    value = getattr(module, class_def.name, None)
    return value if isinstance(value, type) else None


def _syntactically_extends_row(class_def: astroid.ClassDef) -> bool:
    for base in class_def.bases:
        if base.as_string() in {"clauz3.Row", "Row"}:
            return True
    return False


def _annotations_from_class_def(class_def: astroid.ClassDef) -> dict[str, type]:
    annotations: dict[str, type] = {}
    for node in class_def.body:
        if not isinstance(node, astroid.AnnAssign):
            continue
        if not isinstance(node.target, astroid.AssignName):
            continue
        field_type = _annotation_type(node.annotation)
        if field_type is None:
            return {}
        annotations[node.target.name] = field_type
    return annotations


def _annotation_type(node: astroid.NodeNG) -> type | None:
    return _ANNOTATION_NAME_TO_TYPE.get(node.as_string())


def _sort_name(schema: type) -> str:
    """Return a process-unique Z3 sort name for *schema*.

    Z3 resolves ``z3.Datatype(name)`` against a per-context registry, so two
    schemas declared under one name become one sort and the later declaration
    silently rewrites the earlier sort's fields. The qualified name alone is
    not unique either: two trusted roots can supply the same dotted module
    path, and ``row_schema_from_class_def`` mints synthetic schemas that carry
    their source module's name. The serial therefore ties each sort to one
    class object, matching how ``_DATATYPE_CACHE`` is keyed; the qualified name
    is kept as a prefix so counterexamples and SMT dumps stay readable.

    It is a monotonic counter rather than ``len(_DATATYPE_CACHE)``: per-proof
    isolation snapshots that cache and restores it between proofs, so its size
    is not monotonic and a size-derived serial would hand out a name twice.

    >>> class Eg:
    ...     __annotations__ = {'x': str}
    >>> _sort_name(Eg) == _sort_name(Eg)
    False
    """
    return f"{schema.__module__}.{schema.__qualname__}#{next(_SORT_SERIAL)}"


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
        raise UnsupportedError(f"{schema.__name__} has no annotated fields")

    dt = z3.Datatype(_sort_name(schema))
    fields: list[tuple[str, z3.SortRef]] = []
    for fname, ftype in annotations.items():
        sort = _TYPE_TO_SORT.get(ftype)
        if sort is None:
            raise UnsupportedError(
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
    raise UnsupportedError(f"no accessor for field {name!r} on {dt.name()}")


@types.add
class RowSort(ProxySort):
    """Symbolic row wrapping a Z3 datatype value for a known schema.

    >>> class Eg:
    ...     __annotations__ = {'x': str}
    >>> import z3
    >>> dt = z3_datatype_for_row(Eg)
    >>> from .._strings import string_val
    >>> val = dt.constructor(0)(string_val("hi"))
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

        Raises UnsupportedError for unknown field names.
        """
        annotations = self.schema.__annotations__
        if name not in annotations:
            raise UnsupportedError(
                f"unknown field {name!r} on {self.schema.__name__}"
            )
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
        raise UnsupportedError(f"unexpected field type {ftype!r}")


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
        from .._strings import string_val

        self.expr = string_val(f"<class {schema.__name__}>")


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

    @methods.add(name="__bool__")
    def m_bool(self, ctx: Context) -> BoolSort:
        """A query result is a Python list, so only nonempty results are true."""
        return types.bool(expr=self.length_expr > 0)

    def at(self, i: z3.ArithRef, *, ctx: Any) -> RowSort:
        """Return a RowSort for the element at index i."""
        return RowSort(schema=self.row_schema, expr=z3.Select(self.array_expr, i))
