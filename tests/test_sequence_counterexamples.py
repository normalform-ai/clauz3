"""A bounded search can refute an obligation, never prove one."""

from typing import Any

import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._counterexamples import sequence_counterexample
from clauz3._vendor.deal_solver._domains import value_domain
from clauz3._vendor.deal_solver._proxies import ListSort, StrSort
from clauz3._vendor.deal_solver._strings import string_sort, string_val, string_value
from clauz3._vendor.deal_solver._theorem import Theorem
from clauz3.prover import prove_text


@pytest.mark.parametrize("custom_context", [False, True])
def test_search_retains_domain_and_postcondition_constraints(
    custom_context: bool,
) -> None:
    ctx = z3.Context() if custom_context else z3.main_ctx()
    sequence = z3.Const("returned", z3.SeqSort(string_sort(ctx)))
    proxy = ListSort(sequence, subtypes=(StrSort(string_val("", ctx)).factory,))
    constraints = [
        value_domain(proxy),
        z3.Length(sequence) == 2,
        z3.Length(sequence[0]) == 2,
        z3.BV2Int(sequence[0][0], is_signed=False) > 0x2FFFF,
        sequence[1] == string_val("ab", ctx),
    ]
    model = sequence_counterexample(constraints, timeout_ms=2000)
    assert model is not None
    first = string_value(model.eval(sequence[0]))
    assert len(first) == 2
    assert 0x2FFFF < ord(first[0]) <= 0x10FFFF
    assert string_value(model.eval(sequence[1])) == "ab"
    assert model.eval(z3.Length(sequence)).as_long() == 2


class UndecidedSolver(z3.Solver):  # type: ignore[misc]
    def check(self, *assumptions: Any) -> z3.CheckSatResult:
        return z3.unknown

    def reason_unknown(self) -> str:
        return "test undecided obligation"


@pytest.mark.parametrize("satisfiable", [False, True], ids=["unsat", "outside-bound"])
def test_exhausted_search_never_proves_an_obligation(satisfiable: bool) -> None:
    sequence = z3.Const("larger_list", z3.SeqSort(string_sort()))
    solver = UndecidedSolver()
    solver.add(z3.Length(sequence) == 5)
    if not satisfiable:
        solver.add(z3.Length(sequence) == 0)

    proof = Theorem._prove(solver, "guarantee")

    assert proof.conclusion == Conclusion.SKIP
    assert proof.example is None
    assert str(proof.error) == "test undecided obligation"


def test_expired_search_does_not_manufacture_a_counterexample() -> None:
    sequence = z3.Const("list", z3.SeqSort(string_sort()))
    assert sequence_counterexample([z3.Length(sequence) > 0], timeout_ms=0) is None


@pytest.mark.parametrize(
    "annotation", ["list[str]", "tuple[str, ...]", "list[list[str]]"]
)
def test_unconstrained_nested_inputs_have_high_unicode_counterexamples(
    annotation: str,
) -> None:
    setup = "    if len(values) > 0:\n        text = values[0]\n"
    if annotation == "list[list[str]]":
        setup = (
            "    if len(values) > 0:\n"
            "        if len(values[0]) > 0:\n"
            "            text = values[0][0]\n"
        )
    indent = "            " if annotation == "list[list[str]]" else "        "
    source = (
        f"def main(values: {annotation}) -> None:\n"
        + setup
        + f"{indent}if len(text) == 1:\n"
        + f"{indent}    assert ord(text) <= 0x2FFFF\n"
    )
    (result,) = prove_text(source)
    assert result.proof.conclusion == Conclusion.FAIL, str(result.proof)
    assert result.proof.example is not None
    values = dict(result.proof.example)["values"]
    assert isinstance(values, list)
    text = values[0][0] if annotation == "list[list[str]]" else values[0]
    assert len(text) == 1
    assert 0x2FFFF < ord(text) <= 0x10FFFF
