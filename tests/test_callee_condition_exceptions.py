"""A callee predicate cannot hide an exception after an earlier effect."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import Outcome, ProgramError, execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source

DIAGNOSTIC = "condition predicate may raise"


def _condition(route: str, expression: str) -> str:
    if route == "requires":
        return f"@clauz3.requires(lambda value: {expression})"
    if route == "post":
        return f"@clauz3.ensures(lambda result: {expression})"
    assert route == "ensure"
    return f"@clauz3.ensures(lambda value, result: {expression})"


def _program(
    tmp_path: Path,
    condition: str,
    *,
    trusted: bool,
    minimum: int = 2,
    operation: str = "check(3)",
    helper: str = "",
    sample: int | str = 0,
    annotation: str = "int",
) -> tuple[str, Path]:
    package = tmp_path / f"callee_predicates_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
        "@clauz3.trusted\n"
        f"def sample() -> {annotation}:\n"
        f"    return {sample!r}\n"
        + helper
        + condition
        + "\n"
        + ("@clauz3.trusted\n" if trusted else "")
        + f"def check(value: {annotation}) -> {annotation}:\n"
        + "    return value\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def enough_records() -> ContractSpec:\n"
        f"    return effect('record').count() >= {minimum}\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record, check, sample\n"
        f"from {package.name}.contracts import enough_records\n"
        "@clauz3.guarantee(enough_records())\n"
        "def main() -> None:\n"
        "    record('first')\n"
        f"    {operation}\n"
        "    record('second')\n"
    )
    return source, package


def _execute(source: str, package: Path) -> Outcome:
    return execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )


def _assert_refused_before_approval(
    source: str,
    package: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    kind: str | None = None,
    diagnostic: str = DIAGNOSTIC,
) -> None:
    proofs = prove_text(source, trusted_roots=[package], import_paths=[package.parent])
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert diagnostic in str(proofs[0].proof)
    if kind is not None:
        assert kind in str(proofs[0].proof)
    effects = importlib.import_module(f"{package.name}.effects")
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
    assert diagnostic in str(exc_info.value.proofs[0].proof)
    if kind is not None:
        assert kind in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute_source.assert_not_called()
    assert effects.EVENTS == []


@pytest.mark.parametrize("trusted", [False, True], ids=["inlined", "trusted"])
@pytest.mark.parametrize("route", ["requires", "post", "ensure"])
def test_callee_predicate_error_stops_before_approval_and_the_first_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    trusted: bool,
) -> None:
    argument = "result" if route == "post" else "value"
    source, package = _program(
        tmp_path,
        _condition(route, f"len({argument}) == 0"),
        trusted=trusted,
    )

    _assert_refused_before_approval(source, package, monkeypatch, kind="TypeError")

    # Even an inlined helper lives in the genuine trusted package at runtime,
    # where its native decorator runs. No second record follows its exception.
    with pytest.raises(TypeError, match="object of type 'int' has no len"):
        _execute(source, package)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["first"]


@pytest.mark.parametrize("trusted", [False, True], ids=["inlined", "trusted"])
@pytest.mark.parametrize("route", ["requires", "post", "ensure"])
def test_valid_callee_predicates_complete_the_promised_records(
    tmp_path: Path, route: str, trusted: bool
) -> None:
    argument = "result" if route == "post" else "value"
    source, package = _program(
        tmp_path, _condition(route, f"{argument} == 3"), trusted=trusted
    )

    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    outcome = _execute(source, package)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["first", "second"]
    assert outcome.effects == (
        (f"{package.name}.effects.record", ("first",), {}),
        (f"{package.name}.effects.check", (3,), {}),
        (f"{package.name}.effects.record", ("second",), {}),
    )


def test_valid_predicate_does_not_make_a_stronger_guarantee_vacuous(
    tmp_path: Path,
) -> None:
    source, package = _program(
        tmp_path,
        _condition("post", "result == 3"),
        trusted=True,
        minimum=3,
    )

    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "guarantee" in str(proofs[0].proof)
    _execute(source, package)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["first", "second"]


@pytest.mark.parametrize(
    ("route", "predicate", "trusted"),
    [
        ("requires", "True or len(value) == 0", False),
        ("post", "(False and len(result) == 0) or result == 3", True),
        ("ensure", "value == 3 or len(value) == 0", True),
    ],
    ids=["requires-dead-or", "post-dead-and", "ensure-known-argument"],
)
def test_dead_predicate_operands_do_not_introduce_exceptions(
    tmp_path: Path, route: str, predicate: str, trusted: bool
) -> None:
    source, package = _program(tmp_path, _condition(route, predicate), trusted=trusted)

    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    _execute(source, package)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["first", "second"]


@pytest.mark.parametrize("trusted", [False, True], ids=["inlined", "trusted"])
def test_nested_predicate_helper_cannot_hide_its_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trusted: bool
) -> None:
    source, package = _program(
        tmp_path,
        _condition("requires", "has_length(value)"),
        trusted=trusted,
        helper=("def has_length(value: int) -> bool:\n    return len(value) == 0\n"),
    )

    _assert_refused_before_approval(source, package, monkeypatch, kind="TypeError")

    with pytest.raises(TypeError, match="object of type 'int' has no len"):
        _execute(source, package)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["first"]


@pytest.mark.parametrize(
    "sample", ["missing", "a"], ids=["runtime-error", "runtime-valid"]
)
def test_possible_symbolic_predicate_exception_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample: str
) -> None:
    source, package = _program(
        tmp_path,
        _condition("post", "{'a': 1}[result] == 1"),
        trusted=True,
        operation="check(sample())",
        sample=sample,
        annotation="str",
    )

    # The materialized return can be absent from the dictionary, even when
    # this particular trusted implementation returns a key that is present.
    _assert_refused_before_approval(source, package, monkeypatch, kind="KeyError")

    if sample == "missing":
        with pytest.raises(KeyError, match="missing"):
            _execute(source, package)
    else:
        _execute(source, package)
    effects = importlib.import_module(f"{package.name}.effects")
    assert (["first"] if sample == "missing" else ["first", "second"]) == effects.EVENTS


@pytest.mark.parametrize("trusted", [False, True], ids=["inlined", "trusted"])
def test_nested_predicate_assertion_is_refused_before_the_first_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trusted: bool
) -> None:
    source, package = _program(
        tmp_path,
        _condition("requires", "positive(value)"),
        trusted=trusted,
        operation="check(-1)",
        helper=(
            "def positive(value: int) -> bool:\n    assert value > 0\n    return True\n"
        ),
    )

    _assert_refused_before_approval(
        source,
        package,
        monkeypatch,
        diagnostic="condition predicate has an unchecked proof obligation",
    )

    with pytest.raises(AssertionError):
        _execute(source, package)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["first"]


def test_proved_nested_predicate_assertion_remains_supported(tmp_path: Path) -> None:
    source, package = _program(
        tmp_path,
        _condition("requires", "positive(value)"),
        trusted=True,
        operation="check(1)",
        helper=(
            "def positive(value: int) -> bool:\n    assert value > 0\n    return True\n"
        ),
    )

    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
    _execute(source, package)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["first", "second"]


def test_direct_body_exception_is_still_a_failed_proof(tmp_path: Path) -> None:
    source, package = _program(
        tmp_path, _condition("requires", "value == 3"), trusted=True, operation="len(3)"
    )

    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "TypeError" in str(proofs[0].proof)
    with pytest.raises(ProgramError, match="TypeError"):
        _execute(source, package)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["first"]
