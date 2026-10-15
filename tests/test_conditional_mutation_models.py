"""Guarded expression evaluation must not apply conditionally skipped writes."""

from textwrap import indent

import astroid
import pytest

from clauz3._vendor.deal_solver import Conclusion, UnsupportedError
from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._context._trace import (
    BOOLEAN_OPERAND,
    GUARDED_EXPRESSION,
)
from clauz3._vendor.deal_solver._eval_stmt import eval_stmt
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text

ERROR = "mutating methods inside a boolean operand are not supported"


def _source(body: str, helpers: str = "") -> str:
    return helpers + "def main() -> None:\n" + indent(body, "    ") + "\n"


@pytest.mark.parametrize(
    "expression",
    [
        "values.append(1) if False else 0",
        "0 if True else values.append(1)",
        "False and values.append(1)",
        "True or values.clear()",
        "values.append(1) if True else 0",
        "True and values.append(1)",
    ],
    ids=["then", "else", "and", "or", "selected-then", "selected-and"],
)
def test_conditional_method_mutation_is_skipped_or_refused(expression: str) -> None:
    proofs = prove_text(_source(f"values = [0]\nunused = {expression}\nassert True"))
    live = expression in {
        "values.append(1) if True else 0",
        "True and values.append(1)",
    }
    assert [item.proof.conclusion for item in proofs] == [
        Conclusion.SKIP if live else Conclusion.OK
    ]
    if live:
        guard = "boolean operand" if " and " in expression else "guarded expression"
        assert f"mutating methods inside a {guard}" in str(proofs[0].proof)


@pytest.mark.parametrize(
    "expression",
    [
        "change() if False else 0",
        "0 if True else change()",
        "False and change()",
        "True or change()",
        "2 < 1 < change()",
    ],
    ids=["then", "else", "and", "or", "comparison-tail"],
)
@pytest.mark.parametrize("item_assignment", [False, True], ids=["method", "item-write"])
def test_dead_conditional_helpers_do_not_apply_writes(
    expression: str, item_assignment: bool
) -> None:
    helper_body = (
        "values = {'x': 0}\nvalues['x'] = 1\nreturn values['x']\n"
        if item_assignment
        else "values = [0]\nvalues.append(1)\nreturn len(values)\n"
    )
    helpers = "def change() -> int:\n" + indent(helper_body, "    ")
    proofs = prove_text(
        _source(f"unused = {expression}\nassert True", helpers), target="main"
    )
    assert [item.proof.conclusion for item in proofs] == [Conclusion.OK]
    assert (
        execute(
            _source(f"unused = {expression}\nassert True", helpers),
            source_name="plan.py",
            policy=build_policy(),
        ).effects
        == ()
    )


@pytest.mark.parametrize(
    "expression",
    [
        "values.pop('x') and False",
        "values.pop('x') or False",
        "0 < values.pop('x') < 2",
        "1 if values.pop('x') else 0",
        "0 if False else 1\nremoved = values.pop('x')",
    ],
    ids=[
        "first-and",
        "first-or",
        "first-comparison",
        "conditional-test",
        "guard-cleanup",
    ],
)
def test_unconditional_expression_writes_remain_supported(expression: str) -> None:
    source = _source(
        f"values = {{'x': 1}}\nunused = {expression}\nassert 'x' not in values"
    )
    proofs = prove_text(source)
    expected = (
        Conclusion.SKIP if expression == "0 < values.pop('x') < 2" else Conclusion.OK
    )
    assert [item.proof.conclusion for item in proofs] == [expected]
    if expected is Conclusion.SKIP:
        assert "mutating methods inside a guarded expression" in str(proofs[0].proof)
    assert execute(source, source_name="plan.py", policy=build_policy()).effects == ()


def test_conditional_guard_is_removed_after_refusal() -> None:
    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    nodes = astroid.parse(
        "values = [0]\nTrue and values.append(1)\nvalues.append(1)"
    ).body
    eval_stmt(nodes[0], ctx=ctx)
    with pytest.raises(UnsupportedError, match=ERROR):
        eval_stmt(nodes[1], ctx=ctx)
    assert BOOLEAN_OPERAND not in ctx.trace
    assert GUARDED_EXPRESSION not in ctx.trace
    eval_stmt(nodes[2], ctx=ctx)
