"""Static contract experiments for agent-authored Python."""

from collections.abc import Callable
from inspect import Signature
from typing import TypeVar

from clauz3._vendor.deal_solver._funcs._registry import register
from clauz3.conditions import ensures, requires
from clauz3.effects import mock, trusted
from clauz3.paths import DirectoryPath, FilePath
from clauz3.row import ColumnRef, Row
from clauz3.spec import ContractSpec, contract, effect

__version__ = "0.1.0"

F = TypeVar("F", bound=Callable[..., object])


def guarantee(predicate: ContractSpec, /) -> Callable[[F], F]:
    """Attach a clauz3 guarantee to a function.

    ``predicate`` is a :class:`~clauz3.spec.ContractSpec` built by a trusted
    contracts module, e.g. ``emails.only(["bob@example.com"])``. Stack one
    decorator per guarantee.

    This is a runtime no-op: under plain Python the function is returned
    unchanged, and under ``clauz3 run`` the decorator is never evaluated at
    all. It is load-bearing for the prover, which reads the first positional
    argument from the AST and discharges it as a symbolic guarantee; the
    approval request and receipt bind to the same text. Removing the decorator
    removes the contract; coverage policies can report the resulting gap.
    """

    def decorate(func: F) -> F:
        return func

    return decorate


def solver(
    name: str,
    *,
    signature: Callable[..., object] | Signature | None = None,
) -> Callable[[F], F]:
    """Register a model with an explicit supported Python-facing signature.

    ``signature`` is a callable whose signature to use, or an inspect.Signature.
    Models without this metadata are refused when called during a proof.
    """
    return register(name, signature=signature)


__all__ = [
    "ColumnRef",
    "ContractSpec",
    "DirectoryPath",
    "FilePath",
    "Row",
    "__version__",
    "contract",
    "effect",
    "ensures",
    "guarantee",
    "mock",
    "requires",
    "solver",
    "trusted",
]
