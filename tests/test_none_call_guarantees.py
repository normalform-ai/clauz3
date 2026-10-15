"""Python None call results must not hide native trusted effects."""

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

_RESULTS = [
    pytest.param("", "record('safe')", id="trusted-call"),
    pytest.param("", "quiet()", id="ordinary-fallthrough"),
    pytest.param("values = [0]", "values.append(1)", id="append"),
    pytest.param("values = [0]", "values.extend([1])", id="extend"),
    pytest.param("values = {0}", "values.add(1)", id="set-add"),
    pytest.param("values = {'x': 1}", "values.clear()", id="dict-clear"),
    pytest.param("", "print('ignored')", id="print-noop"),
]


def _program(tmp_path: Path, body: str, helpers: str = "") -> tuple[str, Path]:
    package = tmp_path / f"none_result_{uuid4().hex}"
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
        "def no_bad() -> ContractSpec:\n"
        "    return effect('record').where("
        "lambda event: event.value == 'bad').empty()\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import no_bad\n"
        "def quiet() -> None:\n    pass\n"
        + helpers
        + "\n@clauz3.guarantee(no_bad())\ndef main() -> None:\n"
        + "".join(f"    {line}\n" for line in body.splitlines())
    )
    return source, package


def _check_runtime(source: str, package: Path, *, bad: bool) -> None:
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert ("bad" in effects.EVENTS) is bad
    assert outcome.effects == tuple(
        (f"{package.name}.effects.record", (value,), {}) for value in effects.EVENTS
    )


@pytest.mark.parametrize(("setup", "expression"), _RESULTS)
@pytest.mark.parametrize("comparison", ["!=", "=="], ids=["not-false", "false"])
def test_call_results_compare_as_none_in_native_guarantees(
    tmp_path: Path, setup: str, expression: str, comparison: str
) -> None:
    body = f"result = {expression}\nif result {comparison} False:\n    record('bad')"
    if setup:
        body = setup + "\n" + body
    source, package = _program(tmp_path, body)
    bad = comparison == "!="

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    expected = Conclusion.FAIL if bad else Conclusion.OK
    assert [result.proof.conclusion for result in results] == [expected]
    _check_runtime(source, package, bad=bad)


@pytest.mark.parametrize(
    ("setup", "expression", "state"),
    [
        ("values = [0]", "values.append(1)", "len(values) == 2"),
        ("values = [0]", "values.extend([1])", "len(values) == 2"),
        ("values = {0}", "values.add(1)", "1 in values"),
        ("values = {'x': 1}", "values.clear()", "'x' not in values"),
    ],
    ids=["append", "extend", "set-add", "dict-clear"],
)
@pytest.mark.parametrize(
    "negated", [True, False], ids=["false-result", "truthy-result"]
)
def test_mutator_none_result_preserves_its_receiver_update(
    tmp_path: Path, setup: str, expression: str, state: str, negated: bool
) -> None:
    result_guard = "not result" if negated else "result"
    source, package = _program(
        tmp_path,
        f"{setup}\nresult = {expression}\n"
        f"if {result_guard} and {state}:\n    record('bad')",
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    expected = Conclusion.FAIL if negated else Conclusion.SKIP
    assert [result.proof.conclusion for result in results] == [expected]
    if not negated:
        assert "unsupported boolean operand type NoneType" in str(results[0].proof)
    _check_runtime(source, package, bad=negated)


@pytest.mark.parametrize(
    "body",
    [
        "values = {'x': 7}\nresult = values.pop('x')\n"
        "if result == 7 and 'x' not in values:\n    record('bad')",
        "values = {7}\nresult = values.pop()\n"
        "if result == 7 and len(values) == 0:\n    record('bad')",
    ],
    ids=["dict-pop", "set-pop"],
)
def test_value_returning_mutators_keep_the_returned_value_and_new_state(
    tmp_path: Path, body: str
) -> None:
    source, package = _program(tmp_path, body)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]
    _check_runtime(source, package, bad=True)


def test_conditional_normal_fallthrough_cannot_hide_an_effect(tmp_path: Path) -> None:
    source, package = _program(
        tmp_path,
        "if maybe(False) != False:\n    record('bad')",
        "def maybe(flag: bool) -> bool:\n    if flag:\n        return False\n",
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "None" in str(results[0].proof)
    _check_runtime(source, package, bad=True)


@pytest.mark.parametrize(
    "body",
    [
        "if record('safe') != False:\n    record('bad')",
        "if quiet() != False:\n    record('bad')",
        "values = [0]\nif not values.append(1):\n    record('bad')",
    ],
    ids=["trusted", "ordinary", "mutator"],
)
def test_none_result_false_guarantee_stops_before_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    source, package = _program(tmp_path, body)
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
