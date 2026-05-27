"""Remote git source resolution for ``clauz3 install`` and ``clauz3 test``.

Both commands historically accepted only local filesystem paths and the
``stdlib:`` scheme. This module adds support for git remotes — full git
URLs and a ``gh:org/repo`` shorthand — by resolving the remote to a content-
addressed local clone under ``~/.cache/clauz3/sources/<sha>/`` and handing
the resolved path back to the existing path-based resolvers.

Accepted source forms:

- ``gh:org/repo`` — shorthand for ``https://github.com/org/repo.git`` at HEAD.
- ``gh:org/repo@<ref>`` — same with an explicit branch, tag, or sha.
- ``https://...`` / ``git@host:...`` / ``ssh://...`` / ``file://...`` /
  ``git://...`` — full git URLs, optionally with ``@<ref>`` appended.

Cache semantics: a source's commit sha is resolved via ``git ls-remote``
on every invocation; the cache key is the resolved sha. Pinning a source
to ``@<sha>`` therefore hits a stable cache entry forever; pinning to a
moveable ref (HEAD, a branch, or a tag the upstream re-tags) re-resolves
on each call and produces a fresh cache entry whenever upstream moves.
Auth is entirely git's concern — SSH key, credential helper, ``gh auth
setup-git`` all work without any clauz3-specific configuration.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: ``gh:org/repo`` and ``gh:org/repo@<ref>`` shorthand pattern.
_GH_SHORTHAND_RE = re.compile(
    r"^gh:(?P<org>[\w.-]+)/(?P<repo>[\w.-]+?)(?:@(?P<ref>[\w./-]+))?$"
)

#: Schemes that identify a source as a git remote rather than a local path.
_REMOTE_PREFIXES = (
    "gh:",
    "https://",
    "http://",
    "git@",
    "git://",
    "ssh://",
    "file://",
)

#: Pattern matching a raw commit sha (7-40 hex chars).
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


class SourceError(Exception):
    """The remote source is invalid or could not be cloned."""


@dataclass(frozen=True)
class _ParsedSource:
    """A remote source split into git URL and optional ref."""

    url: str
    ref: str | None


def is_remote_source(source: str | Path) -> bool:
    """True if ``source`` looks like a git remote rather than a local path."""
    return str(source).startswith(_REMOTE_PREFIXES)


def resolve_remote_source(source: str) -> Path:
    """Resolve a remote git source to a local cache directory.

    Returns the path to the cached clone of the resolved commit. The clone
    is content-addressed by sha, so multiple invocations against the same
    sha share a single cache entry.
    """
    parsed = _parse_source(source)
    sha = _ls_remote_sha(parsed.url, parsed.ref)
    cache = _cache_root() / sha
    if not cache.exists():
        _git_clone(parsed.url, sha, cache)
    return cache


def _parse_source(source: str) -> _ParsedSource:
    match = _GH_SHORTHAND_RE.match(source)
    if match:
        url = f"https://github.com/{match['org']}/{match['repo']}.git"
        return _ParsedSource(url=url, ref=match["ref"])
    url, ref = _split_url_ref(source)
    return _ParsedSource(url=url, ref=ref)


def _split_url_ref(source: str) -> tuple[str, str | None]:
    """Split a possible ``URL@ref`` into ``(url, ref)``.

    Only an ``@`` that appears after the last ``/`` in the URL counts as a
    ref delimiter, so SSH URLs like ``git@host:org/repo`` (where the ``@``
    is part of the user) are not mis-parsed.
    """
    last_slash = source.rfind("/")
    last_at = source.rfind("@")
    if last_at > last_slash and last_slash != -1:
        return source[:last_at], source[last_at + 1 :]
    return source, None


def _ls_remote_sha(url: str, ref: str | None) -> str:
    """Resolve ``ref`` (or HEAD if None) to a commit sha via ``git ls-remote``.

    Accepts a literal sha as ``ref`` even if the upstream does not advertise
    it as a named reference: ``ls-remote`` will return empty, and we fall
    back to the literal sha (which the subsequent clone+checkout step will
    fetch directly).
    """
    target = ref or "HEAD"
    try:
        completed = subprocess.run(
            ["git", "ls-remote", url, target],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        raise SourceError(
            f"git ls-remote failed for {url} {target}: {exc.stderr.strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"git ls-remote timed out for {url}") from exc

    output = completed.stdout.strip()
    if not output:
        if ref and _SHA_RE.match(ref):
            return ref
        raise SourceError(f"ref {target!r} not found in {url}")
    first_line = output.splitlines()[0]
    return first_line.split("\t", 1)[0]


def _git_clone(url: str, sha: str, dest: Path) -> None:
    """Clone ``url`` into ``dest`` and check out ``sha``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--quiet", url, str(dest)],
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
        subprocess.run(
            ["git", "-C", str(dest), "checkout", "--quiet", sha],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        raise SourceError(
            f"git clone of {url} (sha {sha}) failed: {exc.stderr.strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"git clone of {url} timed out") from exc


def _cache_root() -> Path:
    """Cache root directory.

    Defaults to ``~/.cache/clauz3/sources/``. Override with the
    ``CLAUZ3_CACHE`` environment variable (used by tests and by users who
    want the cache somewhere other than the home directory).
    """
    override = os.environ.get("CLAUZ3_CACHE")
    if override:
        return Path(override).expanduser() / "sources"
    return Path.home() / ".cache" / "clauz3" / "sources"
