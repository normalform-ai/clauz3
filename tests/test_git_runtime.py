"""The git trusted boundary: pushes go through git_push, and nothing writes files."""

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import deal
import pytest

GIT_ROOT = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib/git"


@pytest.fixture
def git_effects(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The bundled git effects, loaded by path to run their bodies."""
    monkeypatch.syspath_prepend(str(GIT_ROOT))
    spec = importlib.util.spec_from_file_location(
        "git_effects", GIT_ROOT / "tools/git/trusted/effects.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("a refused git call reached the subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)


@pytest.mark.usefixtures("no_subprocess")
def test_general_git_refuses_push(git_effects: ModuleType) -> None:
    with pytest.raises(deal.PreContractError, match="git_push"):
        git_effects.git("push", ["origin", "main", "--force"], "/repo")


@pytest.mark.usefixtures("no_subprocess")
@pytest.mark.parametrize(
    "subcommand,args",
    [
        ("diff", ["--output=/tmp/out"]),
        ("log", ["--output", "/tmp/out"]),
        ("show", ["HEAD", "--output=x"]),
    ],
)
def test_git_refuses_output_files(
    git_effects: ModuleType, subcommand: str, args: list[str]
) -> None:
    with pytest.raises(ValueError, match="--output"):
        git_effects.git(subcommand, args, "/repo")


def test_git_runs_inspection_commands(
    git_effects: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def record(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", record)
    assert git_effects.git("log", ["--oneline", "-5"], "/repo") == "ok"
    assert calls == [
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            "/repo",
            "log",
            "--oneline",
            "-5",
        ]
    ]


@pytest.mark.usefixtures("no_subprocess")
@pytest.mark.parametrize(
    "subcommand,args",
    [
        ("for-each-repo", ["--config", "x.repo", "push", "origin", "main"]),
        ("filter-branch", ["--tree-filter", "true"]),
        ("send-email", ["HEAD~1"]),
        ("imap-send", []),
        ("difftool", ["-x", "sh"]),
        ("mergetool", []),
        ("merge-index", ["sh", "-a"]),
        ("hook", ["run", "pre-commit"]),
        ("maintenance", ["start"]),
        ("stage", ["-A"]),
        ("annotate", ["file"]),
        ("pickaxe", ["file"]),
        ("init-db", []),
        ("fsck-objects", []),
    ],
)
def test_git_refuses_commands_that_run_programs_publish_or_alias(
    git_effects: ModuleType, subcommand: str, args: list[str]
) -> None:
    with pytest.raises(deal.PreContractError, match="built-in"):
        git_effects.git(subcommand, args, "/repo")


@pytest.mark.usefixtures("no_subprocess", "deal_disabled")
@pytest.mark.parametrize(
    "subcommand,args",
    [
        ("push", ["origin", "main"]),
        ("for-each-repo", ["--config", "x.repo", "push"]),
        ("lol", []),
        ("diff", ["--output=/tmp/out"]),
    ],
)
def test_git_body_refuses_without_deal(
    git_effects: ModuleType, subcommand: str, args: list[str]
) -> None:
    with pytest.raises(ValueError):
        git_effects.git(subcommand, args, "/repo")


@pytest.mark.usefixtures("no_subprocess")
@pytest.mark.parametrize("remote,branch", [("origin", "--mirror"), ("--force", "main")])
def test_git_push_refuses_options_as_remote_or_branch(
    git_effects: ModuleType, remote: str, branch: str
) -> None:
    with pytest.raises(deal.PreContractError, match="start with"):
        git_effects.git_push(remote, branch, "/repo")


def test_git_never_runs_repository_hooks(
    git_effects: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook written into .git/hooks must not run, so it cannot push."""
    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "test@example.com")
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    marker = tmp_path / "hook-ran"
    hook = repo / ".git/hooks/post-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    git_effects.git("commit", ["--allow-empty", "-q", "-m", "x"], str(repo))
    assert git_effects.git("log", ["--oneline"], str(repo)).strip()
    assert not marker.exists()
