"""Runtime semantics and proof boundary for the text library."""

import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.libraries.install import install_library
from clauz3.prover import prove_path

STDLIB = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib"
TEXT = STDLIB / "text"
CASES = [
    ("never_read_under_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "never_read_under_pass.py",
        Conclusion.OK,
        "assertion, assertion, guarantee",
    ),
    ("only_read_under_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "only_read_under_pass.py",
        Conclusion.OK,
        "assertion, assertion, guarantee",
    ),
    ("parent_path_fail.py", Conclusion.FAIL, "assertion"),
    ("positive_line_fail.py", Conclusion.FAIL, "assertion"),
    ("reads_at_most_fail.py", Conclusion.FAIL, "guarantee"),
    ("reads_at_most_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
]


@pytest.mark.parametrize(
    ("contents", "line_number", "expected"),
    [
        (b"first\nsecond\n", 1, "first"),
        (b"first\r\nsecond\r\n", 2, "second"),
        (b"first\rsecond\r", 2, "second"),
        (b"first\n  second \t", 2, "  second \t"),
        (b"first\n\nthird", 2, ""),
        (b"", 1, ""),
        (b"first\n", 2, ""),
        ("café\n".encode(), 1, "café"),
    ],
)
def test_read_line_runtime(
    tmp_path: Path, contents: bytes, line_number: int, expected: str
) -> None:
    spec = importlib.util.spec_from_file_location(
        "text_effects", TEXT / "tools/text/trusted/effects.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    read_line = cast(Callable[[str, int], str], module.read_line)
    source = tmp_path / "message.txt"
    source.write_bytes(contents)
    assert read_line(str(source), line_number) == expected
    assert source.read_bytes() == contents


@pytest.mark.parametrize(
    ("case_name", "conclusion", "description"),
    CASES,
    ids=[case_name for case_name, _, _ in CASES],
)
def test_text_proofs(case_name: str, conclusion: Conclusion, description: str) -> None:
    results = prove_path(
        TEXT / "tests/cases" / case_name,
        trusted_roots=[TEXT / "tools/text/trusted"],
        import_roots=[TEXT, STDLIB / "filesystem"],
    )
    assert len(results) == 1
    proof = results[0].proof
    assert proof.conclusion == conclusion
    assert proof.error is None
    assert proof.description == description


def test_text_proof_cases_are_all_classified() -> None:
    on_disk = {case.name for case in (TEXT / "tests/cases").glob("*.py")}
    pinned = {case_name for case_name, _, _ in CASES}
    assert on_disk == pinned, "Pin every text fixture's conclusion and description"


def test_text_installs_dependency(tmp_path: Path) -> None:
    install_library("stdlib:text", into=tmp_path)
    assert (tmp_path / ".clauz3/tools/text/trusted/effects.py").is_file()
    assert (tmp_path / ".clauz3/tools/filesystem/trusted/contracts.py").is_file()
