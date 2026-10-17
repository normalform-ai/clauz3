"""Runtime semantics and proof boundary for the filesystem line readers."""

from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_path

STDLIB = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib"
FS = STDLIB / "filesystem"
CASES = [
    ("text_never_read_under_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "text_never_read_under_pass.py",
        Conclusion.OK,
        "assertion, assertion, guarantee",
    ),
    ("text_only_read_under_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "text_only_read_under_pass.py",
        Conclusion.OK,
        "assertion, assertion, guarantee",
    ),
    ("text_parent_path_fail.py", Conclusion.FAIL, "assertion"),
    ("text_positive_line_fail.py", Conclusion.FAIL, "assertion"),
    ("text_reads_at_most_fail.py", Conclusion.FAIL, "guarantee"),
    ("text_reads_at_most_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
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
    tmp_path: Path,
    filesystem_effects: ModuleType,
    contents: bytes,
    line_number: int,
    expected: str,
) -> None:
    read_line = cast(Callable[[str, int], str], filesystem_effects.read_line)
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
        FS / "tests/cases" / case_name,
        trusted_roots=[FS / "tools/filesystem/trusted"],
        import_roots=[FS],
    )
    assert len(results) == 1
    proof = results[0].proof
    assert proof.conclusion == conclusion
    assert proof.error is None
    assert proof.description == description


def test_text_proof_cases_are_all_classified() -> None:
    on_disk = {case.name for case in (FS / "tests/cases").glob("text_*.py")}
    pinned = {case_name for case_name, _, _ in CASES}
    assert on_disk == pinned, "Pin every text fixture's conclusion and description"
