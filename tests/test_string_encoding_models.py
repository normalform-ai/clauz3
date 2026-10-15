"""Literal encoding agrees with Python before string, regex, and float operations."""

import re

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text


def _assert_conclusion(source: str, expected: Conclusion) -> None:
    results = prove_text(source)
    assert [result.proof.conclusion for result in results] == [expected], [
        str(result.proof) for result in results
    ]


@pytest.mark.parametrize(
    "value",
    [
        r"\u{41}",
        r"\u{0}",
        r"\u{1f600}",
        r"prefix\u{41}suffix",
        r"\\u{41}",
        r"\u0041",
        "Ω😀\0\ud800\U0002ffff\U00030000\U0010ffff",
    ],
    ids=[
        "escaped-ascii",
        "escaped-nul",
        "escaped-emoji",
        "embedded-escape",
        "repeated-backslash",
        "python-style-escape",
        "unicode-and-nul",
    ],
)
def test_literal_equals_character_assembly_and_has_python_length(value: str) -> None:
    assembled = " + ".join(ascii(character) for character in value)
    source = (
        "def main() -> None:\n"
        f"    value = {ascii(value)}\n"
        f"    assert value == ({assembled})\n"
        f"    assert len(value) == {len(value)}\n"
    )

    _assert_conclusion(source, Conclusion.OK)


@pytest.mark.parametrize(
    "assertion",
    [
        r'assert r"\u{41}" == "A"',
        r'assert len(r"\u{41}") == 1',
        r'assert ord(r"\u{41}"[0]) == 65',
        r'assert r"\u{0}" == "\0"',
        r'assert r"\u{1f600}" == "😀"',
    ],
    ids=["ascii-equality", "length", "index", "nul-equality", "emoji-equality"],
)
def test_z3_escape_spelling_does_not_prove_false_python_assertions(
    assertion: str,
) -> None:
    _assert_conclusion(f"def main() -> None:\n    {assertion}\n", Conclusion.FAIL)


@pytest.mark.parametrize(
    "value",
    ["Ω", "😀", "\0", "\ud800", "\U0002ffff", "\U00030000", "\U0010ffff"],
    ids=["greek", "emoji", "nul", "surrogate", "old-maximum", "plane-three", "maximum"],
)
@pytest.mark.parametrize("correct", [True, False], ids=["true", "false"])
def test_unicode_character_code_is_exact(value: str, correct: bool) -> None:
    expected = ord(value) if correct else ord(value) + 1
    source = f"def main() -> None:\n    assert ord({ascii(value)}) == {expected}\n"

    _assert_conclusion(source, Conclusion.OK if correct else Conclusion.FAIL)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("re.fullmatch({pattern}, {value})", Conclusion.OK),
        ("re.match({pattern}, {value})", Conclusion.OK),
        ("re.compile({pattern}).fullmatch({value})", Conclusion.SKIP),
    ],
    ids=["fullmatch", "match", "compiled-pattern"],
)
@pytest.mark.parametrize(
    "value",
    [r"\u{41}", "Ω", "😀", "\0", "\ud800", "\U0002ffff", "\U00030000", "\U0010ffff"],
    ids=[
        "literal-escape",
        "greek",
        "emoji",
        "nul",
        "surrogate",
        "old-maximum",
        "plane-three",
        "maximum",
    ],
)
def test_unicode_regex_matches_respect_the_runtime_surface(
    expression: str, expected: Conclusion, value: str
) -> None:
    pattern = re.escape(value)
    assert re.fullmatch(pattern, value) is not None
    call = expression.format(pattern=ascii(pattern), value=ascii(value))

    results = prove_text(f"import re\ndef main() -> None:\n    assert {call}\n")
    assert [result.proof.conclusion for result in results] == [expected]
    if expected is Conclusion.SKIP:
        # The pattern constructor accepts these Unicode strings, but its
        # fullmatch attribute has no interpreter surface. Direct calls above
        # retain the positive encoding controls.
        assert "attribute is outside the interpreter surface Pattern fullmatch" in str(
            results[0].proof
        )


