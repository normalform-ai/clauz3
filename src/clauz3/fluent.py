"""Stateful (fluent) contracts in the Reiter successor-state-axiom style.

The base relation language treats trusted calls as an unordered multiset of
guarded facts. That cannot express post-state properties such as "the door is
locked at the end" because ``unlock; lock`` and ``lock; unlock`` record the same
fact set but leave the door in opposite states.

A :class:`Fluent` adds a named, keyed, mutable cell whose value is determined by
the *ordered* sequence of trusted calls that write it. A trusted function
declares its successor-state axiom with the :func:`effect` decorator::

    DoorLocked = fluent("door_locked", value=bool, initial=True)

    @deal.has("trusted")
    @effect(lambda door: DoorLocked.set(door, False))
    def unlock_door(door: str) -> None: ...

Contracts are written over the fluent's final valuation::

    @contract
    def all_doors_locked_at_end() -> ContractSpec:
        return DoorLocked.final.all(lambda d: d.value == True)

    @contract
    def door_locked_at_end(door: str) -> ContractSpec:
        return DoorLocked.final[door] == True

The encoding mirrors the array-threading the program subset already uses for
local variables: each fluent is a Z3 array over its key sort, initialised to a
constant, then folded through the recorded fact trace in program order. Each
contributing fact applies a guarded ``Store`` (``If(cond, Store(a, k, v), a)``)
so only reachable calls mutate the array and later writes win. The fold runs at
contract-solve time over ``ctx.facts``, which symbolic execution has already
flattened into program order, so no change to the executor is required.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import z3

from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import types
from clauz3.spec import ContractSpec, LambdaSpec, _as_proxy

# Value/key types a fluent may carry. These map directly onto Z3 sorts and the
# matching value proxies, which is all the relation language already supports.
_SORTS: dict[type, Callable[[], z3.SortRef]] = {
    bool: z3.BoolSort,
    int: z3.IntSort,
    str: z3.StringSort,
}


def fluent(
    name: str,
    *,
    value: type,
    key: type = str,
    initial: Any,
) -> Fluent:
    """Declare a fluent (a keyed, mutable cell threaded through trusted calls).

    ``key`` is the type of the cell's index (``str`` for door/zone/device
    names, ``int`` for numeric ids). ``value`` is the type of the stored value.
    ``initial`` is the value every key holds before any trusted call runs.
    """
    if key not in _SORTS:
        raise UnsupportedError(f"unsupported fluent key type {key!r}")
    if value not in _SORTS:
        raise UnsupportedError(f"unsupported fluent value type {value!r}")
    if not isinstance(initial, value):
        raise UnsupportedError(
            f"fluent {name!r} initial value {initial!r} is not a {value.__name__}"
        )
    return Fluent(name=name, key_type=key, value_type=value, initial=initial)


def effect(
    builder: Callable[..., FluentUpdate | list[FluentUpdate]],
) -> Callable[[Any], Any]:
    """Declare a trusted function's successor-state axiom.

    ``builder`` is a callable whose parameters mirror (a subset of) the trusted
    function's parameters; it returns one or more :class:`FluentUpdate` values
    built with ``SomeFluent.set(key, value)``. The decorator is inert at runtime
    (like ``clauz3.guarantee``): it only records, on each referenced fluent, how
    this trusted call mutates it so the prover can fold it into the final state.
    """
    param_names = list(inspect.signature(builder).parameters)
    refs = {pname: _ParamRef(pname) for pname in param_names}
    result = builder(**refs)
    updates = list(result) if isinstance(result, (list, tuple)) else [result]
    for update in updates:
        if not isinstance(update, FluentUpdate):
            raise UnsupportedError(
                "@effect builder must return SomeFluent.set(...) value(s)"
            )

    def decorate(func: Any) -> Any:
        for update in updates:
            update.fluent._register_effect(func_name=func.__name__, update=update)
        return func

    return decorate


@dataclass(frozen=True)
class _ParamRef:
    """A reference to a trusted-function parameter inside an ``@effect`` axiom."""

    name: str


@dataclass(frozen=True)
class FluentUpdate:
    """A single ``fluent[key] = value`` successor-state assignment."""

    fluent: Fluent
    key: _ParamRef | str | int
    value: _ParamRef | bool | int | str


class Fluent:
    """A keyed, mutable cell whose value is a function of the trusted trace."""

    def __init__(
        self,
        *,
        name: str,
        key_type: type,
        value_type: type,
        initial: Any,
    ) -> None:
        self.name = name
        self.key_type = key_type
        self.value_type = value_type
        self.initial = initial
        # func name -> updates it applies to this fluent, in declaration order.
        self._effects: dict[str, list[FluentUpdate]] = {}

    def set(self, key: Any, value: Any) -> FluentUpdate:
        """Build a successor-state assignment for use inside ``@effect``."""
        return FluentUpdate(fluent=self, key=key, value=value)

    @property
    def final(self) -> FluentFinal:
        """The fluent's valuation after the whole program has run."""
        return FluentFinal(fluent=self)

    def _register_effect(self, *, func_name: str, update: FluentUpdate) -> None:
        self._effects.setdefault(func_name, []).append(update)

    # ── Z3 helpers ──────────────────────────────────────────────────────────

    def _key_sort(self) -> z3.SortRef:
        return _SORTS[self.key_type]()

    def _value_sort(self) -> z3.SortRef:
        return _SORTS[self.value_type]()

    def _initial_array(self) -> z3.ArrayRef:
        return z3.K(self._key_sort(), _literal_z3(self.value_type, self.initial))

    def _key_proxy(self, expr: z3.ExprRef) -> Any:
        return _proxy_for(self.key_type, expr)

    def _value_proxy(self, expr: z3.ExprRef) -> Any:
        return _proxy_for(self.value_type, expr)

    def _resolve(self, spec: Any, py_type: type, args: dict[str, Any]) -> z3.ExprRef:
        """Resolve a key/value spec to a Z3 expression for a given call's args."""
        if isinstance(spec, _ParamRef):
            if spec.name not in args:
                raise UnsupportedError(
                    f"fluent {self.name!r} effect references unknown parameter "
                    f"{spec.name!r}"
                )
            proxy = args[spec.name]
            return proxy.expr if hasattr(proxy, "expr") else proxy
        return _literal_z3(py_type, spec)

    def _final_array(self, ctx: Any) -> z3.ArrayRef:
        """Fold the recorded fact trace into this fluent's final-state array.

        ``ctx.facts`` is iterated in program order (symbolic execution flattens
        branch facts into a single guarded layer). Each contributing call applies
        a guarded ``Store`` so only reachable calls mutate the array and the last
        write along any path wins.
        """
        arr = self._initial_array()
        for fact in ctx.facts:
            updates = self._effects.get(fact.name)
            if not updates:
                continue
            if getattr(fact, "quantifiers", ()):
                raise UnsupportedError(
                    "fluent effects inside loops are not supported in v1; see "
                    "docs/todos/fluents.md"
                )
            cond = fact.cond.expr if hasattr(fact.cond, "expr") else fact.cond
            for update in updates:
                key_expr = self._resolve(update.key, self.key_type, fact.args)
                value_expr = self._resolve(update.value, self.value_type, fact.args)
                arr = z3.If(cond, z3.Store(arr, key_expr, value_expr), arr)
        return arr


