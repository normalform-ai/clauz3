"""Handler registry, deliberately shaped like the prover's.

``deal_solver._registry.HandlersRegistry`` dispatches symbolic evaluation on
astroid node type and raises ``UnsupportedError`` for anything unregistered.
This is the same table with a concrete value domain instead of a symbolic one,
so "what the prover can reason about" and "what the interpreter will run" are
two registries that can be compared key-for-key -- see
``tests/test_interp_parity.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, TypeVar

import astroid

from .errors import UnsupportedNode

if TYPE_CHECKING:
    from .interpreter import Frame

T = TypeVar("T")
Handler = Callable[[Any, "Frame"], T]


class HandlerRegistry(Generic[T]):
    """Dispatch on exact astroid node type; unregistered nodes fail closed."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._handlers: dict[type[astroid.NodeNG], Handler[T]] = {}

    def register(
        self,
        node_type: type[astroid.NodeNG],
    ) -> Callable[[Handler[T]], Handler[T]]:
        if node_type in self._handlers:
            # Not an ``assert``: ``python -O`` strips those, and a silently
            # replaced handler in the table the fail-closed story rests on is
            # not something to discover in production.
            raise RuntimeError(f"duplicate {self._kind} handler for {node_type}")

        def wrapper(handler: Handler[T]) -> Handler[T]:
            self._handlers[node_type] = handler
            return handler

        return wrapper

    @property
    def node_types(self) -> frozenset[type[astroid.NodeNG]]:
        return frozenset(self._handlers)

    def __call__(self, node: astroid.NodeNG, frame: Frame) -> T:
        handler = self._handlers.get(type(node))
        if handler is None:
            raise UnsupportedNode(
                f"{self._kind} {type(node).__name__} is outside the proved subset",
                f"(line {getattr(node, 'lineno', '?')})",
            )
        return handler(node, frame)
