"""Impossible trusted Unicode postconditions must not approve an effect trace."""

import importlib
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock
from uuid import uuid4

import deal
import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source


@pytest.fixture
def recording_layer(tmp_path: Path) -> Path:
    package = tmp_path / f"unicode_vacuity_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
        "@clauz3.trusted\n"
        "@clauz3.ensures(lambda result: len(result) == 1 and ord(result) > 0x10FFFF)\n"
        "def read_bad() -> str:\n"
        "    return chr(0x10FFFF)\n"
        "@clauz3.trusted\n"
        "@clauz3.ensures(lambda result: len(result) == 1 and ord(result) == 0x10FFFF)\n"
        "def read_valid() -> str:\n"
        "    return chr(0x10FFFF)\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect('record').empty()\n"
    )
    return package


def _source(package: Path, body: str) -> str:
    return (
        "import clauz3\n"
        f"from {package.name}.effects import read_bad, read_valid, record\n"
        f"from {package.name}.contracts import silent\n"
        "@clauz3.guarantee(silent())\n"
        "def main() -> None:\n" + body
    )


def _effects(package: Path) -> ModuleType:
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.__file__ is not None
    assert Path(effects.__file__).resolve() == (package / "effects.py").resolve()
    return effects


@pytest.mark.parametrize(
    "body",
    [
        "    read_bad()\n    record('forbidden')\n",
        "    record('forbidden')\n    read_bad()\n",
    ],
    ids=["contradiction-before-effect", "contradiction-after-effect"],
)
def test_impossible_trusted_postcondition_blocks_approval(
    recording_layer: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    source = _source(recording_layer, body)
    proofs = prove_text(
        source, trusted_roots=[recording_layer], import_paths=[recording_layer.parent]
    )
    assert [result.proof.conclusion for result in proofs] == [Conclusion.VACUOUS]
    assert proofs[0].proof.description == "assumptions are contradictory"
    assert _effects(recording_layer).EVENTS == []

    approval = Mock(side_effect=AssertionError("approval must not be reached"))
    execution = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approval)
    monkeypatch.setattr("clauz3.runner._execute_source", execution)
    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[recording_layer],
            import_roots=[recording_layer.parent],
            approval_service_url="http://approval.invalid",
        )
    assert [result.proof.conclusion for result in exc_info.value.proofs] == [
        Conclusion.VACUOUS
    ]
    approval.assert_not_called()
    execution.assert_not_called()
    assert _effects(recording_layer).EVENTS == []


def test_bypassing_proof_records_before_the_impossible_runtime_postcondition(
    recording_layer: Path,
) -> None:
    source = _source(recording_layer, "    record('forbidden')\n    read_bad()\n")
    with pytest.raises(deal.PostContractError):
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(
                trusted_roots=[recording_layer], import_roots=[recording_layer.parent]
            ),
            import_roots=[recording_layer.parent],
        )
    assert _effects(recording_layer).EVENTS == ["forbidden"]


@pytest.mark.parametrize("record", [False, True], ids=["quiet", "forbidden-effect"])
def test_valid_trusted_unicode_postcondition_keeps_guarantee_meaningful(
    recording_layer: Path, record: bool
) -> None:
    body = "    read_valid()\n"
    if record:
        body += "    record('forbidden')\n"
    source = _source(recording_layer, body)
    proofs = prove_text(
        source, trusted_roots=[recording_layer], import_paths=[recording_layer.parent]
    )
    assert [result.proof.conclusion for result in proofs] == [
        Conclusion.FAIL if record else Conclusion.OK
    ]
    execute(
        source,
        source_name="plan.py",
        policy=build_policy(
            trusted_roots=[recording_layer], import_roots=[recording_layer.parent]
        ),
        import_roots=[recording_layer.parent],
    )
    expected = ["forbidden"] if record else []
    assert expected == _effects(recording_layer).EVENTS
