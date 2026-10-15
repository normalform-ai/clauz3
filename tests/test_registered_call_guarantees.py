"""Registered-call options must not hide native trusted effects."""

import importlib
from pathlib import Path
from textwrap import indent
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import ProgramError, execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source

_BINDING_ERROR = "unsupported registered call argument binding"
_SUM_START = [
    pytest.param("sum([1], start=1)", "", id="keyword-start"),
    pytest.param("sum([1], 1)", "", id="positional-start"),
    pytest.param("total([1], start=1)", "total = sum\n", id="aliased-start"),
]
_MALFORMED = [
    pytest.param("len([1, 2], unknown=True)", id="unknown-keyword"),
    pytest.param("len(items=[1, 2])", id="implementation-parameter-name"),
    pytest.param("len()", id="missing-argument"),
]


def _program(
    tmp_path: Path, body: str, *, guarantee: str = "silent()"
) -> tuple[str, Path]:
    package = tmp_path / f"registered_call_{uuid4().hex}"
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
        "def silent() -> ContractSpec:\n"
        "    return effect('record').empty()\n"
        "@contract\n"
        "def only(value: str = 'one') -> ContractSpec:\n"
        "    return effect('record').all(lambda event: event.value == value)\n"
    )
    source = (
        "import clauz3\n"
        "import math\n"
        "from math import isclose as close\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import silent, only\n"
        f"@clauz3.guarantee({guarantee})\n"
        "def main() -> None:\n" + indent(body, "    ") + "\n"
    )
    return source, package


def _check_runtime(source: str, package: Path, events: list[str]) -> None:
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert events == effects.EVENTS
    assert outcome.effects == tuple(
        (f"{package.name}.effects.record", (value,), {}) for value in events
    )


@pytest.mark.parametrize(("call", "setup"), _SUM_START)
def test_unmodeled_sum_start_cannot_hide_an_effect(
    tmp_path: Path, call: str, setup: str
) -> None:
    source, package = _program(tmp_path, setup + f"if {call} == 2:\n    record('one')")

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert _BINDING_ERROR in str(results[0].proof)
    # Python accepts both start forms and executes the forbidden effect.
    _check_runtime(source, package, ["one"])


@pytest.mark.parametrize("call", _MALFORMED)
def test_invalid_registered_calls_are_clean_refusals(tmp_path: Path, call: str) -> None:
    source, package = _program(tmp_path, f"if {call} == 0:\n    record('one')")

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert _BINDING_ERROR in str(results[0].proof)
    with pytest.raises(ProgramError):
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
            import_roots=[tmp_path],
        )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


@pytest.mark.parametrize(
    "call",
    ["sum([1], start=1)", "len([1, 2], unknown=True)", "len()"],
    ids=["ignored-option", "unknown-keyword", "missing-argument"],
)
def test_registered_call_refusal_precedes_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, call: str
) -> None:
    source, package = _program(tmp_path, f"if {call} == 2:\n    record('one')")
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
        Conclusion.SKIP
    ]
    assert _BINDING_ERROR in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute_source.assert_not_called()
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


@pytest.mark.parametrize(
    ("body", "expected", "events"),
    [
        ("if sum([1]) == 2:\n    record('one')", Conclusion.OK, []),
        ("if sum([1]) + 1 == 2:\n    record('one')", Conclusion.FAIL, ["one"]),
        (
            "total = sum\nif total([1]) == 1:\n    record('one')",
            Conclusion.FAIL,
            ["one"],
        ),
    ],
    ids=["sum-default", "explicit-addition", "sum-alias"],
)
def test_supported_sum_calls_preserve_exact_guarantees(
    tmp_path: Path, body: str, expected: Conclusion, events: list[str]
) -> None:
    source, package = _program(tmp_path, body)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [expected]
    _check_runtime(source, package, events)


@pytest.mark.parametrize(
    ("call", "expected", "events"),
    [
        ("math.isclose(1.0, 1.5, abs_tol=0.6)", Conclusion.FAIL, ["one"]),
        ("math.isclose(1.0, 1.5, abs_tol=0.1)", Conclusion.OK, []),
        ("close(1.0, 1.5, rel_tol=0.5)", Conclusion.FAIL, ["one"]),
    ],
    ids=["absolute-tolerance", "small-tolerance", "aliased-relative-tolerance"],
)
def test_modeled_registered_keywords_preserve_their_semantics(
    tmp_path: Path, call: str, expected: Conclusion, events: list[str]
) -> None:
    source, package = _program(tmp_path, f"if {call}:\n    record('one')")

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [expected]
    _check_runtime(source, package, events)


@pytest.mark.parametrize(
    ("guarantee", "event", "expected"),
    [
        ("only(value='one')", "one", Conclusion.OK),
        ("only(value='one')", "bad", Conclusion.FAIL),
        ("only()", "one", Conclusion.OK),
    ],
    ids=["keyword-allowed", "keyword-violation", "helper-default"],
)
def test_registered_contract_helper_keywords_and_defaults_are_preserved(
    tmp_path: Path, guarantee: str, event: str, expected: Conclusion
) -> None:
    source, package = _program(tmp_path, f"record({event!r})", guarantee=guarantee)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [expected]
    _check_runtime(source, package, [event])
