"""Call results must preserve Python's None and collection mutation semantics."""

from textwrap import dedent, indent

import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProofResult, prove_text

_QUIET = "def quiet() -> None:\n    pass\n\n"


def _prove(source: str) -> ProofResult:
    results = prove_text(dedent(source))
    assert len(results) == 1
    return results[0]


def _assertion(expression: str) -> ProofResult:
    return _prove(_QUIET + f"def main() -> None:\n    assert {expression}\n")


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("quiet() == False", Conclusion.FAIL),
        ("False == quiet()", Conclusion.FAIL),
        ("quiet() != False", Conclusion.OK),
        ("False != quiet()", Conclusion.OK),
        ("quiet() == 0", Conclusion.FAIL),
        ("0 != quiet()", Conclusion.OK),
        ("quiet() == quiet()", Conclusion.OK),
        ("quiet() != quiet()", Conclusion.FAIL),
        ("not quiet()", Conclusion.OK),
        ("bool(quiet())", Conclusion.FAIL),
    ],
)
def test_no_return_result_has_exact_none_equality_and_truthiness(
    expression: str, expected: Conclusion
) -> None:
    assert _assertion(expression).proof.conclusion is expected


@pytest.mark.parametrize(
    ("comparison", "expected"),
    [("!= False", Conclusion.OK), ("== False", Conclusion.FAIL)],
)
def test_none_can_be_stored_and_forwarded(
    comparison: str, expected: Conclusion
) -> None:
    result = _prove(
        _QUIET
        + "def forward(value):\n    return value\n\n"
        + "def main() -> None:\n"
        + "    value = forward(quiet())\n"
        + "    assert not value\n"
        + f"    assert value {comparison}\n"
    )
    assert result.proof.conclusion is expected


@pytest.mark.parametrize(
    "body",
    [
        "if flag:\n    value = quiet()\nelse:\n    value = quiet()\n"
        "assert value == quiet()\nassert not value",
        "value = quiet() if flag else quiet()\nassert value == quiet()",
        "value = 7 if quiet() else 9\nassert value == 9",
    ],
    ids=["statement-join", "expression-join", "none-condition"],
)
def test_none_branch_joins_and_conditions_are_supported(body: str) -> None:
    result = _prove(_QUIET + "def main(flag: bool) -> None:\n" + indent(body, "    "))
    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize(
    "body",
    [
        "if flag:\n    value = quiet()\nelse:\n    value = False\nassert not value",
        "value = quiet() if flag else 0\nassert not value",
    ],
    ids=["statement-join", "expression-join"],
)
def test_mixed_none_and_scalar_joins_fail_closed(body: str) -> None:
    result = _prove(_QUIET + "def main(flag: bool) -> None:\n" + indent(body, "    "))
    assert result.proof.conclusion is Conclusion.SKIP
    assert "None" in str(result.proof.error)


