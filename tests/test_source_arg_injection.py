"""Issue #70: git argument injection in remote source resolution.

`git ls-remote` / `git clone` / `git checkout` run with list-form argv (no
shell), but without a `--` end-of-options separator a parsed url/ref that
begins with `-` is interpreted by git as an option (e.g. `--upload-pack=cmd`,
`-oProxyCommand=...`), which is RCE. The resolver must reject option-like
url/ref values and pass `--` before positionals.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clauz3 import source as src
from clauz3.source import SourceError, _parse_source, resolve_remote_source

# ── reject option-like url/ref ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "https://github.com/o/r.git@--upload-pack=evilcmd",
        "git@github.com:o/r.git@-oProxyCommand=evil",
        "ssh://-oProxyCommand=evil/repo",
        "--upload-pack=evil",
    ],
)
def test_parse_source_rejects_option_like(bad: str) -> None:
    with pytest.raises(SourceError):
        _parse_source(bad)


def test_resolve_rejects_option_like_ref() -> None:
    with pytest.raises(SourceError):
        resolve_remote_source("https://github.com/o/r.git@--upload-pack=evilcmd")


# ── argv carries the -- separator ─────────────────────────────────────────────


def test_ls_remote_argv_has_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(list(argv))

        class R:
            stdout = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\tHEAD\n"
            stderr = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    src._ls_remote_sha("https://github.com/o/r.git", "main")

    argv = captured[0]
    assert argv[0] == "git"
    assert "ls-remote" in argv
    assert "--" in argv
    sep = argv.index("--")
    # url and ref must come after the end-of-options marker
    assert argv[sep + 1] == "https://github.com/o/r.git"
    assert argv[sep + 2] == "main"


def test_clone_argv_has_separator(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    captured: list[list[str]] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(list(argv))

        class R:
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    src._git_clone(
        "https://github.com/o/r.git",
        "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        tmp_path / "dest",
    )

    clone_argv = captured[0]
    assert clone_argv[0] == "git"
    assert "clone" in clone_argv
    assert "--" in clone_argv
    sep = clone_argv.index("--")
    assert clone_argv[sep + 1] == "https://github.com/o/r.git"


# ── hardening: ext:: disabled, terminal prompt off ────────────────────────────


def _capture_run(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[list[str], dict[str, str] | None]]:
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(argv: list[str], **kwargs: object) -> object:
        env = kwargs.get("env")
        calls.append((list(argv), env if isinstance(env, dict) else None))

        class R:
            stdout = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\tHEAD\n"
            stderr = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_ext_transport_disabled_in_every_invocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _capture_run(monkeypatch)
    src._ls_remote_sha("https://github.com/o/r.git", "main")
    src._git_clone(
        "https://github.com/o/r.git",
        "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        tmp_path / "dest",
    )
    for argv, _env in calls:
        assert "-c" in argv
        assert "protocol.ext.allow=never" in argv


def test_terminal_prompt_disabled_in_subprocess_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _capture_run(monkeypatch)
    src._ls_remote_sha("https://github.com/o/r.git", "main")
    _argv, env = calls[0]
    assert env is not None
    assert env["GIT_TERMINAL_PROMPT"] == "0"


# ── checkout argv shape and dash-sha rejection ────────────────────────────────


def test_checkout_argv_puts_sha_before_trailing_separator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _capture_run(monkeypatch)
    sha = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    src._git_clone("https://github.com/o/r.git", sha, tmp_path / "dest")
    checkout = next(argv for argv, _env in calls if "checkout" in argv)
    # sha is read as a revision: it precedes a trailing `--`, never after it.
    assert checkout[-1] == "--"
    assert sha in checkout[:-1]


def test_dash_sha_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _capture_run(monkeypatch)
    with pytest.raises(SourceError):
        src._git_clone(
            "https://github.com/o/r.git", "-oProxyCommand=evil", tmp_path / "d"
        )


# ── gh: shorthand option-like ref is rejected at parse time ────────────────────


def test_gh_shorthand_dash_ref_rejected() -> None:
    # `-evil` matches the shorthand ref pattern, so without the explicit reject
    # it would yield ref='-evil'. The added _reject_option_like on the shorthand
    # path must refuse it at parse time.
    with pytest.raises(SourceError):
        _parse_source("gh:org/repo@-evil")
