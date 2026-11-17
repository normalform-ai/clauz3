"""CLI failures a user can cause are reported as ``error: ...``, not tracebacks.

Exit 2 means the command could not be carried out as asked (a bad source, an
unreadable program, a broken trusted file); exit 1 is reserved for a program
that did not prove or that raised after it was proved and approved.
"""

import subprocess
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from clauz3.cli import main


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "store"))


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    repo = tmp_path / "upstream"
    (repo / "tools/x/trusted").mkdir(parents=True)
    (repo / "tools/x/trusted/effects.py").write_text("# placeholder\n")
    for command in (
        ["init", "--quiet", "-b", "main"],
        ["add", "-A"],
        [
            *("-c", "user.email=test@example.com", "-c", "user.name=Test"),
            *("commit", "--quiet", "-m", "init"),
        ],
    ):
        subprocess.run(["git", "-C", str(repo), *command], check=True)
    return repo


def _reported(capsys: CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err
    return captured.err


@pytest.mark.parametrize("command", ["install", "test"])
def test_an_unreachable_remote_source_is_an_error_not_a_traceback(
    command: str, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    missing = (tmp_path / "no-such-repo.git").as_uri()
    argv = [command, missing]
    if command == "install":
        argv += ["--into", str(tmp_path / "project")]

    assert main(argv) == 2

    assert "git ls-remote failed" in _reported(capsys)
    assert not (tmp_path / "project").exists()


@pytest.mark.parametrize("command", ["install", "test"])
def test_an_unknown_ref_is_an_error_not_a_traceback(
    command: str, upstream: Path, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    argv = [command, f"{upstream.as_uri()}@no-such-ref"]
    if command == "install":
        argv += ["--into", str(tmp_path / "project")]

    assert main(argv) == 2

    assert "'no-such-ref' not found" in _reported(capsys)
