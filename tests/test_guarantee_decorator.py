"""``clauz3.guarantee`` is typed precisely and inert at runtime (issue #2).

The static half of this file is checked by ``just check`` (strict mypy over
``tests``). Each rejected call below carries a ``type: ignore`` for the exact
error mypy must report; because strict mode enables ``warn_unused_ignores``,
loosening the signature back to ``object`` (or to a decorator that returns
``Any``) fails type checking here.
"""

from typing import assert_type

import pytest

import clauz3
from clauz3.spec import ContractSpec, effect, no_guarantees

SEND_EMAIL = effect("send_email")


@clauz3.guarantee(no_guarantees())
@clauz3.guarantee(SEND_EMAIL.empty())
def main(x: int) -> str:
    return str(x)


def test_decorated_function_keeps_its_signature() -> None:
    assert_type(main(3), str)
    assert main(3) == "3"
    with pytest.raises(TypeError):
        main(3, 4)  # type: ignore[call-arg]


def test_decorator_returns_the_function_unchanged() -> None:
    def body() -> None: ...

    assert clauz3.guarantee(no_guarantees())(body) is body


def test_predicate_must_be_a_contract_spec() -> None:
    assert_type(no_guarantees(), ContractSpec)

    @clauz3.guarantee("hello")  # type: ignore[arg-type]
    def bad_str() -> None: ...

    @clauz3.guarantee(42)  # type: ignore[arg-type]
    def bad_int() -> None: ...

    @clauz3.guarantee(no_guarantees)  # type: ignore[arg-type]
    def bad_uncalled_helper() -> None: ...

    @clauz3.guarantee(None)  # type: ignore[arg-type]
    def bad_none() -> None: ...


def test_predicate_is_positional_only() -> None:
    # Match the prover/runner grammar: exactly one positional argument.
    # The public signature also refuses keyword forms under plain Python.
    with pytest.raises(TypeError, match="positional-only"):
        clauz3.guarantee(predicate=no_guarantees())  # type: ignore[call-arg]
