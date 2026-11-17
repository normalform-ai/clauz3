"""The sha-keyed source cache holds only verified checkouts of that sha.

A cache entry is trusted forever under its name, so it must never be created
by a clone that did not end up at the commit it is named after. The clone used
to be made directly at the cache path and kept when the checkout failed or was
interrupted; the retry then found the directory and returned the default
branch's content as the pinned commit. Network-free: the remote is a
``file://`` repository in ``tmp_path``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clauz3 import source as source_mod
from clauz3.source import SourceError, resolve_remote_source

MISSING_SHA = "0123456789abcdef0123456789abcdef01234567"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _commit(repo: Path, text: str) -> str:
    (repo / "tools/x/trusted/effects.py").write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", text.strip("# \n"))
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    repo = tmp_path / "upstream"
    (repo / "tools/x/trusted").mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _commit(repo, "# first\n")
    return repo


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "cache"))
    return tmp_path / "cache/sources"


def _entries(cache: Path) -> list[str]:
    return sorted(path.name for path in cache.iterdir()) if cache.is_dir() else []


def test_a_failed_checkout_does_not_create_the_cache_entry(
    upstream: Path, cache: Path
) -> None:
    pinned = f"{upstream.as_uri()}@{MISSING_SHA}"

    with pytest.raises(SourceError, match="failed"):
        resolve_remote_source(pinned)

    assert _entries(cache) == []
    # The retry must fail the same way, not serve the default branch as the pin.
    with pytest.raises(SourceError, match="failed"):
        resolve_remote_source(pinned)
    assert _entries(cache) == []


def test_an_interrupted_clone_does_not_create_the_cache_entry(
    upstream: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(url: str, sha: str, dest: Path) -> None:
        dest.mkdir(parents=True)
        (dest / "half-written").write_text("partial\n")
        raise KeyboardInterrupt

    monkeypatch.setattr(source_mod, "_git_clone", interrupted)

    with pytest.raises(KeyboardInterrupt):
        resolve_remote_source(upstream.as_uri())

    assert _entries(cache) == []


def test_a_clone_at_another_commit_is_not_cached_under_the_sha(
    upstream: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _git(upstream, "rev-parse", "HEAD")
    _commit(upstream, "# second\n")

    def clone_without_checkout(url: str, sha: str, dest: Path) -> None:
        subprocess.run(["git", "clone", "--quiet", "--", url, str(dest)], check=True)

    monkeypatch.setattr(source_mod, "_git_clone", clone_without_checkout)

    with pytest.raises(SourceError, match=first):
        resolve_remote_source(f"{upstream.as_uri()}@{first}")

    assert _entries(cache) == []


def test_a_pinned_sha_is_cached_at_that_commit(upstream: Path, cache: Path) -> None:
    first = _git(upstream, "rev-parse", "HEAD")
    _commit(upstream, "# second\n")

    cached = resolve_remote_source(f"{upstream.as_uri()}@{first}")

    assert cached == cache / first
    assert (cached / "tools/x/trusted/effects.py").read_text() == "# first\n"
    assert _git(cached, "rev-parse", "HEAD") == first
    assert _entries(cache) == [first]


def test_an_abbreviated_sha_and_an_annotated_tag_still_resolve(
    upstream: Path, cache: Path
) -> None:
    first = _git(upstream, "rev-parse", "HEAD")
    _git(upstream, "tag", "-a", "v1", "-m", "release")
    _commit(upstream, "# second\n")

    short = resolve_remote_source(f"{upstream.as_uri()}@{first[:10]}")
    tagged = resolve_remote_source(f"{upstream.as_uri()}@v1")

    for cached in (short, tagged):
        assert (cached / "tools/x/trusted/effects.py").read_text() == "# first\n"
        assert _git(cached, "rev-parse", "HEAD") == first


def test_a_concurrently_created_entry_is_used_and_the_scratch_clone_removed(
    upstream: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = _git(upstream, "rev-parse", "HEAD")
    real_clone = source_mod._git_clone

    def racing(url: str, sha: str, dest: Path) -> None:
        real_clone(url, sha, dest)
        # Another process publishes the same commit while this one is cloning.
        other = cache / sha
        subprocess.run(["git", "clone", "--quiet", "--", url, str(other)], check=True)
        (other / "published-by-other").write_text("")

    monkeypatch.setattr(source_mod, "_git_clone", racing)

    cached = resolve_remote_source(upstream.as_uri())

    assert cached == cache / sha
    assert (cached / "published-by-other").is_file()
    assert _entries(cache) == [sha]
