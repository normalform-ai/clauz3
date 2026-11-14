"""The gh trusted boundary accepts one spelling per long flag."""

import importlib.util
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

GH_ROOT = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib/gh"

GhEffect = Callable[[str, str, list[str], str], str]


@pytest.fixture
def gh_effect(monkeypatch: pytest.MonkeyPatch) -> GhEffect:
    """The bundled gh effect, loaded by path to run its body."""
    monkeypatch.syspath_prepend(str(GH_ROOT))
    spec = importlib.util.spec_from_file_location(
        "gh_effects", GH_ROOT / "tools/gh/trusted/effects.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(GhEffect, module.gh)


@pytest.mark.parametrize(
    "args",
    [
        ["42", "--admin=true"],
        ["42", "--admin="],
        ["--repo=other/repo"],
        ["42", "--body=looks good"],
    ],
)
def test_gh_rejects_joined_long_flags_before_subprocess(
    gh_effect: GhEffect, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("a --flag=value argument reached the subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="separate arguments"):
        gh_effect("pr", "merge", args, "cmungall/agent-deal")


def test_gh_passes_separate_flags_and_appends_repo(
    gh_effect: GhEffect, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def record(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", record)
    result = gh_effect("pr", "comment", ["42", "--body", "a=b"], "cmungall/agent-deal")
    assert result == "ok"
    assert calls == [
        ["gh", "pr", "comment", "42", "--body", "a=b", "--repo", "cmungall/agent-deal"]
    ]
