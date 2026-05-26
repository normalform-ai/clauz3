"""Static contract experiments for agent-authored Python."""

from collections.abc import Callable
from typing import TypeVar

from clauz3._vendor.deal_solver._funcs._registry import register
from clauz3.row import ColumnRef, Row
from clauz3.spec import contract, effect

__version__ = "0.1.0"

F = TypeVar("F", bound=Callable[..., object])


def guarantee(_predicate: object) -> Callable[[F], F]:
    """Attach a clauz3 guarantee to a function.

    At runtime this decorator is deliberately inert. The prover reads the
    decorator from the AST and asks the corresponding registered solver to
    translate it into z3 constraints.
    """

    def decorate(func: F) -> F:
        return func

    return decorate


def solver(name: str) -> Callable[[F], F]:
    """Register a symbolic implementation for a predicate name."""
    return register(name)


__all__ = [
    "ColumnRef",
    "Row",
    "__version__",
    "contract",
    "effect",
    "guarantee",
    "solver",
]
