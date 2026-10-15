"""Discarded comprehension mutations cannot hide a native trusted effect."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import UnsupportedNode, execute
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source


def _refusal(body: str) -> str:
    if "add = values.append" in body or "__call__" in body:
        return "mutating method requires a direct call on a named receiver"
    if "lambda items" in body:
        return "mutating a function parameter or its alias is not supported"
    return "mutating a captured collection is not supported"


_ELEMENT = "values = [0]\n_ = [values.append(1) for _ in [0]]"
_FILTER = "values = [0]\n_ = [0 for _ in [0] if values.append(1)]"

_MUTATIONS = [
    pytest.param(_ELEMENT, "len(values) == 2", id="element"),
    pytest.param(_FILTER, "len(values) == 2", id="filter"),
    pytest.param(
        "values = [0]\nadd = values.append\n_ = [add(1) for _ in [0]]",
        "len(values) == 2",
        id="bound-alias",
    ),
    pytest.param(
        "values = [0]\n_ = [[values.append(1) for _ in [0]] for _ in [0]]",
        "len(values) == 2",
        id="nested-comprehension",
    ),
    pytest.param(
        "values = {0}\n_ = [values.add(1) for _ in [0]]",
        "1 in values",
        id="set-add",
    ),
    pytest.param(
        "values = {'x': 0}\n_ = [values.clear() for _ in [0]]",
        "'x' not in values",
        id="dict-clear",
    ),
    pytest.param(
        "values = [0]\ncall = lambda items: items.append(1)\n"
        "_ = [call(values) for _ in [0]]",
        "len(values) == 2",
        id="parameter-lambda",
    ),
]


def _program(tmp_path: Path, body: str, guard: str) -> tuple[str, Path]:
    package = tmp_path / f"comprehension_mutation_{uuid4().hex}"
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
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import silent\n"
        "@clauz3.guarantee(silent())\n"
        "def main() -> None:\n"
        + "".join(f"    {line}\n" for line in body.splitlines())
        + f"    if {guard}:\n"
        + "        record('one')\n"
    )
    return source, package


def _check_runtime(source: str, package: Path, *, recorded: bool) -> None:
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    expected_effects: tuple[tuple[str, tuple[str, ...], dict[str, object]], ...] = (
        ((f"{package.name}.effects.record", ("one",), {}),) if recorded else ()
    )
    assert outcome.effects == expected_effects
    effects = importlib.import_module(f"{package.name}.effects")
    recorded_values = effects.EVENTS
    expected_records = ["one"] if recorded else []
    assert recorded_values == expected_records


@pytest.mark.parametrize(("body", "guard"), _MUTATIONS)
def test_discarded_mutation_cannot_prove_a_false_silent_guarantee(
    tmp_path: Path, body: str, guard: str
) -> None:
    source, package = _program(tmp_path, body, guard)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert _refusal(source) in str(results[0].proof)
    # This is an actual interpreter execution of the formerly certified
    # source, using only an in-memory recording effect.
    _check_runtime(source, package, recorded=True)


@pytest.mark.parametrize("body", [_ELEMENT, _FILTER], ids=["element", "filter"])
def test_comprehension_mutation_stops_before_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    source, package = _program(tmp_path, body, "len(values) == 2")
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
    assert _refusal(source) in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute_source.assert_not_called()
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


def test_wrapped_mutator_is_refused_by_proof_and_interpreter(tmp_path: Path) -> None:
    source, package = _program(
        tmp_path,
        "values = [0]\n_ = [values.append.__call__(1) for _ in [0]]",
        "len(values) == 2",
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert _refusal(source) in str(results[0].proof)
    # Unlike the ordinary bound alias above, this spelling was already
    # outside the interpreter's supported attribute surface.
    with pytest.raises(UnsupportedNode, match="attribute access on BoundMethod"):
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
            import_roots=[tmp_path],
        )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


@pytest.mark.parametrize(
    "mutation",
    [
        "values.append(1)",
        "_ = [value for value in [values.pop('key')]]",
        "_ = [len(values) for _ in [0]]\nvalues.append(1)",
    ],
    ids=["direct", "outer-iterable", "after-comprehension"],
)
def test_mutation_outside_comprehension_body_still_refutes_guarantee(
    tmp_path: Path, mutation: str
) -> None:
    initial = "{'key': 1}" if "values.pop(" in mutation else "[0]"
    source, package = _program(
        tmp_path,
        f"values = {initial}\n{mutation}",
        "len(values) == 0" if "values.pop('key')" in mutation else "len(values) == 2",
    )

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    expected = Conclusion.SKIP if "values.pop(" in mutation else Conclusion.FAIL
    assert [result.proof.conclusion for result in results] == [expected]
    diagnostic = (
        "mutating a captured collection" if expected is Conclusion.SKIP else "guarantee"
    )
    assert diagnostic in str(results[0].proof)
    _check_runtime(source, package, recorded=True)


@pytest.mark.parametrize(
    "body",
    [
        "result = [value + 1 for value in [0, 1]]\nassert result == [1, 2]",
        "result = [value + 1 for value in [0, 1, 2] if value > 0]\n"
        "assert result == [2, 3]",
        "values = [0]\nresult = [len(values) for _ in [0]]\nassert result == [1]",
        "values = []\nresult = [len(values + [1]) for _ in [0]]\n"
        "assert result == [1]\nassert len(values) == 0",
    ],
    ids=["arithmetic", "filter", "captured-len", "empty-list-concatenation"],
)
def test_pure_comprehensions_preserve_silent_guarantee(
    tmp_path: Path, body: str
) -> None:
    source, package = _program(tmp_path, body, "False")

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]
    _check_runtime(source, package, recorded=False)
