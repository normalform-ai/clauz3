"""Maintainer snapshots must never turn failed or partial retrieval into zero."""

import importlib.util
import json
import stat
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture
def counts() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts/github_download_counts.py"
    spec = importlib.util.spec_from_file_location("github_download_counts", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def responses(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType, values: list[Any]
) -> list[list[str]]:
    pending = iter(values)
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert kwargs["timeout"] == 120
        value = next(pending)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, subprocess.CompletedProcess):
            return value
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    monkeypatch.setattr(counts.subprocess, "run", run)
    return commands


def test_release_and_asset_pagination(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType
) -> None:
    commands = responses(
        monkeypatch,
        counts,
        [
            [[{"id": 1, "assets": [{"id": 999}]}], [{"id": 2}]],
            [[{"id": 10, "download_count": 4}], [{"id": 11, "download_count": 6}]],
            [[{"id": 12, "download_count": 8}]],
        ],
    )
    result = counts.release_counts()
    assert result["download_count"] == 18
    assert result["release_count"] == 2
    assert result["asset_count"] == 3
    assert [x["asset_id"] for x in result["assets"]] == [10, 11, 12]
    assert [x[-3] for x in commands] == [
        "repos/normalform-ai/clauz3/releases?per_page=100",
        "repos/normalform-ai/clauz3/releases/1/assets?per_page=100",
        "repos/normalform-ai/clauz3/releases/2/assets?per_page=100",
    ]
    for command in commands:
        assert command[command.index("--method") + 1] == "GET"
        assert command[command.index("--hostname") + 1] == "github.com"
        assert command[-2:] == ["--paginate", "--slurp"]


@pytest.mark.parametrize("value", [None, {}, [], [None], [[1]]])
def test_invalid_pages(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType, value: Any
) -> None:
    responses(monkeypatch, counts, [value])
    with pytest.raises(ValueError):
        counts.release_counts()


@pytest.mark.parametrize("value", [None, -1, True, "4", 1.5])
def test_invalid_download_count(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType, value: Any
) -> None:
    responses(
        monkeypatch,
        counts,
        [
            [[{"id": 1}]],
            [[{"id": 2, "download_count": value}]],
        ],
    )
    with pytest.raises(ValueError):
        counts.release_counts()


@pytest.mark.parametrize(
    "values",
    [
        [[[{"id": 1}, {"id": 1}]], [[]]],
        [[[{"id": 1}]], [[{"id": 2, "download_count": 1}] * 2]],
    ],
)
def test_repeated_ids(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType, values: list[Any]
) -> None:
    responses(monkeypatch, counts, values)
    with pytest.raises(ValueError, match="Repeated"):
        counts.release_counts()


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (FileNotFoundError(), "gh is not installed"),
        (subprocess.TimeoutExpired("gh", 120), "timed out"),
        (OSError("private path"), "Could not execute gh"),
        (subprocess.CompletedProcess([], 1, "", "secret: HTTP 403"), "HTTP 403"),
        (subprocess.CompletedProcess([], 1, "", "secret: HTTP 404"), "HTTP 404"),
        (subprocess.CompletedProcess([], 1, "", "secret: HTTP 429"), "HTTP 429"),
        (subprocess.CompletedProcess([], 1, "", "secret"), "gh exit 1"),
        (subprocess.CompletedProcess([], 0, "not JSON", ""), "invalid JSON"),
    ],
)
def test_request_failures(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType, failure: Any, reason: str
) -> None:
    responses(monkeypatch, counts, [failure])
    with pytest.raises(ValueError, match=reason) as error:
        counts.release_counts()
    assert "secret" not in str(error.value)
    assert "private path" not in str(error.value)


