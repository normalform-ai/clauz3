"""Write scopes distinguish exact names, directory boundaries and recursive effects."""

import inspect
from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.differential_contracts import check_contract
from clauz3.guarantees import contract_descriptions
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import _concrete_contracts, _selected_layer_context, prove_text

LIBRARY = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib/filesystem"
ROOTS = [LIBRARY / "tools/filesystem/trusted"]


def program(guarantee: str, body: str) -> str:
    return f"""import clauz3
from clauz3 import FilePath, DirectoryPath
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import (
    write_file, append_file, touch, chmod, make_dir, remove, copy, move, read_file,
)
@clauz3.guarantee(fs.{guarantee})
def main() -> None:
    {body}
"""


@pytest.mark.parametrize(
    "scope,body,allowed",
    [
        (
            'only_writes_files([FilePath("test.md")])',
            'write_file("test.md", "hello")',
            True,
        ),
        (
            'only_writes_files([FilePath("test.md")])',
            'write_file("./test.md", "hello")',
            True,
        ),
        (
            'only_writes_files([FilePath("./test.md")])',
            'write_file("test.md", "hello")',
            True,
        ),
        (
            'only_writes_files([FilePath("test.md")])',
            'write_file("test.md.bak", "hello")',
            False,
        ),
        (
            'only_writes_files([FilePath("test.md")])',
            'write_file("test.md/child", "hello")',
            False,
        ),
        (
            'only_writes_files([FilePath("test.md")])',
            'write_file("/test.md", "hello")',
            False,
        ),
        (
            'only_writes_files([FilePath("a"), FilePath("b")])',
            'append_file("b", "hello")',
            True,
        ),
        ('only_writes_files([FilePath("test.md")])', 'touch("test.md")', True),
        ('only_writes_files([FilePath("test.md")])', 'chmod("test.md", 420)', True),
        ('only_writes_files([FilePath("test.md")])', 'make_dir("test.md")', False),
        ('only_writes_files([FilePath("test.md")])', 'remove("test.md")', False),
        (
            'only_writes_files([FilePath("test.md")])',
            'copy("source", "test.md")',
            False,
        ),
        (
            'only_writes_files([FilePath("source"), FilePath("test.md")])',
            'move("source", "test.md")',
            False,
        ),
        ("only_writes_files([])", 'touch("test.md")', False),
        ("only_writes_files([])", "pass", True),
        ("only_writes_files([])", 'read_file("test.md")', True),
        (
            'only_writes_files_under(DirectoryPath("data"))',
            'write_file("data/a", "hello")',
            True,
        ),
        (
            'only_writes_files_under(DirectoryPath("data/"))',
            'write_file("./data/a", "hello")',
            True,
        ),
        (
            'only_writes_files_under(DirectoryPath("./data/"))',
            'write_file("data/a", "hello")',
            True,
        ),
        (
            'only_writes_files_under(DirectoryPath("data"))',
            'write_file("database/a", "hello")',
            False,
        ),
        (
            'only_writes_files_under(DirectoryPath("data"))',
            'write_file("data-old/a", "hello")',
            False,
        ),
        (
            'only_writes_files_under(DirectoryPath("data"))',
            'write_file("/data/a", "hello")',
            False,
        ),
        ('only_writes_files_under(DirectoryPath("data"))', 'remove("data")', True),
        (
            'only_writes_files_under(DirectoryPath("data"))',
            'make_dir("data/sub")',
            True,
        ),
        (
            'only_writes_files_under(DirectoryPath("data"))',
            'copy("outside", "data/sub")',
            True,
        ),
        (
            'only_writes_files_under(DirectoryPath("data"))',
            'move("outside", "data/sub")',
            False,
        ),
        (
            'only_writes_files_under(DirectoryPath("data"))',
            'move("data/sub", "outside")',
            False,
        ),
        (
            'only_writes_files_under(DirectoryPath("data"))',
            'move("data/a", "data/b")',
            True,
        ),
        (
            'only_writes_files_under(DirectoryPath("."))',
            'write_file("./a", "hello")',
            True,
        ),
        (
            'only_writes_files_under(DirectoryPath("."))',
            'write_file("/a", "hello")',
            False,
        ),
        (
            'only_writes_files_under(DirectoryPath("/"))',
            'write_file("/a", "hello")',
            True,
        ),
        (
            'only_writes_files_under(DirectoryPath("/"))',
            'write_file("a", "hello")',
            False,
        ),
        (
            'only_writes_files_under(DirectoryPath("/data/"))',
            'write_file("/database/a", "hello")',
            False,
        ),
        (
            'only_writes_files_under(DirectoryPath("data"))',
            'write_file("data/../outside", "hello")',
            False,
        ),
        ('only_write_under("test.md")', 'write_file("test.md.bak", "hello")', True),
        ("writes_at_most(1)", 'copy("a", "b")', True),
        ("writes_at_most(1)", 'move("a", "b")', False),
        ("writes_at_most(2)", 'move("a", "b")', True),
        ("creates_at_most(1)", 'make_dir("data")', True),
        ("no_creates()", 'make_dir("data")', False),
    ],
)
def test_write_scope(scope: str, body: str, allowed: bool) -> None:
    results = prove_text(
        program(scope, body), trusted_roots=ROOTS, import_paths=[LIBRARY]
    )
    assert len(results) == 1
    assert results[0].proof.conclusion is (
        Conclusion.OK if allowed else Conclusion.FAIL
    ), results[0].proof


