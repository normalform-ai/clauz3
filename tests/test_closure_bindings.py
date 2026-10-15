"""Nested helpers must resolve the lexical bindings present when they run."""

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


def _program(tmp_path: Path, case: str) -> tuple[str, Path]:
    package = tmp_path / f"closure_bindings_{uuid4().hex}"
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
        "from clauz3.spec import ContractSpec, contract, effect\n"
        f"from {package.name}.effects import record\n\n"
        "Record = effect(record)\n\n"
        "@contract\n"
        "def no_records() -> ContractSpec:\n"
        "    return Record.empty()\n"
    )
    header = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import no_records\n\n"
        "def quiet(value: str) -> None:\n    pass\n\n"
    )
    if case in {
        "rebound",
        "no-rebind",
        "quiet-closure",
        "local-import",
        "alias-chain",
    }:
        initial = "quiet" if case == "quiet-closure" else "record"
        binding = f"    action = {initial}\n"
        if case == "local-import":
            binding = f"    from {package.name}.effects import record as action\n"
        elif case == "alias-chain":
            binding = "    original = record\n    action = original\n"
        body = (
            "@clauz3.guarantee(no_records())\n"
            "def main() -> None:\n"
            f"{binding}"
            "    def helper() -> None:\n"
            '        action("one")\n'
            '        action("two")\n'
            "    helper()\n"
        )
        if case == "rebound":
            body += "    action = quiet\n"
        elif case == "alias-chain":
            body += "    original = quiet\n"
    elif case == "scalar-closure":
        body = (
            "@clauz3.guarantee(no_records())\n"
            "def main() -> None:\n"
            '    value = "one"\n'
            "    def helper() -> str:\n"
            "        return value\n"
            '    assert helper() == "one"\n'
        )
    else:
        assert case in {"module-record", "module-quiet"}
        global_binding = (
            f"from {package.name}.effects import record as action\n\n"
            if case == "module-record"
            else "def action(value: str) -> None:\n    pass\n\n"
        )
        local_action = "quiet" if case == "module-record" else "record"
        body = (
            global_binding + 'def helper() -> None:\n    action("one")\n\n'
            "@clauz3.guarantee(no_records())\n"
            "def main() -> None:\n"
            f"    action = {local_action}\n"
            "    helper()\n"
        )
    return header + body, package


def _prove(source: str, package: Path) -> list[ProofResult]:
    return prove_text(
        source,
        source_name="plan.py",
        trusted_roots=[package],
        import_paths=[package.parent],
    )


@pytest.mark.parametrize("case", ["rebound", "alias-chain"])
def test_rebound_closure_is_refused_before_its_guarantee_can_pass(
    tmp_path: Path,
    case: str,
) -> None:
    source, package = _program(tmp_path, case)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "closure binding" in str(proofs[0].proof)


def test_rebound_closure_stops_run_before_approval_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, package = _program(tmp_path, "rebound")
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
        Conclusion.SKIP
    ]
    assert "closure binding" in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute_source.assert_not_called()


@pytest.mark.parametrize("case", ["no-rebind", "local-import"])
def test_stable_effect_closure_refutes_the_explicit_guarantee(
    tmp_path: Path,
    case: str,
) -> None:
    source, package = _program(tmp_path, case)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)


def test_stable_quiet_closure_remains_provable(tmp_path: Path) -> None:
    source, package = _program(tmp_path, "quiet-closure")

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    assert "guarantee" in str(proofs[0].proof)


def test_unsupported_scalar_capture_still_refuses_a_proof(tmp_path: Path) -> None:
    source, package = _program(tmp_path, "scalar-closure")

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "unsupported closure binding value" in str(proofs[0].proof)


@pytest.mark.parametrize(
    ("case", "expected"),
    [("module-record", Conclusion.FAIL), ("module-quiet", Conclusion.OK)],
)
def test_module_helper_does_not_inherit_the_callers_same_named_local(
    tmp_path: Path, case: str, expected: Conclusion
) -> None:
    source, package = _program(tmp_path, case)

    proofs = _prove(source, package)

    assert [proof.proof.conclusion for proof in proofs] == [expected]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("rebound", ["one", "two"]),
        ("no-rebind", ["one", "two"]),
        ("local-import", ["one", "two"]),
        ("alias-chain", ["one", "two"]),
        ("quiet-closure", []),
        ("scalar-closure", []),
        ("module-record", ["one"]),
        ("module-quiet", []),
    ],
)
def test_runtime_uses_the_actual_lexical_binding(
    tmp_path: Path, case: str, expected: list[str]
) -> None:
    source, package = _program(tmp_path, case)

    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(
            trusted_roots=[package], import_roots=[tmp_path], extra_allowed=()
        ),
        import_roots=[tmp_path],
    )

    assert outcome.effects == tuple(
        (f"{package.name}.effects.record", (value,), {}) for value in expected
    )
    effects = importlib.import_module(f"{package.name}.effects")
    recorded = effects.EVENTS
    assert recorded == expected
