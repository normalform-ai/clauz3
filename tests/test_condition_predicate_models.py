"""Condition evaluation must check its own errors before yielding assumptions."""

from textwrap import dedent

import astroid
import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._context import Context, ReturnInfo
from clauz3._vendor.deal_solver._eval_contracts import eval_contracts
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import BoolSort, types
from clauz3.prover import AgentTheorem, ProofResult, prove_text

RETURN_ERROR = "postcondition requires a modeled return on every normal completion path"


def _prove(source: str) -> ProofResult:
    results = prove_text(dedent(source))
    assert len(results) == 1
    assert results[0].name == "main"
    return results[0]


def _condition(
    decorator: str, *, guard: BoolSort
) -> tuple[astroid.FunctionDef, Context]:
    module = astroid.parse(
        f"import clauz3\n{decorator}\ndef check(value: int) -> int:\n    return value\n"
    )
    function = module.body[-1]
    assert isinstance(function, astroid.FunctionDef)
    context = Context.make_empty(get_contracts=AgentTheorem.get_contracts)
    value = types.int.val(3, ctx=context)
    context.scope.set("value", value)
    context.returns.add(ReturnInfo(value=value, cond=guard))
    return function, context


@pytest.mark.parametrize("parameters", ["result", "value, result"])
@pytest.mark.parametrize("guard_kind", ["false", "true", "symbolic"])
def test_postcondition_error_uses_its_return_guard(
    parameters: str, guard_kind: str
) -> None:
    expression = (
        z3.Bool("return_taken")
        if guard_kind == "symbolic"
        else z3.BoolVal(guard_kind == "true")
    )
    function, context = _condition(
        f"@clauz3.ensures(lambda {parameters}: len(result) == 0)",
        guard=BoolSort(expression),
    )

    if guard_kind != "false":
        with pytest.raises(
            UnsupportedError, match="condition predicate may raise TypeError"
        ):
            eval_contracts(function, context, completed=False)
        return

    contracts = eval_contracts(function, context, completed=False)
    assert all(z3.is_true(z3.simplify(post.expr)) for post in contracts.post)
    assert context.exceptions.layer == []


@pytest.mark.parametrize("contradictory_predicate", [False, True])
@pytest.mark.parametrize("assumption_kind", ["false", "opposite-guard"])
def test_assumptions_and_predicate_result_cannot_hide_a_live_error(
    contradictory_predicate: bool, assumption_kind: str
) -> None:
    guard = z3.Bool("return_taken")
    expression = "len(result) == 0"
    if contradictory_predicate:
        expression = f"({expression}) and False"
    function, context = _condition(
        f"@clauz3.ensures(lambda result: {expression})", guard=BoolSort(guard)
    )
    context.given.add(
        BoolSort(z3.BoolVal(False) if assumption_kind == "false" else z3.Not(guard))
    )

    with pytest.raises(
        UnsupportedError, match="condition predicate may raise TypeError"
    ):
        eval_contracts(function, context, completed=False)


@pytest.mark.parametrize(
    "condition", ["True or len(value) == 0", "not (False and len(value) == 0)"]
)
@pytest.mark.parametrize("kind", ["requires", "ensures"])
def test_dead_predicate_operands_remain_supported(condition: str, kind: str) -> None:
    function, context = _condition(
        f"@clauz3.{kind}(lambda value: {condition})", guard=BoolSort(z3.BoolVal(True))
    )
    contracts = eval_contracts(function, context, completed=False)
    values = contracts.pre if kind == "requires" else contracts.post
    assert all(z3.is_true(z3.simplify(value.expr)) for value in values)
    assert not context.exceptions.layer


@pytest.mark.parametrize("parameters", ["result", "value, result"])
def test_invalid_postcondition_on_unreachable_return_is_ignored(
    parameters: str,
) -> None:
    result = _prove(
        "import clauz3\n"
        f"@clauz3.ensures(lambda {parameters}: {{1: True}}[result])\n"
        "def check(value: int) -> int:\n"
        "    if False:\n        return 0\n"
        "    return 1\n"
        "def main() -> None:\n    assert check(3) == 1\n"
    )
    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize("trusted", [False, True], ids=["ordinary", "trusted"])
@pytest.mark.parametrize("mode", ["live-invalid", "valid", "skipped"])
def test_nested_predicate_assertion_is_checked_before_call_assumptions(
    trusted: bool, mode: str
) -> None:
    argument = 1 if mode == "valid" else -1
    predicate = "True or predicate(value)" if mode == "skipped" else "predicate(value)"
    boundary = "@clauz3.trusted\n" if trusted else ""
    source = (
        "import clauz3\n"
        "def predicate(value: int) -> bool:\n"
        "    assert value > 0\n    return True\n"
        f"@clauz3.requires(lambda value: {predicate})\n"
        f"{boundary}def check(value: int) -> int:\n    return value\n"
        f"def main() -> None:\n    check({argument})\n    assert True\n"
    )
    result = _prove(source)
    if mode == "live-invalid":
        assert result.proof.conclusion is Conclusion.SKIP
        assert "condition predicate has an unchecked proof obligation" in str(
            result.proof.error
        )
    else:
        assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize("trusted", [False, True], ids=["ordinary", "trusted"])
