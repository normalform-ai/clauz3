"""Boolean expressions select operands and condition their evaluation."""

from textwrap import dedent

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProofResult, prove_text


def _prove(source: str) -> ProofResult:
    results = [result for result in prove_text(dedent(source)) if result.name == "main"]
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize(
    "expression",
    [
        "0 or 'x'",
        "1 and 'x'",
        "'' or 'fallback'",
        "'first' or 'second'",
        "'' and 'second'",
        "0 or 7",
        "2 and 7",
        "2 or 7",
        "0 and 7",
        "0.0 or 1.25",
        "-0.0 or 1.25",
        "-0.0 and 1.25",
        "2.5 and 1.25",
        "True and 1",
        "False and 1",
        "False or 1",
        "True or 1",
        "False or True",
        "False or False",
        "0 or '' or 'last'",
        "1 and 'middle' and 7",
        "1 and '' and 7",
    ],
)
@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_literal_boolean_expression_returns_its_selected_value(
    expression: str, correct: bool
) -> None:
    expected = eval(expression, {"__builtins__": {}}, {})
    comparison = "==" if correct else "!="
    result = _prove(
        f"def main() -> None:\n"
        f"    value = {expression}\n"
        f"    assert value {comparison} {expected!r}\n"
    )
    assert result.proof.conclusion is (Conclusion.OK if correct else Conclusion.FAIL)


@pytest.mark.parametrize(
    ("annotation", "expression", "expected"),
    [
        ("int", "value or 7", "value if value != 0 else 7"),
        ("int", "value and 7", "7 if value != 0 else value"),
        ("str", "value or 'fallback'", "value if len(value) > 0 else 'fallback'"),
        ("str", "value and 'last'", "'last' if len(value) > 0 else value"),
        ("bool", "value or False", "value"),
        ("bool", "value and True", "value"),
        ("float", "value or 1.25", "value if value else 1.25"),
    ],
)
@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_symbolic_same_type_operands_preserve_their_values(
    annotation: str, expression: str, expected: str, correct: bool
) -> None:
    comparison = "==" if correct else "!="
    header = (
        "import clauz3\n@clauz3.requires(lambda value: value == value)\n"
        if annotation == "float"
        else ""
    )
    result = _prove(
        f"{header}def main(value: {annotation}) -> None:\n"
        f"    selected = {expression}\n"
        f"    assert selected {comparison} ({expected})\n"
    )
    assert result.proof.conclusion is (Conclusion.OK if correct else Conclusion.FAIL)


@pytest.mark.parametrize(
    "expression", ["True or [0][2]", "False and [0][2]", "True or missing()"]
)
def test_statically_skipped_operand_is_not_evaluated(expression: str) -> None:
    result = _prove(f"def main() -> None:\n    {expression}\n    assert True\n")
    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize("expression", ["False or [0][2]", "True and [0][2]"])
def test_selected_invalid_operand_is_still_checked(expression: str) -> None:
    result = _prove(f"def main() -> None:\n    {expression}\n    assert True\n")
    assert result.proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize(
    "expression", ["False and impossible()", "True or impossible()"]
)
def test_skipped_callee_assumptions_cannot_erase_a_false_assertion(
    expression: str,
) -> None:
    result = _prove(
        "import clauz3\n"
        "@clauz3.ensures(lambda result: False)\n"
        "def impossible() -> bool:\n    return True\n\n"
        "def main() -> None:\n"
        f"    {expression}\n"
        "    assert False\n"
    )
    assert result.proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize("expression", ["flag and 'x'", "flag or 'x'"])
def test_symbolic_mixed_type_results_are_refused(expression: str) -> None:
    result = _prove(
        f"def main(flag: bool) -> None:\n    selected = {expression}\n    assert True\n"
    )
    assert result.proof.conclusion is Conclusion.SKIP
    assert "boolean operand" in str(result.proof.error)


@pytest.mark.parametrize("expression", ["values or [1]", "values and [1]"])
def test_boolean_selection_does_not_introduce_unmodeled_collection_aliases(
    expression: str,
) -> None:
    result = _prove(
        "def main() -> None:\n"
        "    values = [0]\n"
        f"    selected = {expression}\n"
        "    selected.append(1)\n"
        "    assert len(values) == 1\n"
    )
    assert result.proof.conclusion is Conclusion.SKIP
    assert "boolean operand" in str(result.proof.error)


