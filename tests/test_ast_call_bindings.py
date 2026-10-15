"""AST-backed calls bind Python parameter kinds or refuse before evaluation."""

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProofResult, prove_text


def _prove(signature: str, expression: str, call: str, expected: int) -> ProofResult:
    results = prove_text(
        f"def helper({signature}) -> int:\n"
        f"    return {expression}\n\n"
        "def main() -> None:\n"
        f"    assert {call} == {expected}\n"
    )
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize(
    ("signature", "call"),
    [
        pytest.param("value: int", "helper(1, 2)", id="extra-positional"),
        pytest.param("value: int", "helper()", id="missing-positional"),
        pytest.param("value: int", "helper(1, value=2)", id="positional-and-keyword"),
        pytest.param("value: int", "helper(1, unknown=2)", id="unknown-keyword"),
        pytest.param(
            "value: int, /", "helper(value=1)", id="positional-only-as-keyword"
        ),
        pytest.param(
            "value: int, *, required: int", "helper(1)", id="missing-keyword-only"
        ),
        pytest.param("*, required: int", "helper(1)", id="keyword-only-as-positional"),
        pytest.param("*values: int", "helper()", id="variadic-positional"),
        pytest.param("**values: int", "helper()", id="variadic-keyword"),
    ],
)
def test_malformed_ast_function_calls_are_refused(signature: str, call: str) -> None:
    # The constant return deliberately leaves parameters unread: an invalid
    # binding must be refused even if the body would never notice the error.
    result = _prove(signature, "1", call, 1)

    assert result.proof.conclusion is Conclusion.SKIP
    assert "unsupported function argument binding" in str(result.proof)


@pytest.mark.parametrize(
    ("signature", "call", "detail"),
    [
        pytest.param(
            "value: int",
            "helper(value=1, value=2)",
            "duplicate keyword",
            id="duplicate",
        ),
        pytest.param("ctx: int", "helper(ctx=1)", "reserved keyword", id="ctx"),
        pytest.param("self: int", "helper(self=1)", "reserved keyword", id="self"),
        pytest.param(
            "self: int",
            "helper.__call__(self=1)",
            "reserved keyword",
            id="wrapped-self",
        ),
        pytest.param(
            "var_name: int", "helper(var_name=1)", "reserved keyword", id="var-name"
        ),
        pytest.param(
            "value: int", "helper(*[1])", "positional unpacking", id="star-args"
        ),
        pytest.param(
            "value: int",
            "helper(**{'value': 1})",
            "keyword unpacking",
            id="star-keywords",
        ),
    ],
)
def test_call_dispatch_refuses_ambiguous_or_internal_keyword_bindings(
    signature: str, call: str, detail: str
) -> None:
    result = _prove(signature, "1", call, 1)

    assert result.proof.conclusion is Conclusion.SKIP
    assert f"unsupported call argument binding: {detail}" in str(result.proof)


@pytest.mark.parametrize(
    ("signature", "expression", "call", "expected"),
    [
        pytest.param("value: int", "value", "helper(value=7)", 7, id="keyword"),
        pytest.param("ctx: int", "ctx", "helper(7)", 7, id="positional-ctx"),
        pytest.param("self: int", "self", "helper(7)", 7, id="positional-self"),
        pytest.param(
            "var_name: int", "var_name", "helper(7)", 7, id="positional-var-name"
        ),
        pytest.param("value: int = 9", "value", "helper(7)", 7, id="explicit-default"),
    ],
)
def test_valid_bindings_prove_the_actual_argument_values(
    signature: str, expression: str, call: str, expected: int
) -> None:
    result = _prove(signature, expression, call, expected)
    assert result.proof.conclusion is Conclusion.OK

    wrong = _prove(signature, expression, call, expected + 1)
    assert wrong.proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize(
    ("signature", "expression", "call", "expected"),
    [
        pytest.param("value: int, /", "value", "helper(7)", 7, id="positional-only"),
        pytest.param("*, value: int", "value", "helper(value=7)", 7, id="keyword-only"),
        pytest.param(
            "first: int, /, second: int, *, third: int",
            "first * 100 + second * 10 + third",
            "helper(1, 2, third=3)",
            123,
            id="mixed-positional",
        ),
        pytest.param(
            "first: int, /, second: int, *, third: int",
            "first * 100 + second * 10 + third",
            "helper(1, third=3, second=2)",
            123,
            id="mixed-keywords",
        ),
        pytest.param(
            "*, value: int = 9",
            "value",
            "helper(value=7)",
            7,
            id="explicit-keyword-default",
        ),
    ],
)
def test_ordinary_helper_parameter_kinds_match_the_interpreter_subset(
    signature: str, expression: str, call: str, expected: int
) -> None:
    # Python can bind these valid calls, but the subset interpreter refuses
    # their helper declarations/calls. They must not acquire a proof approval.
    result = _prove(signature, expression, call, expected)

    assert result.proof.conclusion is Conclusion.SKIP
    assert (
        "unsupported function argument binding: positional-only or "
        "keyword-only parameters in ordinary functions"
    ) in str(result.proof)


@pytest.mark.parametrize(
    "signature",
    [
        pytest.param("value: int = 1", id="positional-default"),
        pytest.param("value: int = 1, /", id="positional-only-default"),
        pytest.param("*, value: int = 1", id="keyword-only-default"),
    ],
)
def test_ordinary_function_omitted_defaults_are_not_invented(signature: str) -> None:
    # Default expressions are evaluated at definition time in Python, which the
    # prover does not model; a constant body must not hide an omitted binding.
    result = _prove(signature, "1", "helper()", 1)

    assert result.proof.conclusion is Conclusion.SKIP
    assert "unsupported function argument binding" in str(result.proof)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param("helper.__call__(value=7)", id="one-wrapper"),
        pytest.param("helper.__call__.__call__(value=7)", id="nested-wrapper"),
    ],
)
def test_call_wrappers_refuse_even_valid_user_keywords(call: str) -> None:
    # The call-surface repair refuses wrappers before binding or body evaluation,
    # matching the interpreter. Direct keyword calls are covered above.
    for expected in (7, 8):
        result = _prove("value: int", "value", call, expected)
        assert result.proof.conclusion is Conclusion.SKIP
        assert "explicit __call__ attributes are not supported" in str(result.proof)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param("helper.__call__(1, value=2)", id="duplicate-binding"),
        pytest.param("helper.__call__(1, unknown=2)", id="unknown-keyword"),
    ],
)
def test_call_wrappers_cannot_bypass_function_binding_checks(call: str) -> None:
    result = _prove("value: int", "1", call, 1)

    assert result.proof.conclusion is Conclusion.SKIP
    assert "explicit __call__ attributes are not supported" in str(result.proof)
