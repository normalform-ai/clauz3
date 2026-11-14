"""search_files runs ripgrep and returns bounded, typed matches."""

import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import deal
import pytest

Search = Callable[[str, str, str, int], list[Any]]


@pytest.fixture
def search_files(filesystem_effects: ModuleType) -> Search:
    return cast(Search, filesystem_effects.search_files)


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("import os\nTODO = 1\n")
    (tmp_path / "src/notes.txt").write_text("TODO later\r\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build/gen.py").write_text("TODO generated\n")
    (tmp_path / ".gitignore").write_text("build/\n")
    # ripgrep honours .gitignore only inside a git repository.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def rows(matches: list[Any]) -> list[tuple[str, int, str]]:
    return sorted((m.path, m.line_number, m.text) for m in matches)


def test_returns_path_line_number_and_text(search_files: Search, tree: Path) -> None:
    assert rows(search_files("TODO", "src", "", 10)) == [
        ("src/app.py", 2, "TODO = 1"),
        ("src/notes.txt", 1, "TODO later"),
    ]


def test_glob_restricts_file_names(search_files: Search, tree: Path) -> None:
    assert rows(search_files("TODO", "src", "*.py", 10)) == [
        ("src/app.py", 2, "TODO = 1"),
    ]


def test_respects_gitignore(search_files: Search, tree: Path) -> None:
    assert [m.path for m in search_files("generated", ".", "", 10)] == []


def test_stops_at_max_results(search_files: Search, tree: Path) -> None:
    assert len(search_files("TODO", "src", "", 1)) == 1


def test_no_matches_returns_empty_list(search_files: Search, tree: Path) -> None:
    assert search_files("absent-text", "src", "", 10) == []


def test_pattern_starting_with_dash_is_a_pattern(
    search_files: Search, tree: Path
) -> None:
    (tree / "src/flags.txt").write_text("use -v here\n")
    assert rows(search_files("-v", "src", "", 10)) == [
        ("src/flags.txt", 1, "use -v here"),
    ]


def test_regex_patterns(search_files: Search, tree: Path) -> None:
    assert rows(search_files(r"^import \w+$", "src", "", 10)) == [
        ("src/app.py", 1, "import os"),
    ]


def test_ripgrep_errors_propagate(search_files: Search, tree: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        search_files("(", "src", "", 10)


@pytest.mark.parametrize("pattern,max_results", [("", 10), ("TODO", 0), ("TODO", 1001)])
def test_preconditions(
    search_files: Search, tree: Path, pattern: str, max_results: int
) -> None:
    with pytest.raises(deal.PreContractError):
        search_files(pattern, "src", "", max_results)
