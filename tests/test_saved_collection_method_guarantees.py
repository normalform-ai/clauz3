"""Stale collection receivers must not hide effects from native guarantees."""

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

SAVED_DIAGNOSTIC = "methods on mutable receivers require an immediate call"
PARAMETER_DIAGNOSTIC = "mutating a function parameter or its alias is not supported"


def _program(
    tmp_path: Path, body: str, *, helpers: str = "", maximum: int = 1
) -> tuple[str, Path]:
    package = tmp_path / f"saved_collection_{uuid4().hex}"
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
        "def within_budget() -> ContractSpec:\n"
        f"    return effect('record').count() <= {maximum}\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import within_budget\n"
        + helpers
        + "@clauz3.guarantee(within_budget())\n"
        "def main() -> None:\n" + indent(body, "    ")
    )
    return source, package


def _collection_body(method: str, *, saved: bool) -> str:
    if method == "copy":
        setup = "targets = ['a']\n"
        mutate = "targets.append('b')\n"
        argument = ""
        query = "len({call}) == 2"
    elif method == "get":
        setup = "targets = {'size': 0}\n"
        mutate = "targets['size'] = 2\n"
        argument = "'size', 0"
        query = "{call} == 2"
    else:
        assert method == "union"
        setup = "targets = {'a'}\n"
        mutate = "targets.add('b')\n"
        argument = "{'a'}"
        query = "'b' in {call}"
    save = f"snapshot = targets.{method}\n" if saved else ""
    call = f"snapshot({argument})" if saved else f"targets.{method}({argument})"
    return (
        setup
        + save
        + mutate
        + f"if {query.format(call=call)}:\n    record('a')\n    record('b')\n"
    )


@pytest.mark.parametrize("method", ["copy", "get", "union"])
def test_saved_pure_collection_method_refused_before_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    source, package = _program(tmp_path, _collection_body(method, saved=True))
    _assert_refused_before_approval(source, package, monkeypatch, SAVED_DIAGNOSTIC)
    _assert_actual_records(source, package)


@pytest.mark.parametrize("method", ["copy", "get", "union"])
@pytest.mark.parametrize("maximum", [1, 2], ids=["violated", "sufficient"])
def test_immediate_collection_queries_preserve_guarantee_results(
    tmp_path: Path, method: str, maximum: int
) -> None:
    source, package = _program(
        tmp_path, _collection_body(method, saved=False), maximum=maximum
    )
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    expected = Conclusion.FAIL if maximum == 1 else Conclusion.OK
    assert [proof.proof.conclusion for proof in proofs] == [expected]
    _assert_actual_records(source, package)


@pytest.mark.parametrize(
    "mutation",
    [
        "items.append('b')\n",
        "alias = items\nalias.append('b')\n",
        "alias = items\nif True:\n    chain = alias\n    chain.append('b')\n",
        "alias = identity(items)\nalias.append('b')\n",
    ],
    ids=["parameter", "alias", "conditional-alias-chain", "returned-alias"],
)
def test_helper_parameter_mutation_refused_before_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    helpers = (
        "def identity(items: list[str]) -> list[str]:\n"
        "    return items\n"
        "def change(items: list[str]) -> None:\n" + indent(mutation, "    ")
    )
    body = (
        "targets = ['a']\n"
        "change(targets)\n"
        "if len(targets) == 2:\n"
        "    record('a')\n"
        "    record('b')\n"
    )
    source, package = _program(tmp_path, body, helpers=helpers)
    _assert_refused_before_approval(source, package, monkeypatch, PARAMETER_DIAGNOSTIC)
    _assert_actual_records(source, package)


@pytest.mark.parametrize("maximum", [1, 2], ids=["violated", "sufficient"])
def test_helper_local_collection_mutation_preserves_guarantee_results(
    tmp_path: Path, maximum: int
) -> None:
    helpers = (
        "def count_targets() -> int:\n"
        "    targets = ['a']\n"
        "    targets.append('b')\n"
        "    return len(targets)\n"
    )
    body = "if count_targets() == 2:\n    record('a')\n    record('b')\n"
    source, package = _program(tmp_path, body, helpers=helpers, maximum=maximum)
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    expected = Conclusion.FAIL if maximum == 1 else Conclusion.OK
    assert [proof.proof.conclusion for proof in proofs] == [expected]
    _assert_actual_records(source, package)


