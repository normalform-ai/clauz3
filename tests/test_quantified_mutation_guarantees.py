"""Quantified loops must not discard collection updates hiding native effects."""

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

_APPEND = "values = [0]\nfor row in query():\n    values.append(row.id)"
_SETITEM = "values = {0: 0}\nfor row in query():\n    values[row.id] = 1"
_BOUND_ALIAS = "values = [0]\nadd = values.append\nfor row in query():\n    add(row.id)"
_MUTATIONS = [
    pytest.param(_APPEND, "len(values) == 2", "", id="list-append"),
    pytest.param(_SETITEM, "1 in values", "", id="dict-subscript"),
    pytest.param(
        _BOUND_ALIAS,
        "len(values) == 2",
        "",
        id="bound-alias",
    ),
    pytest.param(
        "values = [0]\nfor row in query():\n"
        "    if row.id == 1:\n        values.append(row.id)",
        "len(values) == 2",
        "",
        id="branch-child",
    ),
    pytest.param(
        "values = [0]\nfor row in query():\n"
        "    for item in [1]:\n        values.append(item)",
        "len(values) == 2",
        "",
        id="nested-literal-loop",
    ),
    pytest.param(
        "values = [0]\nfor row in query():\n"
        "    for other in query():\n        values.append(other.id)",
        "len(values) == 2",
        "",
        id="nested-quantified-loop",
    ),
    pytest.param(
        "values = [0]\nadd = lambda items, value: items.append(value)\n"
        "for row in query():\n    add(values, row.id)",
        "len(values) == 2",
        "",
        id="parameter-lambda",
    ),
    pytest.param(
        "values = [0]\nfor row in query():\n    add(values, row.id)",
        "len(values) == 2",
        "def add(items: list[int], value: int) -> None:\n    items.append(value)\n",
        id="helper-method",
    ),
    pytest.param(
        "values = {0: 0}\nfor row in query():\n    put(values, row.id)",
        "1 in values",
        "def put(items: dict[int, int], key: int) -> None:\n    items[key] = 1\n",
        id="helper-subscript",
    ),
    pytest.param(
        "values = {0}\nfor row in query():\n    values.add(row.id)",
        "1 in values",
        "",
        id="set-add",
    ),
    pytest.param(
        "values = {0: 0}\nfor row in query():\n    values.clear()",
        "0 not in values",
        "",
        id="dict-clear",
    ),
]


def _program(
    tmp_path: Path, body: str, guard: str, helpers: str = ""
) -> tuple[str, Path]:
    package = tmp_path / f"quantified_mutation_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "rows.py").write_text(
        "import clauz3\nclass Item(clauz3.Row):\n    id: int\n"
    )
    (package / "effects.py").write_text(
        "import clauz3\n"
        f"from {package.name}.rows import Item\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
        "@clauz3.trusted\n"
        "@clauz3.ensures(lambda result: len(result) == 1)\n"
        "def query() -> list[Item]:\n"
        "    return [Item(id=1)]\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def silent() -> ContractSpec:\n"
        "    return effect('record').empty()\n"
    )
    source = (
        "import clauz3\n"
        f"from {package.name}.effects import record, query\n"
        f"from {package.name}.contracts import silent\n"
        + helpers
        + "\n@clauz3.guarantee(silent())\ndef main() -> None:\n"
        + "".join(f"    {line}\n" for line in body.splitlines())
        + f"    if {guard}:\n        record('one')\n"
    )
    return source, package


def _check_runtime(source: str, package: Path, *, recorded: bool) -> None:
    outcome = execute(
        source,
        source_name="plan.py",
        policy=build_policy(trusted_roots=[package], import_roots=[package.parent]),
        import_roots=[package.parent],
    )
    effects = importlib.import_module(f"{package.name}.effects")
    actual_events = effects.EVENTS
    expected_events = ["one"] if recorded else []
    assert actual_events == expected_events
    records = [
        effect
        for effect in outcome.effects
        if effect[0] == f"{package.name}.effects.record"
    ]
    assert records == (
        [(f"{package.name}.effects.record", ("one",), {})] if recorded else []
    )


@pytest.mark.parametrize(("body", "guard", "helpers"), _MUTATIONS)
def test_quantified_mutation_cannot_certify_a_false_silent_guarantee(
    tmp_path: Path, body: str, guard: str, helpers: str
) -> None:
    source, package = _program(tmp_path, body, guard, helpers)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    # The binding gates can refuse before method dispatch reaches the loop
    # guard. Every shape still has the same exact SKIP classification.
    if body == _BOUND_ALIAS:
        diagnostic = "mutating method requires a direct call on a named receiver"
    elif "add(values, row.id)" in body:
        diagnostic = "mutating a function parameter or its alias is not supported"
    else:
        diagnostic = "quantified loop"
    assert diagnostic in str(results[0].proof)
    # The deterministic nonempty query exercises an actual iteration and
    # exposes the formerly hidden event, using only an in-memory recorder.
    _check_runtime(source, package, recorded=True)


@pytest.mark.parametrize(
    ("body", "guard"),
    [(_APPEND, "len(values) == 2"), (_SETITEM, "1 in values")],
    ids=["method", "subscript"],
)
def test_quantified_mutation_stops_before_approval_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, guard: str
) -> None:
    source, package = _program(tmp_path, body, guard)
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
    assert "quantified loop" in str(exc_info.value.proofs[0].proof)
    approve.assert_not_called()
    execute_source.assert_not_called()
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.EVENTS == []


@pytest.mark.parametrize(
    ("body", "guard"),
    [
        ("values = [0]\nfor item in [1]:\n    values.append(item)", "len(values) == 2"),
        ("values = {0: 0}\nfor item in [1]:\n    values[item] = 1", "1 in values"),
        (
            "values = [0]\nfor row in query():\n    assert row.id == row.id\n"
            "values.append(1)",
            "len(values) == 2",
        ),
    ],
    ids=["literal-method", "literal-subscript", "after-quantified-loop"],
)
def test_mutation_outside_quantified_body_still_refutes_guarantee(
    tmp_path: Path, body: str, guard: str
) -> None:
    source, package = _program(tmp_path, body, guard)

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]
    _check_runtime(source, package, recorded=True)


@pytest.mark.parametrize(
    "body",
    [
        "for row in query():\n    built = [] + [row.id]\n    assert len(built) == 1",
        "for row in query():\n    built = [row.id]\n    assert len(built) == 1",
        "for row in query():\n    built = {row.id: 1}\n    assert row.id in built",
    ],
    ids=["empty-list-concatenation", "list-construction", "dict-construction"],
)
def test_pure_row_computation_remains_supported(tmp_path: Path, body: str) -> None:
    source, package = _program(tmp_path, body, "False")

    results = prove_text(source, trusted_roots=[package], import_paths=[tmp_path])

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]
    _check_runtime(source, package, recorded=False)
