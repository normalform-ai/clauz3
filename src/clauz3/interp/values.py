"""The interpreter's value domain and its trusted boundary.

The proved subset ranges over a small, closed set of values: ``None``, ``bool``,
``int``, ``float``, ``str``, and finite containers of those, plus ``clauz3.Row``
instances handed back by trusted layers. Nothing else is ever a value an
interpreted program can hold.

That closure is what removes the sandbox-escape problem rather than mitigating
it. CPython escapes all take the same shape: reach a live Python object, walk
its attributes to ``__class__`` / ``__globals__`` / ``__subclasses__``, and from
there to ``os``. An interpreted program has no way to name a live Python object
in the first place -- the only ones that exist are behind
:class:`TrustedCallable` and :class:`TrustedModule`, which expose a call and a
fixed attribute table, never the object itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import astroid

from clauz3.row import Row

from .errors import TrustedBoundaryError

#: Scalar types an interpreted program may hold.
SCALAR_TYPES: tuple[type, ...] = (bool, int, float, str)

#: Container types an interpreted program may hold, checked elementwise.
CONTAINER_TYPES: tuple[type, ...] = (list, tuple, set, frozenset, dict)


@dataclass
class Scope:
    """A lexical scope: locals over an optional enclosing scope."""

    names: dict[str, Any] = field(default_factory=dict)
    parent: Scope | None = None

    def get_own(self, name: str) -> Any:
        """Look up ``name`` in this layer only, ignoring enclosing scopes.

        Needed to distinguish "not bound yet in this function" from "not a
        local at all" -- the difference between ``UnboundLocalError`` and
        resolving to an enclosing binding.
        """
        return self.names.get(name, _MISSING)

    def get(self, name: str) -> Any:
        scope: Scope | None = self
        while scope is not None:
            if name in scope.names:
                return scope.names[name]
            scope = scope.parent
        return _MISSING

    def set(self, name: str, value: Any) -> None:
        self.names[name] = value

    def child(self) -> Scope:
        return Scope(parent=self)


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"


_MISSING = _Missing()


@dataclass(frozen=True)
class UserFunction:
    """A function defined in the untrusted program.

    Calling one re-enters the interpreter over its body. Decorators are
    recorded but never evaluated -- ``@clauz3.guarantee(...)`` is a proof
    annotation, and nothing else is allowed on an untrusted definition.
    """

    node: astroid.FunctionDef
    closure: Scope
    #: The lexically enclosing ``(scope, local_names)`` chain, captured where the
    #: ``def`` appears. Python binds scope lexically, so this must come from the
    #: definition site and never from whoever calls it.
    enclosing: tuple[tuple[Scope, frozenset[str]], ...] = ()
    #: Default arguments, evaluated at ``def`` time exactly as CPython does.
    #: At module level they are additionally restricted to literals by
    #: ``interpreter._require_inert``: a default evaluated outside the proved
    #: body is unproved whoever evaluates it. Do not remove that gate on the
    #: reasoning that the interpreter is the one evaluating -- that confuses
    #: containment (structural here) with soundness (not).
    defaults: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return str(self.node.name)


@dataclass(frozen=True)
class LambdaFunction:
    """A ``lambda`` defined in the untrusted program."""

    node: astroid.Lambda
    closure: Scope
    #: As :attr:`UserFunction.enclosing` -- captured lexically, at the lambda.
    enclosing: tuple[tuple[Scope, frozenset[str]], ...] = ()
    #: As :attr:`UserFunction.defaults`. Absent here for several rounds, which
    #: let ``lambda x=send_email(...): x`` skip the ``_eval_defaults`` gate
    #: entirely -- the effect was neither performed nor refused.
    defaults: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "<lambda>"


@dataclass(frozen=True)
class TrustedCallable:
    """A real Python function from a trusted layer.

    This is the one place where interpretation stops and CPython resumes.
    Trusted layers are full-expressivity Python by design: they open sockets,
    write files, and shell out. What they may *exchange* with the interpreted
    program is restricted to the subset value domain in both directions.
    """

    qualname: str
    func: Callable[..., Any]
    #: True for a trusted-layer function, False for a modelled library function
    #: (``math.sin``) or a framework no-op (``clauz3.guarantee``). Only the
    #: former is an *effect*: recording library calls in ``Outcome.effects``
    #: would make it incomparable with what the proof reasoned about.
    trusted: bool = False


@dataclass(frozen=True)
class ImportedName:
    """Inert binding used only while checking module declarations.

    An import can bind a supported library name without loading its Python
    module. This value is deliberately not callable and has no runtime surface.
    """

    qualname: str


@dataclass(frozen=True)
class TrustedModule:
    """A trusted or allowlisted Python module, exposed through a name table.

    Attribute access returns a wrapped callable or a subset constant. It never
    returns the underlying Python object, so ``fs.__dict__`` and friends have
    nothing to bind to.
    """

    qualname: str
    #: ``None`` during a declaration-only check of a nontrusted import.
    module: ModuleType | None
    #: The complete set of attribute names this module exposes, computed once by
    #: :func:`clauz3.interp.interpreter.exposed_names`. Never ``None``: a module
    #: whose surface has not been decided exposes nothing.
    allowed: frozenset[str] = frozenset()
    #: Names the prover *does* model here but the boundary cannot marshal --
    #: ``re.match`` and friends return live ``Match``/``Pattern`` objects. Kept
    #: apart from ``allowed`` so the refusal can name the actual reason: the
    #: diagnostic used to tell a ``re`` user "the prover models none of its
    #: functions", which is false and points at a fix that cannot work.
    withheld: frozenset[str] = frozenset()
    #: Symbolically registered names without a framework-captured runtime
    #: implementation. An unused namespace remains inert; binding or reading
    #: one of these members must be refused before approval.
    unavailable: frozenset[str] = frozenset()
    #: Whether this module lives under a trusted root. Propagated to the
    #: callables read off it.
    trusted: bool = False

    def require_provider(self, name: str | None = None) -> None:
        """Refuse an unavailable member or an escaping incomplete namespace."""
        missing = self.unavailable if name is None else self.unavailable & {name}
        if missing:
            raise TrustedBoundaryError(
                f"{self.qualname!r} has no fixed interpreter provider for its "
                f"modelled names: {', '.join(sorted(missing))}"
            )

    def attr(self, name: str) -> Any:
        self.require_provider(name)
        if name.startswith("_"):
            raise TrustedBoundaryError(
                f"{self.qualname}.{name}: private attributes are not addressable "
                "from an interpreted program",
            )
        if name not in self.allowed:
            if name in self.withheld:
                raise TrustedBoundaryError(
                    f"{self.qualname}.{name} is modelled by the prover but "
                    "returns a value outside the interpreter's domain, so a "
                    "program using it can prove and cannot run. Rewrite it out "
                    "of the program, or add the return type to the domain.",
                )
            if not self.allowed:
                # Most often a dotted ``--allow-import`` opt-in: importable, but
                # outside every trusted root, so it is treated as a library and
                # the prover models none of it. Say that rather than printing an
                # empty allowlist.
                raise TrustedBoundaryError(
                    f"{self.qualname} exposes nothing to interpreted programs: "
                    "it is importable but not under a trusted root, and the "
                    "prover models none of its functions",
                )
            raise TrustedBoundaryError(
                f"{self.qualname}.{name} is not exposed to interpreted programs "
                f"(allowed: {', '.join(sorted(self.allowed))})",
            )
        if self.module is None:
            return ImportedName(f"{self.qualname}.{name}")
        try:
            value = getattr(self.module, name)
        except AttributeError as exc:
            raise TrustedBoundaryError(
                f"{self.qualname} has no attribute {name!r}",
            ) from exc
        return wrap_trusted(f"{self.qualname}.{name}", value, trusted=self.trusted)


@dataclass(frozen=True)
class RowClass:
    """A ``clauz3.Row`` subclass, usable as a constructor and a schema."""

    schema: type[Row]

    @property
    def name(self) -> str:
        return self.schema.__name__


@dataclass(frozen=True)
class BoundMethod:
    """A modelled method bound to a subset value (``"ab".startswith``).

    The receiver is always subset data, so ``impl`` is CPython's own unbound
    method for that type -- there is no attacker-defined class in the domain for
    it to dispatch into. See :mod:`clauz3.interp.methods`.
    """

    receiver: Any
    name: str
    impl: Callable[..., Any]


@dataclass(frozen=True)
class BuiltinFunction:
    """A builtin implemented by the interpreter, not by CPython lookup."""

    name: str
    impl: Callable[..., Any]


CallableValue = (
    UserFunction
    | LambdaFunction
    | TrustedCallable
    | BuiltinFunction
    | BoundMethod
    | RowClass
)


def wrap_trusted(qualname: str, value: Any, *, trusted: bool = False) -> Any:
    """Wrap a value pulled out of a trusted module for interpreted use.

    A module is never wrapped here. Re-exporting a module through an attribute
    would make a trusted layer's *own imports* part of its interpreted surface:
    ``import os`` at the top of ``effects.py`` would put ``effects.os.system``
    one attribute access away. Submodules are reached through ``import``
    statements instead, which run the allowlist check.
    """
    if isinstance(value, ModuleType):
        raise TrustedBoundaryError(
            f"{qualname} is a module; import it directly rather than reaching it "
            "through another module's attributes",
        )
    if isinstance(value, type) and issubclass(value, Row):
        return RowClass(schema=value)
    if callable(value):
        return TrustedCallable(qualname=qualname, func=value, trusted=trusted)
    return marshal_in(value, origin=qualname)


def marshal_in(value: Any, *, origin: str, _seen: set[int] | None = None) -> Any:
    """Admit a value returned by trusted code into the interpreted program.

    Fail-closed: anything outside the subset value domain is rejected rather
    than passed through. Containers are copied so a trusted layer cannot hand
    out a live reference to its own mutable state.

    ``_seen`` breaks cycles. A trusted layer returning ``a = []; a.append(a)``
    would otherwise recurse to a native ``RecursionError``, which escapes the
    documented error contract on the surface this design calls load-bearing.
    """
    _seen = set() if _seen is None else _seen
    if id(value) in _seen:
        raise TrustedBoundaryError(
            f"{origin} returned a cyclic container, which is outside the subset "
            "value domain",
        )
    normalised = normalise_scalar(value)
    if normalised is not None:
        # A ``StrEnum`` member or any ``str``/``int`` subclass is a live object
        # with overridable behaviour: ``methods.lookup`` would resolve
        # ``getattr(type(value), name)`` on the subclass and ``a + b`` would
        # dispatch into its ``__add__``. Narrowing to the exact builtin is what
        # makes "there are no user-defined classes in the domain" true.
        return normalised
    if value is None or isinstance(value, SCALAR_TYPES):
        return value
    if isinstance(value, Row):
        return _marshal_row(value, marshal_in, origin=origin, seen=_seen | {id(value)})
    if isinstance(value, type) and issubclass(value, Row):
        # A Row schema is declarative data, not a live object: the interpreter
        # only ever reads its field table.
        return RowClass(schema=value)
    if isinstance(value, (list, tuple, set, frozenset)):
        _seen = _seen | {id(value)}
        items = [marshal_in(item, origin=origin, _seen=_seen) for item in value]
        if isinstance(value, list):
            return items
        if isinstance(value, tuple):
            return tuple(items)
        # ``frozenset`` is a declared domain type; collapsing it to ``set``
        # silently changed a value's type across the boundary. ``isinstance``,
        # not ``type(...) is``: an exact-type test let a ``frozenset``
        # *subclass* through as a mutable ``set``, which is the very
        # substitution this branch exists to prevent. The exact builtin is
        # rebuilt either way, matching ``normalise_scalar``'s convention for
        # subclasses of the scalar types.
        return frozenset(items) if isinstance(value, frozenset) else set(items)
    if isinstance(value, Mapping):
        _seen = _seen | {id(value)}
        return {
            marshal_in(key, origin=origin, _seen=_seen): marshal_in(
                item, origin=origin, _seen=_seen
            )
            for key, item in value.items()
        }
    raise TrustedBoundaryError(
        f"{origin} returned {type(value).__name__}, which is outside the subset "
        "value domain (None/bool/int/float/str, containers thereof, or Row)",
    )


def normalise_scalar(value: Any) -> Any:
    """The exact-builtin form of a scalar, or None if ``value`` is not one.

    ``bool`` is tested first: it subclasses ``int``, and collapsing it would
    turn ``True`` into ``1``.
    """
    for exact in (bool, int, float, str):
        if isinstance(value, exact):
            return value if type(value) is exact else exact(value)
    return None


def marshal_out(value: Any, *, origin: str, _seen: set[int] | None = None) -> Any:
    """Check a value being handed from the interpreter to trusted code.

    Callables and schema objects are interpreter-internal and never cross out:
    a trusted layer that received a :class:`UserFunction` could only call it by
    re-entering the interpreter behind its back.

    **Mutable containers are copied on the way out**, which is a real semantic
    difference from the ``exec`` path: a trusted function that appends to a list
    argument mutates the caller's object under CPython and has no observable
    effect here. The copy is deliberate -- a trusted layer must not retain a
    live handle on interpreter state -- so the obligation is on trusted layers
    to return values rather than mutate arguments. See the trusted-layer
    obligations in ``docs/todos/subset-interpreter.md``.

    ``_seen`` breaks cycles, as in :func:`marshal_in`.
    """
    _seen = set() if _seen is None else _seen
    if id(value) in _seen:
        raise TrustedBoundaryError(
            f"cannot pass a cyclic container to {origin}",
        )
    normalised = normalise_scalar(value)
    if normalised is not None:
        return normalised
    if value is None or isinstance(value, SCALAR_TYPES):
        return value
    if isinstance(value, Row):
        # Rebuilt, not waved through. ``Row.__init__`` checks field *names*
        # only, so ``R(x=lambda n: n)`` would otherwise hand a trusted layer a
        # LambdaFunction holding a live interpreter Scope. Returning a new Row
        # built from marshalled fields also makes the documented copy invariant
        # real: an earlier version validated the fields and then returned the
        # original, so a list nested in a Row crossed uncopied and the trusted
        # layer could mutate the interpreter's own object.
        return _marshal_row(value, marshal_out, origin=origin, seen=_seen | {id(value)})
    if isinstance(value, RowClass):
        # Trusted layers take a Row schema as the shape to return rows in
        # (``read_rows(UserRow, ...)``). Unwrap to the class itself.
        return value.schema
    if isinstance(value, (list, tuple, set, frozenset)):
        _seen = _seen | {id(value)}
        items = [marshal_out(item, origin=origin, _seen=_seen) for item in value]
        if isinstance(value, list):
            return items
        if isinstance(value, tuple):
            return tuple(items)
        # ``frozenset`` is a declared domain type; collapsing it to ``set``
        # silently changed a value's type across the boundary. ``isinstance``,
        # not ``type(...) is``: an exact-type test let a ``frozenset``
        # *subclass* through as a mutable ``set``, which is the very
        # substitution this branch exists to prevent. The exact builtin is
        # rebuilt either way, matching ``normalise_scalar``'s convention for
        # subclasses of the scalar types.
        return frozenset(items) if isinstance(value, frozenset) else set(items)
    if isinstance(value, dict):
        _seen = _seen | {id(value)}
        return {
            marshal_out(key, origin=origin, _seen=_seen): marshal_out(
                item, origin=origin, _seen=_seen
            )
            for key, item in value.items()
        }
    raise TrustedBoundaryError(
        f"cannot pass {type(value).__name__} to {origin}: only subset data may "
        "cross into trusted code",
    )


def _marshal_row(
    row: Row,
    marshal: Callable[..., Any],
    *,
    origin: str,
    seen: set[int],
) -> Row:
    """Rebuild ``row`` with every declared field marshalled.

    ``seen`` is threaded through, and the row's own id is already in it. Without
    that, every hop through a ``Row`` reset cycle detection, so a container
    holding the row that holds the container recursed to a native
    ``RecursionError`` -- reachable from untrusted code with nothing but a
    subscript assignment.
    """
    fields = {
        name: marshal(
            getattr(row, name),
            origin=f"{origin} field {name!r}",
            _seen=seen,
        )
        for name in type(row).__annotations__
    }
    return type(row)(**fields)


def type_name(value: Any) -> str:
    """Human-readable type name for diagnostics."""
    return type(value).__name__
