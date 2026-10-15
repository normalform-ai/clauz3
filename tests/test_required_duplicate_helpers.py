"""Required-policy triggering follows the helper the interpreter will call."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.policy import transitive_calls
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source


def _program(
    tmp_path: Path, *, last_effectful: bool, required: bool = True
) -> tuple[str, Path]:
    package = tmp_path / f"duplicate_helper_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n\n"
        "EVENTS: list[str] = []\n\n"
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
    if required:
        (package / "policy.py").write_text(
            "from clauz3.policy import domain_policy\n\n"
            'POLICY = domain_policy(when_used="record", required=["silent"])\n'
        )
    first_body = "pass" if last_effectful else 'record("first")'
    last_body = 'record("last")' if last_effectful else "pass"
    source = (
        f"from {package.name}.effects import record\n\n"
        f"def helper() -> None:\n    {first_body}\n\n"
        f"def helper() -> None:\n    {last_body}\n\n"
        "def main() -> None:\n    helper()\n"
    )
    return source, package


@pytest.mark.parametrize(
    "last_effectful", [False, True], ids=["pure-last", "effect-last"]
)
def test_required_policy_uses_the_last_helper_binding(
    tmp_path: Path, last_effectful: bool
) -> None:
    source, package = _program(tmp_path, last_effectful=last_effectful)
    expected_calls = {"helper", "record"} if last_effectful else {"helper"}
    assert (
        transitive_calls(
            source, "main", import_roots=[tmp_path], trusted_roots=[package]
        )
        == expected_calls
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    expected = Conclusion.FAIL if last_effectful else Conclusion.OK
    assert [result.proof.conclusion for result in results] == [expected]
    if last_effectful:
        assert "guarantee" in str(results[0].proof)


def test_effectful_last_helper_proves_without_the_required_policy(
    tmp_path: Path,
) -> None:
    source, package = _program(tmp_path, last_effectful=True, required=False)
    assert transitive_calls(
        source, "main", import_roots=[tmp_path], trusted_roots=[package]
    ) == {"helper", "record"}
    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_required_helper_violation_stops_before_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, package = _program(tmp_path, last_effectful=True)
    approve = Mock(side_effect=AssertionError("required violation reached approval"))
    execute_source = Mock(
        side_effect=AssertionError("required violation reached execution")
    )
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute_source)

    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="duplicate_helpers.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )
    assert [result.proof.conclusion for result in exc_info.value.proofs] == [
        Conclusion.FAIL
    ]
    assert "guarantee" in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute_source.assert_not_called()


@pytest.mark.parametrize(
    "last_effectful", [False, True], ids=["pure-last", "effect-last"]
)
def test_interpreter_executes_the_same_last_helper_binding(
    tmp_path: Path, last_effectful: bool
) -> None:
    # Deliberately invoke the interpreter directly: the only effect appends to
    # a test-local list, independently confirming which helper body executes.
    source, package = _program(tmp_path, last_effectful=last_effectful)
    outcome = execute(
        source,
        source_name="duplicate_helpers.py",
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    expected_effects: tuple[tuple[str, tuple[str, ...], dict[str, object]], ...] = (
        ((f"{package.name}.effects.record", ("last",), {}),) if last_effectful else ()
    )
    assert outcome.effects == expected_effects
    effects = importlib.import_module(f"{package.name}.effects")
    expected_events = ["last"] if last_effectful else []
    assert expected_events == effects.EVENTS
