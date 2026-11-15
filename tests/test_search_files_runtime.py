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


def test_hidden_files_skipped_unless_glob_matches_them(
    search_files: Search, tree: Path
) -> None:
    (tree / "src/.env").write_text("TODO hidden file\n")
    (tree / "src/.secret").mkdir()
    (tree / "src/.secret/key").write_text("TODO hidden directory\n")
    assert rows(search_files("TODO hidden", "src", "", 10)) == []
    assert rows(search_files("TODO hidden", "src", "*.py", 10)) == []
    # ripgrep applies a --glob whitelist before its hidden-file rule.
    assert rows(search_files("TODO hidden", "src", ".*", 10)) == [
        ("src/.env", 1, "TODO hidden file"),
    ]
    assert rows(search_files("TODO hidden", "src", "*", 10)) == [
        ("src/.env", 1, "TODO hidden file"),
        ("src/.secret/key", 1, "TODO hidden directory"),
    ]


def test_missing_path_raises(search_files: Search, tree: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        search_files("TODO", "missing", "", 10)


def test_non_utf8_line(search_files: Search, tree: Path) -> None:
    (tree / "src/latin1.txt").write_bytes(b"TODO \xff here\n")
    matches = search_files("TODO", "src/latin1.txt", "", 10)
    assert len(matches) == 1
    assert matches[0].text.startswith("TODO ")


MATCH_LINE = (
    '{"type":"match","data":{"path":{"text":"./src/app.py"},'
    '"lines":{"text":"TODO = 1\\n"},"line_number":2,'
    '"absolute_offset":0,"submatches":[]}}'
)


@pytest.fixture
def fake_rg(
    filesystem_effects: ModuleType,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[str, float], Search]:
    """Put a fake ``rg`` running ``body`` first on PATH; return search_files.

    The fake is a single process (its shebang runs the interpreter directly,
    not a shell that could fork an orphaned grandchild that outlives a kill
    and keeps its inherited stdout pipe open). ``timeout`` replaces the
    overall timeout; it must outlast the interpreter's start-up.
    """

    def install(body: str, timeout: float) -> Search:
        fake_bin = tmp_path_factory.mktemp("fake-rg-bin")
        fake = fake_bin / "rg"
        fake.write_text(f"#!{sys.executable}\nimport os, sys, time\n{body}\n")
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setattr(filesystem_effects, "DEFAULT_TIMEOUT_SECONDS", timeout)
        return cast(Search, filesystem_effects.search_files)

    return install


def test_overall_timeout_while_streaming(
    fake_rg: Callable[[str, float], Search], tree: Path
) -> None:
    # A slow rg that keeps streaming matches (stdout never reaches EOF) must
    # be cut off by the overall timeout, with no partial list returned.
    search = fake_rg(
        f"while True:\n    print({MATCH_LINE!r}, flush=True)\n    time.sleep(0.05)",
        1.5,
    )
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        search("TODO", "src", "", 1000)
    assert time.monotonic() - start < 5


def test_overall_timeout_after_output_ends(
    fake_rg: Callable[[str, float], Search], tree: Path
) -> None:
    # rg closes stdout but does not exit, so the timer fires while waiting
    # for the exit status: that is a timeout, not a ripgrep error.
    search = fake_rg("os.close(1)\ntime.sleep(10)", 1.5)
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        search("TODO", "src", "", 10)
    assert time.monotonic() - start < 5


def test_error_after_a_match_returns_no_partial_results(
    fake_rg: Callable[[str, float], Search], tree: Path
) -> None:
    search = fake_rg(
        f"print({MATCH_LINE!r}, flush=True)\n"
        "sys.stderr.buffer.write(b'rg: ./src/\\xff: Permission denied\\n')\n"
        "sys.exit(2)",
        30,
    )
    with pytest.raises(subprocess.CalledProcessError) as raised:
        search("TODO", "src", "", 10)
    assert raised.value.returncode == 2
    assert "Permission denied" in raised.value.stderr


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
