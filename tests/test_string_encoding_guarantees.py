"""Python strings must keep their exact text across the symbolic boundary."""

import importlib
import re
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import TrustedBoundaryError, execute
from clauz3.prover import ProofResult, prove_text
from clauz3.runner import RunProofError, run_source

RAW_GUARDS = (
    pytest.param(r'r"\u{41}" != "A"', id="raw-escape-is-not-a-character"),
    pytest.param(r'len(r"\u{41}") == 6', id="raw-escape-length"),
)


@pytest.fixture
def recording_layer(tmp_path: Path) -> Path:
    package = tmp_path / f"string_encoding_{uuid4().hex}"
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
        '    return effect("record").empty()\n\n'
        "@contract\n"
        "def budget_by_text_length(text: str) -> ContractSpec:\n"
        '    return effect("record").count() <= len(text)\n'
    )
    return package


def _program(package: Path, body: str, guarantee: str = "silent()") -> str:
    return (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import silent, budget_by_text_length\n\n"
        f"@clauz3.guarantee({guarantee})\n"
        "def main() -> None:\n" + body
    )


def _prove(source: str, package: Path) -> list[ProofResult]:
    return prove_text(
        source,
        source_name="plan.py",
        trusted_roots=[package],
        import_paths=[package.parent],
    )


@pytest.mark.parametrize("guard", RAW_GUARDS)
def test_raw_escape_cannot_hide_a_trusted_effect(
    recording_layer: Path, guard: str
) -> None:
    source = _program(recording_layer, f'    if {guard}:\n        record("one")\n')

    proofs = _prove(source, recording_layer)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize("guard", RAW_GUARDS)
def test_guard_excluding_the_actual_raw_text_remains_provable(
    recording_layer: Path, guard: str
) -> None:
    source = _program(
        recording_layer, f'    if not ({guard}):\n        record("one")\n'
    )

    proofs = _prove(source, recording_layer)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]


def _assert_runner_refuses(
    source: str, package: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        Conclusion.FAIL
    ]
    approve.assert_not_called()
    execute_source.assert_not_called()


@pytest.mark.parametrize("guard", RAW_GUARDS)
def test_raw_escape_violation_stops_before_approval_and_execution(
    recording_layer: Path, monkeypatch: pytest.MonkeyPatch, guard: str
) -> None:
    source = _program(recording_layer, f'    if {guard}:\n        record("one")\n')

    _assert_runner_refuses(source, recording_layer, monkeypatch)


def _assert_runtime_events(source: str, package: Path, expected: list[str]) -> None:
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(
            trusted_roots=[package], import_roots=[package.parent], extra_allowed=()
        ),
        import_roots=[package.parent],
    )

    assert outcome.effects == tuple(
        (f"{package.name}.effects.record", (value,), {}) for value in expected
    )
    effects = importlib.import_module(f"{package.name}.effects")
    recorded = effects.EVENTS
    assert recorded == expected


@pytest.mark.parametrize("guard", RAW_GUARDS)
@pytest.mark.parametrize("inverted", [False, True])
def test_interpreter_preserves_raw_backslash_text(
    recording_layer: Path, guard: str, inverted: bool
) -> None:
    condition = f"not ({guard})" if inverted else guard
    source = _program(recording_layer, f'    if {condition}:\n        record("one")\n')

    _assert_runtime_events(source, recording_layer, [] if inverted else ["one"])


@pytest.mark.parametrize("text", ["☃", "\x00"])
def test_contract_helper_receives_a_character_not_its_z3_escape(
    recording_layer: Path, text: str
) -> None:
    source = _program(
        recording_layer,
        '    record("one")\n    record("two")\n',
        f"budget_by_text_length({text!r})",
    )

    proofs = _prove(source, recording_layer)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)


@pytest.mark.parametrize("text", ["☃", "\x00"])
def test_contract_argument_decoding_violation_stops_before_approval(
    recording_layer: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    source = _program(
        recording_layer,
        '    record("one")\n    record("two")\n',
        f"budget_by_text_length({text!r})",
    )

    _assert_runner_refuses(source, recording_layer, monkeypatch)


@pytest.mark.parametrize("text", ["☃", "\x00"])
def test_interpreter_executes_both_effects_exceeding_one_character_budget(
    recording_layer: Path, text: str
) -> None:
    source = _program(
        recording_layer,
        '    record("one")\n    record("two")\n',
        f"budget_by_text_length({text!r})",
    )

    _assert_runtime_events(source, recording_layer, ["one", "two"])


def test_literal_escape_keeps_its_six_character_contract_budget(
    recording_layer: Path,
) -> None:
    source = _program(
        recording_layer,
        '    record("one")\n    record("two")\n',
        r'budget_by_text_length(r"\u{41}")',
    )

    proofs = _prove(source, recording_layer)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]


def _unicode_regex_program(package: Path) -> str:
    return "import re\n" + _program(
        package, '    if re.match("Ω", "Ω😀"):\n        record("one")\n'
    )


def test_unicode_regex_suffix_cannot_hide_a_trusted_effect(
    recording_layer: Path,
) -> None:
    # Independent Python semantics: the literal prefix matches regardless of
    # the later code point. The interpreter currently refuses Match objects.
    assert re.match("Ω", "Ω😀") is not None

    proofs = _prove(_unicode_regex_program(recording_layer), recording_layer)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)


def test_unicode_regex_violation_stops_before_approval_and_execution(
    recording_layer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_runner_refuses(
        _unicode_regex_program(recording_layer), recording_layer, monkeypatch
    )


def test_interpreter_still_refuses_regex_match_objects(
    recording_layer: Path,
) -> None:
    source = _unicode_regex_program(recording_layer)

    with pytest.raises(TrustedBoundaryError, match="outside the interpreter's domain"):
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(
                trusted_roots=[recording_layer],
                import_roots=[recording_layer.parent],
                extra_allowed=(),
            ),
            import_roots=[recording_layer.parent],
        )

    effects = importlib.import_module(f"{recording_layer.name}.effects")
    recorded = effects.EVENTS
    assert recorded == []
