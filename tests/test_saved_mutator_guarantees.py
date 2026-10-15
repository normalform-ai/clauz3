"""Saving a mutator must not hide the effects enabled by its real mutation."""

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

DIAGNOSTIC = "mutating method requires a direct call on a named receiver"


def _program(
    tmp_path: Path, *, method: str, saved: bool, maximum: int = 1
) -> tuple[str, Path]:
    package = tmp_path / f"saved_mutator_{uuid4().hex}"
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
        "def within_budget() -> ContractSpec:\n"
        f"    return effect('record').count() <= {maximum}\n"
    )
    if method == "extend":
        setup = "targets = []\n"
        argument = "['a', 'b']"
        effects = "if len(targets) == 2:\n    record('a')\n    record('b')\n"
    else:
        assert method == "clear"
        setup = "targets = ['a', 'b']\n"
        argument = ""
        effects = "if len(targets) == 0:\n    record('a')\n    record('b')\n"
    mutation = (
        f"change = targets.{method}\nchange({argument})\n"
        if saved
        else f"targets.{method}({argument})\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import within_budget\n"
        "@clauz3.guarantee(within_budget())\n"
        "def main() -> None:\n" + indent(setup + mutation + effects, "    ")
    )
    return source, package


@pytest.mark.parametrize("method", ["extend", "clear"])
def test_saved_mutator_cannot_hide_records_or_reach_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    source, package = _program(tmp_path, method=method, saved=True)
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert DIAGNOSTIC in str(proofs[0].proof)
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
    assert DIAGNOSTIC in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    executor.assert_not_called()
    assert effects.EVENTS == []

    _assert_actual_records(source, package)


@pytest.mark.parametrize("method", ["extend", "clear"])
@pytest.mark.parametrize(
    "maximum", [2, 1], ids=["sufficient-budget", "too-small-budget"]
)
def test_direct_named_mutator_preserves_effect_guarantee_results(
    tmp_path: Path, method: str, maximum: int
) -> None:
    source, package = _program(tmp_path, method=method, saved=False, maximum=maximum)
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    expected = Conclusion.OK if maximum == 2 else Conclusion.FAIL
    assert [proof.proof.conclusion for proof in proofs] == [expected]
    _assert_actual_records(source, package)


def _assert_actual_records(source: str, package: Path) -> None:
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["a", "b"]
    assert outcome.effects == (
        (f"{package.name}.effects.record", ("a",), {}),
        (f"{package.name}.effects.record", ("b",), {}),
    )
