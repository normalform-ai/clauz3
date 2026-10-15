"""Relation predicates must retain Python's selected Boolean operands."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source


def _program(tmp_path: Path, predicate: str, value: str) -> tuple[str, Path]:
    package = tmp_path / f"boolean_relation_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    oracle = predicate.replace("event.value", "value")
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "def python_predicate(value: str) -> bool:\n"
        f"    return {oracle}\n"
        "@contract\n"
        "def filtered() -> ContractSpec:\n"
        f"    return effect('record').where(lambda event: {predicate}).empty()\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import filtered\n"
        "@clauz3.guarantee(filtered())\n"
        "def main() -> None:\n"
        f"    record({value!r})\n"
    )
    return source, package


@pytest.mark.parametrize(
    ("predicate", "value", "expected"),
    [
        ("(event.value or 'safe') == 'bad'", "bad", Conclusion.FAIL),
        ("(event.value or 'bad') == 'bad'", "", Conclusion.FAIL),
        ("(event.value and 'bad') == 'bad'", "bad", Conclusion.FAIL),
        ("(event.value or 'safe') == 'bad'", "good", Conclusion.OK),
        (
            "event.value == 'bad' or event.value == 'worse'",
            "bad",
            Conclusion.FAIL,
        ),
        (
            "event.value == 'bad' and event.value == 'worse'",
            "bad",
            Conclusion.OK,
        ),
    ],
    ids=[
        "or-left",
        "or-fallback",
        "and-value",
        "unmatched",
        "boolean-or",
        "boolean-and",
    ],
)
def test_relation_predicate_matches_python_operand_value_selection(
    tmp_path: Path, predicate: str, value: str, expected: Conclusion
) -> None:
    source, package = _program(tmp_path, predicate, value)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [expected]
    contracts = importlib.import_module(f"{package.name}.contracts")
    assert contracts.python_predicate(value) is (expected is Conclusion.FAIL)
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert [value] == effects.EVENTS
    assert outcome.effects == ((f"{package.name}.effects.record", (value,), {}),)


def test_relation_predicate_is_checked_after_the_entry_returns(tmp_path: Path) -> None:
    source, package = _program(tmp_path, "(event.value or 'safe') == 'bad'", "bad")
    source = source.replace("def main() -> None:", "def main() -> int:")
    source += "    return 7\n"

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]
    contracts = importlib.import_module(f"{package.name}.contracts")
    assert contracts.python_predicate("bad") is True
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["bad"]
    assert outcome.value == 7
    assert outcome.effects == ((f"{package.name}.effects.record", ("bad",), {}),)


def test_matching_relation_effect_stops_before_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, package = _program(tmp_path, "(event.value or 'safe') == 'bad'", "bad")
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

    assert [result.proof.conclusion for result in exc_info.value.proofs] == [
        Conclusion.FAIL
    ]
    approve.assert_not_called()
    execute_source.assert_not_called()
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ("len(3) != 0", Conclusion.SKIP),
        ("True and len(3) != 0", Conclusion.SKIP),
        ("False and len(3) != 0", Conclusion.OK),
    ],
    ids=["direct-invalid", "selected-invalid", "unselected-control"],
)
def test_live_relation_exceptions_are_not_hidden_by_an_entry_return(
    tmp_path: Path, predicate: str, expected: Conclusion
) -> None:
    source, package = _program(tmp_path, predicate, "bad")
    source = source.replace("def main() -> None:", "def main() -> int:")
    source += "    return 7\n"

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [expected]
    contracts = importlib.import_module(f"{package.name}.contracts")
    if expected is Conclusion.SKIP:
        assert "effect lambda may raise an exception TypeError" in str(results[0].proof)
        with pytest.raises(TypeError):
            contracts.python_predicate("bad")
    else:
        assert contracts.python_predicate("bad") is False
    # Runtime guarantee decorators are inert: execution independently shows
    # the recorded event whose predicate the prover must actually validate.
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["bad"]
    assert outcome.value == 7
    assert outcome.effects == ((f"{package.name}.effects.record", ("bad",), {}),)
