"""Lambda capture restrictions preserve lexical identity across call scopes."""

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_text

_PROGRAMS = {
    "forwarded-capture": (
        "def invoke(fn) -> int:\n"
        "    value = 99\n"
        "    return fn()\n"
        "def forward(fn) -> int:\n"
        "    return invoke(fn)\n"
        "def main() -> None:\n"
        "    value = 7\n"
        "    read = lambda: value\n"
        "    assert forward(read) == 7\n"
    ),
    "stateless-callback": (
        "def invoke(fn) -> int:\n"
        "    return fn()\n"
        "def main() -> None:\n"
        "    read = lambda: 7\n"
        "    assert invoke(read) == 7\n"
    ),
    "parameter-shadow": (
        "def invoke(fn, value: int) -> int:\n"
        "    return fn(value)\n"
        "def main() -> None:\n"
        "    value = 99\n"
        "    step = lambda value: value + 1\n"
        "    assert invoke(step, 3) == 4\n"
    ),
    "nested-capture": (
        "def main() -> None:\n"
        "    value = 1\n"
        "    read = lambda: (lambda: value)()\n"
        "    if True:\n"
        "        value = 2\n"
        "        assert read() == 1\n"
    ),
    "nested-parameter": (
        "def invoke(fn) -> int:\n"
        "    return fn()\n"
        "def main() -> None:\n"
        "    value = 99\n"
        "    read = lambda: (lambda value: value)(3)\n"
        "    assert invoke(read) == 3\n"
    ),
    "outer-lambda-parameter": (
        "def invoke(fn, value: int) -> int:\n"
        "    return fn(value)\n"
        "def main() -> None:\n"
        "    read = lambda value: (lambda: value)()\n"
        "    assert invoke(read, 3) == 3\n"
    ),
    "separate-activations": (
        "def make(value: int):\n"
        "    return lambda: value\n"
        "def main() -> None:\n"
        "    first = make(1)\n"
        "    second = make(2)\n"
        "    assert first() == 1\n"
        "    assert second() == 2\n"
    ),
    "captured-comprehension": (
        "def main() -> None:\n"
        "    offset = 1\n"
        "    step = lambda value: value + offset\n"
        "    values = [step(value) for value in [1, 2]]\n"
        "    assert values == [2, 3]\n"
    ),
    "comprehension-variable": (
        "def invoke(fn):\n"
        "    return fn()\n"
        "def main() -> None:\n"
        "    value = 99\n"
        "    read = lambda: [value for value in [1, 2]]\n"
        "    assert invoke(read) == [1, 2]\n"
    ),
    "comprehension-first-iterable": (
        "def invoke(fn):\n"
        "    values = [2]\n"
        "    return fn()\n"
        "def main() -> None:\n"
        "    values = [1]\n"
        "    read = lambda: [values for values in values]\n"
        "    assert invoke(read) == [1]\n"
    ),
    "late-bound-capture": (
        "def main() -> None:\n"
        "    read = lambda: value\n"
        "    value = 1\n"
        "    if True:\n"
        "        value = 2\n"
        "        assert read() == 1\n"
    ),
    "captured-callable": (
        "def main() -> None:\n"
        "    value = 1\n"
        "    read = lambda: value\n"
        "    forward = lambda: read()\n"
        "    if True:\n"
        "        value = 2\n"
        "        assert forward() == 1\n"
    ),
}


@pytest.mark.parametrize(
    "case",
    [
        "forwarded-capture",
        "nested-capture",
        "separate-activations",
        "captured-comprehension",
        "comprehension-first-iterable",
        "late-bound-capture",
        "captured-callable",
    ],
)
def test_captured_lambda_is_refused_outside_its_defining_symbolic_scope(
    case: str,
) -> None:
    results = prove_text(_PROGRAMS[case])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "lambda capture from a different symbolic scope" in str(results[0].proof)


@pytest.mark.parametrize(
    "case",
    [
        "stateless-callback",
        "parameter-shadow",
        "nested-parameter",
        "outer-lambda-parameter",
        "comprehension-variable",
    ],
)
def test_lambda_local_parameters_are_not_mistaken_for_enclosing_captures(
    case: str,
) -> None:
    results = prove_text(_PROGRAMS[case])

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_stateless_callback_still_refutes_an_incorrect_assertion() -> None:
    source = _PROGRAMS["stateless-callback"].replace("== 7", "== 8")

    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]
    assert "assertion" in str(results[0].proof)


def test_returned_lambda_activations_keep_independent_values_at_runtime() -> None:
    # Refusing this shape is conservative: the interpreter keeps both cells,
    # while substituting caller locals would lose each activation's identity.
    outcome = execute(
        _PROGRAMS["separate-activations"],
        source_name="lambda_edges.py",
        policy=build_policy(),
        import_roots=[],
    )
    assert outcome.effects == ()


def test_comprehension_first_iterable_keeps_its_lexical_capture_at_runtime() -> None:
    # The first iterable is evaluated outside the comprehension's own scope;
    # its same-named iteration target must not hide the lambda's capture.
    outcome = execute(
        _PROGRAMS["comprehension-first-iterable"],
        source_name="lambda_edges.py",
        policy=build_policy(),
        import_roots=[],
    )
    assert outcome.effects == ()