@pytest.mark.parametrize(
    ("pattern", "value"),
    [(re.escape(r"\u{41}"), "A"), ("Ω", "A"), ("😀", "Ω"), ("\0", "0")],
    ids=["literal-escape", "greek", "emoji", "nul"],
)
def test_regex_encoding_does_not_turn_a_nonmatch_into_a_proof(
    pattern: str, value: str
) -> None:
    assert re.fullmatch(pattern, value) is None
    source = (
        "import re\ndef main() -> None:\n"
        f"    assert re.fullmatch({ascii(pattern)}, {ascii(value)})\n"
    )

    _assert_conclusion(source, Conclusion.FAIL)


@pytest.mark.parametrize(
    "value", ["1.5", "١.٥", "１２.５"], ids=["ascii", "arabic", "fullwidth"]
)
@pytest.mark.parametrize("correct", [True, False], ids=["true", "false"])
def test_float_conversion_decodes_the_exact_unicode_decimal(
    value: str, correct: bool
) -> None:
    expected = float(value) + (0.0 if correct else 1.0)
    source = f"def main() -> None:\n    assert float({ascii(value)}) == {expected!r}\n"

    _assert_conclusion(source, Conclusion.OK if correct else Conclusion.FAIL)


def test_literal_z3_escape_is_not_a_valid_decimal_string() -> None:
    value = r"\u{31}"
    with pytest.raises(ValueError):
        float(value)
    source = f"def main() -> None:\n    assert float({ascii(value)}) == 1.0\n"

    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "cannot convert string literal to float" in str(results[0].proof)


@pytest.mark.parametrize("value", ["\U00030000", "\U0010ffff"])
@pytest.mark.parametrize(
    "correct", [True, False], ids=["python-length", "false-length"]
)
def test_literal_above_native_z3_character_domain_keeps_python_length(
    value: str, correct: bool
) -> None:
    assert len(value) == 1
    # Unsupported characters used to become the literal Z3 escape text instead.
    expected_length = 1 if correct else len(rf"\u{{{ord(value):x}}}")
    source = (
        f"def main() -> None:\n    assert len({ascii(value)}) == {expected_length}\n"
    )

    _assert_conclusion(source, Conclusion.OK if correct else Conclusion.FAIL)


@pytest.mark.parametrize(
    "pattern",
    [r"\U00030000", r"[\U00030000-\U00030001]", r"[^\U00030000]"],
    ids=["literal", "range", "negated-literal"],
)
def test_ascii_regex_escapes_cover_characters_above_native_z3_domain(
    pattern: str,
) -> None:
    matches = re.fullmatch(pattern, "a") is not None
    source = (
        "import re\ndef main() -> None:\n"
        f"    assert not re.fullmatch({ascii(pattern)}, 'a')\n"
    )

    _assert_conclusion(source, Conclusion.FAIL if matches else Conclusion.OK)


@pytest.mark.parametrize(
    ("operation", "pattern", "value"),
    [
        ("match", "Ω", "Ω😀"),
        ("fullmatch", "Ω.", "Ω😀"),
        ("fullmatch", "[^Ω]", "😀"),
        ("match", "Ω", "Ω\U00030000"),
        ("fullmatch", "Ω.", "Ω\U0010ffff"),
        ("fullmatch", "[^Ω]", "\U0010ffff"),
        ("fullmatch", "[\U00100000-\U0010ffff]*", "\U00100000\U0010ffff"),
    ],
    ids=[
        "unicode-suffix",
        "unicode-dot",
        "unicode-negated-literal",
        "high-suffix",
        "high-dot",
        "high-negated-literal",
        "high-range-star",
    ],
)
@pytest.mark.parametrize("correct", [True, False], ids=["true", "false"])
def test_regex_matching_covers_the_supported_unicode_alphabet(
    operation: str, pattern: str, value: str, correct: bool
) -> None:
    match = (
        re.match(pattern, value)
        if operation == "match"
        else re.fullmatch(pattern, value)
    )
    assert match is not None
    negate = "" if correct else "not "
    source = (
        "import re\ndef main() -> None:\n"
        f"    assert {negate}re.{operation}({ascii(pattern)}, {ascii(value)})\n"
    )

    _assert_conclusion(source, Conclusion.OK if correct else Conclusion.FAIL)


