"""Selected expressions preserve values and cannot silently discard mutations."""

from collections.abc import Callable
from textwrap import indent

import astroid
import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion, UnsupportedError
from clauz3._vendor.deal_solver._context import Context, _trace
from clauz3._vendor.deal_solver._eval_expr import eval_expr
from clauz3.prover import prove_text

MUTATION_ERROR = "mutating methods inside a guarded expression are not supported"
SUBSCRIPT_ERROR = "subscript assignment inside a guarded expression is not supported"


def _source(body: str, helpers: str = "") -> str:
    return (
        "import clauz3\nimport deal\n"
        + helpers
        + "def main(flag: bool) -> None:\n"
        + indent(body, "    ")
    )


def _conclusion(source: str, expected: Conclusion, diagnostic: str = "") -> None:
    results = prove_text(source, target="main")
    assert [result.proof.conclusion for result in results] == [expected]
    if diagnostic:
        assert diagnostic in str(results[0].proof)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("'selected' if True else 0", "'selected'"),
        ("0 if False else 'selected'", "'selected'"),
        ("False if True else 5", "False"),
        ("-0.0 if True else 'unused'", "0.0"),
        ("7 if True else len(3)", "7"),
        ("len(3) if False else 7", "7"),
    ],
)
@pytest.mark.parametrize("correct", [True, False])
def test_constant_ternary_keeps_selected_scalar_and_skips_other_arm(
    expression: str, expected: str, correct: bool
) -> None:
    comparison = "==" if correct else "!="
    _conclusion(
        _source(f"assert ({expression}) {comparison} {expected}"),
        Conclusion.OK if correct else Conclusion.FAIL,
    )


@pytest.mark.parametrize(
    ("left", "right"), [(1, 2), ("a", "b"), (True, False), (1.5, -2.5)]
)
def test_symbolic_ternary_keeps_each_selected_scalar(
    left: object, right: object
) -> None:
    _conclusion(
        _source(
            f"value = {left!r} if flag else {right!r}\n"
            f"if flag:\n    assert value == {left!r}\n"
            f"else:\n    assert value == {right!r}"
        ),
        Conclusion.OK,
    )


@pytest.mark.parametrize(
    "expression", ["True if flag else 0", "1 if flag else 2.0", "'x' if flag else 1"]
)
def test_symbolic_ternary_does_not_coerce_scalar_types(expression: str) -> None:
    _conclusion(
        _source(f"value = {expression}\nassert True"),
        Conclusion.SKIP,
        "cannot merge conditional expression arms of different types",
    )


@pytest.mark.parametrize("condition", ["True", "flag"])
@pytest.mark.parametrize("value", ["[1]", "(1,)", "{'a': 1}", "{1}", "helper"])
def test_ternary_cannot_create_mutable_or_callable_result_aliases(
    condition: str, value: str
) -> None:
    _conclusion(
        _source(
            f"selected = {value} if {condition} else {value}\nassert True",
            "def helper() -> int:\n    return 1\n",
        ),
        Conclusion.SKIP,
        "unsupported conditional expression result type",
    )


@pytest.mark.parametrize(
    "expression",
    [
        "data.pop('k') if True else 0",
        "0 if False else data.pop('k')",
        "data.pop('k') if flag else 0",
        "0 < 1 < data.pop('k')",
        "data.pop('k') > 1 > 0",
        "0 < data.pop('k') < 3",
        "invoke(data) if flag else 0",
        "0 < 1 < invoke(data)",
        "callback(data) if flag else 0",
        "0 < 1 < callback(data)",
        "saved('k') if flag else 0",
        "0 < 1 < saved('k')",
        "data.pop.__call__('k') if flag else 0",
    ],
)
def test_mutation_dispatch_is_refused_in_every_evaluated_guarded_step(
    expression: str,
) -> None:
    saved = "saved = data.pop\n" if "saved(" in expression else ""
    diagnostic = MUTATION_ERROR
    if saved or ".__call__" in expression:
        diagnostic = "mutating method requires a direct call on a named receiver"
    elif "invoke(" in expression or "callback(" in expression:
        diagnostic = "mutating a function parameter or its alias is not supported"
    _conclusion(
        _source(
            "data = {'k': 2}\n" + saved + "callback = lambda value: value.pop('k')\n"
            f"unused = {expression}\nassert True",
            "def invoke(value: dict[str, int]) -> int:\n    return value.pop('k')\n",
        ),
        Conclusion.SKIP,
        diagnostic,
    )


@pytest.mark.parametrize(
    "expression", ["invoke(data) if flag else 0", "0 < 1 < invoke(data)"]
)
def test_helper_subscript_writes_are_refused(expression: str) -> None:
    _conclusion(
        _source(
            f"data = {{'k': 2}}\nunused = {expression}\nassert True",
            "def invoke(value: dict[str, int]) -> int:\n"
            "    value['k'] = 9\n    return 2\n",
        ),
        Conclusion.SKIP,
        SUBSCRIPT_ERROR,
    )


@pytest.mark.parametrize(
    "expression",
    [
        "0 if True else data.pop('k')",
        "data.pop('k') if False else 0",
        "1 > 2 > data.pop('k')",
    ],
)
def test_skipped_mutations_do_not_refuse_and_direct_mutation_still_works(
    expression: str,
) -> None:
    _conclusion(
        _source(
            f"data = {{'k': 2}}\nunused = {expression}\n"
            "assert data.pop('k') == 2\nassert 'k' not in data"
        ),
        Conclusion.OK,
    )


def test_guard_does_not_treat_pure_collection_operations_as_mutation() -> None:
    _conclusion(
        _source(
            "value = len([] + [1]) if flag else helper()\nassert value == 1",
            "def helper() -> int:\n    data = {'k': 1}\n    return data['k']\n",
        ),
        Conclusion.OK,
    )


def test_guard_is_removed_even_when_a_mutating_method_refuses(
    ctx_factory: Callable[[], Context],
) -> None:
    ctx = ctx_factory()
    data = eval_expr(node=astroid.extract_node("{'k': 2}"), ctx=ctx)
    ctx.ownership.bind("data", data, ctx=ctx)
    ctx.scope.set("data", data)
    with pytest.raises(UnsupportedError, match=MUTATION_ERROR):
        eval_expr(node=astroid.extract_node("data.pop('k') if True else 0"), ctx=ctx)
    assert _trace.GUARDED_EXPRESSION not in ctx.trace
    result = eval_expr(node=astroid.extract_node("data.pop('k')"), ctx=ctx)
    assert z3.simplify(result.expr).as_long() == 2


@pytest.mark.parametrize(
    "body", ["assert not (1 < 0 < 'invalid')", "assert not (1 < 0 < len(3))"]
)
def test_dead_comparison_suffix_includes_the_comparison_operation(body: str) -> None:
    _conclusion(_source(body), Conclusion.OK)


def test_symbolic_invalid_comparison_cannot_excuse_the_opposite_path() -> None:
    source = (
        "import deal\n@deal.raises(TypeError)\n"
        "def main(value: int) -> None:\n"
        "    unused = 0 < value in 3\n"
        "    assert value > 0\n"
    )
    _conclusion(source, Conclusion.FAIL)
