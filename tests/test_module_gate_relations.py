"""Module-gate imports must preserve trusted relation field validation."""

from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3 import interp, runner
from clauz3._vendor.deal_solver import Conclusion
from clauz3.approvals.authority import ApprovedLayer
from clauz3.approvals.decisions import Decision
from clauz3.approvals.policy import ApprovalPolicy, Rule, evaluate_policy
from clauz3.prover import ProverConfigError, prove_path


def _program(tmp_path: Path, *, invalid_field: bool) -> tuple[Path, Path, str]:
    # A fresh module is essential: the regression is importing a relation for
    # the first time outside known_effects, before the ordinary proof loader.
    package = tmp_path / f"module_gate_relations_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    raise AssertionError('proof must not execute the effect')\n"
    )
    field = "missing" if invalid_field else "value"
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        f"PREBUILT = effect('record').all(lambda event: event.{field} == 'x')\n"
        "@contract\n"
        "def only_x() -> ContractSpec:\n"
        "    return PREBUILT\n"
    )
    # Empty facts exposed the invalid schema being accepted vacuously. The
    # valid control makes a real symbolic call to exercise its predicate.
    body = "pass" if invalid_field else "record('x')"
    source = (
        "import clauz3\n"
        f"import {package.name}.contracts as pol\n"
        f"from {package.name}.effects import record\n"
        "@clauz3.guarantee(pol.only_x())\n"
        "def main() -> None:\n"
        f"    {body}\n"
    )
    entry = tmp_path / "plan.py"
    entry.write_text(source)
    return entry, package, source


def test_prove_path_refuses_prebuilt_relation_with_unknown_field(
    tmp_path: Path,
) -> None:
    entry, package, _ = _program(tmp_path, invalid_field=True)

    with pytest.raises(ProverConfigError, match="reads 'missing'"):
        prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])


@pytest.mark.parametrize("with_clause", [False, True], ids=["empty", "nonempty"])
def test_approval_refuses_prebuilt_relation_with_unknown_field(
    tmp_path: Path, with_clause: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    _, package, source = _program(tmp_path, invalid_field=True)
    policy = ApprovalPolicy(
        imports=(("pol", f"{package.name}.contracts"),),
        rules=(
            Rule(
                name="allow",
                decision=Decision.AUTO_APPROVED,
                clauses=("pol.only_x()",) if with_clause else (),
            ),
        ),
    )

    decision = evaluate_policy(
        policy,
        {
            "program": source,
            "trusted_roots": [str(package)],
            "import_roots": [str(tmp_path)],
        },
        authorized_layers=(
            ApprovedLayer(trusted_roots=(package,), import_roots=(tmp_path,)),
        ),
    )

    assert decision is None
    assert "reads 'missing'" in capsys.readouterr().err


def test_prove_path_preserves_valid_prebuilt_relation(tmp_path: Path) -> None:
    entry, package, _ = _program(tmp_path, invalid_field=False)

    results = prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_run_refuses_unknown_relation_field_before_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry, package, source = _program(tmp_path, invalid_field=True)
    approve = Mock(side_effect=AssertionError("approval must not be requested"))
    execute = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr(runner, "submit_approval_request", approve)
    monkeypatch.setattr(interp, "execute", execute)

    # A trusted schema configuration fault uses the runner's configuration
    # family; it must not become a runtime program error.
    with pytest.raises(runner.RunConfigError, match="reads 'missing'"):
        runner.run_source(
            source,
            source_name=str(entry),
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )

    approve.assert_not_called()
    execute.assert_not_called()


def test_run_valid_prebuilt_relation_reaches_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry, package, source = _program(tmp_path, invalid_field=False)
    approve = Mock(side_effect=RuntimeError("mock approval reached"))
    execute = Mock(side_effect=AssertionError("execution must follow approval"))
    monkeypatch.setattr(runner, "submit_approval_request", approve)
    monkeypatch.setattr(interp, "execute", execute)

    with pytest.raises(RuntimeError, match="mock approval reached"):
        runner.run_source(
            source,
            source_name=str(entry),
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )

    approve.assert_called_once()
    execute.assert_not_called()


@pytest.mark.parametrize("with_clause", [False, True], ids=["empty", "nonempty"])
def test_approval_preserves_valid_prebuilt_relation(
    tmp_path: Path, with_clause: bool
) -> None:
    _, package, source = _program(tmp_path, invalid_field=False)
    policy = ApprovalPolicy(
        imports=(("pol", f"{package.name}.contracts"),),
        rules=(
            Rule(
                name="allow",
                decision=Decision.AUTO_APPROVED,
                clauses=("pol.only_x()",) if with_clause else (),
            ),
        ),
    )

    decision = evaluate_policy(
        policy,
        {
            "program": source,
            "trusted_roots": [str(package)],
            "import_roots": [str(tmp_path)],
        },
        authorized_layers=(
            ApprovedLayer(trusted_roots=(package,), import_roots=(tmp_path,)),
        ),
    )

    assert decision is not None
    assert decision.decision == "auto_approved"
