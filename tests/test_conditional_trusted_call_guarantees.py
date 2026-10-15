"""Conditional expressions cannot invent trusted calls skipped by Python."""

import importlib
from pathlib import Path
from textwrap import indent
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source

# Body, minimum promised calls, actual runtime events. Runtime witnesses have
# their own tests so a baseline proof failure cannot prevent their execution.
CONDITIONAL_CALLS = {
    "or-rhs": ("unused = True or audit('x')", 1, []),
    "and-rhs": ("unused = False and audit('x')", 1, []),
    "ternary-then": ("unused = audit('x') if False else 0", 1, []),
    "ternary-else": ("unused = 0 if True else audit('x')", 1, []),
    "both-arms": ("unused = audit('a') if True else audit('b')", 2, ["a"]),
    "comparison-tail": ("unused = 2 < 1 < audit('x')", 1, []),
    "nested-helper-or": ("unused = True or relay('x')", 1, []),
    "nested-helper-arms": ("unused = relay('a') if True else relay('b')", 2, ["a"]),
    "nested-helper-tail": ("unused = 2 < 1 < relay('x')", 1, []),
    "after-earlier-effect": (
        "audit('before')\nunused = True or audit('x')",
        2,
        ["before"],
    ),
    # This call really runs and must now satisfy a one-call guarantee.
    "live-rhs": ("unused = False or audit('x')", 1, ["x"]),
}

UNCONDITIONAL_CALLS = {
    "direct": "audit('x')",
    "first-or-operand": "unused = audit('x') or 1",
    "first-and-operand": "unused = audit('x') and 0",
    "ternary-test": "unused = 1 if audit('x') == 0 else 2",
    "first-comparison-left": "unused = audit('x') < 1 < 2",
    "first-comparison-right": "unused = -1 < audit('x') < 1",
    "first-operand-helper": "unused = relay('x') or 1",
    "after-nested-guards": "unused = False or (1 if True else 2)\naudit('x')",
}


def _program(tmp_path: Path, body: str, minimum: int) -> tuple[str, Path]:
    package = tmp_path / f"conditional_calls_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "@clauz3.ensures(lambda result: result == 0)\n"
        "def audit(value: str) -> int:\n"
        "    EVENTS.append(value)\n"
        "    return 0\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def at_least(count: int) -> ContractSpec:\n"
        "    return effect('audit').count() >= count\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import audit\n"
        f"from {package.name}.contracts import at_least\n"
        "def nested(value: str) -> int:\n"
        "    return audit(value)\n"
        "def relay(value: str) -> int:\n"
        "    return nested(value)\n"
        f"@clauz3.guarantee(at_least({minimum}))\n"
        "def main() -> None:\n" + indent(body, "    ") + "\n"
    )
    return source, package


@pytest.mark.parametrize("case", CONDITIONAL_CALLS)
def test_conditional_trusted_calls_preserve_counts_before_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    body, minimum, events = CONDITIONAL_CALLS[case]
    source, package = _program(tmp_path, body, minimum)
    proofs = prove_text(
        source, target="main", trusted_roots=[package], import_paths=[tmp_path]
    )
    expected = Conclusion.OK if len(events) >= minimum else Conclusion.FAIL
    assert [result.proof.conclusion for result in proofs] == [expected]
    effects = importlib.import_module(package.name + ".effects")
    assert effects.EVENTS == []
    if expected is Conclusion.OK:
        return
    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    executor = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", executor)
    with pytest.raises(RunProofError) as exc:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )
    assert [result.proof.conclusion for result in exc.value.proofs] == [Conclusion.FAIL]
    approve.assert_not_called()
    executor.assert_not_called()
    assert effects.EVENTS == []


@pytest.mark.parametrize("case", CONDITIONAL_CALLS)
def test_conditional_call_runtime_trace_is_independent_of_proof(
    tmp_path: Path, case: str
) -> None:
    body, minimum, expected = CONDITIONAL_CALLS[case]
    source, package = _program(tmp_path, body, minimum)
    _assert_runtime(source, package, expected)


@pytest.mark.parametrize("position", UNCONDITIONAL_CALLS)
@pytest.mark.parametrize("minimum", [1, 2], ids=["satisfied", "violated"])
def test_unconditional_positions_preserve_exact_effect_guarantees(
    tmp_path: Path, position: str, minimum: int
) -> None:
    source, package = _program(tmp_path, UNCONDITIONAL_CALLS[position], minimum)
    proofs = prove_text(
        source, target="main", trusted_roots=[package], import_paths=[tmp_path]
    )
    assert [result.proof.conclusion for result in proofs] == [
        Conclusion.OK if minimum == 1 else Conclusion.FAIL
    ]
    _assert_runtime(source, package, ["x"])


def _assert_runtime(source: str, package: Path, expected: list[str]) -> None:
    result = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    effects = importlib.import_module(package.name + ".effects")
    assert effects.__file__ is not None
    assert Path(effects.__file__).resolve() == (package / "effects.py").resolve()
    assert expected == effects.EVENTS
    assert result.effects == tuple(
        (package.name + ".effects.audit", (event,), {}) for event in expected
    )
