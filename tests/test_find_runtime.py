"""The find trusted boundary must never expose find's action language."""

import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import patch

import deal
import pytest


@pytest.fixture
def find_effect(
    filesystem_effects: ModuleType,
) -> Callable[[str, list[str]], list[str]]:
    return cast(Callable[[str, list[str]], list[str]], filesystem_effects.find)


@pytest.mark.parametrize(
    "args",
    [
        ["-exec", "touch", "sentinel", ";"],
        ["-execdir", "true", ";"],
        ["-delete"],
        ["-fprint", "sentinel"],
        ["-print"],
        ["-print0"],
        ["-printf", "not a path"],
        ["-follow"],
        ["-L"],
        ["-name", "*", "-o", "-delete"],
        ["(", "-name", "*", ")"],
        ["-not", "-maxdepth", "1"],
        ["-not", "-not", "-name", "*"],
        ["-not"],
        ["-name"],
        ["-type", "x"],
        ["-maxdepth", "-1"],
        ["-maxdepth", "1 -delete"],
        ["-maxdepth", "١"],
        ["-path", "a\0b"],
        ["second-root"],
    ],
)
def test_find_rejects_actions_before_subprocess(
    find_effect: Callable[[str, list[str]], list[str]],
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid find arguments reached the subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ValueError):
        find_effect("/repo", args)


def test_find_filters_and_preserves_path_bytes(
    find_effect: Callable[[str, list[str]], list[str]], tmp_path: Path
) -> None:
    (tmp_path / "nested").mkdir()
    special = tmp_path / "nested/line\nreturn\r.txt"
    special.write_text("hello")
    (tmp_path / "nested/skip.txt").write_text("hello")
    (tmp_path / "other.py").write_text("hello")
    result = find_effect(
        str(tmp_path),
        [
            "-type",
            "f",
            "-name",
            "*.txt",
            "-not",
            "-path",
            "*/skip.txt",
            "-mindepth",
            "2",
            "-maxdepth",
            "2",
        ],
    )
    assert result == [str(special)]


@pytest.mark.parametrize("name", ["-L", "!", "(", "-exec"])
def test_find_treats_option_like_roots_as_paths(
    find_effect: Callable[[str, list[str]], list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / name).mkdir()
    (tmp_path / name / "file").write_text("hello")
    with patch("subprocess.run", wraps=subprocess.run) as run:
        assert find_effect(name, ["-type", "f"]) == [f"./{name}/file"]
    assert run.call_args.args[0] == ["find", "-P", f"./{name}", "-type", "f", "-print0"]


def test_find_does_not_follow_directory_symlinks(
    find_effect: Callable[[str, list[str]], list[str]], tmp_path: Path
) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret").write_text("hello")
    link = root / "linked"
    link.symlink_to(outside, target_is_directory=True)
    assert find_effect(str(root), ["-type", "f"]) == []
    assert find_effect(str(root), ["-type", "l"]) == [str(link)]
    assert find_effect(str(link), ["-type", "f"]) == []


@pytest.mark.parametrize("suffix", ["/"])
def test_root_path_resolution_can_follow_a_symlink(
    find_effect: Callable[[str, list[str]], list[str]], tmp_path: Path, suffix: str
) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    target = outside / "file"
    target.write_text("hello")
    link = root / "linked"
    link.symlink_to(outside, target_is_directory=True)
    result = find_effect(str(link) + suffix, ["-type", "f"])
    assert len(result) == 1
    assert Path(result[0]).resolve() == target


@pytest.mark.parametrize("path", ["/tmp/x/.", "/tmp//x", "/tmp/./x", "C:\\tmp\\x"])
def test_dot_and_empty_segments_are_refused_before_running(
    find_effect: Callable[[str, list[str]], list[str]], path: str
) -> None:
    # The precondition refuses the path before the body's own check runs.
    with pytest.raises(deal.PreContractError, match=r"\.\., \. or empty segments"):
        find_effect(path, ["-type", "f"])


@pytest.mark.parametrize("root", [".", "src", "./src"])
def test_relative_path_filters_use_prefixed_spelling(
    find_effect: Callable[[str, list[str]], list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("hello")
    assert find_effect(root, ["-path", "./src/*.py"]) == ["./src/app.py"]
    assert find_effect(root, ["-path", "src/*.py"]) == []


def test_depth_options_precede_filters(
    find_effect: Callable[[str, list[str]], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    find_effect("/repo", ["-type", "f", "-maxdepth", "5", "-mindepth", "1"])
    assert commands == [
        [
            "find",
            "-P",
            "/repo",
            "-maxdepth",
            "5",
            "-mindepth",
            "1",
            "-type",
            "f",
            "-print0",
        ]
    ]
    with pytest.raises(ValueError, match="depth limits cannot be negated"):
        find_effect("/repo", ["-not", "-maxdepth", "1"])
    assert len(commands) == 1


def test_action_like_name_is_only_a_filter_operand(
    find_effect: Callable[[str, list[str]], list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "-delete"
    target.write_text("keep me")
    commands: list[list[str]] = []
    real_run = subprocess.run

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return real_run(command, **kwargs)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(subprocess, "run", run)
    assert find_effect(str(tmp_path), ["-name", "-delete"]) == [str(target)]
    assert commands == [["find", "-P", str(tmp_path), "-name", "-delete", "-print0"]]
    assert target.read_text() == "keep me"
