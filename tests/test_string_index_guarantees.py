"""A failed string search cannot justify a completed effect guarantee."""

import importlib
from pathlib import Path
from textwrap import indent
from types import ModuleType
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import ProgramError, execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source

DIAGNOSTIC = "str.index search may raise ValueError"


def _program(
    tmp_path: Path,
    body: str,
    *,
    trusted: bool = False,
    expected_index: int = 1,
    minimum: int = 2,
) -> tuple[str, Path]:
    package = tmp_path / f"string_index_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    boundary = "@clauz3.trusted\n" if trusted else ""
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
        f"@clauz3.requires(lambda value: 'abc'.index(value) == {expected_index})\n"
        f"{boundary}def check(value: str) -> str:\n"
        "    return value\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def enough_records() -> ContractSpec:\n"
        f"    return effect('record').count() >= {minimum}\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record, check\n"
        f"from {package.name}.contracts import enough_records\n"
        "@clauz3.guarantee(enough_records())\n"
        "def main() -> None:\n"
        "    record('first')\n" + indent(body, "    ") + "\n    record('second')\n"
    )
    return source, package


def _assert_refused(
    source: str, package: Path, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    proofs = prove_text(source, trusted_roots=[package], import_paths=[package.parent])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert DIAGNOSTIC in str(proofs[0].proof)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.__file__ is not None
    assert Path(effects.__file__).resolve() == (package / "effects.py").resolve()
    assert effects.EVENTS == []

    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    execute_source = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", execute_source)
    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[package.parent],
            approval_service_url="http://approval.invalid",
        )
    assert [proof.proof.conclusion for proof in exc_info.value.proofs] == [
        Conclusion.SKIP
    ]
    assert DIAGNOSTIC in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute_source.assert_not_called()
    assert effects.EVENTS == []
    return effects


@pytest.mark.parametrize("trusted", [False, True], ids=["inlined", "trusted"])
def test_missing_index_in_a_precondition_is_refused_before_any_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trusted: bool
) -> None:
    source, package = _program(
        tmp_path, "check('missing')", trusted=trusted, expected_index=-1
    )
    effects = _assert_refused(source, package, monkeypatch)

    with pytest.raises(ValueError, match="substring not found"):
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
            import_roots=[tmp_path],
        )
    assert effects.EVENTS == ["first"]


@pytest.mark.parametrize(
    "expression",
    ["'abc'.index('missing')", "'abc'.index('a', 4)", "'abc'.index('', 4)"],
    ids=["missing", "start-beyond-end", "empty-needle-beyond-end"],
)
def test_missing_direct_index_is_refused_before_any_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expression: str
) -> None:
    source, package = _program(tmp_path, f"assert {expression} == -1")
    effects = _assert_refused(source, package, monkeypatch)

    with pytest.raises(ProgramError) as exc_info:
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
            import_roots=[tmp_path],
        )
    assert exc_info.value.kind == "ValueError"
    assert "substring not found" in str(exc_info.value)
    assert effects.EVENTS == ["first"]


@pytest.mark.parametrize("trusted", [False, True], ids=["inlined", "trusted"])
def test_successful_precondition_search_completes_both_records(
    tmp_path: Path, trusted: bool
) -> None:
    source, package = _program(tmp_path, "check('b')", trusted=trusted)
    _assert_successful(source, package)


@pytest.mark.parametrize(
    "body",
    [
        "assert 'abc'.index('b', -2) == 1",
        "assert 'abc'.index('', -1) == 2",
        "assert 'abc'.index('', 3) == 3",
    ],
    ids=["negative-start", "empty-needle-negative-start", "empty-needle-at-end"],
)
def test_successful_direct_search_completes_both_records(
    tmp_path: Path, body: str
) -> None:
    source, package = _program(tmp_path, body)
    _assert_successful(source, package)


def _assert_successful(source: str, package: Path) -> None:
    proofs = prove_text(source, trusted_roots=[package], import_paths=[package.parent])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
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


def test_successful_search_does_not_prove_a_stronger_record_guarantee(
    tmp_path: Path,
) -> None:
    source, package = _program(tmp_path, "check('b')", minimum=3)
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)
