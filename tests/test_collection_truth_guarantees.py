"""Collection emptiness must not discard reachable trusted effects."""

import importlib
from pathlib import Path
from textwrap import indent
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.interp.errors import ProgramError
from clauz3.prover import prove_text
from clauz3.runner import RunProofError, run_source


def _program(
    tmp_path: Path,
    body: str,
    *,
    populated: bool = False,
    bounded_query: bool = False,
    guarantee: str = "silent()",
) -> tuple[str, Path]:
    package = tmp_path / f"collection_truth_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    post = (
        f"@clauz3.ensures(lambda result: len(result) == {int(populated)})\n"
        if bounded_query
        else ""
    )
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "class Item(clauz3.Row):\n    value: int\n"
        "@clauz3.trusted\n"
        + post
        + "def read_rows() -> list[Item]:\n"
        + ("    return [Item(value=1)]\n" if populated else "    return []\n")
        + "@clauz3.trusted\n"
        "def read_set() -> set[int]:\n"
        + ("    return {1}\n" if populated else "    return set()\n")
        + "@clauz3.trusted\n"
        "def record(value: str) -> None:\n    EVENTS.append(value)\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n    return effect('record').empty()\n"
        "@contract\n"
        "def twice() -> ContractSpec:\n    return effect('record').count() >= 2\n"
    )
    source = (
        "import clauz3\nimport math\n"
        f"from {package.name}.effects import read_rows, read_set, record\n"
        f"from {package.name}.contracts import silent, twice\n"
        "def quiet() -> None:\n    pass\n"
        f"@clauz3.guarantee({guarantee})\n"
        "def main() -> None:\n" + indent(body, "    ") + "\n"
    )
    return source, package


def _runtime(source: str, package: Path, expected: list[str]) -> None:
    module = importlib.import_module(f"{package.name}.effects")
    assert module.__file__ is not None
    assert Path(module.__file__).resolve() == (package / "effects.py").resolve()
    assert module.EVENTS == []
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    assert expected == module.EVENTS
    assert [
        args[0]
        for name, args, _kwargs in outcome.effects
        if name == f"{package.name}.effects.record"
    ] == expected


def _refused(
    source: str,
    package: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected: Conclusion,
) -> None:
    approval = Mock(side_effect=AssertionError("approval must not be reached"))
    execution = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approval)
    monkeypatch.setattr("clauz3.runner._execute_source", execution)
    with pytest.raises(RunProofError) as caught:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[package.parent],
            approval_service_url="http://approval.invalid",
        )
    assert [p.proof.conclusion for p in caught.value.proofs] == [expected]
    approval.assert_not_called()
    execution.assert_not_called()


@pytest.mark.parametrize("container", ["read_rows()", "read_set()"])
@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize(
    "branch",
    [
        "unused = quiet() if values else record('one')",
        "unused = record('one') if not values else quiet()",
        "if not values:\n    record('one')",
        "if values:\n    record('one')",
    ],
)
def test_symbolic_collection_truth_preserves_both_possible_effect_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    container: str,
    populated: bool,
    branch: str,
) -> None:
    # Both arms return None: keep this a collection-truth/effect test rather
    # than triggering the independent mixed-result-type refusal.
    source, package = _program(
        tmp_path, f"values = {container}\n{branch}", populated=populated
    )
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [p.proof.conclusion for p in proofs] == [Conclusion.FAIL]
    _refused(source, package, monkeypatch, Conclusion.FAIL)
    records = populated if branch.startswith("if values:") else not populated
    _runtime(source, package, ["one"] if records else [])


@pytest.mark.parametrize("populated", [False, True])
def test_query_postcondition_can_determine_truth(
    tmp_path: Path, populated: bool
) -> None:
    source, package = _program(
        tmp_path,
        "values = read_rows()\nunused = quiet() if values else record('one')",
        populated=populated,
        bounded_query=True,
    )
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [p.proof.conclusion for p in proofs] == [
        Conclusion.OK if populated else Conclusion.FAIL
    ]
    _runtime(source, package, [] if populated else ["one"])


@pytest.mark.parametrize("value", ["set()", "{1}.difference({1})", "{1}"])
def test_concrete_set_truth_selects_the_runtime_arm(tmp_path: Path, value: str) -> None:
    source, package = _program(
        tmp_path, f"values = {value}\nunused = quiet() if values else record('one')"
    )
    populated = value == "{1}"
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [p.proof.conclusion for p in proofs] == [
        Conclusion.OK if populated else Conclusion.FAIL
    ]
    _runtime(source, package, [] if populated else ["one"])


@pytest.mark.parametrize("value", ["'12'", "'not a number'", "[1]"])
def test_trunc_cannot_prove_effects_after_a_non_numeric_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    source, package = _program(
        tmp_path,
        f"record('first')\nunused = math.trunc({value})\nrecord('second')",
        guarantee="twice()",
    )
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [p.proof.conclusion for p in proofs] == [Conclusion.SKIP]
    assert "math.trunc requires an int, bool, or float" in str(proofs[0].proof)
    _refused(source, package, monkeypatch, Conclusion.SKIP)
    with pytest.raises(ProgramError, match="TypeError: math.trunc") as caught:
        execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
            import_roots=[tmp_path],
        )
    assert isinstance(caught.value.__cause__, TypeError)
    assert importlib.import_module(f"{package.name}.effects").EVENTS == ["first"]
