"""Contradictory assumptions must never count as a successful proof."""

from textwrap import dedent

import pytest
import z3

from clauz3._vendor import deal_solver
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._theorem import _has_quantifier
from clauz3.prover import AgentTheorem, prove_text


@pytest.mark.parametrize(
    "source",
    [
        """
        import deal

        @deal.pre(lambda: False)
        def main() -> None:
            assert False
        """,
        """
        import deal

        @deal.post(lambda result: result != result)
        def helper() -> int:
            return 1

        def main() -> None:
            helper()
            assert False
        """,
        """
        import deal

        @deal.pre(lambda: False)
        def main() -> None:
            pass
        """,
        """
        import deal

        @deal.pre(lambda value: value > 0)
        @deal.pre(lambda value: value < 0)
        def main(value: int) -> None:
            assert True
        """,
    ],
    ids=[
        "target-precondition",
        "helper-postcondition",
        "no-obligations",
        "combined-target-preconditions",
    ],
)
def test_contradictory_assumptions_are_vacuous(source: str) -> None:
    result = prove_text(dedent(source))[0]

    assert result.proof.conclusion == deal_solver.Conclusion.VACUOUS
    assert result.proof.description == "assumptions are contradictory"
    assert not result.ok


@pytest.mark.parametrize(
    "source",
    [
        """
        import deal

        @deal.pre(lambda value: value > 0)
        def main(value: int) -> None:
            assert value > 0
        """,
        """
        import deal

        @deal.post(lambda result: result > 0)
        def helper() -> int:
            return 1

        def main() -> None:
            result = helper()
            assert result > 0
        """,
    ],
    ids=["target-precondition", "helper-postcondition"],
)
def test_satisfiable_assumptions_still_prove(source: str) -> None:
    result = prove_text(dedent(source))[0]

    assert result.proof.conclusion == deal_solver.Conclusion.OK
    assert result.ok


def test_quantified_assumptions_do_not_block_the_proof() -> None:
    """A set operation must not cost the whole proof.

    ``len()`` on a set converts it to a list, and that conversion asserts a
    ``ForAll`` witness axiom into ``given``. The axiom is satisfiable by
    construction, but checking it standalone is undecidable within the
    timeout, so treating ``unknown`` as a failure would take every program
    containing a set from ``proved!`` to a timeout ``SKIP``.
    """
    result = prove_text(
        dedent("""
        def main() -> None:
            items = {1, 2, 3}
            assert len(items) >= 0
        """)
    )[0]

    assert result.proof.conclusion == deal_solver.Conclusion.OK
    assert result.ok


def test_guarantee_cannot_add_its_own_contradictory_assumption() -> None:
    """A guarantee's own evaluation must not make the theorem vacuous.

    Driven through ``AgentTheorem`` rather than ``prove_text``: ``bogus() < 0``
    is no longer a guarantee an entry file may write (see
    ``tests/test_guarantee_grammar.py``), but the freezing of the antecedent it
    exercises lives in the theorem, below that gate, and still has to hold for
    the contract calls that are allowed.
    """

    theorem = next(
        theorem
        for theorem in AgentTheorem.from_text(
            dedent("""
            import clauz3
            import deal

            @deal.post(lambda result: result != result)
            def bogus() -> int:
                return 1

            @clauz3.guarantee(bogus() < 0)
            def main() -> None:
                pass
            """)
        )
        if theorem.name == "main"
    )

    proof = theorem.prove()

    assert proof.conclusion == deal_solver.Conclusion.FAIL
    assert proof.description == "guarantee"


def test_constraints_initialize_a_fresh_theorem() -> None:
    theorem = next(
        AgentTheorem.from_text("""
        def main() -> None:
            assert False
        """)
    )

    constraints = list(theorem.constraints)

    assert [constraint.description for constraint in constraints] == ["assertion"]


def test_quantifier_scan_visits_shared_dag_nodes_once() -> None:
    term: z3.BoolRef = z3.Bool("shared")
    for _ in range(100):
        term = z3.And(term, term)

    assert not _has_quantifier(term)


def test_quantifier_scan_fails_closed_at_its_work_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "clauz3._vendor.deal_solver._theorem.MAX_QUANTIFIER_SCAN_NODES",
        1,
    )
    term = z3.And(z3.Bool("left"), z3.Bool("right"))

    with pytest.raises(UnsupportedError, match="too large to inspect"):
        _has_quantifier(term)


def test_undecided_assumptions_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    theorem = next(
        AgentTheorem.from_text(
            dedent("""
            import deal

            @deal.pre(lambda value: value > 0)
            def main(value: int) -> None:
                assert True
            """)
        )
    )
    # Materialize the assumptions before replacing only the solver used by the
    # satisfiability check. The proof must stop at that check rather than let a
    # trivially true obligation turn an unknown assumption set into ``OK``.
    assert theorem._assumption_terms

    timeout_values: list[int] = []

    class UnknownSolver:
        def __init__(self, *, ctx: z3.Context | None = None) -> None:
            pass

        def set(self, name: str, value: int) -> None:
            assert name == "timeout"
            timeout_values.append(value)

        def add(self, *args: object) -> None:
            pass

        def check(self) -> z3.CheckSatResult:
            return z3.unknown

        def reason_unknown(self) -> str:
            return "test timeout"

    monkeypatch.setattr(z3, "Solver", UnknownSolver)

    proof = theorem.prove()

    assert proof.conclusion is deal_solver.Conclusion.SKIP
    assert proof.description == "failed to check assumptions"
    assert str(proof.error) == "test timeout"
    assert timeout_values == [1_000]
