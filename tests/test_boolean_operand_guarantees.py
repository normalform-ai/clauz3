"""Boolean expressions retain selected values and condition their effects."""

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


def _program(
    tmp_path: Path,
    body: str,
    *,
    clause: str = "silent()",
    additional_clause: str | None = None,
    chosen: bool = False,
) -> tuple[str, Path]:
    package = tmp_path / f"boolean_values_{uuid4().hex}"
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
    decorators = f"@clauz3.guarantee({clause})\n"
    if additional_clause is not None:
        decorators += f"@clauz3.guarantee({additional_clause})\n"
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import choose, record\n"
        f"from {package.name}.contracts import silent, at_most_one, at_least_one\n"
        "def quiet() -> None:\n"
        "    pass\n"
        "def bool_rhs() -> bool:\n"
        "    record('rhs')\n"
        "    return True\n"
        "def string_rhs() -> str:\n"
        "    record('rhs')\n"
        "    return 'rhs'\n"
        + decorators
        + "def main() -> None:\n"
        + indent(body, "    ")
        + "\n"
    )
    return source, package


def _check_runtime(
    source: str,
    package: Path,
    events: list[str],
    *,
    choose_called: bool = False,
) -> None:
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert events == effects.EVENTS
    prefix: tuple[tuple[str, tuple[object, ...], dict[str, object]], ...] = (
        ((f"{package.name}.effects.choose", (), {}),) if choose_called else ()
    )
    assert outcome.effects == prefix + tuple(
        (f"{package.name}.effects.record", (value,), {}) for value in events
    )


@pytest.mark.parametrize(
    ("expression", "selected"),
    [
        ("0 or 'x'", "'x'"),
        ("1 and 'x'", "'x'"),
        ("quiet() or 'x'", "'x'"),
        ("'kept' or 'other'", "'kept'"),
        ("'' and 'other'", "''"),
        ("0.0 or 2.5", "2.5"),
        ("'truthy' and 7", "7"),
    ],
    ids=[
        "zero-or-string",
        "one-and-string",
        "none-or-string",
        "left-string",
        "empty-string",
        "float",
        "integer",
    ],
)
def test_selected_operand_value_cannot_hide_a_record(
    tmp_path: Path, expression: str, selected: str
) -> None:
    source, package = _program(
        tmp_path,
        f"value = {expression}\nif value == {selected}:\n    record('bad')",
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    expected = Conclusion.SKIP if "quiet()" in expression else Conclusion.FAIL
    assert [result.proof.conclusion for result in results] == [expected]
    diagnostic = (
        "unsupported boolean operand type NoneType"
        if expected is Conclusion.SKIP
        else "guarantee"
    )
    assert diagnostic in str(results[0].proof)
    _check_runtime(source, package, ["bad"])


@pytest.mark.parametrize(
    ("expression", "test", "expected", "events"),
    [
        ("'x' if not 0 else ''", "value == 'x'", Conclusion.FAIL, ["bad"]),
        ("False or True", "value == True", Conclusion.FAIL, ["bad"]),
        ("False or False", "value == True", Conclusion.OK, []),
    ],
    ids=["ternary", "true-boolean", "false-boolean"],
)
def test_existing_ternary_and_boolean_controls_remain_exact(
    tmp_path: Path,
    expression: str,
    test: str,
    expected: Conclusion,
    events: list[str],
) -> None:
    source, package = _program(
        tmp_path, f"value = {expression}\nif {test}:\n    record('bad')"
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [expected]
    _check_runtime(source, package, events)


@pytest.mark.parametrize(
    ("body", "clause", "expected", "events"),
    [
        ("False and bool_rhs()", "silent()", Conclusion.OK, []),
        ("True or bool_rhs()", "silent()", Conclusion.OK, []),
        ("True and bool_rhs()", "silent()", Conclusion.FAIL, ["rhs"]),
        ("False or bool_rhs()", "silent()", Conclusion.FAIL, ["rhs"]),
        ("False and bool_rhs()", "at_least_one()", Conclusion.FAIL, []),
        (
            "value = True and string_rhs()\nif value == 'rhs':\n    record('after')",
            "at_most_one()",
            Conclusion.FAIL,
            ["rhs", "after"],
        ),
        (
            "value = False or string_rhs()\nif value == 'rhs':\n    record('after')",
            "at_most_one()",
            Conclusion.FAIL,
            ["rhs", "after"],
        ),
    ],
    ids=[
        "skipped-and",
        "skipped-or",
        "live-and",
        "live-or",
        "missing-required-effect",
        "string-and",
        "string-or",
    ],
)
def test_rhs_effects_and_return_values_follow_short_circuit_selection(
    tmp_path: Path,
    body: str,
    clause: str,
    expected: Conclusion,
    events: list[str],
) -> None:
    source, package = _program(tmp_path, body, clause=clause)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [expected]
    _check_runtime(source, package, events)


@pytest.mark.parametrize("chosen", [False, True], ids=["runtime-false", "runtime-true"])
@pytest.mark.parametrize("complementary", [True, False], ids=["exclusive", "repeated"])
def test_symbolic_rhs_effects_keep_their_conditions(
    tmp_path: Path, chosen: bool, complementary: bool
) -> None:
    second_operator = "or" if complementary else "and"
    source, package = _program(
        tmp_path,
        f"flag = choose()\nflag and bool_rhs()\nflag {second_operator} bool_rhs()",
        clause="at_most_one()",
        additional_clause="at_least_one()" if complementary else None,
        chosen=chosen,
    )

    # choose has no postcondition: proof must cover both Boolean outcomes,
    # independently of the concrete trusted implementation exercised below.
    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    expected = Conclusion.OK if complementary else Conclusion.FAIL
    assert [result.proof.conclusion for result in results] == [expected]
    count = 1 if complementary else (2 if chosen else 0)
    _check_runtime(source, package, ["rhs"] * count, choose_called=True)


@pytest.mark.parametrize("chosen", [False, True], ids=["runtime-false", "runtime-true"])
def test_symbolic_rhs_does_not_invent_a_required_effect(
    tmp_path: Path, chosen: bool
) -> None:
    source, package = _program(
        tmp_path,
        "flag = choose()\nflag and bool_rhs()",
        clause="at_least_one()",
        chosen=chosen,
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]
    _check_runtime(source, package, ["rhs"] if chosen else [], choose_called=True)


@pytest.mark.parametrize(
    ("body", "clause"),
    [
        ("value = 0 or 'x'\nif value == 'x':\n    record('bad')", "silent()"),
        ("value = 1 and 'x'\nif value == 'x':\n    record('bad')", "silent()"),
        ("value = quiet() or 'x'\nif value == 'x':\n    record('bad')", "silent()"),
        ("False and bool_rhs()", "at_least_one()"),
    ],
    ids=["or-value", "and-value", "none-fallback", "missing-required-effect"],
)
def test_boolean_expression_violation_stops_before_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, clause: str
) -> None:
    source, package = _program(tmp_path, body, clause=clause)
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
        Conclusion.SKIP if "quiet()" in body else Conclusion.FAIL
    ]
    approve.assert_not_called()
    execute_source.assert_not_called()
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


