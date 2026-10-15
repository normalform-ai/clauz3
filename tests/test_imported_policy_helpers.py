"""Imported helpers and re-exports must retain their actual effect policies."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import ProofResult, prove_text
from clauz3.runner import RunProofError, build_approval_request, run_source

SHAPES = ("helper", "nested-helper", "helper-alias", "effect-reexport")


def _program(
    tmp_path: Path,
    shape: str,
    *,
    required: bool = True,
    explicit: bool = False,
) -> tuple[str, Path]:
    package = tmp_path / f"imported_policy_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("from .effects import record as alias\n")
    (package / "effects.py").write_text(
        "import clauz3\n\n"
        "EVENTS = []\n\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        '    return effect("record").empty()\n'
    )
    (package / "helpers.py").write_text(
        "from .effects import record\n\n"
        "def blast() -> None:\n"
        '    record("one")\n'
        '    record("two")\n\n'
        "def quiet() -> None:\n"
        "    pass\n"
    )
    (package / "outer.py").write_text(
        "from .helpers import blast as inner\n\ndef dispatch() -> None:\n    inner()\n"
    )
    field = "required" if required else "recommended"
    (package / "policy.py").write_text(
        "from clauz3.policy import domain_policy\n"
        'POLICY = domain_policy(when_used="record", '
        f'{field}=["silent"], label="recording")\n'
    )
    if shape == "helper":
        entry_import = f"from {package.name}.helpers import blast\n"
        body = "    blast()\n"
    elif shape == "nested-helper":
        entry_import = f"from {package.name}.outer import dispatch\n"
        body = "    dispatch()\n"
    elif shape == "helper-alias":
        entry_import = f"from {package.name}.helpers import blast as emit\n"
        body = "    emit()\n"
    elif shape == "effect-reexport":
        entry_import = f"from {package.name} import alias as emit\n"
        body = '    emit("one")\n    emit("two")\n'
    else:
        assert shape == "quiet"
        entry_import = f"from {package.name}.helpers import quiet\n"
        body = "    quiet()\n"
    decorator = "@clauz3.guarantee(checks.silent())\n" if explicit else ""
    source = (
        "import clauz3\n"
        f"from {package.name} import contracts as checks\n"
        + entry_import
        + f"\n{decorator}def main() -> None:\n"
        + body
    )
    return source, package


def _prove(source: str, package: Path) -> list[ProofResult]:
    return prove_text(
        source,
        source_name="plan.py",
        trusted_roots=[package],
        import_paths=[package.parent],
    )


@pytest.mark.parametrize("shape", SHAPES)
def test_imported_call_enforces_the_actual_effects_required_policy(
    tmp_path: Path, shape: str
) -> None:
    source, package = _program(tmp_path, shape)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize("shape", SHAPES)
def test_explicit_guarantee_already_refutes_the_imported_effects(
    tmp_path: Path, shape: str
) -> None:
    # This control separates missing policy applicability from symbolic
    # execution: the prover already sees both effects through these imports.
    source, package = _program(tmp_path, shape, required=False, explicit=True)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize("shape", SHAPES)
def test_imported_required_violation_stops_before_approval_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    source, package = _program(tmp_path, shape)
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

    assert [proof.proof.conclusion for proof in exc_info.value.proofs] == [
        Conclusion.FAIL
    ]
    approve.assert_not_called()
    execute_source.assert_not_called()


@pytest.mark.parametrize("shape", SHAPES)
def test_approval_coverage_includes_imported_helpers_actual_domain(
    tmp_path: Path, shape: str
) -> None:
    source, package = _program(tmp_path, shape, required=False)
    proofs = _prove(source, package)
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]

    request = build_approval_request(
        source=source,
        source_name="plan.py",
        target="main",
        proofs=proofs,
        trusted_roots=[package],
        import_roots=[tmp_path],
    )

    assert request["coverage"] == [
        {
            "domain": "recording",
            "status": "silent_gap",
            "missing_required": [],
            "missing_recommended": ["silent"],
        }
    ]


@pytest.mark.parametrize("shape", SHAPES)
def test_imported_calls_really_execute_both_recorded_effects(
    tmp_path: Path, shape: str
) -> None:
    source, package = _program(tmp_path, shape)

    execute(
        source,
        source_name="plan.py",
        policy=build_policy(
            trusted_roots=[package], import_roots=[tmp_path], extra_allowed=()
        ),
        import_roots=[tmp_path],
    )

    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["one", "two"]


def test_harmless_imported_helper_remains_provable_without_effect_coverage(
    tmp_path: Path,
) -> None:
    source, package = _program(tmp_path, "quiet")
    proofs = _prove(source, package)
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]

    request = build_approval_request(
        source=source,
        source_name="plan.py",
        target="main",
        proofs=proofs,
        trusted_roots=[package],
        import_roots=[tmp_path],
    )
    assert request["coverage"] == []

    execute(
        source,
        source_name="plan.py",
        policy=build_policy(
            trusted_roots=[package], import_roots=[tmp_path], extra_allowed=()
        ),
        import_roots=[tmp_path],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


def test_custom_target_keeps_its_required_policy_and_coverage_with_iterator_roots(
    tmp_path: Path,
) -> None:
    source, package = _program(tmp_path, "nested-helper")
    source = source.replace("def main()", "def task()")
    source += "\ndef main() -> None:\n    pass\n"

    proofs = prove_text(
        source,
        target="task",
        source_name="plan.py",
        trusted_roots=[package],
        import_paths=[tmp_path],
    )
    assert [(proof.name, proof.proof.conclusion) for proof in proofs] == [
        ("task", Conclusion.FAIL)
    ]
    assert "guarantee" in str(proofs[0].proof)
    assert [proof.proof.conclusion for proof in _prove(source, package)] == [
        Conclusion.OK
    ]

    request = build_approval_request(
        source=source,
        source_name="plan.py",
        target="task",
        proofs=proofs,
        trusted_roots=iter([package]),
        import_roots=iter([tmp_path]),
    )

    assert request["target"] == "task"
    assert request["trusted_roots"] == [str(package)]
    assert request["import_roots"] == [str(tmp_path)]
    assert request["coverage"] == [
        {
            "domain": "recording",
            "status": "required_gap",
            "missing_required": ["silent"],
            "missing_recommended": [],
        }
    ]