@pytest.mark.parametrize(
    "scope",
    [
        'only_writes_files(["test.md"])',
        'only_writes_files(FilePath("test.md"))',
        'only_writes_files([DirectoryPath("data")])',
        'only_writes_files_under("data")',
        'only_writes_files_under(FilePath("data"))',
    ],
)
def test_wrong_write_path_types_are_refused(scope: str) -> None:
    results = prove_text(
        program(scope, "pass"), trusted_roots=ROOTS, import_paths=[LIBRARY]
    )
    assert results[0].proof.conclusion is Conclusion.SKIP
    assert "TypeError" in str(results[0].proof)


def test_handoff_exact_write_proves_displays_and_executes_only_in_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = program(
        'only_writes_files([FilePath("test.md")])', 'write_file("test.md", "hello")'
    )
    monkeypatch.chdir(tmp_path)
    with _selected_layer_context(ROOTS, [LIBRARY]):
        results = prove_text(source, trusted_roots=ROOTS, import_paths=[LIBRARY])
        assert results[0].proof.conclusion is Conclusion.OK
        descriptions = contract_descriptions(source=source, target="main")
        description = next(iter(descriptions.values()))
        assert description["parameters"] == {"files": ["test.md"]}
        assert 'exact paths ["test.md"]' in str(description["text"])
        assert "Parent directories" in str(description["text"])
        specs = _concrete_contracts(source=source, source_name="plan.py", target="main")
        signatures = {"write_file": inspect.signature(lambda path, content: None)}
        assert check_contract(
            specs[0],
            [
                (
                    "tools.filesystem.trusted.effects.write_file",
                    ("./test.md", "hello"),
                    {},
                )
            ],
            signatures,
        )
        assert not check_contract(
            specs[0],
            [
                (
                    "tools.filesystem.trusted.effects.write_file",
                    ("test.md.bak", "hello"),
                    {},
                )
            ],
            signatures,
        )
        outcome = execute(
            source,
            source_name="plan.py",
            policy=build_policy(trusted_roots=ROOTS, import_roots=[LIBRARY]),
            import_roots=[LIBRARY],
        )
        assert len(outcome.effects) == 1
    assert (tmp_path / "test.md").read_text() == "hello"
    assert not (tmp_path / "test.md.bak").exists()
