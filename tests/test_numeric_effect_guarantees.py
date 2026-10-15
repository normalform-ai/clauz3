"""Python numeric results must not hide real trusted effects."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source

_CONDITIONS = [
    "int(-1.5) == -1",
    "math.trunc(-1.5) == -1",
    "5 % 2.9 == 2.1",
    "1.0 // 0.1 == 9.0",
    "9007199254740993 / 1 == 9007199254740992.0",
]
_IDS = [
    "int-truncation",
    "math-truncation",
    "mixed-modulo",
    "float-floor",
    "division-rounding",
]


def _program(tmp_path: Path, condition: str) -> tuple[str, Path]:
    package = tmp_path / f"numeric_effects_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect('record').empty()\n"
    )
    source = (
        "import clauz3\n"
        "import math\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import silent\n"
        "@clauz3.guarantee(silent())\n"
        "def main() -> None:\n"
        f"    if {condition}:\n"
        "        record('one')\n"
    )
    return source, package


@pytest.mark.parametrize("condition", _CONDITIONS, ids=_IDS)
@pytest.mark.parametrize("inverse", [False, True], ids=["live", "inverse"])
def test_python_numeric_result_controls_the_actual_effect(
    tmp_path: Path, condition: str, inverse: bool
) -> None:
    source, package = _program(tmp_path, f"not ({condition})" if inverse else condition)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [
        Conclusion.OK if inverse else Conclusion.FAIL
    ]
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    events = [] if inverse else ["one"]
    assert events == effects.EVENTS
    assert outcome.effects == tuple(
        (f"{package.name}.effects.record", (value,), {}) for value in events
    )


@pytest.mark.parametrize("condition", _CONDITIONS[:2], ids=_IDS[:2])
def test_negative_float_conversion_violation_blocks_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, condition: str
) -> None:
    source, package = _program(tmp_path, condition)
    approval = Mock(side_effect=AssertionError("approval must not be reached"))
    execution = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approval)
    monkeypatch.setattr("clauz3.runner._execute_source", execution)

    with pytest.raises(RunProofError) as error:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )

    assert [result.proof.conclusion for result in error.value.proofs] == [
        Conclusion.FAIL
    ]
    assert "guarantee" in str(error.value.proofs[0].proof)
    approval.assert_not_called()
    execution.assert_not_called()
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []
