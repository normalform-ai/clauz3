"""Path-join proofs agree with the corresponding Python platform semantics."""

import ntpath
import posixpath
from textwrap import indent

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text

_CONCRETE_CASES = [
    pytest.param("posixpath", ("a", "b"), id="posix-relative"),
    pytest.param("posixpath", ("a", "/b"), id="posix-absolute-reset"),
    pytest.param("posixpath", ("", "b"), id="posix-empty-prefix"),
    pytest.param("posixpath", ("a/", "b"), id="posix-existing-separator"),
    pytest.param("posixpath", ("a", ""), id="posix-empty-tail"),
    pytest.param("posixpath", ("", "", ""), id="posix-all-empty"),
    pytest.param(
        "posixpath", ("/root", "child", "/reset", "leaf"), id="posix-multiple-reset"
    ),
    pytest.param("posixpath", ("//host", "dir", "leaf"), id="posix-double-root"),
    pytest.param("posixpath", ("a", "..", "b"), id="posix-parent-not-normalized"),
    pytest.param("posixpath", ("root", "/", "leaf"), id="posix-root-component"),
    pytest.param("ntpath", ("a", "b"), id="windows-relative"),
    pytest.param("ntpath", ("", "b"), id="windows-empty-prefix"),
    pytest.param("ntpath", ("a\\", "b"), id="windows-existing-separator"),
    pytest.param("ntpath", ("a", ""), id="windows-empty-tail"),
    pytest.param("ntpath", ("C:\\base", "\\reset"), id="windows-root-keeps-drive"),
    pytest.param("ntpath", ("C:\\base", "D:\\other", "tail"), id="windows-drive-reset"),
    pytest.param("ntpath", ("C:\\base", "C:tail"), id="windows-same-drive-relative"),
    pytest.param("ntpath", ("C:\\base", "c:tail"), id="windows-drive-case"),
    pytest.param("ntpath", ("C:\\base", "D:tail"), id="windows-other-drive-relative"),
    pytest.param("ntpath", ("\\\\server\\share", "dir"), id="windows-unc-share"),
    pytest.param(
        "ntpath", ("C:/base", "leaf", "/reset"), id="windows-alternate-separator"
    ),
    pytest.param("ntpath", ("a", "u{41}"), id="windows-literal-z3-escape"),
    pytest.param("ntpath", ("Ω", "😀"), id="windows-unicode"),
    pytest.param("ntpath", ("a", "b\x00c"), id="windows-nul"),
    pytest.param("ntpath", ("\U0010ffff", "\ud800"), id="windows-full-unicode"),
    pytest.param("posixpath", ("\U0010ffff", "\ud800"), id="posix-full-unicode"),
]


@pytest.mark.parametrize(("module", "parts"), _CONCRETE_CASES)
@pytest.mark.parametrize("correct", [True, False], ids=["valid", "incorrect"])
def test_concrete_path_join_matches_stdlib_oracle(
    module: str, parts: tuple[str, ...], correct: bool
) -> None:
    oracle = posixpath if module == "posixpath" else ntpath
    expected = oracle.join(*parts)
    if not correct:
        # The former slash-concatenation model proved these wrong outputs.
        # Refuting them is load-bearing where the native result differs.
        wrong = "/".join(parts)
        expected = wrong if wrong != expected else expected + "__incorrect__"
    expected_expression = repr(expected)
    arguments = ", ".join(repr(part) for part in parts)
    source = (
        f"import {module} as paths\n"
        "def main() -> None:\n"
        f"    assert paths.join({arguments}) == {expected_expression}\n"
    )

    results = prove_text(source, extra_allowed_imports=[module])

    conclusion = Conclusion.OK if correct else Conclusion.FAIL
    assert [result.proof.conclusion for result in results] == [conclusion]


@pytest.mark.parametrize(
    ("parameters", "body", "conclusion"),
    [
        pytest.param(
            "base: str",
            'assert paths.join(base, "/reset") == "/reset"',
            Conclusion.OK,
            id="absolute-tail-discards-symbolic-prefix",
        ),
        pytest.param(
            "tail: str",
            'assert paths.join("", tail) == tail',
            Conclusion.OK,
            id="empty-prefix-preserves-symbolic-tail",
        ),
        pytest.param(
            "tail: str",
            'if not tail.startswith("/"):\n'
            '    assert paths.join("/base/", tail) == "/base/" + tail',
            Conclusion.OK,
            id="existing-separator-with-symbolic-tail",
        ),
        pytest.param(
            "base: str",
            'if base == "" or base.endswith("/"):\n'
            '    assert paths.join(base, "") == base\n'
            "else:\n"
            '    assert paths.join(base, "") == base + "/"',
            Conclusion.OK,
            id="empty-tail-conditional-separator",
        ),
        pytest.param(
            "base: str, tail: str",
            'assert paths.join(base, "/reset", tail) == paths.join("/reset", tail)',
            Conclusion.OK,
            id="absolute-middle-discards-prefix",
        ),
        pytest.param(
            "base: str",
            'assert paths.join(base, "/reset") == base + "//reset"',
            Conclusion.FAIL,
            id="old-symbolic-concatenation-is-false",
        ),
    ],
)
def test_symbolic_posix_path_join(
    parameters: str, body: str, conclusion: Conclusion
) -> None:
    source = (
        "import posixpath as paths\n"
        f"def main({parameters}) -> None:\n"
        f"{indent(body, '    ')}\n"
    )

    results = prove_text(source, extra_allowed_imports=["posixpath"])

    assert [result.proof.conclusion for result in results] == [conclusion]


def test_symbolic_windows_path_join_is_refused() -> None:
    source = (
        "import ntpath as paths\n"
        "def main(base: str) -> None:\n"
        '    assert paths.join(base, "leaf") == base + "/leaf"\n'
    )

    results = prove_text(source, extra_allowed_imports=["ntpath"])

    assert [result.proof.conclusion for result in results] == [Conclusion.SKIP]
    assert "symbolic Windows path join (ntpath.join) is not supported" in str(
        results[0].proof
    )