def test_exception_in_a_nested_predicate_call_is_not_lost(trusted: bool) -> None:
    boundary = "@clauz3.trusted\n" if trusted else ""
    result = _prove(
        "import clauz3\n"
        "def predicate(value: int) -> bool:\n    return len(value) == 0\n"
        "@clauz3.requires(lambda value: predicate(value))\n"
        f"{boundary}def check(value: int) -> int:\n    return value\n"
        "def main() -> None:\n    check(3)\n    assert True\n"
    )
    assert result.proof.conclusion is Conclusion.SKIP
    assert "condition predicate may raise TypeError" in str(result.proof.error)


def test_nested_assertion_on_unreachable_return_does_not_refuse_live_return() -> None:
    result = _prove(
        "import clauz3\n"
        "def predicate(value: int) -> bool:\n"
        "    assert value > 0\n    return True\n"
        "@clauz3.ensures(lambda result: predicate(result))\n"
        "def check() -> int:\n"
        "    if False:\n        return -1\n"
        "    return 1\n"
        "def main() -> None:\n    assert check() == 1\n"
    )
    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize("parameters", ["result", "value, result"])
@pytest.mark.parametrize(
    "live_result", [1, 0], ids=["dead-predicate", "live-predicate"]
)
def test_nested_invalid_precondition_uses_the_enclosing_return_guard(
    parameters: str, live_result: int
) -> None:
    result = _prove(
        "import clauz3\n"
        "@clauz3.requires(lambda value: len(value) == 0)\n"
        "def predicate(value: int) -> bool:\n"
        "    return True\n"
        f"@clauz3.ensures(lambda {parameters}: result == 1 or predicate(result))\n"
        "def check(value: int) -> int:\n"
        "    if False:\n        return 0\n"
        "    return value\n"
        "def main() -> None:\n"
        f"    assert check({live_result}) == {live_result}\n"
    )

    if live_result == 1:
        assert result.proof.conclusion is Conclusion.OK
    else:
        assert result.proof.conclusion is Conclusion.SKIP
        assert "condition predicate may raise TypeError" in str(result.proof.error)


@pytest.mark.parametrize("parameters", ["result", "flag, result"])
@pytest.mark.parametrize("partial", [False, True], ids=["no-return", "partial-return"])
@pytest.mark.parametrize("callee", [False, True], ids=["theorem", "inlined-callee"])
def test_postconditions_require_every_normal_completion_to_have_a_modeled_return(
    parameters: str, partial: bool, callee: bool
) -> None:
    name = "check" if callee else "main"
    annotation = "int" if partial else "None"
    body = "    if flag:\n        return 1\n" if partial else "    assert True\n"
    source = (
        "import clauz3\n"
        f"@clauz3.ensures(lambda {parameters}: True)\n"
        f"def {name}(flag: bool) -> {annotation}:\n{body}"
    )
    if callee:
        source += "def main(flag: bool) -> None:\n    check(flag)\n    assert True\n"

    result = _prove(source)

    assert result.proof.conclusion is Conclusion.SKIP
    diagnostic = (
        "implicit None fallthrough with value returns"
        if callee and partial
        else RETURN_ERROR
    )
    assert diagnostic in str(result.proof.error)


@pytest.mark.parametrize("parameters", ["result", "value, result"])
@pytest.mark.parametrize("assumption", [False, True])
def test_completion_coverage_does_not_use_given_assumptions(
    parameters: str, assumption: bool
) -> None:
    guard = z3.Bool("return_taken")
    function, context = _condition(
        f"@clauz3.ensures(lambda {parameters}: True)", guard=BoolSort(guard)
    )
    context.given.add(BoolSort(guard if assumption else z3.BoolVal(False)))

    with pytest.raises(UnsupportedError, match=RETURN_ERROR):
        eval_contracts(function, context, completed=True)


@pytest.mark.parametrize("parameters", ["result", "value, result"])
def test_trusted_prepass_does_not_require_a_return_before_materialization(
    parameters: str,
) -> None:
    function, context = _condition(
        f"@clauz3.ensures(lambda {parameters}: True)", guard=BoolSort(z3.BoolVal(False))
    )
    context.returns.layer.clear()

    contracts = eval_contracts(function, context, completed=False)

    assert list(contracts.post) == []
    with pytest.raises(UnsupportedError, match=RETURN_ERROR):
        eval_contracts(function, context, completed=True)


@pytest.mark.parametrize("callee", [False, True], ids=["theorem", "inlined-callee"])
def test_postconditions_preserve_exhaustive_arithmetic_return_paths(
    callee: bool,
) -> None:
    name = "check" if callee else "main"
    source = (
        "import clauz3\n"
        "@clauz3.ensures(lambda result: result == 1)\n"
        f"def {name}(value: int) -> int:\n"
        "    if value > 0:\n        return 1\n"
        "    if value == 0:\n        return 1\n"
        "    if value < 0:\n        return 1\n"
    )
    if callee:
        source += "def main(value: int) -> None:\n    assert check(value) == 1\n"

    assert _prove(source).proof.conclusion is Conclusion.OK


@pytest.mark.parametrize("always_raises", [False, True])
def test_modeled_raise_paths_do_not_require_postcondition_returns(
    always_raises: bool,
) -> None:
    predicate = "len(result) == 0" if always_raises else "result == 1"
    returns = "" if always_raises else "    if flag:\n        return 1\n"
    source = (
        "import clauz3\nimport deal\n"
        "@deal.raises(ValueError)\n"
        f"@clauz3.ensures(lambda result: {predicate})\n"
        "def main(flag: bool) -> int:\n"
        "    assert True\n" + returns + "    raise ValueError('stopped')\n"
    )

    assert _prove(source).proof.conclusion is Conclusion.OK
