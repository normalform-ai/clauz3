"""Method bindings refuse collection observations and writes they cannot track."""

from textwrap import indent

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text

SAVED_ERROR = "methods on mutable receivers require an immediate call"
PARAMETER_ERROR = "mutating a function parameter or its alias is not supported"


def _source(body: str) -> str:
    return "def main() -> None:\n" + indent(body, "    ") + "\n"


@pytest.mark.parametrize(
    "body",
    [
        "values = [1]\nsaved = values.copy\nvalues.append(2)\nassert len(saved()) == 1",
        "values = [1]\nsaved = values.count\nvalues.append(1)\nassert saved(1) == 1",
        "values = {'x': 1}\nsaved = values.get\nvalues.clear()\n"
        "assert saved('x', 0) == 1",
        "values = {1}\nsaved = values.union\nvalues.add(2)\n"
        "assert len(saved({1})) == 1",
        "values = []\nsaved = values.copy\nassert True",
        "values = {}\nsaved = values.get\nassert True",
        "values = ([1],)\nsaved = values.count\nassert True",
    ],
    ids=[
        "list-copy",
        "list-count",
        "dict-get",
        "set-union",
        "empty-list",
        "empty-dict",
        "nested-mutable-tuple",
    ],
)
def test_saved_collection_methods_refuse_stale_receiver_snapshots(body: str) -> None:
    results = prove_text(_source(body))

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert SAVED_ERROR in str(results[0].proof.error)


@pytest.mark.parametrize(
    "helper_body",
    [
        "items.append(2)",
        "alias = items\nalias.append(2)",
        "first = items\nsecond = first\nsecond.append(2)",
        "if True:\n    alias = items\n    alias.append(2)",
        "alias = identity(items)\nalias.append(2)",
        "alias = items.copy()\nalias.append(2)",
        "items = []\nitems.append(2)",
    ],
    ids=[
        "parameter",
        "alias",
        "chain",
        "conditional",
        "returned-alias",
        "copy",
        "reassignment",
    ],
)
def test_parameter_dependent_mutations_are_refused_conservatively(
    helper_body: str,
) -> None:
    source = (
        "def identity(items: list[int]) -> list[int]:\n    return items\n"
        "def push(items: list[int]) -> None:\n"
        + indent(helper_body, "    ")
        + "\n"
        + _source("values = [1]\npush(values)\nassert len(values) == 1")
    )

    results = prove_text(source, target="main")

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert PARAMETER_ERROR in str(results[0].proof.error)


@pytest.mark.parametrize("oversized", [False, True], ids=["cycle", "budget"])
def test_unsettled_mutation_dependencies_fail_closed(oversized: bool) -> None:
    if oversized:
        assignments = ["alias0 = [1]"] + [
            f"alias{index} = alias{index - 1}" for index in range(1, 270)
        ]
        body = "\n".join([*assignments, "alias269.append(2)"])
        reason = "binding budget exceeded"
    else:
        body = "values = [1]\nalias = values\nvalues = alias\nvalues.append(2)"
        reason = "cyclic binding"

    results = prove_text(_source(body + "\nassert True"))

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "unsupported mutation receiver binding" in str(results[0].proof.error)
    assert reason in str(results[0].proof.error)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("'abc'.startswith", "saved('a')"),
        ("(1).conjugate", "saved() == 1"),
        ("(1.5).is_integer", "not saved()"),
        ("(1, 2).count", "saved(1) == 1"),
        ("().count", "saved(1) == 0"),
    ],
    ids=["str", "int", "float", "tuple", "empty-tuple"],
)
def test_saved_immutable_methods_remain_supported(
    expression: str, expected: str
) -> None:
    source = _source(f"saved = {expression}\nassert {expected}")

    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]
    assert execute(source, source_name="plan.py", policy=build_policy()).effects == ()


@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_fresh_local_mutation_in_helper_remains_supported(correct: bool) -> None:
    source = (
        "def make(value: int) -> int:\n"
        "    items = [1]\n"
        "    items.append(value)\n"
        "    return len(items)\n"
        + _source(f"assert make(2) {'==' if correct else '!='} 2")
    )

    results = prove_text(source, target="main")

    assert [result.proof.conclusion for result in results] == [
        Conclusion.OK if correct else Conclusion.FAIL
    ]
    if correct:
        assert (
            execute(source, source_name="plan.py", policy=build_policy()).effects == ()
        )


