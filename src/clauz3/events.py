"""Events: what a trusted call does to the world, declared next to the call.

A trusted function is a *tool*; an event is something the tool does to a
resource. Several tools can perform the same event --- ``read_file``, ``grep``
and ``copy`` all read a file --- so a contract stated over the event covers
every tool that performs it, including tools added later::

    class FsEvent(Event):
        path: str

    class Read(FsEvent): ...
    class Write(FsEvent): ...

    @clauz3.trusted
    @performs(lambda src, dest: [Read(src), Write(dest)])
    def copy(src: str, dest: str) -> None: ...

    @contract
    def never_read_under(prefix: str) -> ContractSpec:
        return Read.all(lambda e: not e.path.startswith(prefix))

An event class is a frozen dataclass and also a relation: ``Read.all(...)``,
``Read.empty()``, ``Read.count()`` and the rest query every event of that class
*or a subclass* that a reachable trusted call performed. Subclassing is how a
layer arranges its events: if ``Delete`` subclasses ``Write``, a read-only
contract over ``Write`` also forbids deletion.

The ``@performs`` builder mirrors the tool's parameters by name, like the
fluent :func:`clauz3.fluent.effect`, and may name any subset of them. Each
event field must be one of those parameters. The declaration is trusted: a
tool that performs an event it does not declare is invisible to contracts over
that event, exactly as an undeclared effect would be.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, TypeVar, dataclass_transform

from clauz3.spec import (
    ContractSpec,
    FilteredRelation,
    Relation,
    RelationError,
    SumSpec,
)

F = TypeVar("F", bound=Callable[..., object])

#: The events each trusted function performs, by ``module.qualname``. Keyed by
#: qualified name, as the prover records facts, so two layers' functions that
#: share a short name cannot borrow each other's events. A re-imported module
#: replaces its entry rather than adding to it.
PERFORMED: dict[str, tuple[Event, ...]] = {}

#: Names an event field may not take, because the class uses them as a relation.
_RESERVED = frozenset({"all", "where", "empty", "count", "sum", "distinct"})


@dataclass(frozen=True)
class _ParamRef:
    """A trusted-function parameter, standing in for an event field's value."""

    name: str


@dataclass_transform(frozen_default=True)
class Event:
    """Base class for events. Subclasses become frozen dataclasses."""

    _relation: ClassVar[EventRelation]
    # Set by the dataclass transform; declared so mypy sees a dataclass.
    __dataclass_fields__: ClassVar[dict[str, dataclasses.Field[Any]]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        dataclass(frozen=True)(cls)
        clashes = sorted(
            field.name for field in dataclasses.fields(cls) if field.name in _RESERVED
        )
        if clashes:
            raise RelationError(
                f"event {cls.__name__} has fields {clashes!r}, which are reserved "
                "for querying it as a relation"
            )
        cls._relation = EventRelation(cls)

    @classmethod
    def all(cls, predicate: Callable[[Any], Any]) -> ContractSpec:
        return cls._relation.all(predicate)

    @classmethod
    def where(cls, predicate: Callable[[Any], Any]) -> FilteredRelation:
        return cls._relation.where(predicate)

    @classmethod
    def empty(cls) -> ContractSpec:
        return cls._relation.empty()

    @classmethod
    def count(cls) -> SumSpec:
        return cls._relation.count()

    @classmethod
    def sum(cls, selector: Callable[[Any], Any]) -> SumSpec:
        return cls._relation.sum(selector)

    @classmethod
    def distinct(cls, key: Callable[[Any], Any]) -> ContractSpec:
        return cls._relation.distinct(key)


class EventRelation(Relation):
    """The events of one class and its subclasses, derived from trusted calls."""

    fields: frozenset[str]

    def __init__(self, event: type[Event]) -> None:
        self.event = event
        self.key = _qualified(event)
        self.fields = frozenset(field.name for field in dataclasses.fields(event))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, EventRelation) and other.key == self.key

    def __hash__(self) -> int:
        return hash(self.key)

    def describe(self) -> str:
        return f"event {self.event.__name__}"

    def derive(
        self, qualified_name: str, args: Mapping[str, Any]
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """The ``(event name, fields)`` of each matching event one call performs.

        >>> class Ping(Event):
        ...     host: str
        >>> PERFORMED["net.ping"] = (Ping(_ParamRef("target")),)
        >>> list(EventRelation(Ping).derive("net.ping", {"target": "h", "n": 3}))
        [('Ping', {'host': 'h'})]
        >>> del PERFORMED["net.ping"]
        """

        for event in PERFORMED.get(qualified_name, ()):
            # By name, not ``isinstance``: switching trusted layers can import
            # an events module twice, and a contract holding one copy's class
            # must still see events built from the other. Matching nothing
            # would make the contract hold vacuously. Two layers sharing a
            # dotted module name are refused by the prover (#111).
            if self.key not in map(_qualified, type(event).__mro__):
                continue
            values: dict[str, Any] = {}
            for field in dataclasses.fields(event):
                ref = getattr(event, field.name)
                if ref.name not in args:
                    raise RelationError(
                        f"{qualified_name} performs {type(event).__name__} from "
                        f"parameter {ref.name!r}, which the recorded call lacks"
                    )
                values[field.name] = args[ref.name]
            yield type(event).__name__, values

    def facts(self, ctx: Any) -> list[Any]:
        derived = []
        for fact in ctx.facts:
            for name, values in self.derive(fact.qualified_name, fact.args):
                # Same reachability, loop quantifiers and call as the tool.
                derived.append(fact._replace(name=name, args=values))
        return derived


def _qualified(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def performs(builder: Callable[..., Event | list[Event]]) -> Callable[[F], F]:
    """Declare the events a trusted function performs.

    ``builder`` takes (a subset of) the function's parameters by name and
    returns one event or a list of them. It is called once, at import, with a
    placeholder per parameter; at prove time each recorded call to the function
    is read as these events, with the call's arguments in place.

    Place it directly above the ``def``, below ``@clauz3.trusted``.
    """

    names = list(inspect.signature(builder).parameters)
    result = builder(**{name: _ParamRef(name) for name in names})
    events = tuple(result) if isinstance(result, list) else (result,)
    for event in events:
        if not isinstance(event, Event):
            raise RelationError(
                f"@performs must return Event instances, not {type(event).__name__}"
            )
        for field in dataclasses.fields(event):
            if not isinstance(getattr(event, field.name), _ParamRef):
                raise RelationError(
                    f"@performs: {type(event).__name__}.{field.name} must be a "
                    "parameter of the trusted function, not a computed value"
                )

    def decorate(func: F) -> F:
        unknown = sorted(set(names) - set(inspect.signature(func).parameters))
        if unknown:
            raise RelationError(
                f"@performs on {func.__name__} names {unknown!r}, which are not "
                "its parameters"
            )
        PERFORMED[f"{func.__module__}.{func.__qualname__}"] = events
        return func

    return decorate
