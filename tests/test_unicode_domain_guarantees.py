"""Full Python Unicode must remain reachable in native effect guarantees."""

import importlib
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock
from uuid import uuid4

import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._model import Model
from clauz3._vendor.deal_solver._strings import string_sort, string_val
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source


@pytest.fixture
def recording_layer(tmp_path: Path) -> Path:
    package = tmp_path / f"unicode_domain_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "class Result(clauz3.Row):\n"
        "    text: str\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
        "@clauz3.trusted\n"
        "@clauz3.ensures(lambda result: len(result) == 1)\n"
        "def read_value() -> str:\n"
        "    return chr(0x30000)\n"
        "@clauz3.trusted\n"
        "def read_values() -> list[str]:\n"
        "    return [chr(0x30000)]\n"
        "@clauz3.trusted\n"
        '@clauz3.ensures(lambda result: result == ["\\U00030000"])\n'
        "def read_known_values() -> list[str]:\n"
        "    return [chr(0x30000)]\n"
        "@clauz3.trusted\n"
        "def read_rows() -> list[Result]:\n"
        "    return [Result(text=chr(0x30000))]\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect('record').empty()\n"
        "@contract\n"
        "def twice() -> ContractSpec:\n"
        "    return effect('record').count() >= 2\n"
        "@contract\n"
        "def budget(text: str) -> ContractSpec:\n"
        "    return effect('record').count() <= len(text)\n"
    )
    return package


def _program(package: Path, body: str, guarantee: str = "silent()") -> str:
    return (
        "import clauz3\n"
        f"from {package.name}.effects import (\n"
        "    record, read_value, read_values, read_known_values, read_rows,\n)\n"
        f"from {package.name}.contracts import silent, twice, budget\n"
        f"@clauz3.guarantee({guarantee})\n"
        "def main() -> None:\n" + body
    )


def _effects(package: Path) -> ModuleType:
    module = importlib.import_module(f"{package.name}.effects")
    assert module.__file__ is not None
    assert Path(module.__file__).resolve() == (package / "effects.py").resolve()
    return module


def _assert_refused_before_approval(
    source: str,
    package: Path,
    monkeypatch: pytest.MonkeyPatch,
    conclusion: Conclusion = Conclusion.FAIL,
) -> None:
    approval = Mock(side_effect=AssertionError("approval must not be reached"))
    execution = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approval)
    monkeypatch.setattr("clauz3.runner._execute_source", execution)
    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[package.parent],
            approval_service_url="http://approval.invalid",
        )
    assert len(exc_info.value.proofs) == 1
    proof = exc_info.value.proofs[0].proof
    assert proof.conclusion == conclusion
    assert proof.error is None
    approval.assert_not_called()
    execution.assert_not_called()
    assert _effects(package).EVENTS == []


def _assert_runtime_events(source: str, package: Path, expected: list[str]) -> None:
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    assert expected == _effects(package).EVENTS
    records = [
        args[0]
        for name, args, _kwargs in outcome.effects
        if name == f"{package.name}.effects.record"
    ]
    assert records == expected


@pytest.mark.parametrize("upper", [0xFFFF, 0x2FFFF, 0x10FFFF])
@pytest.mark.parametrize("container", ["scalar", "list", "known_list", "query"])
def test_trusted_high_character_cannot_hide_an_effect(
    recording_layer: Path,
    monkeypatch: pytest.MonkeyPatch,
    upper: int,
    container: str,
) -> None:
    if container == "scalar":
        prefix = "    value = read_value()\n"
        indentation = "    "
    else:
        call = {
            "list": "read_values()",
            "known_list": "read_known_values()",
            "query": "read_rows()",
        }[container]
        item = "values[0].text" if container == "query" else "values[0]"
        prefix = (
            f"    values = {call}\n    if len(values) > 0:\n        value = {item}\n"
        )
        indentation = "        "
    source = _program(
        recording_layer,
        prefix
        + f"{indentation}if len(value) == 1:\n"
        + f"{indentation}    if ord(value) > {upper}:\n"
        + f"{indentation}        record('one')\n",
    )
    proofs = prove_text(
        source, trusted_roots=[recording_layer], import_paths=[recording_layer.parent]
    )
    expected = Conclusion.OK if upper == 0x10FFFF else Conclusion.FAIL
    assert len(proofs) == 1
    proof = proofs[0].proof
    assert proof.conclusion == expected, str(proof)
    assert proof.error is None
    if expected == Conclusion.FAIL:
        assert proof.example is not None
        assert dict(proof.example)
        if container in {"list", "known_list"}:
            returned = [
                value for name, value in proof.example if name.startswith("read_")
            ]
            assert len(returned) == 1
            assert isinstance(returned[0], list)
            assert len(returned[0][0]) == 1
            assert upper < ord(returned[0][0]) <= 0x10FFFF
        if container == "known_list":
            assert ["\U00030000"] in dict(proof.example).values()
    assert _effects(recording_layer).EVENTS == []
    if expected == Conclusion.FAIL:
        _assert_refused_before_approval(source, recording_layer, monkeypatch, expected)
    _assert_runtime_events(
        source, recording_layer, [] if expected == Conclusion.OK else ["one"]
    )


def test_high_character_after_an_effect_cannot_prove_a_second_effect(
    recording_layer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _program(
        recording_layer,
        "    record('first')\n"
        "    value = read_value()\n"
        "    if len(value) == 1:\n"
        "        if ord(value) <= 0x2FFFF:\n"
        "            record('second')\n",
        "twice()",
    )
    _assert_refused_before_approval(source, recording_layer, monkeypatch)
    # The runtime boundary must accept the complete string rather than raising
    # after 'first', which would leave a prefix of the claimed effect trace.
    _assert_runtime_events(source, recording_layer, ["first"])


@pytest.mark.parametrize("text", ["\ud800", "\U00030000", "\U0010ffff"])
@pytest.mark.parametrize("count", [1, 2])
def test_contract_literal_uses_python_character_length(
    recording_layer: Path, text: str, count: int
) -> None:
    source = _program(
        recording_layer,
        "    record('one')\n" * count,
        f"budget({text!r})",
    )
    proofs = prove_text(
        source, trusted_roots=[recording_layer], import_paths=[recording_layer.parent]
    )
    assert [result.proof.conclusion for result in proofs] == [
        Conclusion.OK if count == 1 else Conclusion.FAIL
    ]
    _assert_runtime_events(source, recording_layer, ["one"] * count)


@pytest.mark.parametrize("text", ["", "a\x00\ud800\U00030000\U0010ffff", r"\u{41}"])
def test_counterexample_string_decodes_to_exact_python_text(text: str) -> None:
    value = z3.Const("text", string_sort())
    solver = z3.Solver()
    solver.add(value == string_val(text))
    assert solver.check() == z3.sat

    decoded = dict(Model(solver.model()))

    assert decoded["text"] == text
    assert f"text={text!r}" in str(Model(solver.model()))
