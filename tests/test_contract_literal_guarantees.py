"""Contract builders must receive the Python meaning of literal arguments."""

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
from clauz3.spec import EmptySpec, NoGuaranteesSpec

_FORBIDDEN = [
    pytest.param("conditional(False)", "conditional", False, id="false"),
    pytest.param("int_conditional(0)", "int_conditional", 0, id="zero-integer"),
    pytest.param("float_conditional(0.0)", "float_conditional", 0.0, id="zero-float"),
    pytest.param("conditional(allow=False)", "conditional", False, id="keyword-false"),
    pytest.param(
        "int_conditional(value=0)", "int_conditional", 0, id="keyword-zero-integer"
    ),
    pytest.param(
        "float_conditional(value=0.0)",
        "float_conditional",
        0.0,
        id="keyword-zero-float",
    ),
]


def _program(tmp_path: Path, clause: str) -> tuple[str, Path]:
    package = tmp_path / f"contract_literal_{uuid4().hex}"
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
        "from clauz3.spec import ContractSpec, contract, effect, no_guarantees\n"
        "@contract\n"
        "def conditional(allow: bool = True) -> ContractSpec:\n"
        "    if not allow:\n"
        "        return effect('record').empty()\n"
        "    return no_guarantees()\n"
        "@contract\n"
        "def int_conditional(value: int = 1) -> ContractSpec:\n"
        "    if not value:\n"
        "        return effect('record').empty()\n"
        "    return no_guarantees()\n"
        "@contract\n"
        "def float_conditional(value: float = 1.0) -> ContractSpec:\n"
        "    if not value:\n"
        "        return effect('record').empty()\n"
        "    return no_guarantees()\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect('record').empty()\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import conditional, int_conditional, "
        "float_conditional, silent\n"
        f"@clauz3.guarantee({clause})\n"
        "def main() -> None:\n"
        "    record('bad')\n"
    )
    return source, package


def _check_runtime(source: str, package: Path) -> None:
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["bad"]
    assert outcome.effects == ((f"{package.name}.effects.record", ("bad",), {}),)


@pytest.mark.parametrize(("clause", "helper", "argument"), _FORBIDDEN)
def test_falsy_contract_literal_cannot_remove_an_effect_guarantee(
    tmp_path: Path, clause: str, helper: str, argument: bool | int | float
) -> None:
    source, package = _program(tmp_path, clause)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]
    assert "guarantee" in str(results[0].proof)
    contracts = importlib.import_module(f"{package.name}.contracts")
    assert isinstance(getattr(contracts, helper)(argument), EmptySpec)
    _check_runtime(source, package)


@pytest.mark.parametrize(
    ("clause", "helper", "arguments"),
    [
        ("conditional(True)", "conditional", (True,)),
        ("int_conditional(2)", "int_conditional", (2,)),
        ("float_conditional(0.5)", "float_conditional", (0.5,)),
        ("conditional()", "conditional", ()),
        ("int_conditional()", "int_conditional", ()),
        ("float_conditional()", "float_conditional", ()),
    ],
    ids=[
        "true",
        "nonzero-integer",
        "nonzero-float",
        "bool-default",
        "int-default",
        "float-default",
    ],
)
def test_permissive_literal_and_default_contract_controls(
    tmp_path: Path,
    clause: str,
    helper: str,
    arguments: tuple[bool | int | float, ...],
) -> None:
    source, package = _program(tmp_path, clause)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]
    contracts = importlib.import_module(f"{package.name}.contracts")
    assert isinstance(getattr(contracts, helper)(*arguments), NoGuaranteesSpec)
    _check_runtime(source, package)


def test_unconditional_silence_control_observes_the_same_effect(
    tmp_path: Path,
) -> None:
    source, package = _program(tmp_path, "silent()")

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]
    _check_runtime(source, package)


@pytest.mark.parametrize(("clause", "helper", "argument"), _FORBIDDEN)
def test_falsy_contract_literal_blocks_approval_and_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clause: str,
    helper: str,
    argument: bool | int | float,
) -> None:
    source, package = _program(tmp_path, clause)
    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    execute_source = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute_source)

    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )

    assert [result.proof.conclusion for result in exc_info.value.proofs] == [
        Conclusion.FAIL
    ]
    approve.assert_not_called()
    execute_source.assert_not_called()
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []
    contracts = importlib.import_module(f"{package.name}.contracts")
    assert isinstance(getattr(contracts, helper)(argument), EmptySpec)
