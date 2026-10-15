"""Quantified value domains participate in detecting impossible assumptions."""

import time
from contextlib import nullcontext

import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._theorem import _has_quantifier
from clauz3.diagnostics import proof
from clauz3.prover import AgentTheorem, prove_text


@pytest.mark.parametrize("decorator", ["clauz3.requires", "deal.pre"])
def test_unicode_domain_contradiction_is_vacuous(decorator: str) -> None:
    source = (
        "import clauz3\nimport deal\n"
        f"@{decorator}(lambda value: len(value) == 1 and ord(value) > 0x10FFFF)\n"
        "def main(value: str) -> None:\n"
        "    assert False\n"
    )
    result = prove_text(source)[0]

    assert result.proof.conclusion is Conclusion.VACUOUS
    assert result.proof.description == "assumptions are contradictory"
    assert not result.ok


@pytest.mark.parametrize("assertion", ["True", "False"])
def test_satisfiable_unicode_domain_keeps_live_obligations(assertion: str) -> None:
    result = prove_text(
        "import clauz3\n"
        "@clauz3.requires(lambda value: len(value) == 1 and ord(value) == 0x10FFFF)\n"
        "def main(value: str) -> None:\n"
        f"    assert {assertion}\n"
    )[0]

    assert result.proof.conclusion is (
        Conclusion.OK if assertion == "True" else Conclusion.FAIL
    )


def test_quantified_only_contradiction_is_vacuous() -> None:
    theorem = next(AgentTheorem.from_text("def main() -> None:\n    assert False\n"))
    index = z3.Int("impossible_index")
    # Exercise the public theorem's frozen-assumption boundary without a
    # quantifier-free conjunct: the fast pass has nothing to inspect here.
    theorem.__dict__["_assumption_terms"] = [z3.ForAll(index, index != index)]

    proof = theorem.prove()

    assert proof.conclusion is Conclusion.VACUOUS


@pytest.mark.parametrize("monitored", [False, True])
def test_undecided_quantified_pass_does_not_claim_vacuity(
    monkeypatch: pytest.MonkeyPatch,
    monitored: bool,
) -> None:
    theorem = next(
        AgentTheorem.from_text(
            "import deal\n@deal.pre(lambda value: len(value) == 1)\n"
            "def main(value: str) -> None:\n    assert True\n"
        )
    )
    assert any(_has_quantifier(term) for term in theorem._assumption_terms)
    original_solver = z3.Solver
    timeouts: list[int] = []

    class UnknownQuantifiedSolver:
        def __init__(self, *, ctx: z3.Context | None = None) -> None:
            self.solver = original_solver(ctx=ctx)
            self.quantified = False

        def set(self, name: str, value: int) -> None:
            timeouts.append(value)
            self.solver.set(name, value)

        def add(self, *terms: z3.ExprRef) -> None:
            self.quantified = any(_has_quantifier(term) for term in terms)
            self.solver.add(*terms)

        def check(self) -> z3.CheckSatResult:
            if self.quantified:
                return z3.unknown
            return self.solver.check()

    monkeypatch.setattr(z3, "Solver", UnknownQuantifiedSolver)

    with (
        proof.monitoring(proof.Monitor(time.monotonic() + 0.5, lambda _: None))
        if monitored
        else nullcontext()
    ):
        assert theorem._check_assumptions() is None
    if monitored:
        assert len(timeouts) == 2
        assert all(0 < timeout <= 500 for timeout in timeouts)
    else:
        assert timeouts == [1_000]


@pytest.mark.parametrize("annotation", ["str", "list[str]", "dict[str, str]"])
def test_generated_domains_alone_need_no_vacuity_query(
    monkeypatch: pytest.MonkeyPatch, annotation: str
) -> None:
    theorem = next(
        AgentTheorem.from_text(
            f"def main(value: {annotation}) -> None:\n    assert True\n"
        )
    )
    assert any(_has_quantifier(term) for term in theorem._assumption_terms)

    def unexpected_query(*, ctx: z3.Context | None = None) -> z3.Solver:
        raise AssertionError("unconstrained Python domains already have a witness")

    monkeypatch.setattr(z3, "Solver", unexpected_query)
    assert theorem._check_assumptions() is None


def test_domain_of_a_derived_expression_is_not_assumed_satisfiable() -> None:
    from clauz3._vendor.deal_solver._domains import add_value_domain
    from clauz3._vendor.deal_solver._proxies import StrSort

    theorem = next(AgentTheorem.from_text("def main() -> None:\n    assert True\n"))
    invalid = StrSort(z3.Unit(z3.BitVecVal(0x110000, 21)))
    add_value_domain(invalid, ctx=theorem._context)
    assert theorem.prove().conclusion is Conclusion.VACUOUS