@pytest.mark.parametrize("effectful_condition", [True, False], ids=["effect", "scalar"])
def test_conditions_cannot_invent_effects_that_satisfy_a_lower_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, effectful_condition: bool
) -> None:
    package = tmp_path / f"condition_events_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    predicate = "probe(value)" if effectful_condition else "value > 0"
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def probe(value: int) -> bool:\n"
        "    EVENTS.append('one')\n"
        "    return value > 0\n"
        f"@clauz3.requires(lambda value: {predicate})\n"
        "@clauz3.trusted\n"
        "def check(value: int) -> None:\n"
        "    pass\n"
    )
    bound = (
        "effect('probe').count() >= 2"
        if effectful_condition
        else ("effect('check').count() >= 1")
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def enough_calls() -> ContractSpec:\n"
        f"    return {bound}\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import check\n"
        f"from {package.name}.contracts import enough_calls\n"
        "@clauz3.guarantee(enough_calls())\n"
        "def main() -> None:\n"
        "    check(1)\n"
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    expected = Conclusion.SKIP if effectful_condition else Conclusion.OK
    assert [result.proof.conclusion for result in results] == [expected]
    if effectful_condition:
        assert "trusted calls inside condition predicates are not supported" in str(
            results[0].proof
        )
        approve = Mock(side_effect=AssertionError("approval must not be reached"))
        execute_source = Mock(
            side_effect=AssertionError("execution must not be reached")
        )
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
        approve.assert_not_called()
        execute_source.assert_not_called()

    # The native precondition runs once. Repeated symbolic contract evaluation
    # must not invent a second probe call that satisfies the lower bound.
    execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert (["one"] if effectful_condition else []) == effects.EVENTS
