from __future__ import annotations

import typing
from dataclasses import dataclass


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
                    raise TypeError(
                        f"v1 only supports str/int/bool field types; "
                        f"{cls_name}.{fname} is {ftype!r}. See "
                        f"docs/symbolic-iteration.md."
                    )
                ftype = mcs._STRING_TYPE_MAP[ftype]
            if ftype not in mcs._ALLOWED_TYPES:
                raise TypeError(
                    f"v1 only supports str/int/bool field types; "
                    f"{cls_name}.{fname} is {ftype!r}. See "
                    f"docs/symbolic-iteration.md."
                )
            resolved[fname] = ftype
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