@pytest.mark.parametrize(
    ("expression", "assertion"),
    [
        pytest.param("lambda value: value", "read() == 0", id="missing-positional"),
        pytest.param("lambda value: value", "read(1, 2) == 1", id="extra-positional"),
        pytest.param("lambda value: value", "read(value=1) == 0", id="keyword"),
        pytest.param("lambda *, value: value", "read(value=1) == 0", id="keyword-only"),
        pytest.param("lambda *values: 0", "read(1, 2) == 0", id="varargs"),
        pytest.param("lambda **values: 0", "read(value=1) == 0", id="kwargs"),
        pytest.param("lambda value=1: value", "read() == 0", id="default"),
    ],
)
def test_unsupported_lambda_argument_shapes_are_refused(
    expression: str, assertion: str
) -> None:
    # Missing/keyword/default parameters must not fall through to this outer
    # value. Varargs and kwargs are conservative signature refusals, including
    # when the body does not use those parameters.
    source = (
        "def main() -> None:\n"
        "    value = 0\n"
        f"    read = {expression}\n"
        f"    assert {assertion}\n"
    )

    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "unsupported lambda argument binding" in str(results[0].proof)


@pytest.mark.parametrize("parameter", ["ctx", "var_name"])
@pytest.mark.parametrize("callable_name", ["read", "read.__call__", "call"])
def test_lambda_keyword_names_cannot_collide_with_dispatch_metadata(
    parameter: str, callable_name: str
) -> None:
    source = (
        "def main() -> None:\n"
        f"    {parameter} = 0\n"
        f"    read = lambda {parameter}: {parameter}\n"
        + ("    call = read.__call__\n" if callable_name == "call" else "")
        + f"    assert {callable_name}({parameter}=1) == 0\n"
    )

    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    diagnostic = (
        "explicit __call__ attributes are not supported"
        if callable_name == "call"
        else "unsupported call argument binding: reserved keyword"
    )
    assert diagnostic in str(results[0].proof)


@pytest.mark.parametrize("parameter", ["ctx", "var_name"])
def test_lambda_dispatch_metadata_names_can_be_positional_parameters(
    parameter: str,
) -> None:
    source = (
        "def main() -> None:\n"
        f"    {parameter} = 0\n"
        f"    read = lambda {parameter}: {parameter}\n"
        "    assert read(1) == 1\n"
    )

    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


@pytest.mark.parametrize("parameters", ["value", "value, /"])
@pytest.mark.parametrize(
    ("expected", "conclusion"), [(4, Conclusion.OK), (5, Conclusion.FAIL)]
)
def test_exact_positional_lambda_binding_preserves_proof_results(
    parameters: str, expected: int, conclusion: Conclusion
) -> None:
    source = (
        "def invoke(fn) -> int:\n"
        "    return fn(3)\n"
        "def main() -> None:\n"
        "    value = 99\n"
        f"    step = lambda {parameters}: value + 1\n"
        f"    assert invoke(step) == {expected}\n"
    )

    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [conclusion]


def test_module_callable_reference_is_not_a_local_lambda_capture() -> None:
    source = (
        "def increment(value: int) -> int:\n"
        "    return value + 1\n"
        "def invoke(fn) -> int:\n"
        "    return fn()(3)\n"
        "def main() -> None:\n"
        "    read = lambda: increment\n"
        "    assert invoke(read) == 4\n"
    )

    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "def main() -> None:\n"
            "    values = [2]\n"
            "    read = lambda: len(values)\n"
            "    assert read() == 1\n",
            id="captured-list",
        ),
        pytest.param(
            "def invoke(fn, values: list[int]) -> int:\n"
            "    return fn(values)\n"
            "def main() -> None:\n"
            "    read = lambda values: len(values)\n"
            "    assert invoke(read, [2]) == 1\n",
            id="parameter-list-across-scopes",
        ),
    ],
)
def test_lambda_read_only_list_access_remains_supported(source: str) -> None:
    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.OK]


def test_mutation_after_a_lambda_call_still_refutes_an_incorrect_assertion() -> None:
    # Leaving the lambda must remove its mutation guard from the shared trace.
    # The later append must update the list and make the final assertion fail.
    source = (
        "def main() -> None:\n"
        "    values = [2]\n"
        "    read = lambda: len(values)\n"
        "    assert read() == 1\n"
        "    fresh = [2]\n"
        "    fresh.append(3)\n"
        "    assert fresh == [2]\n"
    )

    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]
    assert "assertion" in str(results[0].proof)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "def append(values: list[int]) -> None:\n"
            "    values.append(1)\n"
            "def main() -> None:\n"
            "    values = [0]\n"
            "    mutate = lambda: append(values)\n"
            "    mutate()\n"
            "    assert values == [0]\n",
            id="helper-call",
        ),
        pytest.param(
            "def main() -> None:\n"
            "    values = [0]\n"
            "    append = values.append\n"
            "    mutate = lambda: append(1)\n"
            "    mutate()\n"
            "    assert values == [0]\n",
            id="aliased-method",
        ),
        pytest.param(
            "def main() -> None:\n"
            "    values = {0}\n"
            "    mutate = lambda: values.add(1)\n"
            "    mutate()\n"
            "    assert 1 not in values\n",
            id="set-add",
        ),
        pytest.param(
            "def main() -> None:\n"
            '    values = {"key": 0}\n'
            "    mutate = lambda: values.clear()\n"
            "    mutate()\n"
            '    assert values == {"key": 0}\n',
            id="dict-clear",
        ),
    ],
)
def test_lambda_mutation_through_helpers_aliases_and_collections_is_refused(
    source: str,
) -> None:
    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    if "def append" in source:
        diagnostic = "mutating a function parameter or its alias is not supported"
    elif "append = values.append" in source:
        diagnostic = "mutating method requires a direct call on a named receiver"
    else:
        diagnostic = "mutating a captured collection is not supported"
    assert diagnostic in str(results[0].proof)