@pytest.mark.parametrize(
    ("pattern", "value"),
    [(r"Ω\w", "Ωβ"), ("(?i)Ω", "ω"), ("(?i:Ω)", "ω")],
    ids=["unicode-word-category", "global-inline-flags", "scoped-inline-flags"],
)
def test_regex_categories_and_inline_flags_are_refused(
    pattern: str, value: str
) -> None:
    assert re.fullmatch(pattern, value) is not None
    source = (
        "import re\ndef main() -> None:\n"
        f"    assert not re.fullmatch({ascii(pattern)}, {ascii(value)})\n"
    )

    _assert_conclusion(source, Conclusion.SKIP)


@pytest.mark.parametrize(
    "expression",
    [
        "re.match({pattern}, {value}, flags={flags})",
        "re.fullmatch({pattern}, {value}, flags={flags})",
        "re.compile({pattern}, flags={flags}).fullmatch({value})",
    ],
    ids=["match", "fullmatch", "compile"],
)
def test_regex_keyword_flags_are_refused(expression: str) -> None:
    pattern = "Ω"
    value = "ω"
    flags = int(re.IGNORECASE)
    assert re.fullmatch(pattern, value, flags=flags) is not None
    call = expression.format(pattern=ascii(pattern), value=ascii(value), flags=flags)
    source = f"import re\ndef main() -> None:\n    assert not {call}\n"

    _assert_conclusion(source, Conclusion.SKIP)


@pytest.mark.parametrize(
    "pattern", ["Ω{0}", "Ω{0,0}"], ids=["exact-zero", "zero-range"]
)
@pytest.mark.parametrize("value", ["", "Ω"], ids=["empty", "nonempty"])
def test_zero_repetitions_match_only_the_empty_string(pattern: str, value: str) -> None:
    matches = re.fullmatch(pattern, value) is not None
    assert matches == (value == "")
    source = (
        "import re\ndef main() -> None:\n"
        f"    assert re.fullmatch({ascii(pattern)}, {ascii(value)})\n"
    )

    _assert_conclusion(source, Conclusion.OK if matches else Conclusion.FAIL)


@pytest.mark.parametrize(
    "expression",
    [
        "re.match({pattern}, {value}, {flags})",
        "re.fullmatch({pattern}, {value}, {flags})",
        "re.compile({pattern}, {flags}).fullmatch({value})",
    ],
    ids=["match", "fullmatch", "compile"],
)
def test_regex_positional_flags_are_refused(expression: str) -> None:
    pattern = "Ω"
    value = "ω"
    flags = int(re.IGNORECASE)
    assert re.fullmatch(pattern, value, flags) is not None
    call = expression.format(pattern=ascii(pattern), value=ascii(value), flags=flags)
    source = f"import re\ndef main() -> None:\n    assert not {call}\n"

    _assert_conclusion(source, Conclusion.SKIP)


@pytest.mark.parametrize(
    "expression",
    [
        "re.match('Ω', 0)",
        "re.fullmatch('Ω', 0)",
        "re.compile('Ω').fullmatch(0)",
    ],
    ids=["match", "fullmatch", "compiled-pattern"],
)
def test_invalid_regex_subject_in_dead_operand_cannot_suppress_a_false_assertion(
    expression: str,
) -> None:
    source = (
        "import deal\nimport re\n"
        "@deal.raises(TypeError)\n"
        "def main() -> None:\n"
        f"    _ = False and {expression}\n"
        "    assert False\n"
    )

    results = prove_text(source)

    assert [result.proof.conclusion for result in results] == [Conclusion.FAIL]
    assert "assertion" in str(results[0].proof)
