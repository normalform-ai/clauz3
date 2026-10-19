"""Exact files and directory boundaries agree in proof, display, and replay."""

import inspect
import json
from pathlib import Path

import pytest

from clauz3 import DirectoryPath, FilePath
from clauz3._vendor.deal_solver import Conclusion
from clauz3.differential_contracts import check_contract
from clauz3.guarantees import contract_descriptions
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import (
    ProverConfigError,
    _concrete_contracts,
    _selected_layer_context,
    prove_text,
)

LIBRARY = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib/filesystem"
ROOTS = [LIBRARY / "tools/filesystem/trusted"]


def program(guarantee: str, body: str, imports: str = "") -> str:
    return f"""import clauz3
from clauz3 import FilePath, DirectoryPath
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file, find, copy, move
{imports}
@clauz3.guarantee(fs.{guarantee})
def main() -> None:
    {body}
"""


@pytest.mark.parametrize(
    "guarantee,body,allowed",
    [
        ('only_reads_files([FilePath("README.md")])', 'read_file("README.md")', True),
        ('only_reads_files([FilePath("README.md")])', 'read_file("./README.md")', True),
        ('only_reads_files([FilePath("./README.md")])', 'read_file("README.md")', True),
        (
            'only_reads_files([FilePath("README.md")])',
            'read_file("README.md.bak")',
            False,
        ),
        (
            'only_reads_files([FilePath("README.md")])',
            'read_file("README.md/secret")',
            False,
        ),
        ('only_reads_files([FilePath("README.md")])', 'read_file("/README.md")', False),
        ('only_reads_files([FilePath("a"), FilePath("b")])', 'read_file("b")', True),
        ("only_reads_files([])", 'read_file("a")', False),
        ("only_reads_files([])", "pass", True),
        ('only_reads_files([FilePath("data")])', 'find("data", [])', False),
        ('only_reads_files([FilePath("data")])', 'copy("data", "out")', False),
        ('only_reads_files([FilePath("data")])', 'move("data", "out")', False),
        ('only_reads_files_under(DirectoryPath("data"))', 'read_file("data/a")', True),
        ('only_reads_files_under(DirectoryPath("data/"))', 'read_file("data/a")', True),
        (
            'only_reads_files_under(DirectoryPath("./data/"))',
            'read_file("data/a")',
            True,
        ),
        (
            'only_reads_files_under(DirectoryPath("data"))',
            'read_file("./data/a")',
            True,
        ),
        (
            'only_reads_files_under(DirectoryPath("data"))',
            'read_file("database/a")',
            False,
        ),
        (
            'only_reads_files_under(DirectoryPath("data"))',
            'read_file("data-old/a")',
            False,
        ),
        (
            'only_reads_files_under(DirectoryPath("data"))',
            'read_file("/data/a")',
            False,
        ),
        ('only_reads_files_under(DirectoryPath("data"))', 'find("data", [])', True),
        ('only_reads_files_under(DirectoryPath("data"))', 'find("data/", [])', True),
        ('only_reads_files_under(DirectoryPath("data"))', 'find(".", [])', False),
        ('only_reads_files_under(DirectoryPath("data"))', 'copy("data", "out")', True),
        ('only_reads_files_under(DirectoryPath("data"))', 'move(".", "out")', False),
        ('only_reads_files_under(DirectoryPath("."))', 'read_file("a")', True),
        ('only_reads_files_under(DirectoryPath("./"))', 'read_file("./a")', True),
        ('only_reads_files_under(DirectoryPath("."))', 'find(".", [])', True),
        ('only_reads_files_under(DirectoryPath("."))', 'read_file("/a")', False),
        ('only_reads_files_under(DirectoryPath("/"))', 'read_file("/a")', True),
        ('only_reads_files_under(DirectoryPath("/"))', 'read_file("a")', False),
        (
            'only_reads_files_under(DirectoryPath("/data/"))',
            'read_file("/database/a")',
            False,
        ),
        (
            'only_reads_files_under(DirectoryPath("/data/"))',
            'read_file("/data/.env")',
            True,
        ),
        (
            'only_reads_files_under(DirectoryPath("data"))',
            'read_file("data/../secret")',
            False,
        ),
        ('only_read_under("README.md")', 'read_file("README.md.bak")', True),
    ],
)
def test_read_scope(guarantee: str, body: str, allowed: bool) -> None:
    source = program(guarantee, body)
    results = prove_text(source, trusted_roots=ROOTS, import_paths=[LIBRARY])
    assert len(results) == 1
    assert results[0].proof.conclusion is (
        Conclusion.OK if allowed else Conclusion.FAIL
    ), results[0].proof


