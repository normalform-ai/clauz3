"""Python str.index must not expose Z3's missing-substring sentinel."""

from textwrap import indent

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text

SEARCH_DIAGNOSTIC = "str.index search may raise ValueError"
BINDING_DIAGNOSTIC = "unsupported str.index argument binding"


@pytest.mark.parametrize(
    ("text", "needle", "start"),
    [
        ("abc", "b", None),
        ("ababa", "ba", 2),
        ("abc", "b", -2),
        ("abc", "a", -100),
        ("abc", "", 0),
        ("abc", "", 3),
        ("", "", None),
        ("abc", "b", True),
        ("Ω😀Ω", "😀", None),
    ],
)
@pytest.mark.parametrize("correct", [True, False], ids=["correct", "incorrect"])
def test_successful_search_agrees_with_python(
    text: str, needle: str, start: int | None, correct: bool
) -> None:
    expected = text.index(needle) if start is None else text.index(needle, start)
    arguments = repr(needle) + ("" if start is None else f", {start!r}")
    asserted = expected if correct else expected + 1
    source = (
        f"def main() -> None:\n    assert {text!r}.index({arguments}) == {asserted}\n"
    )

    proofs = prove_text(source)

    expected_conclusion = Conclusion.OK if correct else Conclusion.FAIL
    assert [proof.proof.conclusion for proof in proofs] == [expected_conclusion]


@pytest.mark.parametrize(
    ("text", "needle", "start"),
    [("abc", "missing", 0), ("abc", "b", 4), ("abc", "", 4), ("", "", 1)],
)
def test_missing_search_is_refused_instead_of_returning_minus_one(
    text: str, needle: str, start: int
) -> None:
    with pytest.raises(ValueError, match="substring not found"):
        text.index(needle, start)
    source = (
        f"def main() -> None:\n    assert {text!r}.index({needle!r}, {start}) == -1\n"
    )

    proofs = prove_text(source)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert SEARCH_DIAGNOSTIC in str(proofs[0].proof)


@pytest.mark.parametrize(
    ("arguments", "expression"),
    [
        ("text: str", "text.index('a')"),
        ("needle: str", "'abc'.index(needle)"),
        ("start: int", "'abc'.index('a', start)"),
    ],
)
def test_uncertain_symbolic_search_is_refused(arguments: str, expression: str) -> None:
    proofs = prove_text(
        f"def main({arguments}) -> None:\n    assert {expression} >= 0\n"
    )

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert SEARCH_DIAGNOSTIC in str(proofs[0].proof)


def test_success_guard_does_not_use_a_callers_precondition() -> None:
    proofs = prove_text(
        "import clauz3\n"
        "@clauz3.requires(lambda text: 'a' in text)\n"
        "def main(text: str) -> None:\n    assert text.index('a') >= 0\n"
    )

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert SEARCH_DIAGNOSTIC in str(proofs[0].proof)


def test_simplifiably_successful_symbolic_search_remains_supported() -> None:
    proofs = prove_text(
        "def main(text: str) -> None:\n    assert text.index('') == 0\n"
    )

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]


@pytest.mark.parametrize(
    "body",
    [
        "unused = True and 'abc'.index('missing')\nassert False",
        "if True:\n    'abc'.index('missing')\nassert True",
    ],
)
def test_live_search_refusal_cannot_be_hidden_by_allowed_exceptions(
    body: str,
) -> None:
    source = (
        "import deal\n@deal.raises(ValueError)\ndef main() -> None:\n"
        + indent(body, "    ")
        + "\n"
    )

    proofs = prove_text(source)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert SEARCH_DIAGNOSTIC in str(proofs[0].proof)


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
        ("1", "str.index requires a string substring"),
        ("'b', 1.0", "str.index requires an integer start"),
        ("'b', '1'", "str.index requires an integer start"),
    ],
)
def test_unsupported_argument_shapes_are_refused_cleanly(
    arguments: str, diagnostic: str
) -> None:
    proofs = prove_text(f"def main() -> None:\n    'abc'.index({arguments})\n")

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert diagnostic in str(proofs[0].proof)


@pytest.mark.parametrize("keyword", ["ctx", "var_name"])
@pytest.mark.parametrize("aliased", [True, False], ids=["saved-method", "direct"])
def test_reserved_keyword_cannot_collide_with_dispatch_metadata(
    keyword: str, aliased: bool
) -> None:
    setup = "    search = 'abc'.index\n" if aliased else ""
    target = "search" if aliased else "'abc'.index"
    source = f"def main() -> None:\n{setup}    {target}('b', {keyword}=1)\n"

    proofs = prove_text(source)

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "unsupported call argument binding: reserved keyword" in str(proofs[0].proof)


def test_saved_method_retains_supported_positional_arguments() -> None:
    proofs = prove_text(
        "def main() -> None:\n"
        "    search = 'abc'.index\n"
        "    assert search('b', -2) == 1\n"
    )

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]


@pytest.mark.parametrize("keyword", ["ctx", "var_name"])
def test_wrapped_method_keyword_cannot_bypass_the_binding_refusal(
    keyword: str,
) -> None:
    proofs = prove_text(
        "def main() -> None:\n"
        "    search = 'abc'.index.__call__\n"
        f"    search('b', {keyword}=1)\n"
    )

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "explicit __call__ attributes are not supported" in str(proofs[0].proof)
