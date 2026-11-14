"""search_files runs ripgrep and returns bounded, typed matches."""

import os
import subprocess
import sys
import time
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
    paths = [m.path for m in search_files("TODO", ".", "", 10)]
    assert "./src/app.py" in paths
    assert not [p for p in paths if "build/" in p]


def test_ignore_files_apply_only_at_or_below_path(
    search_files: Search, tree: Path
) -> None:
    # The repo-root .gitignore ignores src/gen.py. It is above "src", so a
    # search of "src" must not honour it; a search of "." must, since the
    # ignore file sits at (not above) that search root.
    (tree / "src/gen.py").write_text("TODO generated in src\n")
    with (tree / ".gitignore").open("a") as fh:
        fh.write("src/gen.py\n")
    assert ("src/gen.py", 1, "TODO generated in src") in rows(
        search_files("TODO", "src", "", 10)
    )
    paths = [m.path for m in search_files("TODO", ".", "", 10)]
    assert not [p for p in paths if p.endswith("src/gen.py")]


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


def test_missing_path_raises(search_files: Search, tree: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        search_files("TODO", "missing", "", 10)


def test_non_utf8_line(search_files: Search, tree: Path) -> None:
    (tree / "src/latin1.txt").write_bytes(b"TODO \xff here\n")
    matches = search_files("TODO", "src/latin1.txt", "", 10)
    assert len(matches) == 1
    assert matches[0].text.startswith("TODO ")


def test_overall_timeout_while_streaming(
    filesystem_effects: ModuleType,
    tree: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A real rg that hangs (or is simply slow) must not block search_files
    # forever: an overall timeout must cut it off even while matches are
    # still streaming in (before stdout ever reaches EOF). The fake rg is a
    # single process (its shebang runs the interpreter directly, not a shell
    # that could fork an orphaned grandchild that outlives a kill and keeps
    # its inherited stdout pipe open).
    fake_bin = tmp_path_factory.mktemp("fake-rg-bin")
    fake_rg = fake_bin / "rg"
    fake_rg.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(5)\n")
    fake_rg.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(filesystem_effects, "DEFAULT_TIMEOUT_SECONDS", 0.2)
    search = cast(Search, filesystem_effects.search_files)
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        search("TODO", "src", "", 10)
    assert time.monotonic() - start < 2


def test_ignores_the_users_ripgrep_config(
    search_files: Search,
    tree: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A user config enabling --follow would make rg descend into a symlink
    # under the searched path. search_files must ignore it.
    outside = tmp_path_factory.mktemp("outside-link-target")
    (outside / "linked.txt").write_text("OUTSIDE_SENTINEL\n")
    (tree / "src/link").symlink_to(outside, target_is_directory=True)
    config = tmp_path_factory.mktemp("ripgrep-config") / "ripgreprc"
    config.write_text("--follow\n")
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(config))
    assert search_files("OUTSIDE_SENTINEL", "src", "", 10) == []
