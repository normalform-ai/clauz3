"""edit_file replaces exact text in place and never half-writes a file."""

import os
import stat
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import deal
import pytest

Edit = Callable[..., None]


@pytest.fixture
def edit_file(filesystem_effects: ModuleType) -> Edit:
    return cast(Edit, filesystem_effects.edit_file)


def test_replaces_a_unique_occurrence(edit_file: Edit, tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x = 1\ny = 2\n")
    edit_file(str(target), "y = 2", "y = 3")
    assert target.read_text() == "x = 1\ny = 3\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.py"]


def test_overlapping_matches_count_every_position(
    edit_file: Edit, tmp_path: Path
) -> None:
    target = tmp_path / "a.py"
    target.write_text("aaa\n")
    with pytest.raises(ValueError, match="found 2"):
        edit_file(str(target), "aa", "b")
    assert target.read_text() == "aaa\n"


@pytest.mark.parametrize("text,found", [("x = 1\n", 0), ("y\ny\n", 2)])
def test_ambiguous_or_missing_text_leaves_file_unchanged(
    edit_file: Edit, tmp_path: Path, text: str, found: int
) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(text.encode())
    with pytest.raises(ValueError, match=f"found {found}"):
        edit_file(str(target), "y", "z")
    assert target.read_bytes() == text.encode()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.py"]


def test_replace_all_replaces_every_occurrence(edit_file: Edit, tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("y\ny\n")
    edit_file(str(target), "y", "z", replace_all=True)
    assert target.read_text() == "z\nz\n"


def test_replace_all_still_requires_an_occurrence(
    edit_file: Edit, tmp_path: Path
) -> None:
    target = tmp_path / "a.py"
    target.write_text("x\n")
    with pytest.raises(ValueError, match="found 0"):
        edit_file(str(target), "y", "z", replace_all=True)


def test_preserves_crlf_line_endings(edit_file: Edit, tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"one\r\ntwo\r\n")
    edit_file(str(target), "two", "three")
    assert target.read_bytes() == b"one\r\nthree\r\n"


def test_preserves_permission_bits(edit_file: Edit, tmp_path: Path) -> None:
    target = tmp_path / "run.sh"
    target.write_text("echo hi\n")
    target.chmod(0o750)
    edit_file(str(target), "hi", "bye")
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o750


def test_missing_file_is_not_created(edit_file: Edit, tmp_path: Path) -> None:
    target = tmp_path / "missing.py"
    with pytest.raises(FileNotFoundError):
        edit_file(str(target), "a", "b")
    assert not target.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits")
def test_refuses_a_read_only_file(edit_file: Edit, tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"x = 1\n")
    target.chmod(0o444)
    with pytest.raises(PermissionError):
        edit_file(str(target), "x", "y")
    assert target.read_bytes() == b"x = 1\n"


@pytest.mark.parametrize("old,new", [("", "x"), ("same", "same")])
def test_preconditions_reject_empty_or_unchanged_text(
    edit_file: Edit, tmp_path: Path, old: str, new: str
) -> None:
    target = tmp_path / "a.py"
    target.write_text("same\n")
    with pytest.raises(deal.PreContractError):
        edit_file(str(target), old, new)