@dataclass(frozen=True)
class FluentFinal:
    """Accessor for a fluent's final valuation, used to build contracts."""

    fluent: Fluent

    def all(self, predicate: Callable[[Any], Any]) -> ContractSpec:
        """Every key's final value satisfies ``predicate`` (``d.value`` / ``d.key``)."""
        return FluentAllSpec(
            fluent=self.fluent,
            predicate=LambdaSpec.from_callable(predicate),
        )

    def __getitem__(self, key: Any) -> FluentValueExpr:
        return FluentValueExpr(fluent=self.fluent, key=key)


@dataclass(frozen=True)
class FluentValueExpr:
    """A single key's final value, comparable against a literal in a contract."""

    fluent: Fluent
    key: Any

    def __eq__(self, other: Any) -> ContractSpec:  # type: ignore[override]
        return FluentCompareSpec(fluent=self.fluent, key=self.key, op="==", right=other)

    def __ne__(self, other: Any) -> ContractSpec:  # type: ignore[override]
        return FluentCompareSpec(fluent=self.fluent, key=self.key, op="!=", right=other)

    __hash__ = None  # type: ignore[assignment]


@dataclass(frozen=True)
class FluentAllSpec(ContractSpec):
    fluent: Fluent
    predicate: LambdaSpec

    def solve(self, *, ctx: Any) -> Any:
        arr = self.fluent._final_array(ctx)
        key_var = z3.Const(f"_fluent_{self.fluent.name}_key", self.fluent._key_sort())
        value_proxy = self.fluent._value_proxy(z3.Select(arr, key_var))
        key_proxy = self.fluent._key_proxy(key_var)
        body = self.predicate.evaluate(
            row={"value": value_proxy, "key": key_proxy},
            ctx=ctx,
        ).m_bool(ctx=ctx)
        return types.bool(expr=z3.ForAll([key_var], body.expr))


@dataclass(frozen=True)
class FluentCompareSpec(ContractSpec):
    fluent: Fluent
    key: Any
    op: str
    right: Any

    def solve(self, *, ctx: Any) -> Any:
        arr = self.fluent._final_array(ctx)
        key_expr = _literal_z3(self.fluent.key_type, self.key)
        value_proxy = self.fluent._value_proxy(z3.Select(arr, key_expr))
        right = _as_proxy(self.right, ctx=ctx)
        if self.op == "==":
            return value_proxy.m_eq(right, ctx=ctx)
        if self.op == "!=":
            return value_proxy.m_ne(right, ctx=ctx)
        raise RuntimeError("unreachable")


def _literal_z3(py_type: type, value: Any) -> z3.ExprRef:
    if py_type is bool:
        return z3.BoolVal(bool(value))
    if py_type is int:
        return z3.IntVal(int(value))
    if py_type is str:
        return z3.StringVal(str(value))
    raise UnsupportedError(f"unsupported fluent literal type {py_type!r}")


def _proxy_for(py_type: type, expr: z3.ExprRef) -> Any:
    if py_type is bool:
        return types.bool(expr=expr)
    if py_type is int:
        return types.int(expr=expr)
    if py_type is str:
        return types.str(expr=expr)
    raise UnsupportedError(f"unsupported fluent proxy type {py_type!r}")
