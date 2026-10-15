"""Truth tests require explicit models of Python emptiness."""

from collections.abc import Callable

import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._proxies._row import (
    QueryResultSort,
    z3_datatype_for_row,
)
from clauz3.prover import prove_text
from clauz3.row import Row


@pytest.mark.parametrize(
    ("value", "truth"),
    [("set()", False), ("{1}.difference({1})", False), ("{1}", True)],
)
@pytest.mark.parametrize("correct", [False, True])
def test_set_bool_not_and_conditional_match_python(
    value: str, truth: bool, correct: bool
) -> None:
    source = (
        "def main() -> None:\n"
        f"    values = {value}\n"
        f"    assert bool(values) == {truth!r}\n"
        f"    assert (not values) == {not truth!r}\n"
        f"    assert (1 if values else 0) == {int(truth if correct else not truth)}\n"
    )
    assert [p.proof.conclusion for p in prove_text(source)] == [
        Conclusion.OK if correct else Conclusion.FAIL
    ]


def test_symbolic_set_truth_is_equivalent_to_nonemptiness() -> None:
    source = (
        "def main(values: set[int]) -> None:\n"
        "    assert bool(values) == (values != set())\n"
        "    if values:\n        assert values != set()\n"
        "    else:\n        assert values == set()\n"
    )
    assert [p.proof.conclusion for p in prove_text(source)] == [Conclusion.OK]


def test_query_truth_depends_on_symbolic_length(
    ctx_factory: Callable[[], Context],
) -> None:
    class Item(Row):
        value: int

    ctx = ctx_factory()
    row_sort = z3_datatype_for_row(Item)
    length = z3.Int("result_length")
    query = QueryResultSort(
        row_schema=Item,
        array_expr=z3.Array("result_items", z3.IntSort(), row_sort),
        length_expr=length,
        source=("read_rows", {}),
    )
    solver = z3.Solver()
    solver.add(query.m_bool(ctx=ctx).expr != (length > 0))
    assert solver.check() == z3.unsat
    for count in (0, 1):
        solver = z3.Solver()
        solver.add(length == count, query.m_bool(ctx=ctx).expr == bool(count))
        assert solver.check() == z3.sat


@pytest.mark.parametrize("test", ["helper", "not helper", "bool(helper)"])
def test_unmodeled_truth_conversion_refuses_before_selecting_a_branch(
    test: str,
) -> None:
    source = (
        "def helper() -> int:\n    return 1\n"
        "def main() -> None:\n"
        f"    value = 1 if {test} else 2\n"
        "    assert value == 1\n"
    )
    proofs = prove_text(source, target="main")
    assert [p.proof.conclusion for p in proofs] == [Conclusion.SKIP]
    assert "unsupported truth conversion for function" in str(proofs[0].proof)


@pytest.mark.parametrize("operator", ["and", "or"])
def test_collection_boolean_operands_remain_refused(operator: str) -> None:
    source = f"def main(values: set[int]) -> None:\n    value = values {operator} 1\n"
    proofs = prove_text(source)
    assert [p.proof.conclusion for p in proofs] == [Conclusion.SKIP]
    assert "unsupported boolean operand type" in str(proofs[0].proof)


@pytest.mark.parametrize(("value", "result"), [(True, 1), (12, 12), (-2.5, -2)])
@pytest.mark.parametrize("correct", [False, True])
def test_math_trunc_keeps_supported_numeric_arguments(
    value: bool | int | float, result: int, correct: bool
) -> None:
    source = (
        "import math\ndef main() -> None:\n"
        f"    assert math.trunc({value!r}) == {result if correct else result + 1}\n"
    )
    assert [p.proof.conclusion for p in prove_text(source)] == [
        Conclusion.OK if correct else Conclusion.FAIL
    ]
