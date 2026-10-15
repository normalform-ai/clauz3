"""Optional proof monitoring; never supplies assumptions or changes obligations."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeAlias


class ProofDeadlineExceeded(TimeoutError):
    """The operator's proof deadline expired; no approval may be requested."""


@dataclass
class Monitor:
    deadline: float
    emit: Callable[[dict[str, object]], None]
    expected: dict[tuple[Any, int], tuple[Any, dict[str, object]]] = field(
        default_factory=dict
    )
    obligation: int = 0


_CURRENT: ContextVar[Monitor | None] = ContextVar("proof_monitor", default=None)
_CALL: ContextVar[dict[str, object] | None] = ContextVar("proof_call", default=None)
SourceLocations: TypeAlias = dict[tuple[int, int], dict[str, object]]
_SOURCE: ContextVar[tuple[Any, SourceLocations | None] | None] = ContextVar(
    "proof_source", default=None
)


@contextmanager
def monitoring(monitor: Monitor) -> Iterator[None]:
    token = _CURRENT.set(monitor)
    try:
        yield
    finally:
        _CURRENT.reset(token)


@contextmanager
def source_scope(module: Any, locations: SourceLocations | None) -> Iterator[None]:
    """Scope diagnostic positions to one proved module; never mutate solver ASTs."""
    token = _SOURCE.set((module, locations))
    try:
        yield
    finally:
        _SOURCE.reset(token)


def location(node: Any) -> dict[str, object]:
    position: dict[str, object] = {"line": node.lineno, "column": node.col_offset + 1}
    if scope := _SOURCE.get():
        module, locations = scope
        if node.root() is not module:
            # A trusted module may itself have been rewritten by the loader;
            # its AST coordinates are not certified original-file positions.
            return {"source_kind": "library", "module": node.root().name}
        if locations is not None:
            return locations.get(
                (node.lineno, node.col_offset), {"source_kind": "generated_proof"}
            )
    return {"source_kind": "submitted", **position}


@contextmanager
def call_site(node: Any) -> Iterator[None]:
    token = _CALL.set({**location(node), "call": node.as_string()[:300]})
    try:
        yield
    finally:
        _CALL.reset(token)


def remember(expr: Any, **details: object) -> None:
    if monitor := _CURRENT.get():
        # Retain the expression as well as its ID, preventing ID reuse while
        # constructing the remaining obligations. IDs are local to a Z3 context;
        # identical expressions keep their first location. Metadata is never solved.
        monitor.expected.setdefault(
            (expr.ctx, expr.get_id()), (expr, {**(_CALL.get() or {}), **details})
        )


def details(expr: Any) -> dict[str, object]:
    if monitor := _CURRENT.get():
        item = monitor.expected.get((expr.ctx, expr.get_id()))
        if item is not None:
            return item[1]
    return {}


def inherit(previous: Any, current: Any) -> None:
    if metadata := details(previous):
        remember(current, **metadata)


def active() -> bool:
    return _CURRENT.get() is not None


def phase(name: str, **fields: object) -> None:
    if monitor := _CURRENT.get():
        monitor.emit({"phase": name, **fields})


def solver_timeout_ms(default_ms: int) -> int:
    monitor = _CURRENT.get()
    if monitor is None:
        return default_ms
    remaining = monitor.deadline - time.monotonic()
    if remaining <= 0:
        raise ProofDeadlineExceeded("total proof deadline exceeded")
    return max(1, min(default_ms, int(remaining * 1000)))


def checking(description: str, metadata: dict[str, object] | None) -> None:
    if monitor := _CURRENT.get():
        monitor.obligation += 1
        phase(
            "proving",
            obligation=monitor.obligation,
            description=description,
            **(metadata or {}),
        )


def result(conclusion: str, message: str) -> None:
    if monitor := _CURRENT.get():
        phase(
            "proving",
            obligation=monitor.obligation,
            conclusion=conclusion,
            result=message[:1000],
        )