def test_partial_value_return_with_implicit_none_fails_closed() -> None:
    result = _prove("""
def partial(flag: bool) -> bool:
    if flag:
        return False

def main() -> None:
    assert partial(False) == False
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert "implicit None" in str(result.proof.error)


def test_all_return_paths_can_return_none() -> None:
    result = _prove(
        _QUIET
        + "def choose(flag: bool):\n"
        + "    if flag:\n        return quiet()\n"
        + "    return quiet()\n\n"
        + "def main(flag: bool) -> None:\n"
        + "    assert choose(flag) == quiet()\n"
        + "    assert choose(flag) != False\n"
    )
    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize(
    ("assertion", "expected"),
    [("choose(flag) > 5", Conclusion.OK), ("choose(flag) == 0", Conclusion.FAIL)],
)
def test_complete_scalar_return_paths_remain_supported(
    assertion: str, expected: Conclusion
) -> None:
    result = _prove(
        "def choose(flag: bool) -> int:\n"
        "    if flag:\n        return 7\n"
        "    return 9\n\n"
        "def main(flag: bool) -> None:\n"
        f"    assert {assertion}\n"
    )
    assert result.proof.conclusion is expected


@pytest.mark.parametrize(
    "body",
    [
        "if n < 0:\n    return 0\nif n > 10:\n    return 10\nreturn n",
        "if n > 0:\n    return 1\nif n <= 0:\n    return 0",
    ],
    ids=["multiple-early-returns", "arithmetic-exhaustiveness"],
)
@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_total_return_coverage_is_proved_semantically(body: str, correct: bool) -> None:
    comparison = ">= 0" if correct else "< 0"
    result = _prove(
        "def choose(n: int) -> int:\n"
        + indent(body, "    ")
        + "\n\ndef main(n: int) -> None:\n"
        + f"    assert choose(n) {comparison}\n"
    )
    expected = Conclusion.OK if correct else Conclusion.FAIL
    assert result.proof.conclusion is expected


@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_multiple_exception_conditions_before_a_final_return(correct: bool) -> None:
    extra = "" if correct else " + 1"
    result = _prove(
        "import deal\n\n"
        "def first_two(values: list[int]) -> int:\n"
        "    return values[0] + values[1]\n\n"
        "@deal.pre(lambda values: len(values) >= 2)\n"
        "def main(values: list[int]) -> None:\n"
        f"    assert first_two(values) == values[0] + values[1]{extra}\n"
    )
    expected = Conclusion.OK if correct else Conclusion.FAIL
    assert result.proof.conclusion is expected


def test_multiple_early_returns_do_not_hide_real_fallthrough() -> None:
    result = _prove("""
def partial(n: int) -> int:
    if n < 0:
        return 0
    if n > 10:
        return 10

def main(n: int) -> None:
    assert partial(n) >= 0
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert "implicit None" in str(result.proof.error)


def test_contradictory_callee_assumptions_do_not_cover_fallthrough() -> None:
    result = _prove("""
import deal

@deal.post(lambda result: False)
def inconsistent() -> int:
    return 1

def partial(flag: bool) -> int:
    inconsistent()
    if flag:
        return 7

def main(flag: bool) -> None:
    assert partial(flag) == 7
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert "implicit None" in str(result.proof.error)


def test_unknown_return_coverage_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    timeouts: list[int] = []

    class UnknownSolver:
        def __init__(self, *, ctx: z3.Context | None = None) -> None:
            pass

        def set(self, name: str, value: int) -> None:
            assert name == "timeout"
            timeouts.append(value)

        def add(self, *args: object) -> None:
            pass

        def check(self) -> z3.CheckSatResult:
            return z3.unknown

    monkeypatch.setattr(z3, "Solver", UnknownSolver)
    result = _prove("""
def clamp(n: int) -> int:
    if n < 0:
        return 0
    if n > 10:
        return 10
    return n

def main(n: int) -> None:
    assert clamp(n) >= 0
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert "implicit None" in str(result.proof.error)
    assert timeouts == [1_000]


@pytest.mark.parametrize(
    ("raises", "expected"),
    [("@deal.raises(ValueError)\n", Conclusion.OK), ("", Conclusion.FAIL)],
    ids=["declared-exception", "undeclared-exception"],
)
def test_exception_path_is_not_normal_fallthrough(
    raises: str, expected: Conclusion
) -> None:
    result = _prove(
        "import deal\n\n"
        "def choose(flag: bool) -> int:\n"
        "    if flag:\n        return 7\n"
        "    raise ValueError('stop')\n\n"
        f"{raises}def main(flag: bool) -> None:\n"
        "    assert choose(flag) == 7\n"
    )
    assert result.proof.conclusion is expected


@pytest.mark.parametrize(
    ("initial", "operation", "updated"),
    [
        ("[0]", "append(1)", "[0, 1]"),
        ("[0]", "extend([1, 2])", "[0, 1, 2]"),
        ("[0]", "clear()", "[]"),
        ("{0}", "add(1)", "{0, 1}"),
        ("{0}", "update({1, 2})", "{0, 1, 2}"),
        ("{0, 1}", "discard(1)", "{0}"),
        ("{0}", "clear()", "set()"),
        ("{'x': 0}", "clear()", "{}"),
        ("{'x': 0}", "__setitem__('x', 1)", "{'x': 1}"),
    ],
    ids=[
        "list-append",
        "list-extend",
        "list-clear",
        "set-add",
        "set-update",
        "set-discard",
        "set-clear",
        "dict-clear",
        "dict-setitem",
    ],
)
def test_void_mutators_return_none_and_preserve_receiver_updates(
    initial: str, operation: str, updated: str
) -> None:
    result = _prove(
        _QUIET
        + "def main() -> None:\n"
        + f"    values = {initial}\n"
        + f"    result = values.{operation}\n"
        + "    assert not result\n"
        + "    assert result == quiet()\n"
        + "    assert result != False\n"
        + f"    assert values == {updated}\n"
    )
    expected = Conclusion.SKIP if operation.startswith("__") else Conclusion.OK
    assert result.proof.conclusion is expected
    if expected is Conclusion.SKIP:
        assert "attribute is outside the interpreter surface" in str(result.proof)


def test_mutator_return_cannot_satisfy_a_truthy_assertion() -> None:
    result = _prove("""
def main() -> None:
    values = [0]
    assert values.append(1)
""")
    assert result.proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize(
    ("initial", "operation", "remaining"),
    [("{'x': 7}", "pop('x')", "{}"), ("{7}", "pop()", "set()")],
    ids=["dict-pop", "set-pop"],
)
@pytest.mark.parametrize("correct", [True, False], ids=["correct", "wrong"])
def test_pop_keeps_its_element_result_and_receiver_update(
    initial: str, operation: str, remaining: str, correct: bool
) -> None:
    expected_value = 7 if correct else 8
    result = _prove(
        "def main() -> None:\n"
        f"    values = {initial}\n"
        f"    result = values.{operation}\n"
        f"    assert values == {remaining}\n"
        f"    assert result == {expected_value}\n"
    )
    expected = Conclusion.OK if correct else Conclusion.FAIL
    assert result.proof.conclusion is expected


def test_dictionary_getitem_and_empty_list_add_are_pure() -> None:
    result = _prove("""
def main() -> None:
    mapping = {'x': 7}
    value = mapping['x']
    assert value == 7
    assert mapping == {'x': 7}
    values = []
    combined = values + [1]
    assert combined == [1]
    assert values == []
""")
    assert result.proof.conclusion is Conclusion.OK


def test_repeated_calls_do_not_reuse_mutator_state() -> None:
    result = _prove("""
def build() -> int:
    values = []
    result = values.append(7)
    assert not result
    return len(values)

def main() -> None:
    assert build() == 1
    assert build() == 1
""")
    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize(
    ("comparison", "expected"),
    [("!= False", Conclusion.OK), ("== False", Conclusion.FAIL)],
)
def test_noop_print_returns_exact_none(comparison: str, expected: Conclusion) -> None:
    result = _prove(
        "def main() -> None:\n"
        "    value = print()\n"
        "    assert not value\n"
        f"    assert value {comparison}\n"
    )
    assert result.proof.conclusion is expected


@pytest.mark.parametrize(
    "expression",
    [
        "len(value)",
        "value.missing",
        "value()",
        "1 in value",
        "value + 1",
        "value < 1",
        "value[0]",
    ],
)
def test_unsupported_none_operations_fail_closed(expression: str) -> None:
    result = _prove(
        _QUIET
        + "def main() -> None:\n"
        + "    value = quiet()\n"
        + f"    unused = {expression}\n"
        + "    assert False\n"
    )
    assert result.proof.conclusion is Conclusion.SKIP
    assert "None" in str(result.proof.error)


@pytest.mark.parametrize(
    "expression",
    ["len(quiet())", "'x' + quiet()", "[0][quiet()]", "(0,)[quiet()]"],
)
def test_eager_dead_operand_cannot_turn_none_error_into_an_assumption(
    expression: str,
) -> None:
    result = _prove(
        "import deal\n\n"
        + _QUIET
        + "@deal.raises(TypeError)\n"
        + "def main() -> None:\n"
        + f"    unused = False and ({expression})\n"
        + "    assert False\n"
    )
    assert result.proof.conclusion is Conclusion.FAIL
    assert result.proof.description == "assertion"


@pytest.mark.parametrize("assignment", ["values[quiet()] = 1", "values[0] = quiet()"])
def test_dead_helper_assignment_cannot_suppress_a_none_operand(
    assignment: str,
) -> None:
    result = _prove(
        "import deal\n\n"
        + _QUIET
        + "def assign() -> None:\n"
        + "    values = [0]\n"
        + f"    {assignment}\n\n"
        + "@deal.raises(TypeError)\n"
        + "def main() -> None:\n"
        + "    unused = False and assign()\n"
        + "    assert False\n"
    )
    assert result.proof.conclusion is Conclusion.FAIL
    assert result.proof.description == "assertion"
