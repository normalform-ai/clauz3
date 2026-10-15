"""Validate sampled boundary values without executing trusted effect bodies."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import deal.introspection


class BoundaryAssumptionError(ValueError):
    """An actual call and its sampled return violate the trusted declaration."""


def validate_boundary(
    func: Callable[..., Any],
    args: Sequence[Any],
    kwargs: Mapping[str, Any],
    result: Any,
) -> None:
    """Check concrete pre/post/ensure predicates with Deal's calling convention.

    The sampled return may satisfy the proof's symbolic assumptions yet fail
    the trusted declaration for the actual arguments. Such a replay is not a
    valid environment for checking the theorem's concrete conclusion. Only
    contract validators run here; neither the decorated function nor its
    underlying effect body is called, even when Deal runtime checks are off.
    """
    name = f"{func.__module__}.{func.__qualname__}"
    try:
        # Follow __wrapped__, as Deal and clauz3 preserve the trusted function's
        # original signature. Check arity even for a boundary with no predicates.
        inspect.signature(func).bind(*args, **kwargs)
    except (TypeError, ValueError) as exc:
        raise BoundaryAssumptionError(f"{name}: invalid call arguments: {exc}") from exc

    for contract in deal.introspection.get_contracts(func):
        if isinstance(contract, deal.introspection.Pre):
            kind = "precondition"
            contract_args, contract_kwargs = tuple(args), dict(kwargs)
        elif isinstance(contract, deal.introspection.Post):
            kind = "postcondition"
            contract_args, contract_kwargs = (result,), {}
        elif isinstance(contract, deal.introspection.Ensure):
            kind = "postcondition over arguments and result"
            contract_args, contract_kwargs = tuple(args), {**kwargs, "result": result}
        else:
            continue
        try:
            contract.init()
            contract.validate(*contract_args, **contract_kwargs)
        except Exception as exc:
            raise BoundaryAssumptionError(
                f"{name}: {kind} failed: {type(exc).__name__}: {exc}"
            ) from exc
