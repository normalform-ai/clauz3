"""Python integer text must not hide an executed trusted effect."""

import importlib
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import Outcome, ProgramError, execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source


@pytest.fixture
def recording_layer(tmp_path: Path) -> Path:
    package = tmp_path / f"integer_string_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
        "@clauz3.trusted\n"
        "def choose_text() -> str:\n"
        "    return '-2'\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect('record').empty()\n"
    )
    return package


def _program(package: Path, guard: str) -> str:
    return (
        "import clauz3\n"
        f"from {package.name}.effects import record, choose_text\n"
        f"from {package.name}.contracts import silent\n"
        "@clauz3.guarantee(silent())\n"
        "def main() -> None:\n"
        f"    if {guard}:\n"
        "        record('one')\n"
    )


def _effects(package: Path) -> ModuleType:
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.__file__ is not None
    assert Path(effects.__file__).resolve() == (package / "effects.py").resolve()
    return effects


def _assert_refused_before_execution(
    source: str,
    package: Path,
    monkeypatch: pytest.MonkeyPatch,
    conclusion: Conclusion,
    diagnostic: str | None = None,
) -> ModuleType:
    proofs = prove_text(source, trusted_roots=[package], import_paths=[package.parent])
    assert [proof.proof.conclusion for proof in proofs] == [conclusion]
    if conclusion == Conclusion.FAIL:
        assert "guarantee" in str(proofs[0].proof)
    if diagnostic is not None:
        assert diagnostic in str(proofs[0].proof)
    effects = _effects(package)
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
    assert [proof.proof.conclusion for proof in exc_info.value.proofs] == [conclusion]
    if diagnostic is not None:
        assert diagnostic in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute_source.assert_not_called()
    assert effects.EVENTS == []
    return effects


def _execute(source: str, package: Path) -> Outcome:
    return execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("-2", -2, id="negative-sign"),
        pytest.param("+2", 2, id="positive-sign"),
        pytest.param(" 2 ", 2, id="surrounding-whitespace"),
        pytest.param("1_0", 10, id="underscore"),
        pytest.param("١", 1, id="unicode-decimal"),
    ],
)
def test_python_integer_syntax_cannot_hide_an_effect(
    recording_layer: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    expected: int,
) -> None:
    assert int(text) == expected
    source = _program(recording_layer, f"int({text!r}) == {expected}")
    effects = _assert_refused_before_execution(
        source, recording_layer, monkeypatch, Conclusion.FAIL
    )

    outcome = _execute(source, recording_layer)

    assert effects.EVENTS == ["one"]
    assert outcome.effects == (
        (f"{recording_layer.name}.effects.record", ("one",), {}),
    )


@pytest.mark.parametrize("inverted", [False, True])
def test_decimal_conversion_keeps_both_branch_controls(
    recording_layer: Path, inverted: bool
) -> None:
    operation = "!=" if inverted else "=="
    source = _program(recording_layer, f"int('12') {operation} 12")

    proofs = prove_text(
        source, trusted_roots=[recording_layer], import_paths=[recording_layer.parent]
    )

    expected = Conclusion.OK if inverted else Conclusion.FAIL
    assert [proof.proof.conclusion for proof in proofs] == [expected]
    effects = _effects(recording_layer)
    assert effects.EVENTS == []
    outcome = _execute(source, recording_layer)
    expected_events = [] if inverted else ["one"]
    recorded = effects.EVENTS
    assert recorded == expected_events
    assert outcome.effects == (
        () if inverted else ((f"{recording_layer.name}.effects.record", ("one",), {}),)
    )


@pytest.mark.parametrize("text", ["bad", r"\u{31}"], ids=["invalid", "raw-escape"])
def test_invalid_integer_text_is_refused_before_approval(
    recording_layer: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    with pytest.raises(ValueError):
        int(text)
    source = _program(recording_layer, f"int({text!r}) == -2")
    effects = _assert_refused_before_execution(
        source,
        recording_layer,
        monkeypatch,
        Conclusion.SKIP,
        "cannot convert string literal to int",
    )

    with pytest.raises(ProgramError) as exc_info:
        _execute(source, recording_layer)

    assert exc_info.value.kind == "ValueError"
    assert effects.EVENTS == []


def test_symbolic_integer_text_is_refused_before_approval(
    recording_layer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _program(recording_layer, "int(choose_text()) == -2")
    effects = _assert_refused_before_execution(
        source,
        recording_layer,
        monkeypatch,
        Conclusion.SKIP,
        "expected a concrete string",
    )

    outcome = _execute(source, recording_layer)

    assert effects.EVENTS == ["one"]
    assert outcome.effects == (
        (f"{recording_layer.name}.effects.choose_text", (), {}),
        (f"{recording_layer.name}.effects.record", ("one",), {}),
    )
