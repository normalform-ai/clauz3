"""Guarded expression evaluation preserves effects and refuses lost writes."""

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

_MUTATION_DIAGNOSTIC = "mutating methods inside a guarded expression are not supported"
_MUTATIONS = [
    "values.pop('k') if True else 0",
    "0 if False else values.pop('k')",
    "0 < 1 < values.pop('k')",
    "pop_key(values) if True else 0",
    "pop_lambda(values) if True else 0",
]
_MUTATION_IDS = ["then", "else", "comparison", "helper", "lambda"]


def _program(
    tmp_path: Path,
    body: str,
    *,
    exact_one: bool = False,
    chosen: bool = False,
) -> tuple[str, Path]:
    package = tmp_path / f"guarded_effects_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
        "@clauz3.trusted\n"
        "def choose() -> bool:\n"
        f"    return {chosen!r}\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect('record').empty()\n"
        "@contract\n"
        "def at_most_one() -> ContractSpec:\n"
        "    return effect('record').count() <= 1\n"
        "@contract\n"
        "def at_least_one() -> ContractSpec:\n"
        "    return effect('record').count() >= 1\n"
    )
    decorators = (
        "@clauz3.guarantee(at_most_one())\n@clauz3.guarantee(at_least_one())\n"
        if exact_one
        else "@clauz3.guarantee(silent())\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import choose, record\n"
        f"from {package.name}.contracts import silent, at_most_one, at_least_one\n"
        "def pop_key(values: dict[str, int]) -> int:\n"
        "    return values.pop('k')\n"
        "def rhs() -> int:\n"
        "    record('one')\n"
        "    return 2\n"
        + decorators
        + "def main() -> None:\n"
        + indent(body, "    ")
        + "\n"
    )
    return source, package


def _mutation_body(expression: str) -> str:
    return (
        "values = {'k': 2, 'keep': 0}\n"
        "pop_lambda = lambda items: items.pop('k')\n"
        f"selected = {expression}\n"
        "if 'k' not in values:\n"
        "    record('one')"
    )


def _check_runtime(
    source: str, package: Path, events: list[str], *, choose_called: bool = False
) -> None:
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    assert events == effects.EVENTS
    prefix: tuple[tuple[str, tuple[object, ...], dict[str, object]], ...] = (
        ((f"{package.name}.effects.choose", (), {}),) if choose_called else ()
    )
    assert outcome.effects == prefix + tuple(
        (f"{package.name}.effects.record", (value,), {}) for value in events
    )


@pytest.mark.parametrize("expression", _MUTATIONS, ids=_MUTATION_IDS)
def test_guarded_mutation_cannot_hide_a_real_effect(
    tmp_path: Path, expression: str
) -> None:
    source, package = _program(tmp_path, _mutation_body(expression))

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    diagnostic = (
        "mutating a function parameter or its alias is not supported"
        if "pop_key" in expression or "pop_lambda" in expression
        else _MUTATION_DIAGNOSTIC
    )
    assert diagnostic in str(results[0].proof)
    _check_runtime(source, package, ["one"])


@pytest.mark.parametrize("expression", [_MUTATIONS[0], _MUTATIONS[2]])
def test_guarded_mutation_is_refused_before_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expression: str
) -> None:
    source, package = _program(tmp_path, _mutation_body(expression))
    approval = Mock(side_effect=AssertionError("approval must not be reached"))
    execution = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approval)
    monkeypatch.setattr("clauz3.runner._execute_source", execution)

    with pytest.raises(RunProofError) as error:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[tmp_path],
            approval_service_url="http://approval.invalid",
        )

    assert [result.proof.conclusion for result in error.value.proofs] == [
        Conclusion.SKIP
    ]
    assert _MUTATION_DIAGNOSTIC in str(error.value.proofs[0].proof)
    approval.assert_not_called()
    execution.assert_not_called()
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


@pytest.mark.parametrize(
    ("expression", "expected", "events"),
    [
        ("values.pop('k') if False else 0", Conclusion.OK, []),
        ("2 < 1 < values.pop('k')", Conclusion.OK, []),
        ("values.pop('k')", Conclusion.FAIL, ["one"]),
    ],
    ids=["dead-arm", "dead-comparison-suffix", "direct-mutation"],
)
def test_dead_guarded_mutations_and_direct_mutation_remain_distinct(
    tmp_path: Path, expression: str, expected: Conclusion, events: list[str]
) -> None:
    source, package = _program(tmp_path, _mutation_body(expression))

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [expected]
    _check_runtime(source, package, events)


@pytest.mark.parametrize(
    ("expression", "expected", "events"),
    [
        ("rhs() if True else 0", Conclusion.FAIL, ["one"]),
        ("0 if False else rhs()", Conclusion.FAIL, ["one"]),
        ("rhs() if False else 0", Conclusion.OK, []),
        ("0 < 1 < rhs()", Conclusion.FAIL, ["one"]),
        ("2 < 1 < rhs()", Conclusion.OK, []),
    ],
    ids=["then", "else", "dead-arm", "comparison", "dead-comparison-suffix"],
)
def test_selected_branches_and_comparison_suffixes_preserve_effects(
    tmp_path: Path, expression: str, expected: Conclusion, events: list[str]
) -> None:
    source, package = _program(tmp_path, f"selected = {expression}")

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [expected]
    _check_runtime(source, package, events)


@pytest.mark.parametrize("chosen", [False, True])
def test_complementary_symbolic_arms_record_exactly_once(
    tmp_path: Path, chosen: bool
) -> None:
    source, package = _program(
        tmp_path,
        "flag = choose()\nfirst = rhs() if flag else 0\nsecond = 0 if flag else rhs()",
        exact_one=True,
        chosen=chosen,
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]
    _check_runtime(source, package, ["one"], choose_called=True)
