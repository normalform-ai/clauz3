"""Path models preserve their imported flavour and refuse platform ambiguity."""

import ntpath
import posixpath

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_text


@pytest.mark.parametrize(
    ("statement", "callee", "diagnostic"),
    [
        ("import os.path", "os.path.join", "no unambiguous definition"),
        ("import os as system", "system.path.join", "no unambiguous definition"),
        ("from os import path as paths", "paths.join", "no unambiguous definition"),
        ("from os.path import join", "join", "platform-dependent os.path.join"),
        (
            "from os.path import join as combine",
            "combine",
            "platform-dependent os.path.join",
        ),
    ],
)
def test_os_path_join_does_not_guess_a_platform(
    statement: str, callee: str, diagnostic: str
) -> None:
    source = (
        f'{statement}\ndef main() -> None:\n    assert {callee}("a", "b") == "a/b"\n'
    )

    proofs = prove_text(source, extra_allowed_imports=["os"])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert diagnostic in str(proofs[0].proof)


@pytest.mark.parametrize("module", ["posixpath", "ntpath"])
@pytest.mark.parametrize("alias", ["join", "combine"])
def test_bare_imported_join_keeps_its_flavour(module: str, alias: str) -> None:
    expected = (posixpath if module == "posixpath" else ntpath).join("a", "b")
    source = (
        f"from {module} import join as {alias}\ndef main() -> None:\n"
        f'    assert {alias}("a", "b") == {expected!r}\n'
    )

    proofs = prove_text(source, extra_allowed_imports=[module])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.OK]


@pytest.mark.parametrize("module", ["posixpath", "ntpath"])
@pytest.mark.parametrize("arguments", ["", "1", '"a", 2', '"a", []'])
def test_unsupported_join_arguments_are_refused(module: str, arguments: str) -> None:
    source = f"import {module}\ndef main() -> None:\n    {module}.join({arguments})\n"

    proofs = prove_text(source, extra_allowed_imports=[module])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    diagnostic = (
        "unsupported registered call argument binding"
        if not arguments
        else "path join requires"
    )
    assert diagnostic in str(proofs[0].proof)


@pytest.mark.parametrize("module", ["posixpath", "ntpath"])
@pytest.mark.parametrize("arguments", ["", "1"])
def test_invalid_join_in_a_dead_operand_cannot_suppress_a_false_assertion(
    module: str, arguments: str
) -> None:
    source = (
        f"import deal\nimport {module}\n"
        "@deal.raises(TypeError)\ndef main() -> None:\n"
        f"    _ = False and {module}.join({arguments})\n"
        "    assert False\n"
    )

    proofs = prove_text(source, extra_allowed_imports=[module])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.FAIL]
    assert "assertion" in str(proofs[0].proof)


@pytest.mark.parametrize("module", ["posixpath", "ntpath"])
def test_join_does_not_silently_ignore_keyword_arguments(module: str) -> None:
    source = (
        f"import {module}\ndef main() -> None:\n"
        f'    assert {module}.join("a", unknown="ignored") == "a"\n'
    )

    proofs = prove_text(source, extra_allowed_imports=[module])

    assert [proof.proof.conclusion for proof in proofs] == [Conclusion.SKIP]
    assert "unsupported registered call argument binding" in str(proofs[0].proof)
