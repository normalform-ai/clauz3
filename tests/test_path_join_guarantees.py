"""Registered joins must preserve the path guards that control trusted effects."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import ProofResult, prove_text
from clauz3.runner import RunProofError, run_source

PATH_CASES = [
    pytest.param("posixpath", ("a", "/b"), "/b", id="posix-absolute-component"),
    pytest.param("posixpath", ("", "b"), "b", id="posix-empty-first-component"),
    pytest.param("posixpath", ("a/", "b"), "a/b", id="posix-existing-separator"),
    pytest.param("ntpath", ("a", "b"), "a\\b", id="windows-separator"),
]


def _program(
    tmp_path: Path,
    module: str,
    parts: tuple[str, ...],
    expected: str,
    *,
    matching_guard: bool = True,
) -> tuple[str, Path]:
    package = tmp_path / f"path_join_guard_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
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
    arguments = ", ".join(repr(part) for part in parts)
    operator = "==" if matching_guard else "!="
    source = (
        "import clauz3\n"
        f"import {module}\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import silent\n\n"
        "@clauz3.guarantee(silent())\n"
        "def main() -> None:\n"
        f"    joined = {module}.join({arguments})\n"
        f"    if joined {operator} {expected!r}:\n"
        '        record("one")\n'
    )
    return source, package


def _prove(source: str, package: Path, module: str) -> list[ProofResult]:
    return prove_text(
        source,
        source_name="plan.py",
        trusted_roots=[package],
        import_paths=[package.parent],
        extra_allowed_imports=[module],
    )


@pytest.mark.parametrize(("module", "parts", "expected"), PATH_CASES)
def test_real_path_join_result_cannot_hide_a_trusted_effect(
    tmp_path: Path, module: str, parts: tuple[str, ...], expected: str
) -> None:
    source, package = _program(tmp_path, module, parts, expected)

    proofs = _prove(source, package, module)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize(("module", "parts", "expected"), PATH_CASES)
def test_guard_excluding_the_real_path_join_result_remains_provable(
    tmp_path: Path, module: str, parts: tuple[str, ...], expected: str
) -> None:
    source, package = _program(tmp_path, module, parts, expected, matching_guard=False)

    proofs = _prove(source, package, module)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize(("module", "parts", "expected"), PATH_CASES)
def test_path_join_violation_stops_before_approval_or_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module: str,
    parts: tuple[str, ...],
    expected: str,
) -> None:
    source, package = _program(tmp_path, module, parts, expected)
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
            extra_allowed_imports=[module],
            approval_service_url="http://approval.invalid",
        )

    assert [proof.proof.conclusion for proof in exc_info.value.proofs] == [
        Conclusion.FAIL
    ]
    approve.assert_not_called()
    execute_source.assert_not_called()


@pytest.mark.parametrize(("module", "parts", "expected"), PATH_CASES)
@pytest.mark.parametrize("matching_guard", [True, False])
def test_interpreter_uses_the_actual_platform_path_join_result(
    tmp_path: Path,
    module: str,
    parts: tuple[str, ...],
    expected: str,
    matching_guard: bool,
) -> None:
    source, package = _program(
        tmp_path, module, parts, expected, matching_guard=matching_guard
    )

    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(
            trusted_roots=[package],
            import_roots=[tmp_path],
            extra_allowed=[module],
        ),
        import_roots=[tmp_path],
    )

    expected_effects: tuple[tuple[str, tuple[str, ...], dict[str, object]], ...] = (
        ((f"{package.name}.effects.record", ("one",), {}),) if matching_guard else ()
    )
    assert outcome.effects == expected_effects
    effects = importlib.import_module(f"{package.name}.effects")
    recorded = effects.EVENTS
    assert recorded == (["one"] if matching_guard else [])
