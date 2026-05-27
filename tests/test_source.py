"""Tests for the remote git source resolver in ``clauz3.source``.

Network-free: integration tests use ``file://`` URLs pointing at a tmp
local git repo, so the same code path that handles real GitHub remotes
is exercised without leaving the filesystem.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clauz3.source import (
    SourceError,
    _parse_source,
    _split_url_ref,
    is_remote_source,
    resolve_remote_source,
)

# ── unit tests: source parsing ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "remote"),
    [
        ("gh:org/repo", True),
        ("gh:org/repo@v1.0", True),
        ("https://github.com/org/repo.git", True),
        ("http://example.com/repo.git", True),
        ("git@github.com:org/repo.git", True),
        ("git://example.com/repo.git", True),
        ("ssh://git@example.com/repo.git", True),
        ("file:///tmp/repo", True),
        ("/local/path/to/project", False),
        ("./relative/path", False),
        ("stdlib:filesystem", False),
        ("filesystem", False),
    ],
)
def test_is_remote_source(source: str, remote: bool) -> None:
    assert is_remote_source(source) is remote


def test_parse_gh_shorthand() -> None:
    parsed = _parse_source("gh:normalform-ai/clauz3-tools-autolabs")
    assert parsed.url == "https://github.com/normalform-ai/clauz3-tools-autolabs.git"
    assert parsed.ref is None


def test_parse_gh_shorthand_with_ref() -> None:
    parsed = _parse_source("gh:org/repo@v0.3.1")
    assert parsed.url == "https://github.com/org/repo.git"
    assert parsed.ref == "v0.3.1"


def test_parse_gh_shorthand_with_sha() -> None:
    parsed = _parse_source("gh:org/repo@abc1234")
    assert parsed.url == "https://github.com/org/repo.git"
    assert parsed.ref == "abc1234"


def test_parse_full_https_url_no_ref() -> None:
    parsed = _parse_source("https://github.com/org/repo.git")
    assert parsed.url == "https://github.com/org/repo.git"
    assert parsed.ref is None


def test_parse_full_https_url_with_ref() -> None:
    parsed = _parse_source("https://github.com/org/repo.git@v1.0")
    assert parsed.url == "https://github.com/org/repo.git"
    assert parsed.ref == "v1.0"


def test_split_url_ref_handles_ssh_at_sign() -> None:
    """``git@host:org/repo`` must not mis-parse the SSH user as a ref."""
    url, ref = _split_url_ref("git@github.com:org/repo.git")
    assert url == "git@github.com:org/repo.git"
    assert ref is None


def test_split_url_ref_with_explicit_ref() -> None:
    url, ref = _split_url_ref("git@github.com:org/repo.git@v1.0")
    assert url == "git@github.com:org/repo.git"
    assert ref == "v1.0"


# ── integration tests: file:// URL through full resolver ─────────────────────


@pytest.fixture
def upstream_repo(tmp_path: Path) -> Path:
    """A tiny local git repo with a tools/ layer to clone against."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
    )
    trusted = repo / "tools" / "x" / "trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text("# placeholder trusted effect\n")
    (trusted / "contracts.py").write_text("# placeholder contracts\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--quiet", "-m", "init"],
        check=True,
    )
    return repo


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CLAUZ3_CACHE at a tmp dir so tests don't touch ``~/.cache``."""
    cache = tmp_path / "cache"
    monkeypatch.setenv("CLAUZ3_CACHE", str(cache))
    return cache / "sources"


def test_resolve_local_file_url(upstream_repo: Path, isolated_cache: Path) -> None:
    """A ``file://`` URL clones into the content-addressed cache."""
    url = f"file://{upstream_repo}"
    cached = resolve_remote_source(url)
    assert cached.is_dir()
    assert cached.parent == isolated_cache
    assert (cached / "tools" / "x" / "trusted" / "effects.py").is_file()


def test_resolve_caches_by_sha(upstream_repo: Path, isolated_cache: Path) -> None:
    """Two resolves of the same URL+ref reuse the same cache entry."""
    url = f"file://{upstream_repo}"
    first = resolve_remote_source(url)
    second = resolve_remote_source(url)
    assert first == second
    # cache dir name is the sha (hex, 40 chars)
    assert len(first.name) == 40
    assert all(c in "0123456789abcdef" for c in first.name)


def test_resolve_separate_cache_after_new_commit(
    upstream_repo: Path, isolated_cache: Path
) -> None:
    """A new HEAD sha gets a fresh cache entry."""
    url = f"file://{upstream_repo}"
    first = resolve_remote_source(url)

    # Move HEAD forward.
    (upstream_repo / "tools" / "x" / "trusted" / "effects.py").write_text("# updated\n")
    subprocess.run(["git", "-C", str(upstream_repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(upstream_repo), "commit", "--quiet", "-m", "update"],
        check=True,
    )

    second = resolve_remote_source(url)
    assert first != second
    assert first.exists()
    assert second.exists()


def test_resolve_unreachable_url_raises(isolated_cache: Path) -> None:
    """An unreachable URL raises ``SourceError`` rather than crashing."""
    with pytest.raises(SourceError):
        resolve_remote_source("file:///nonexistent/path/to/nothing.git")


def test_resolve_with_sha_ref(upstream_repo: Path, isolated_cache: Path) -> None:
    """Pinning to ``@<sha>`` checks out that exact commit."""
    head_sha = subprocess.run(
        ["git", "-C", str(upstream_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    url = f"file://{upstream_repo}@{head_sha}"
    cached = resolve_remote_source(url)
    assert cached.name == head_sha
