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
        ref = match["ref"]
        # The shorthand regex already forbids a leading '-' in the ref, but run
        # the same explicit reject everything else uses so there is a single
        # choke point rather than relying on downstream `--`/sha resolution.
        if ref is not None:
            _reject_option_like(ref, "ref")
        return _ParsedSource(url=url, ref=ref)
    url, ref = _split_url_ref(source)
    _reject_option_like(url, "url")
    _reject_option_like_host(url)
    if ref is not None:
        _reject_option_like(ref, "ref")
    return _ParsedSource(url=url, ref=ref)


def _reject_option_like(value: str, kind: str) -> None:
    """Reject a url/ref that git would otherwise parse as an option.

    The ``--`` end-of-options separator added to every git invocation below
    does not protect the *value* of a transport such as ``ssh://-oProxyCommand=``,
    so an option-like url/ref (``--upload-pack=cmd``, ``-oProxyCommand=...``) is
    rejected outright. git treats these as remote-helper/ssh options, i.e. RCE.
    """
    if value.startswith("-"):
        raise SourceError(f"git {kind} may not begin with '-': {value!r}")


def _reject_option_like_host(url: str) -> None:
    """Reject ssh-transport URLs whose host/authority git hands to ssh as an option.

    For ``ssh://`` and scp-like ``user@host:path`` URLs, git invokes ssh with the
    authority as an argv element; if it begins with ``-`` (e.g.
    ``ssh://-oProxyCommand=evil/repo``) ssh interprets it as an option — RCE that
    the ``--`` separator cannot prevent. Non-ssh transports (https/git/file) pass
    the host to libcurl/git as a URL, not argv, so they are unaffected.
    """
    authority = _ssh_authority(url)
    if authority is None:
        return
    host = authority.rsplit("@", 1)[-1].split(":", 1)[0]
    if authority.startswith("-") or host.startswith("-"):
        raise SourceError(f"git ssh host may not begin with '-': {url!r}")


def _ssh_authority(url: str) -> str | None:
    """Return the ``[user@]host[:port]`` authority for ssh-transport URLs, else None."""
    if url.startswith("ssh://"):
        return url[len("ssh://") :].split("/", 1)[0]
    if "://" in url:
        return None
    # scp-like ``[user@]host:path`` (no scheme, host/path separated by ':')
    if ":" in url:
        return url.split(":", 1)[0]
    return None


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
            _git_argv("ls-remote", "--", url, target),
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
            env=_git_env(),
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
    _reject_option_like(url, "url")
    _reject_option_like(sha, "sha")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            _git_argv("clone", "--quiet", "--", url, str(dest)),
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
            env=_git_env(),
        )
        # ``sha`` is a resolved commit (hex) here; the trailing ``--`` forces
        # git to read it as a revision, never a pathspec.
        subprocess.run(
            _git_argv("-C", str(dest), "checkout", "--quiet", sha, "--"),
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
            env=_git_env(),
        )
    except subprocess.CalledProcessError as exc:
        raise SourceError(
            f"git clone of {url} (sha {sha}) failed: {exc.stderr.strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"git clone of {url} timed out") from exc


def _git_argv(*args: str) -> list[str]:
    """git argv with hardening config that applies to every invocation.

    Disables the ``ext::`` remote helper (arbitrary command execution) while
    leaving the documented ``file://`` / ``https`` / ``ssh`` transports working.

    NOTE: command-line ``-c`` is not inherited by child git processes spawned by
    a recursive submodule fetch. This is safe only because ``clone`` here omits
    ``--recurse-submodules`` (so ``.gitmodules`` is an inert tree file). If
    submodule recursion is ever added, switch to the env form
    (``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_n``/``GIT_CONFIG_VALUE_n``), which
    children do inherit, or this guard is silently bypassed for submodules.
    """
    return ["git", "-c", "protocol.ext.allow=never", *args]


def _git_env() -> dict[str, str]:
    """Keep operator Git authentication, but never pass service authority to Git."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CLAUZ3_APPROVAL")
        and key
        not in {
            "CLAUZ3_UI_CONFIG",
            "CLAUZ3_RUN_STATUS_FILE",
            "CLAUZ3_SESSION_LIFETIME_SOCKET",
        }
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


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
