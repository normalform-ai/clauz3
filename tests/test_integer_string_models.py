"""Python integer text conversion cannot hide a branch or a conversion fault."""

import sys

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text


def _check(source: str, expected: Conclusion, diagnostic: str = "") -> None:
    results = prove_text(source)
    assert [result.proof.conclusion for result in results] == [expected], [
        str(result.proof) for result in results
    ]
    if diagnostic:
        assert diagnostic in str(results[0].proof)


@pytest.mark.parametrize(
    "text",
    [
        "12",
        "00012",
        "-2",
        "+2",
        "-0",
        " \t2\n",
        "\u20032\u00a0",
        "1_000",
        "١٢",
        "１２",
        "1" + "0" * 1300,
    ],
    ids=[
        "decimal",
        "leading-zero",
        "minus",
        "plus",
        "negative-zero",
        "ascii-space",
        "unicode-space",
        "underscore",
        "arabic",
        "fullwidth",
        "large-integer",
    ],
)
@pytest.mark.parametrize("correct", [True, False], ids=["true", "false"])
def test_concrete_conversion_agrees_with_python(text: str, correct: bool) -> None:
    expected = int(text) + (0 if correct else 1)
    _check(
        f"def main() -> None:\n    assert int({ascii(text)}) == {expected}\n",
        Conclusion.OK if correct else Conclusion.FAIL,
    )


@pytest.mark.parametrize("correct", [True, False], ids=["true", "false"])
def test_concrete_expression_is_decoded_exactly(correct: bool) -> None:
    _check(
        "def main() -> None:\n"
        "    text = '-' + '٢'\n"
        f"    assert int(text) == {-2 if correct else -1}\n",
        Conclusion.OK if correct else Conclusion.FAIL,
    )


@pytest.mark.parametrize(
    "text",
    ["", " \t", "+", "-", "_1", "1_", "1__0", "0x10", "²", "1\0", r"\u{31}", "bad"],
)
def test_invalid_text_refuses_instead_of_returning_a_sentinel(text: str) -> None:
    with pytest.raises(ValueError):
        int(text)
    _check(
        f"def main() -> None:\n    assert int({ascii(text)}) == -1\n",
        Conclusion.SKIP,
        "cannot convert string literal to int",
    )


@pytest.mark.parametrize("with_precondition", [False, True])
def test_symbolic_text_refuses_even_with_a_caller_constraint(
    with_precondition: bool,
) -> None:
    decorator = "@deal.pre(lambda text: text == '12')\n" if with_precondition else ""
    _check(
        "import deal\n"
        + decorator
        + "def main(text: str) -> None:\n    assert int(text) == 12\n",
        Conclusion.SKIP,
        "expected a concrete string",
    )


@pytest.mark.parametrize(
    "call",
    [
        "int()",
        "int('10', 2)",
        "int('10', base=2)",
        "int(a='10')",
        "int(x='10')",
        "int('10', bad=1)",
        "int('10', ctx=1)",
        "int('10', var_name=1)",
        "int('10', self=1)",
    ],
)
def test_unsupported_builtin_signature_refuses_cleanly(call: str) -> None:
    _check(
        f"def main() -> None:\n    assert {call} == 10\n",
        Conclusion.SKIP,
        "unsupported call argument binding: reserved keyword"
        if any(f"{name}=" in call for name in ("ctx", "var_name", "self"))
        else "int() requires one positional argument and no keywords",
    )


@pytest.mark.parametrize(
    ("binding", "call"),
    [
        ("convert = int", "convert('10', base=2)"),
        ("convert = int", "convert('10', ctx=1)"),
        ("convert = int.__call__", "convert('10', self=1)"),
        ("convert = int.__call__.__call__", "convert('10', var_name=1)"),
        ("", "int(*['10'])"),
        ("", "int('10', **{'base': 2})"),
    ],
)
def test_aliases_wrappers_and_unpacking_cannot_bypass_signature_refusal(
    binding: str, call: str
) -> None:
    _check(
        f"def main() -> None:\n    {binding or 'pass'}\n    assert {call} == 10\n",
        Conclusion.SKIP,
    )


def test_local_function_named_int_retains_its_own_signature() -> None:
    _check(
        "def int(text: str, base: int) -> int:\n    return base\n"
        "def main() -> None:\n    assert int(text='10', base=2) == 2\n",
        Conclusion.OK,
    )


@pytest.mark.parametrize(
    "expression", ["int(12) == 12", "int(True) == 1", "int(1.5) == 1"]
)
def test_existing_nonstring_conversions_remain_supported(expression: str) -> None:
    _check(f"def main() -> None:\n    assert {expression}\n", Conclusion.OK)


def test_skipped_invalid_conversion_in_boolean_does_not_erase_obligations() -> None:
    _check(
        "import deal\n@deal.raises(ValueError)\ndef main() -> None:\n"
        "    value = False and int('bad')\n    assert False\n",
        Conclusion.FAIL,
        "assertion",
    )


def test_configured_decimal_digit_limit_is_preserved() -> None:
    limit = sys.get_int_max_str_digits()
    if not limit or limit > 10_000:
        pytest.skip("host digit limit is disabled or exceeds this bounded fixture")
    text = "9" * (limit + 1)
    with pytest.raises(ValueError):
        int(text)
    _check(
        f"def main() -> None:\n    assert int({text!r}) == -1\n",
        Conclusion.SKIP,
        "cannot convert string literal to int",
    )
    assert sys.get_int_max_str_digits() == limit
