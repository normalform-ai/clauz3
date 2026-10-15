from functools import wraps

import deal
import pytest

import clauz3
from clauz3.differential_boundary import BoundaryAssumptionError, validate_boundary


def test_pre_post_and_ensure_validate_without_running_effect() -> None:
    @clauz3.trusted
    @clauz3.requires(lambda cap: cap > 0)
    @clauz3.ensures(lambda result: result >= 0)
    @clauz3.ensures(lambda cap, result: result <= cap)
    def read(cap: int) -> int:
        raise AssertionError("trusted body must not run")

    validate_boundary(read, (3,), {}, 2)
    validate_boundary(read, (), {"cap": 3}, 2)

    with pytest.raises(BoundaryAssumptionError, match="precondition failed"):
        validate_boundary(read, (0,), {}, 0)
    with pytest.raises(BoundaryAssumptionError, match="postcondition failed"):
        validate_boundary(read, (3,), {}, -1)
    with pytest.raises(
        BoundaryAssumptionError, match="postcondition over arguments and result failed"
    ):
        validate_boundary(read, (3,), {}, 4)


def test_ensure_uses_actual_arguments_not_original_symbolic_arguments() -> None:
    @clauz3.ensures(lambda key, result: result == key)
    @clauz3.trusted
    def echo(key: str) -> str:
        raise AssertionError("trusted body must not run")

    validate_boundary(echo, ("model-key",), {}, "model-key")
    with pytest.raises(BoundaryAssumptionError, match="postcondition"):
        validate_boundary(echo, ("actual-key",), {}, "model-key")


def test_defaults_and_wrapped_signatures_are_preserved() -> None:
    @deal.ensure(lambda value=2, *, result: value == result)
    @deal.pre(lambda value=2: value > 0)
    def read(value: int = 2) -> int:
        raise AssertionError("trusted body must not run")

    @wraps(read)
    def wrapper(*args: object, **kwargs: object) -> int:
        raise AssertionError("wrapper must not run")

    validate_boundary(wrapper, (), {}, 2)
    validate_boundary(wrapper, (), {"value": 3}, 3)
    with pytest.raises(BoundaryAssumptionError, match="invalid call arguments"):
        validate_boundary(wrapper, (1, 2), {}, 1)


def test_no_predicates_still_checks_call_signature() -> None:
    @clauz3.trusted
    def emit(value: int) -> None:
        raise AssertionError("trusted body must not run")

    validate_boundary(emit, (1,), {}, None)
    with pytest.raises(BoundaryAssumptionError, match="invalid call arguments"):
        validate_boundary(emit, (), {}, None)


def test_none_postcondition_receives_none() -> None:
    @clauz3.ensures(lambda result: result is None)
    @clauz3.trusted
    def emit() -> None:
        raise AssertionError("trusted body must not run")

    validate_boundary(emit, (), {}, None)
    with pytest.raises(BoundaryAssumptionError, match="postcondition failed"):
        validate_boundary(emit, (), {}, False)


def test_predicate_exceptions_are_invalid_boundary_assumptions() -> None:
    @clauz3.ensures(lambda result: 1 / result > 0)
    @clauz3.trusted
    def read() -> int:
        raise AssertionError("trusted body must not run")

    with pytest.raises(BoundaryAssumptionError, match="ZeroDivisionError"):
        validate_boundary(read, (), {}, 0)


def test_validator_runs_even_when_runtime_contract_checks_are_disabled() -> None:
    @clauz3.requires(lambda value: value > 0)
    def read(value: int) -> int:
        raise AssertionError("trusted body must not run")

    try:
        deal.disable(warn=False)
        with pytest.raises(BoundaryAssumptionError, match="precondition failed"):
            validate_boundary(read, (0,), {}, 0)
    finally:
        deal.enable()