@pytest.mark.parametrize(
    ("expression", "expected", "diagnostic"),
    [
        ("read + 1", Conclusion.FAIL, "TypeError"),
        ("len(read)", Conclusion.FAIL, "TypeError"),
        ("abs(read)", Conclusion.FAIL, "TypeError"),
        ("read[0]", Conclusion.FAIL, "TypeError"),
        ("int(read)", Conclusion.SKIP, "cannot convert lambda to int"),
        ("float(read)", Conclusion.SKIP, "cannot convert lambda to float"),
    ],
)
def test_lambda_type_diagnostics_do_not_leak_attribute_errors(
    expression: str, expected: Conclusion, diagnostic: str
) -> None:
    results = prove_text(
        _source(f"read = lambda value: value\n{expression}\nassert True")
    )

    assert [result.proof.conclusion for result in results] == [expected]
    assert diagnostic in str(results[0].proof)
    assert "lambda" in str(results[0].proof)


@pytest.mark.parametrize(
    "body",
    [
        "values = [1]\nchange = lambda: values.append(2)\nchange()\n"
        "assert len(values) == 1",
        "values = {'x': 1}\nchange = lambda: values.clear()\nchange()\n"
        "assert len(values) == 1",
        "values = {1}\nalias = values\nchange = lambda: alias.add(2)\nchange()\n"
        "assert len(values) == 1",
        "values = [1]\nunused = [values.append(2) for item in [0]]\n"
        "assert len(values) == 1",
        "values = [1]\nunused = [item.append(2) for item in [values]]\n"
        "assert len(values) == 1",
    ],
    ids=[
        "lambda-list",
        "lambda-dict",
        "lambda-set-alias",
        "comprehension",
        "iterator-alias",
    ],
)
def test_captured_mutation_cannot_discard_receiver_updates(body: str) -> None:
    results = prove_text(_source(body))

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "mutating a captured collection is not supported" in str(results[0].proof)


@pytest.mark.parametrize(
    "helper_body",
    [
        "items['x'] = 2",
        "alias = items\nalias['x'] = 2",
        "alias = identity(items)\nalias['x'] = 2",
        "if True:\n    alias = items\n    alias['x'] = 2",
    ],
    ids=["parameter", "alias", "returned-alias", "conditional-alias"],
)
def test_parameter_item_assignment_uses_the_same_binding_gate(helper_body: str) -> None:
    source = (
        "def identity(items: dict[str, int]) -> dict[str, int]:\n    return items\n"
        "def change(items: dict[str, int]) -> None:\n"
        + indent(helper_body, "    ")
        + "\n"
        + _source("values = {'x': 1}\nchange(values)\nassert values['x'] == 1")
    )
    results = prove_text(source, target="main")

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert PARAMETER_ERROR in str(results[0].proof)


@pytest.mark.parametrize("receiver", ["values.copy()", "nested[0]"])
def test_computed_item_assignment_receivers_are_refused(receiver: str) -> None:
    source = _source(
        f"values = {{'x': 1}}\nnested = [values]\n{receiver}['x'] = 2\nassert True"
    )
    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "subscript assignment requires a named receiver" in str(results[0].proof)


@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_fresh_local_item_assignment_in_a_helper_remains_supported(
    correct: bool,
) -> None:
    source = (
        "def make(value: int) -> int:\n"
        "    items = {'x': 1}\n"
        "    items['x'] = value\n"
        "    return items['x']\n"
        + _source(f"assert make(2) {'==' if correct else '!='} 2")
    )
    results = prove_text(source, target="main")

    assert [result.proof.conclusion for result in results] == [
        Conclusion.OK if correct else Conclusion.FAIL
    ]
    if correct:
        assert (
            execute(source, source_name="plan.py", policy=build_policy()).effects == ()
        )


def test_same_name_literal_loop_binding_refuses_before_the_ownership_gate() -> None:
    source = _source(
        "values = [0]\nfor values in [values, values]:\n    values.append(1)\n"
        "assert len(values) == 2"
    )
    proofs = prove_text(source)
    assert [item.proof.conclusion for item in proofs] == [Conclusion.SKIP]
    assert "cyclic binding" in str(proofs[0].proof)
