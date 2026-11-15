"""The gh trusted boundary accepts one spelling per long flag."""

import importlib.util
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import deal
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


@pytest.mark.parametrize(
    "command,action,args",
    [
        ("pr", "comment", ["42", "--body", "a=b"]),
        # A trailing value-taking flag must not swallow the repository.
        ("label", "create", ["bug", "--description"]),
        ("release", "create", ["v1", "--notes"]),
    ],
)
def test_gh_pins_repo_before_agent_arguments(
    gh_effect: GhEffect,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    action: str,
    args: list[str],
) -> None:
    calls: list[list[str]] = []

    def record(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", record)
    assert gh_effect(command, action, args, "cmungall/agent-deal") == "ok"
    assert calls == [["gh", command, action, "--repo", "cmungall/agent-deal", *args]]


@pytest.mark.usefixtures("no_gh_subprocess")
@pytest.mark.parametrize(
    "args", [["-wS", "repo:other/victim"], ["-Srepo:other/victim"]]
)
def test_gh_refuses_search_inside_short_option_groups(
    gh_effect: GhEffect, args: list[str]
) -> None:
    with pytest.raises(ValueError, match="-S"):
        gh_effect("pr", "list", args, "cmungall/agent-deal")


@pytest.mark.usefixtures("no_gh_subprocess")
@pytest.mark.parametrize("repo", ["ghe.example.com/owner/name", "a/b/c/d"])
def test_gh_refuses_host_qualified_repositories(gh_effect: GhEffect, repo: str) -> None:
    with pytest.raises((ValueError, deal.PreContractError), match="owner/name"):
        gh_effect("pr", "list", [], repo)


@pytest.mark.parametrize(
    "args",
    [
        ["https://github.com/other/repo/pull/1"],
        ["HTTPS://GITHUB.COM/other/repo/pull/1"],
        ["https://github.com:443/other/repo/pull/1"],
        ["https://www.github.com/other/repo/issues/2"],
        ["https://ghe.example.com/other/repo/pull/1"],
    ],
)
def test_gh_refuses_links_that_override_the_repository(
    gh_effect: GhEffect, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("a web address reached the subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="numbers, not web addresses"):
        gh_effect("pr", "view", args, "cmungall/agent-deal")


@pytest.mark.parametrize("command", ["co", "act", "copilot", "alias", "extension"])
def test_gh_refuses_aliases_and_extensions(
    gh_effect: GhEffect, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("a non-built-in gh command reached the subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(deal.PreContractError, match="built-in"):
        gh_effect(command, "view", [], "cmungall/agent-deal")


@pytest.mark.parametrize(
    "command,action,args",
    [("issue", "transfer", ["7", "other/repo"]), ("label", "clone", ["other/repo"])],
)
def test_gh_refuses_actions_that_reach_a_second_repository(
    gh_effect: GhEffect,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    action: str,
    args: list[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("a second-repository action reached the subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(deal.PreContractError, match="second repository"):
        gh_effect(command, action, args, "cmungall/agent-deal")


@pytest.fixture
def no_gh_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("a refused gh call reached the subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)


@pytest.mark.usefixtures("no_gh_subprocess")
@pytest.mark.parametrize(
    "args",
    [
        ["--org", "someorg"],
        ["-o", "someorg"],
        ["-osomeorg"],
        ["--user", "someone"],
        ["-u", "someone"],
        ["--repos", "a,b"],
        ["--owner", "someone"],
        ["--hostname", "ghe.example.com"],
        ["--source", "other"],
        ["--repo", "other/repo"],
        ["-R", "other/repo"],
        ["-Rother/repo"],
        ["-wR", "other/repo"],
        ["--branch-repo", "other/repo"],
        ["--repo-owner", "someone"],
        ["--source-owner", "someone"],
        ["--target-owner", "someone"],
        ["--"],
    ],
)
def test_gh_refuses_flags_that_widen_scope_past_repo(
    gh_effect: GhEffect, args: list[str]
) -> None:
    with pytest.raises((ValueError, deal.PreContractError)):
        gh_effect("secret", "list", args, "cmungall/agent-deal")


@pytest.mark.usefixtures("no_gh_subprocess")
@pytest.mark.parametrize(
    "command,action",
    [
        ("secret", "remove"),
        ("variable", "remove"),
        ("pr", "ls"),
        ("pr", "co"),
        ("issue", "new"),
        ("skill", "add"),
        ("skill", "show"),
    ],
)
def test_gh_refuses_action_aliases(
    gh_effect: GhEffect, command: str, action: str
) -> None:
    with pytest.raises(deal.PreContractError, match="canonical"):
        gh_effect(command, action, [], "cmungall/agent-deal")


@pytest.mark.usefixtures("no_gh_subprocess", "deal_disabled")
@pytest.mark.parametrize(
    "command,action,args",
    [
        ("co", "view", []),
        ("issue", "transfer", ["7", "other/repo"]),
        ("label", "clone", ["other/repo"]),
        ("secret", "remove", ["TOKEN"]),
        ("secret", "list", ["--org", "someorg"]),
        ("pr", "merge", ["42", "--admin=true"]),
        ("pr", "view", ["https://github.com/other/repo/pull/1"]),
    ],
)
def test_gh_body_refuses_without_deal(
    gh_effect: GhEffect, command: str, action: str, args: list[str]
) -> None:
    with pytest.raises(ValueError):
        gh_effect(command, action, args, "cmungall/agent-deal")


def test_gh_itself_rejects_abbreviated_admin(tmp_path: Path) -> None:
    """gh (cobra/pflag) accepts no flag abbreviations, so --adm is not --admin."""
    if shutil.which("gh") is None:
        pytest.skip("gh is not installed")
    completed = subprocess.run(
        ["gh", "pr", "merge", "1", "--adm", "--repo", "octocat/Hello-World"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert completed.returncode != 0
    assert "unknown flag: --adm" in completed.stderr


@pytest.mark.usefixtures("no_gh_subprocess")
def test_gh_api_is_not_available(gh_effect: GhEffect) -> None:
    """gh api ignores --repo for literal endpoints and accepts any method."""
    with pytest.raises(deal.PreContractError, match="built-in"):
        gh_effect("api", "/repos/other/victim", ["-X", "DELETE"], "you/safe")


@pytest.mark.usefixtures("no_gh_subprocess")
@pytest.mark.parametrize(
    "action", ["--org=otherorg", "--org", "-R", "https://github.com/a/b/pull/1"]
)
def test_gh_refuses_flags_or_links_as_the_action(
    gh_effect: GhEffect, action: str
) -> None:
    with pytest.raises(deal.PreContractError, match="action"):
        gh_effect("secret", action, ["list"], "you/safe")


@pytest.mark.usefixtures("no_gh_subprocess", "deal_disabled")
@pytest.mark.parametrize(
    "command,action",
    [("api", "/repos/other/victim"), ("secret", "--org=otherorg"), ("secret", "-o")],
)
def test_gh_body_refuses_api_and_flag_actions_without_deal(
    gh_effect: GhEffect, command: str, action: str
) -> None:
    with pytest.raises(ValueError):
        gh_effect(command, action, ["list"], "you/safe")


@pytest.mark.usefixtures("no_gh_subprocess")
@pytest.mark.parametrize(
    "selector",
    [
        "ghe.example.com/o/r/pull/5",
        "tenant.ghe.com/o/r/pull/5",
        "localhost:8080/o/r/pull/5",
        "o/r/pull/5",
        "o/r/issues/7",
        "o/r/discussions/3",
        "ghe.example.com/o/r/pu%6cl/5",
        "ghe.example.com/o/r/pull/%35",
        "o%2Fr/x/issues/7",
    ],
)
def test_gh_refuses_scheme_less_repository_addresses(
    gh_effect: GhEffect, selector: str
) -> None:
    """gh adds https:// to such selectors and uses their repository."""
    with pytest.raises(ValueError, match="repository address"):
        gh_effect("pr", "view", [selector], "me/mine")


@pytest.mark.parametrize(
    "path", ["./dist/app.tar.gz", "notes/pr.md", "docs/pull/x", "50% done"]
)
def test_gh_allows_ordinary_paths(
    gh_effect: GhEffect, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    def record(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", record)
    assert gh_effect("release", "upload", ["v1", path], "me/mine") == "ok"
