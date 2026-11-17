"""Private per-port copies of each running server's approver sign-in link.

Every server start mints a new approver key, so browser tabs signed in to the
previous server are signed out. The new link used to exist only in the
server's terminal output; it is also kept here, readable only by the user,
for ``clauz3 login-url`` to print. Nothing else reads or prints these files.

>>> url = "http://127.0.0.1:8765/ui/login?key=secret"
>>> _port(url)
8765
>>> _port(http_origin("::1", 8765) + "/ui/login?key=secret")
8765
"""

import json
import os
import secrets
import sys
from contextlib import suppress
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from clauz3.libraries.discovery import global_library_root


class LoginLinkError(Exception):
    """No running clauz3 server has a saved sign-in link."""


def http_origin(host: str, port: int) -> str:
    """The ``http://`` origin for a bind host, bracketing an IPv6 address.

    >>> http_origin("127.0.0.1", 8765)
    'http://127.0.0.1:8765'
    >>> http_origin("::1", 8765)
    'http://[::1]:8765'
    """
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def login_link(origin: str, token: str) -> str:
    """The sign-in link for an approver token, whatever characters it holds.

    An operator-chosen token may contain ``+``, ``&`` or ``#``, which a URL
    would read as a space, a second parameter or a fragment.

    >>> login_link("http://127.0.0.1:8765", "a+b&c#d")
    'http://127.0.0.1:8765/ui/login?key=a%2Bb%26c%23d'
    """
    return f"{origin}/ui/login?key={quote(token, safe='')}"


def keep_link(url: str) -> bool:
    """Save ``url`` if the user store allows it; otherwise warn and go on.

    The saved file is only a convenience copy of a link the server already
    printed, so a read-only or broken store must not stop the server. The
    warning never includes the link.
    """
    try:
        save_login_url(url)
    except (OSError, ValueError) as exc:
        _warn("save", exc)
        return False
    return True


def forget_link(url: str) -> None:
    """Remove a link saved by :func:`keep_link`, warning if that fails."""
    try:
        remove_login_url(url)
    except (OSError, ValueError) as exc:
        _warn("remove", exc)


def login_links_dir() -> Path:
    """The user-only directory holding one ``<port>.url`` file per server."""
    return global_library_root() / "login"


def save_login_url(url: str) -> Path:
    """Write ``url`` for its port, replacing any link from an earlier start."""
    directory = _directory()
    path = directory / f"{_port(url)}.url"
    # Write a fresh 0600 file then rename it, so a reader never sees a partial
    # link and an existing file's permissions cannot leak into the new one.
    temporary = directory / f".{path.name}.{secrets.token_hex(8)}"
    descriptor = os.open(
        temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as stream:
        stream.write(url + "\n")
    os.replace(temporary, path)
    return path


def remove_login_url(url: str) -> None:
    """Remove the saved link unless a newer server on the port replaced it."""
    path = login_links_dir() / f"{_port(url)}.url"
    if _read(path) == url:
        # Another server stopping at the same moment may already have removed it.
        with suppress(FileNotFoundError):
            path.unlink()


def login_url(port: int | None = None) -> str:
    """The sign-in link for the server on ``port``, or the newest running one."""
    directory = login_links_dir()
    if port is not None:
        path = directory / f"{port}.url"
        url = _read(path)
        if url is None:
            raise LoginLinkError(
                f"no clauz3 server sign-in link is saved for port {port}; "
                "start one with `clauz3 ui`"
            )
        if not _serving(url):
            raise LoginLinkError(
                f"the clauz3 server on port {port} is not running; "
                "start it again with `clauz3 ui`"
            )
        return url
    paths = sorted(
        directory.glob("*.url") if directory.is_dir() else [],
        key=lambda p: p.lstat().st_mtime,
        reverse=True,
    )
    for path in paths:
        url = _read(path)
        if url is not None and _serving(url):
            return url
    raise LoginLinkError("no clauz3 server is running; start one with `clauz3 ui`")


def _directory() -> Path:
    directory = login_links_dir()
    if directory.is_symlink():
        raise ValueError("login link directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # ``mkdir`` leaves an existing directory's mode alone; keep it user-only.
    directory.chmod(0o700)
    return directory


def _read(path: Path) -> str | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor) as stream:
        return stream.read(4097).strip() or None


def _port(url: str) -> int:
    port = urlsplit(url).port
    if port is None:
        raise ValueError("a sign-in link must name its port")
    return port


def _serving(url: str) -> bool:
    """Whether a clauz3 server answers the link's unauthenticated health check.

    The request goes to ``/health`` only and carries no key. A port now used by
    another program, or a silent one, does not count as a running server.
    """
    parts = urlsplit(url)
    request = Request(
        f"{parts.scheme}://{parts.netloc}/health",
        # The service's host allow-list always includes 127.0.0.1, and its
        # host check cannot parse a bracketed IPv6 Host header.
        headers={"Host": f"127.0.0.1:{_port(url)}"},
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=1) as response:
            body = response.read(256)
    except OSError:
        return False
    try:
        return bool(json.loads(body) == {"ok": True})
    except ValueError:
        return False


def _warn(action: str, exc: OSError | ValueError) -> None:
    # ``exc`` describes a store path or a malformed port, never the key.
    print(
        f"warning: could not {action} the sign-in link for `clauz3 login-url`: {exc}",
        file=sys.stderr,
    )
