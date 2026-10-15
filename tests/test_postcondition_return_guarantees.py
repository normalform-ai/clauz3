"""Unmodeled completion paths cannot silently skip callee postconditions."""

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

DIAGNOSTIC = "postcondition requires a modeled return on every normal completion path"


def _program(
    tmp_path: Path,
    *,
    kind: str,
    parameters: str,
    minimum: int = 2,
) -> tuple[str, Path]:
    package = tmp_path / f"post_returns_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    condition = f"@clauz3.ensures(lambda {parameters}: len(result) == 0)\n"
    record = (
        "@clauz3.trusted\n"
        "def record(argument: str) -> None:\n"
        "    EVENTS.append(argument)\n"
    )
    if kind == "trusted-none":
        definitions = condition + record
        imported = "record"
        body = "    record('first')\n    record('second')\n"
    else:
        functions = {
            "implicit-none": "def check(argument: bool) -> None:\n    pass\n",
            "partial-return": (
                "def check(argument: bool) -> str:\n"
                "    if argument:\n        return ''\n"
            ),
            "ordinary-total": (
                "def check(argument: bool) -> str:\n"
                "    if argument:\n        return ''\n"
                "    return ''\n"
            ),
            "trusted-value": (
                "@clauz3.trusted\ndef check(argument: bool) -> str:\n    return ''\n"
            ),
        }
        definitions = record + condition + functions[kind]
        imported = "record, check"
        body = "    record('first')\n    check(False)\n    record('second')\n"
    (package / "effects.py").write_text(
        "import clauz3\nEVENTS: list[str] = []\n" + definitions
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def enough_records() -> ContractSpec:\n"
        f"    return effect('record').count() >= {minimum}\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import {imported}\n"
        f"from {package.name}.contracts import enough_records\n"
        "@clauz3.guarantee(enough_records())\n"
        "def main() -> None:\n" + body
    )
    return source, package


@pytest.mark.parametrize("parameters", ["result", "argument, result"])
@pytest.mark.parametrize("kind", ["trusted-none", "implicit-none", "partial-return"])
def test_missing_postcondition_return_stops_before_approval_or_any_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, parameters: str
) -> None:
    diagnostic = (
        "implicit None fallthrough with value returns"
        if kind == "partial-return"
        else DIAGNOSTIC
    )
    source, package = _program(tmp_path, kind=kind, parameters=parameters)
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert diagnostic in str(proofs[0].proof)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.__file__ is not None
    assert Path(effects.__file__).resolve() == (package / "effects.py").resolve()
    assert effects.EVENTS == []

    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    executor = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", executor)
    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )
    assert [proof.proof.conclusion for proof in exc_info.value.proofs] == [
        Conclusion.SKIP
    ]
    assert diagnostic in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    executor.assert_not_called()
    assert effects.EVENTS == []

    with pytest.raises(TypeError, match="NoneType.*has no len"):
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
            import_roots=[tmp_path],
        )
    assert effects.EVENTS == ["first"]


@pytest.mark.parametrize("parameters", ["result", "argument, result"])
@pytest.mark.parametrize("kind", ["ordinary-total", "trusted-value"])
def test_modeled_postcondition_returns_allow_the_complete_record_sequence(
    tmp_path: Path, kind: str, parameters: str
) -> None:
    source, package = _program(tmp_path, kind=kind, parameters=parameters)
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]

    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["first", "second"]
    records = tuple(
        event
        for event in outcome.effects
        if event[0] == f"{package.name}.effects.record"
    )
    assert records == (
        (f"{package.name}.effects.record", ("first",), {}),
        (f"{package.name}.effects.record", ("second",), {}),
    )


def test_modeled_returns_do_not_prove_a_stronger_effect_guarantee(
    tmp_path: Path,
) -> None:
    source, package = _program(
        tmp_path, kind="ordinary-total", parameters="result", minimum=3
    )
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)
