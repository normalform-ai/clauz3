from __future__ import annotations

import types
import typing
from dataclasses import dataclass


class RowSchemaError(TypeError):
    """A Row declaration uses a field type or default the schema rejects.

    Trusted-module loading reports this as a configuration error. Retaining
    TypeError as the base preserves direct callers' exception handling.
    """


@dataclass(frozen=True)
class ColumnRef:
    """Marker for contracts that refer to a structured value source.

    `UserRow.email` (class-level attribute access on a clauz3.Row subclass)
    returns ColumnRef(schema=UserRow, field='email').
    """

    schema: type
    field: str


def _namespace_annotations(
    namespace: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """Extract field annotations from a class-body namespace.

    Python 3.14 (PEP 649/749) defers annotations: the namespace exposes an
    ``__annotate__`` function instead of a plain ``__annotations__`` dict, so
    we resolve it via ``annotationlib``. Earlier versions populate
    ``__annotations__`` directly.
    """
    try:
        import annotationlib  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        return dict(namespace.get("__annotations__", {}))
    annotate = annotationlib.get_annotate_from_class_namespace(namespace)
    if annotate is None:
        return dict(namespace.get("__annotations__", {}))
    # FORWARDREF resolves str/int/bool to real types, yields strings under
    # `from __future__ import annotations`, and never raises on unknown names.
    resolved: dict[str, typing.Any] = annotationlib.call_annotate_function(
        annotate, annotationlib.Format.FORWARDREF
    )
    return resolved


class _RowMeta(type):
    """Metaclass that returns ColumnRef for class-level field access
    and installs frozen-instance behavior on subclasses.
    """

    _ALLOWED_TYPES: typing.ClassVar[tuple[type, ...]] = (str, int, bool)
    _STRING_TYPE_MAP: typing.ClassVar[dict[str, type]] = {
        "str": str,
        "int": int,
        "bool": bool,
    }

    def __new__(
        mcs,
        cls_name: str,
        bases: tuple[type, ...],
        namespace: dict[str, typing.Any],
        **kwargs: typing.Any,
    ) -> _RowMeta:
        annotations = _namespace_annotations(namespace)
        resolved: dict[str, type] = {}
        for fname, ftype in annotations.items():
            # Skip special attributes like __annotations__ itself
            if fname.startswith("__"):
                continue
            # Normalize string annotations (PEP 563 postponed evaluation).
            if isinstance(ftype, str):
                if ftype not in mcs._STRING_TYPE_MAP:
                    raise RowSchemaError(
                        f"v1 only supports str/int/bool field types; "
                        f"{cls_name}.{fname} is {ftype!r}. See "
                        f"docs/explanation/symbolic-iteration.md."
                    )
                ftype = mcs._STRING_TYPE_MAP[ftype]
            if ftype not in mcs._ALLOWED_TYPES:
                raise RowSchemaError(
                    f"v1 only supports str/int/bool field types; "
                    f"{cls_name}.{fname} is {ftype!r}. See "
                    f"docs/explanation/symbolic-iteration.md."
                )
            resolved[fname] = ftype
        # A field may not carry a class-level value. With the default
        # ``__slots__`` Python rejects one anyway; with an explicit
        # ``__slots__`` it would be accepted and the class silently broken
        # (instances could not be created) while static inference of
        # ``row.field`` could read the constant. Refuse it outright so the
        # prover may rely on a row field never inferring to a value.
        # A base's ``__slots__`` installs a member descriptor per field in its
        # ``__dict__``; that is storage, not a default, so a subclass may
        # re-declare an inherited field.
        inherited = {
            name
            for base in bases
            for klass in base.__mro__
            if klass is not object
            for name, value in vars(klass).items()
            if not isinstance(value, types.MemberDescriptorType)
        }
        defaults = sorted(
            name for name in resolved if name in namespace or name in inherited
        )
        if defaults:
            raise RowSchemaError(
                f"{cls_name} fields may not have class-level defaults (own or "
                f"inherited): {', '.join(defaults)}"
            )
        namespace["__annotations__"] = resolved
        namespace.setdefault("__slots__", tuple(resolved.keys()))
        return super().__new__(mcs, cls_name, bases, namespace, **kwargs)

    def __getattribute__(cls, name: str) -> typing.Any:
        if not name.startswith("_"):
            annotations = type.__getattribute__(cls, "__annotations__")
            if name in annotations:
                return ColumnRef(schema=cls, field=name)
        return type.__getattribute__(cls, name)


class Row(metaclass=_RowMeta):
    """Base class for trusted-layer row schemas.

    Subclasses declare fields as annotations:

        class UserRow(Row):
            name: str
            email: str
            consented: bool

    Instances are immutable. Class-level attribute access
    (UserRow.email) returns a ColumnRef marker for use in contracts.
    """

    __slots__: tuple[str, ...] = ()
    __annotations__: dict[str, type] = {}

    def __init__(self, **kwargs: typing.Any) -> None:
        annotations = type(self).__annotations__
        missing = set(annotations) - set(kwargs)
        extra = set(kwargs) - set(annotations)
        if missing:
            raise TypeError(f"missing fields: {sorted(missing)}")
        if extra:
            raise TypeError(f"unknown fields: {sorted(extra)}")
        for name, value in kwargs.items():
            object.__setattr__(self, name, value)

    def __setattr__(self, name: str, value: typing.Any) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return NotImplemented
        return all(
            getattr(self, f) == getattr(other, f) for f in type(self).__annotations__
        )

    def __hash__(self) -> int:
        return hash(tuple(getattr(self, f) for f in type(self).__annotations__))

    def __repr__(self) -> str:
        fields = ", ".join(
            f"{f}={getattr(self, f)!r}" for f in type(self).__annotations__
        )
        return f"{type(self).__name__}({fields})"


def row_classes_by_name(name: str) -> list[type[Row]]:
    """Return the distinct loaded :class:`Row` subclasses bound to *name*.

    Trusted modules are imported before a proof runs, so a schema named in a
    guarantee (``contracts.addresses_from(UserRow, "email")``) is reachable
    through ``sys.modules`` even when the program itself was parsed from a
    string with no importable module of its own. A re-export binds one class
    object under several modules, so candidates are deduplicated by identity;
    two entries therefore mean two genuinely different schemas share the name.
    """

    import sys

    candidates: list[type[Row]] = []
    for module in list(sys.modules.values()):
        if module is None:
            continue
        candidate = getattr(module, name, None)
        if not (
            isinstance(candidate, type)
            and issubclass(candidate, Row)
            and candidate is not Row
        ):
            continue
        if not any(candidate is seen for seen in candidates):
            candidates.append(candidate)
    return candidates


def row_class_by_name(name: str) -> type[Row] | None:
    """Return a loaded :class:`Row` subclass bound to *name*, if any.

    This is an existence check for the guarantee grammar: it establishes that
    a bare name denotes *some* schema, so the argument is a value the reader
    can look up. Which schema a proof actually constrains is decided by the
    prover, which resolves the name through the entry file's own imports and,
    when it must fall back to this scan, refuses an ambiguous name rather than
    picking one (see ``_resolve_row_class`` in the solver; issue #127).
    """

    candidates = row_classes_by_name(name)
    return candidates[0] if candidates else None
