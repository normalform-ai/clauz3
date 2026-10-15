"""Regression tests for how a trusted call's return value is modelled.

A trusted function is never executed by the prover, so its result has to be
invented. Inventing a concrete truthy value decides every branch that reads it
at prove time: one arm is declared unreachable while the runtime still takes
it. The result must instead be a fresh unconstrained value of the annotated
sort, and a return annotation the solver cannot express must fail closed.

A call that really does return ``None`` is the one exception, and even there
the stand-in has to agree with ``bool(None)``, which is False.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_path

FIXTURE = Path(__file__).resolve().parent / "fixtures/trusted_returns"
TRUSTED = FIXTURE / "rtrusted"


def _conclusions(case_name: str) -> list[Conclusion]:
    results = prove_path(
        FIXTURE / "cases" / case_name,
        trusted_roots=[TRUSTED],
        import_roots=[FIXTURE],
    )
    return [result.proof.conclusion for result in results]


@pytest.mark.parametrize(
    "case_name",
    [
        "bool_branch_fail.py",
        "str_branch_fail.py",
        "float_branch_fail.py",
    ],
)
def test_branch_on_trusted_return_stays_reachable(case_name: str) -> None:
    """The arm guarded by a trusted result must remain live for the solver."""
    assert _conclusions(case_name) == [Conclusion.FAIL]


@pytest.mark.parametrize(
    "case_name",
    [
        "none_return_branch_fail.py",
        "helper_none_return_branch_fail.py",
    ],
)
def test_branch_on_none_return_stays_reachable(case_name: str) -> None:
    """`not <call returning None>` is True at runtime, so its arm is live."""
    assert _conclusions(case_name) == [Conclusion.FAIL]


@pytest.mark.parametrize(
    "case_name",
    [
        "bool_branch_pass.py",
        "post_pins_str_pass.py",
    ],
)
def test_symbolic_trusted_return_stays_provable(case_name: str) -> None:
    """A symbolic result still proves what its postconditions pin down."""
    assert _conclusions(case_name) == [Conclusion.OK]


def test_unmodellable_return_annotation_fails_closed() -> None:
    """A return type with no sort is refused, not assumed truthy."""
    assert _conclusions("opaque_return_fail.py") == [Conclusion.SKIP]