@pytest.mark.parametrize(
    ("helper", "setup", "expression"),
    [
        ("", "values = [0]", "values.append(1)"),
        ("", "values = [0]\nadd = values.append", "add(1)"),
        (
            "def mutate(values: list[int]) -> bool:\n"
            "    values.append(1)\n    return True\n",
            "values = [0]",
            "mutate(values)",
        ),
        (
            "def mutate(values: dict[str, int]) -> bool:\n"
            "    values['x'] = 1\n    return True\n",
            "values = {'x': 0}",
            "mutate(values)",
        ),
        (
            "def build() -> bool:\n"
            "    local = [0]\n    local.append(1)\n    return len(local) == 2\n",
            "",
            "build()",
        ),
        ("", "values = [0]", "(lambda: values.append(1))()"),
    ],
    ids=["method", "bound-alias", "helper-method", "helper-item", "local", "lambda"],
)
def test_selected_operand_mutations_are_refused_before_discarding_state(
    helper: str, setup: str, expression: str
) -> None:
    source = helper + "\ndef main(flag: bool) -> None:\n"
    source += "".join(f"    {line}\n" for line in setup.splitlines())
    source += f"    flag and {expression}\n    assert True\n"
    result = _prove(source)
    assert result.proof.conclusion is Conclusion.SKIP
    diagnostic = "inside a boolean operand"
    if "add = values.append" in setup:
        diagnostic = "mutating method requires a direct call on a named receiver"
    elif "values.append" in helper:
        diagnostic = "mutating a function parameter or its alias is not supported"
    elif "lambda" in expression:
        diagnostic = "mutating a captured collection is not supported"
    assert diagnostic in str(result.proof.error)


def test_boolean_guard_does_not_restrict_later_local_mutation() -> None:
    result = _prove("""
def main(flag: bool) -> None:
    values = [0]
    flag and True
    values.append(1)
    assert len(values) == 2
""")
    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize("expression", ["{'x': 1}['x'] == 1", "len([] + [1]) == 1"])
def test_pure_container_construction_can_feed_a_scalar_operand(expression: str) -> None:
    result = _prove(
        "def main(flag: bool) -> None:\n"
        f"    selected = flag and {expression}\n"
        "    assert selected == flag\n"
    )
    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize(
    "predicate",
    [
        "True and False",
        "False or False",
        "(result or 'safe') != 'bad'",
        "(result and 'bad') != 'bad'",
    ],
)
def test_postconditions_still_evaluate_after_the_function_returns(
    predicate: str,
) -> None:
    result = _prove(
        "import clauz3\n"
        f"@clauz3.ensures(lambda result: {predicate})\n"
        "def main() -> str:\n    return 'bad'\n"
    )
    assert result.proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize(
    "predicate",
    [
        "len(3) == 0",
        "True and len(3) == 0",
        "False or len(3) == 0",
        "result > 0 and len(3) == 0",
        "result <= 0 or len(3) == 0",
    ],
)
@pytest.mark.parametrize("parameters", ["result", "argument, result"])
def test_postcondition_boolean_operands_refuse_live_exceptions(
    predicate: str, parameters: str
) -> None:
    result = _prove(
        "import clauz3\n"
        f"@clauz3.ensures(lambda {parameters}: {predicate})\n"
        "def main(argument: int) -> int:\n    return 1\n"
    )
    assert result.proof.conclusion is Conclusion.SKIP
    assert "condition predicate may raise TypeError" in str(result.proof)


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [("True or len(3) == 0", True), ("False and len(3) == 0", False)],
)
def test_postcondition_skipped_operands_do_not_raise(
    predicate: str, expected: bool
) -> None:
    result = _prove(
        "import clauz3\n"
        f"@clauz3.ensures(lambda result: ({predicate}) == {expected})\n"
        "def main() -> int:\n    return 1\n"
    )
    assert result.proof.conclusion is Conclusion.OK
