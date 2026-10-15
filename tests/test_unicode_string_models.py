"""Full Python code points retain string operations and decimal bridges."""

import operator

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text


def _check(source: str, correct: bool) -> None:
    results = prove_text(source)
    expected = Conclusion.OK if correct else Conclusion.FAIL
    assert [result.proof.conclusion for result in results] == [expected], [
        str(result.proof) for result in results
    ]


@pytest.mark.parametrize(
    "expression",
    [
        "len('Ω\U00030000\U0010ffff') == 3",
        "'Ω' + '\U00030000' == 'Ω\U00030000'",
        "'\U00030000\U0010ffff' in 'Ω\U00030000\U0010ffff'",
        "'\U00030000Ω'.startswith('\U00030000')",
        "'Ω\U0010ffff'.endswith('\U0010ffff')",
        "'Ω\U00030000\U0010ffff'.index('\U0010ffff') == 2",
        "'Ω\U00030000\U0010ffff'.find('\U00030000') == 1",
    ],
    ids=["length", "concat", "contains", "prefix", "suffix", "index", "find"],
)
@pytest.mark.parametrize("correct", [True, False], ids=["true", "false"])
def test_full_unicode_sequence_operations(expression: str, correct: bool) -> None:
    negate = "" if correct else "not "
    _check(f"def main() -> None:\n    assert {negate}({expression})\n", correct)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("", "Ω"),
        ("Ω", "Ω\U00030000"),
        ("Ω\U00030000", "Ω"),
        ("\U000fffff", "\U00100000"),
        ("\U0010ffff", "\U00100000"),
        ("\U0010ffff", "\U0010ffff"),
    ],
    ids=[
        "empty-prefix",
        "proper-prefix",
        "longer-prefix",
        "sign-bit",
        "maximum",
        "equal",
    ],
)
@pytest.mark.parametrize("correct", [True, False], ids=["true", "false"])
def test_string_order_is_python_code_point_order(
    left: str, right: str, correct: bool
) -> None:
    source = "def main() -> None:\n"
    for spelling, compare in (
        ("<", operator.lt),
        ("<=", operator.le),
        (">", operator.gt),
        (">=", operator.ge),
    ):
        expected = compare(left, right)
        if not correct:
            expected = not expected
        source += (
            f"    assert ({ascii(left)} {spelling} {ascii(right)}) == {expected}\n"
        )
    _check(source, correct)


@pytest.mark.parametrize("number", [0, 123, -2])
@pytest.mark.parametrize("correct", [True, False], ids=["true", "false"])
def test_concrete_integer_text_uses_python_sign(number: int, correct: bool) -> None:
    expected = str(number) + ("" if correct else "x")
    _check(f"def main() -> None:\n    assert str({number}) == {expected!r}\n", correct)


@pytest.mark.parametrize("number", [0, 123, -2])
@pytest.mark.parametrize("correct", [True, False], ids=["true", "false"])
def test_symbolic_integer_text_bridge_keeps_the_pinned_value(
    number: int, correct: bool
) -> None:
    expected = str(number) + ("" if correct else "x")
    _check(
        f"import deal\n@deal.pre(lambda number: number == {number})\n"
        f"def main(number: int) -> None:\n    assert str(number) == {expected!r}\n",
        correct,
    )


def test_symbolic_equal_strings_have_no_lexical_order() -> None:
    _check(
        "import deal\n@deal.pre(lambda left, right: left == right)\n"
        "def main(left: str, right: str) -> None:\n"
        "    assert not (left < right)\n"
        "    assert left <= right\n"
        "    assert not (left > right)\n"
        "    assert left >= right\n",
        True,
    )


@pytest.mark.parametrize("function", ["min", "max"])
@pytest.mark.parametrize("tuple_input", [False, True], ids=["list", "tuple"])
@pytest.mark.parametrize(
    "values",
    [
        ["a", "b"],
        ["", "Ω", "Ω\U00030000"],
        ["\U00100000", "\U000fffff", "\U0010ffff"],
    ],
    ids=["ascii", "prefix", "unsigned-code-point"],
)
@pytest.mark.parametrize("correct", [True, False], ids=["true", "false"])
def test_iterable_extrema_use_python_string_order(
    function: str, tuple_input: bool, values: list[str], correct: bool
) -> None:
    expected = min(values) if function == "min" else max(values)
    comparison = "==" if correct else "!="
    items = repr(tuple(values) if tuple_input else values)
    _check(
        "def main() -> None:\n"
        f"    assert {function}({items}) {comparison} {expected!r}\n",
        correct,
    )


@pytest.mark.parametrize("function", ["min", "max"])
def test_iterable_numeric_extrema_keep_the_existing_model(function: str) -> None:
    values = [2, -3, 1]
    expected = min(values) if function == "min" else max(values)
    _check(
        f"def main() -> None:\n    assert {function}({values!r}) == {expected}\n",
        True,
    )
