"""String search offsets and missing results follow Python's find semantics."""

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text

BINDING_DIAGNOSTIC = "unsupported str.find argument binding"


@pytest.mark.parametrize(
    ("text", "needle", "start"),
    [
        ("abc", "b", None),
        ("ababa", "ba", 2),
        ("abc", "b", -2),
        ("abc", "b", -100),
        ("abc", "missing", -2),
        ("abc", "b", 4),
        ("abc", "", -1),
        ("abc", "", -100),
        ("abc", "", 3),
        ("abc", "", 4),
        ("", "", None),
        ("", "", -1),
        ("", "", 1),
        ("abc", "a", True),
        ("abc", "a", False),
    ],
)
def test_finite_search_agrees_with_python(
    text: str, needle: str, start: int | None
) -> None:
    expected = text.find(needle) if start is None else text.find(needle, start)
    arguments = repr(needle) + ("" if start is None else f", {start!r}")
    proofs = prove_text(
        f"def main() -> None:\n    assert {text!r}.find({arguments}) == {expected}\n"
    )
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]


@pytest.mark.parametrize(
    "assertion",
    [
        "'abc'.find('b', -2) == -1",
        "'abc'.find('', -1) == -1",
        "'abc'.find('a', True) == 0",
    ],
)
def test_incorrect_search_result_cannot_prove(assertion: str) -> None:
    proofs = prove_text(f"def main() -> None:\n    assert {assertion}\n")
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]


def test_symbolic_text_with_empty_needle_stays_supported() -> None:
    proofs = prove_text("def main(text: str) -> None:\n    assert text.find('') == 0\n")
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]


def test_symbolic_start_preserves_empty_needle_boundaries() -> None:
    proofs = prove_text(
        "def main(start: int) -> None:\n"
        "    result = 'abc'.find('', start)\n"
        "    if start < -3:\n        assert result == 0\n"
        "    if -3 <= start < 0:\n        assert result == 3 + start\n"
        "    if 0 <= start <= 3:\n        assert result == start\n"
        "    if start > 3:\n        assert result == -1\n"
    )
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]


def test_symbolic_needle_keeps_the_missing_result_in_range() -> None:
    proofs = prove_text(
        "def main(needle: str) -> None:\n"
        "    result = 'abc'.find(needle, -2)\n"
        "    assert result == -1 or 1 <= result <= 3\n"
    )
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]


def test_symbolic_bool_start_uses_its_integer_value() -> None:
    proofs = prove_text(
        "def main(start: bool) -> None:\n"
        "    result = 'abc'.find('a', start)\n"
        "    assert result == (-1 if start else 0)\n"
    )
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        ("", BINDING_DIAGNOSTIC),
        ("'b', 0, 3", BINDING_DIAGNOSTIC),
        ("'b', 0, 3, 4", BINDING_DIAGNOSTIC),
        ("'b', start=1", BINDING_DIAGNOSTIC),
        ("other='b'", BINDING_DIAGNOSTIC),
        ("'b', end=3", BINDING_DIAGNOSTIC),
        ("'b', unknown=1", BINDING_DIAGNOSTIC),
        ("1", "str.find requires a string substring"),
        ("'b', 1.0", "str.find requires an integer start"),
        ("'b', '1'", "str.find requires an integer start"),
    ],
)
def test_unsupported_argument_shapes_are_refused_cleanly(
    arguments: str, diagnostic: str
) -> None:
    proofs = prove_text(f"def main() -> None:\n    'abc'.find({arguments})\n")
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert diagnostic in str(proofs[0].proof)


@pytest.mark.parametrize("keyword", ["ctx", "var_name", "self"])
@pytest.mark.parametrize("aliased", [True, False], ids=["saved-method", "direct"])
def test_keywords_cannot_collide_with_dispatch_metadata(
    keyword: str, aliased: bool
) -> None:
    setup = "    search = 'abc'.find\n" if aliased else ""
    target = "search" if aliased else "'abc'.find"
    proofs = prove_text(f"def main() -> None:\n{setup}    {target}('b', {keyword}=1)\n")
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "unsupported call argument binding: reserved keyword" in str(proofs[0].proof)


@pytest.mark.parametrize("keyword", ["ctx", "var_name", "self"])
def test_wrapped_method_keywords_cannot_bypass_binding_refusal(keyword: str) -> None:
    proofs = prove_text(
        "def main() -> None:\n"
        "    search = 'abc'.find.__call__\n"
        f"    search('b', {keyword}=1)\n"
    )
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "explicit __call__ attributes are not supported" in str(proofs[0].proof)


def test_saved_method_keeps_negative_start_and_legitimate_missing_results() -> None:
    proofs = prove_text(
        "def main() -> None:\n"
        "    search = 'abc'.find\n"
        "    assert search('b', -2) == 1\n"
        "    assert search('missing', -2) == -1\n"
    )
    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]