@pytest.mark.parametrize(
    "guarantee",
    [
        'only_reads_files(["README.md"])',
        'only_reads_files(FilePath("README.md"))',
        'only_reads_files([DirectoryPath("data")])',
        'only_reads_files_under("data")',
        'only_reads_files_under(FilePath("data"))',
    ],
)
def test_wrong_path_types_refused(guarantee: str) -> None:
    results = prove_text(
        program(guarantee, "pass"), trusted_roots=ROOTS, import_paths=[LIBRARY]
    )
    assert results[0].proof.conclusion is Conclusion.SKIP
    assert "TypeError" in str(results[0].proof)


@pytest.mark.parametrize("path_type", [FilePath, DirectoryPath])
@pytest.mark.parametrize(
    "value", ["", "../x", "a/../b", "a//b", "a/./b", "a\\b", "a\x00b", "C:a"]
)
def test_invalid_path_literals(
    path_type: type[FilePath] | type[DirectoryPath], value: str
) -> None:
    with pytest.raises(ValueError):
        path_type(value)


@pytest.mark.parametrize(
    "literal,imports",
    [
        ('FilePath("a" + "b")', ""),
        ("FilePath(name)", 'name = "README.md"'),
        ('FilePath(value="README.md")', ""),
        ('FilePath("README.md", "extra")', ""),
        ('FilePath("../README.md")', ""),
        ('FilePath("README.md/")', ""),
        ('FilePath(".")', ""),
        ('FilePath("README.md")', "from pathlib import Path as FilePath"),
        ('FilePath("README.md")', 'FilePath = "oops"'),
        ('FilePath("README.md")', "def FilePath(value: str) -> str:\n    return value"),
        ('clauz3.FilePath("README.md")', "import pathlib as clauz3"),
    ],
)
def test_computed_or_spoofed_constructors_refused(literal: str, imports: str) -> None:
    with pytest.raises(ProverConfigError):
        prove_text(
            program(f"only_reads_files([{literal}])", "pass", imports),
            trusted_roots=ROOTS,
            import_paths=[LIBRARY],
        )


@pytest.mark.parametrize(
    "literal,imports",
    [
        ('FilePath("README.md")', ""),
        ('clauz3.FilePath("README.md")', ""),
        ('F("README.md")', "from clauz3 import FilePath as F"),
        ('c.FilePath("README.md")', "import clauz3 as c"),
    ],
)
def test_typed_paths_reach_display_and_execution(
    literal: str, imports: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "README.md").write_text("example", encoding="utf-8")
    source = program(
        f"only_reads_files([{literal}])", 'read_file("README.md")', imports
    )
    with _selected_layer_context(ROOTS, [LIBRARY]):
        results = prove_text(source, trusted_roots=ROOTS, import_paths=[LIBRARY])
        assert results[0].proof.conclusion is Conclusion.OK
        descriptions = contract_descriptions(source=source, target="main")
        description = next(iter(descriptions.values()))
        assert description["parameters"] == {"files": ["README.md"]}
        assert 'exact files ["README.md"]' in str(description["text"])
        json.dumps(descriptions)
        specs = _concrete_contracts(source=source, source_name="plan.py", target="main")
        # The independent oracle sees the same literal file list.
        signature = inspect.signature(lambda path: None)
        assert check_contract(
            specs[0],
            [("tools.filesystem.trusted.effects.read_file", ("./README.md",), {})],
            {"read_file": signature},
        )
        assert not check_contract(
            specs[0],
            [("tools.filesystem.trusted.effects.read_file", ("README.md.bak",), {})],
            {"read_file": signature},
        )
        outcome = execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=ROOTS, import_roots=[LIBRARY]),
            import_roots=[LIBRARY],
        )
        assert len(outcome.effects) == 1


@pytest.mark.parametrize(
    "guarantee,expected_text",
    [
        ('only_reads_files([FilePath("README.md")])', 'exact files ["README.md"]'),
        ('only_reads_files_under(DirectoryPath("."))', 'directory "."'),
    ],
)
def test_typed_guarantees_can_be_independently_verified(
    guarantee: str, expected_text: str, tmp_path: Path
) -> None:
    from clauz3.approvals.authority import ApprovedLayer
    from clauz3.approvals.profiles.verification import verify
    from clauz3.runner import build_approval_request

    source = program(guarantee, 'read_file("README.md")')
    proofs = prove_text(source, trusted_roots=ROOTS, import_paths=[LIBRARY])
    request = build_approval_request(
        source=source,
        source_name="plan.py",
        target="main",
        proofs=proofs,
        trusted_roots=ROOTS,
        import_roots=[LIBRARY],
    )
    verified = verify(
        request, ApprovedLayer(tuple(ROOTS), (LIBRARY,)), project=str(tmp_path)
    )
    assert verified["signature"]
    assert expected_text in str(verified["contract_descriptions"])