def test_partial_release_failure_preserves_clones_and_unknowns(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType
) -> None:
    commands = responses(
        monkeypatch,
        counts,
        [
            [[{"id": 1}, {"id": 2}]],
            [[{"id": 10, "download_count": 99}]],
            subprocess.CompletedProcess([], 1, "[]", "HTTP 403"),
            {
                "count": 3,
                "uniques": 2,
                "clones": [
                    {"timestamp": "2026-11-16T00:00:00Z", "count": 3, "uniques": 2},
                ],
            },
        ],
    )
    result = counts.snapshot()
    assert result["release_assets"]["count"] is None
    assert "assets" not in result["release_assets"]
    assert result["clones"]["count"] == 3
    assert result["clones"]["uniques"] == 2
    assert result["clones"]["days"][0]["count"] == 3
    assert commands[-1][-1].endswith("/traffic/clones?per=day")
    assert commands[-1][commands[-1].index("--method") + 1] == "GET"
    for key in ("source_archives", "annual_total", "other_distribution_channels"):
        assert result[key]["status"] == "unknown"
        assert result[key]["count"] is None
    assert result["started_at"] <= result["completed_at"]


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"count": 0, "uniques": 0, "clones": None},
        {"count": 1, "uniques": 1, "clones": []},
        {"count": 0, "uniques": None, "clones": []},
        {"count": 0, "uniques": 0, "clones": [{"timestamp": "invalid"}]},
        {
            "count": 0,
            "uniques": 0,
            "clones": [
                {"timestamp": "2026-02-30T00:00:00Z", "count": 0, "uniques": 0},
            ],
        },
        {
            "count": 0,
            "uniques": 0,
            "clones": [
                {"timestamp": "2026-11-16T00:00:00Z", "count": 0, "uniques": 0},
            ]
            * 2,
        },
    ],
)
def test_invalid_clones(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType, value: Any
) -> None:
    responses(monkeypatch, counts, [[[]], value])
    result = counts.snapshot()
    assert result["release_assets"]["download_count"] == 0
    assert result["clones"]["status"] == "unknown"
    assert result["clones"]["count"] is None


def test_empty_results_are_known_zero(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType
) -> None:
    responses(monkeypatch, counts, [[[]], {"count": 0, "uniques": 0, "clones": []}])
    result = counts.snapshot()
    assert result["release_assets"]["download_count"] == 0
    assert result["clones"]["count"] == 0
    assert result["clones"]["status"] == "known"


def test_private_new_files_and_directory(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType, tmp_path: Path
) -> None:
    directory = tmp_path / "private"
    directory.mkdir(mode=0o755)
    monkeypatch.setattr(counts, "OUTPUT_DIR", directory)
    first = counts.save({"annual_total": None})
    second = counts.save({"annual_total": None})
    assert first != second
    assert json.loads(first.read_text()) == {"annual_total": None}
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert stat.S_IMODE(second.stat().st_mode) == 0o600


def test_refuse_symlink_and_checkout_output(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType, tmp_path: Path
) -> None:
    directory = tmp_path / "link"
    directory.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(counts, "OUTPUT_DIR", directory)
    with pytest.raises(ValueError, match="symlink"):
        counts.save({})
    monkeypatch.setattr(counts, "OUTPUT_DIR", Path(__file__).parent / "snapshots")
    with pytest.raises(ValueError, match="outside"):
        counts.save({})


@pytest.mark.parametrize("failed", [False, True])
def test_main_exit_and_saved_unknowns(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType, tmp_path: Path, failed: bool
) -> None:
    monkeypatch.setattr(counts, "OUTPUT_DIR", tmp_path / "private")
    clone_result = (
        subprocess.CompletedProcess([], 1, "", "HTTP 403")
        if failed
        else {"count": 0, "uniques": 0, "clones": []}
    )
    responses(monkeypatch, counts, [[[]], clone_result])
    assert counts.main() == int(failed)
    result = json.loads(next((tmp_path / "private").glob("*.json")).read_text())
    assert result["clones"]["status"] == ("unknown" if failed else "known")


def test_main_save_failure(
    monkeypatch: pytest.MonkeyPatch, counts: ModuleType, tmp_path: Path
) -> None:
    target = tmp_path / "file"
    target.touch()
    monkeypatch.setattr(counts, "OUTPUT_DIR", target)
    responses(monkeypatch, counts, [[[]], {"count": 0, "uniques": 0, "clones": []}])
    assert counts.main() == 1