def _assert_refused_before_approval(
    source: str,
    package: Path,
    monkeypatch: pytest.MonkeyPatch,
    diagnostic: str,
    *,
    expected: Conclusion = Conclusion.SKIP,
) -> None:
    proofs = prove_text(source, trusted_roots=[package], import_paths=[package.parent])
    assert [proof.proof.conclusion for proof in proofs] == [expected]
    assert diagnostic in str(proofs[0].proof)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.__file__ is not None
    assert Path(effects.__file__).resolve() == (package / "effects.py").resolve()
    assert effects.EVENTS == []
    approve = Mock(side_effect=AssertionError("approval must not be reached"))
    executor = Mock(side_effect=AssertionError("execution must not be reached"))
    monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
    monkeypatch.setattr("clauz3.runner._execute_source", executor)
    with pytest.raises(RunProofError) as exc_info:
        run_source(
            source,
            source_name="plan.py",
            trusted_roots=[package],
            import_roots=[package.parent],
            approval_service_url="http://approval.invalid",
        )
    assert [proof.proof.conclusion for proof in exc_info.value.proofs] == [expected]
    assert diagnostic in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    executor.assert_not_called()
    assert effects.EVENTS == []


def _assert_actual_records(source: str, package: Path) -> None:
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == ["a", "b"]
    assert outcome.effects == (
        (f"{package.name}.effects.record", ("a",), {}),
        (f"{package.name}.effects.record", ("b",), {}),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "push = lambda: targets.append('b')\npush()\n",
        "alias = targets\npush = lambda: alias.append('b')\npush()\n",
        "unused = [targets.append('b') for ignored in [0]]\n",
        "unused = [item.append('b') for item in [targets]]\n",
    ],
    ids=[
        "lambda-capture",
        "lambda-captured-alias",
        "comprehension-capture",
        "iterator-alias",
    ],
)
def test_captured_receiver_mutation_refused_before_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    body = (
        "targets = ['a']\n"
        + mutation
        + "if len(targets) == 2:\n    record('a')\n    record('b')\n"
    )
    source, package = _program(tmp_path, body)
    _assert_refused_before_approval(
        source, package, monkeypatch, "mutating a captured collection is not supported"
    )
    _assert_actual_records(source, package)


@pytest.mark.parametrize("alias", [False, True], ids=["parameter", "parameter-alias"])
def test_helper_parameter_item_assignment_refused_before_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: bool
) -> None:
    mutation = "alias = items\nalias['size'] = 2\n" if alias else "items['size'] = 2\n"
    helpers = "def change(items: dict[str, int]) -> None:\n" + indent(mutation, "    ")
    body = (
        "targets = {'size': 0}\nchange(targets)\n"
        "if targets['size'] == 2:\n    record('a')\n    record('b')\n"
    )
    source, package = _program(tmp_path, body, helpers=helpers)
    _assert_refused_before_approval(source, package, monkeypatch, PARAMETER_DIAGNOSTIC)
    _assert_actual_records(source, package)


@pytest.mark.parametrize("maximum", [1, 2], ids=["violated", "sufficient"])
def test_helper_local_item_assignment_preserves_guarantee_results(
    tmp_path: Path, maximum: int
) -> None:
    helpers = (
        "def count_targets() -> int:\n"
        "    targets = {'size': 0}\n"
        "    targets['size'] = 2\n"
        "    return targets['size']\n"
    )
    body = "if count_targets() == 2:\n    record('a')\n    record('b')\n"
    source, package = _program(tmp_path, body, helpers=helpers, maximum=maximum)
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    expected = Conclusion.FAIL if maximum == 1 else Conclusion.OK
    assert [proof.proof.conclusion for proof in proofs] == [expected]
    _assert_actual_records(source, package)


@pytest.mark.parametrize(
    "expression",
    [
        "targets.pop('b') if False else 0",
        "0 if True else targets.pop('b')",
        "False and targets.pop('b')",
        "True or targets.pop('b')",
        "2 < 1 < targets.pop('b')",
    ],
    ids=["ternary-then", "ternary-else", "and-rhs", "or-rhs", "comparison-tail"],
)
def test_statically_skipped_mutation_cannot_hide_records_or_reach_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expression: str
) -> None:
    body = (
        f"targets = {{'b': 1}}\nunused = {expression}\n"
        "if 'b' in targets:\n    record('a')\n    record('b')\n"
    )
    source, package = _program(tmp_path, body)
    _assert_refused_before_approval(
        source,
        package,
        monkeypatch,
        "guarantee",
        expected=Conclusion.FAIL,
    )
    _assert_actual_records(source, package)


@pytest.mark.parametrize("maximum", [1, 2], ids=["violated", "sufficient"])
def test_unconditional_named_pop_preserves_guarantee_results(
    tmp_path: Path, maximum: int
) -> None:
    body = (
        "targets = {'b': 1}\nremoved = targets.pop('b')\n"
        "if removed == 1:\n    record('a')\n    record('b')\n"
    )
    source, package = _program(tmp_path, body, maximum=maximum)
    proofs = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])
    assert [proof.proof.conclusion for proof in proofs] == [
        Conclusion.FAIL if maximum == 1 else Conclusion.OK
    ]
    _assert_actual_records(source, package)
