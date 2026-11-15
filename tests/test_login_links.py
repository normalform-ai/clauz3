"""Private sign-in link files and the ``clauz3 login-url`` command."""

import os
import socket
import stat
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from clauz3.approvals.login_links import (
    LoginLinkError,
    login_links_dir,
    login_url,
    remove_login_url,
    save_login_url,
)
from clauz3.cli import main


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def server() -> Iterator[int]:
    """A listening port standing in for a running clauz3 server."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        yield listener.getsockname()[1]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def link(port: int, key: str = "first") -> str:
    return f"http://127.0.0.1:{port}/ui/login?key={key}"


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_saved_link_is_private_to_the_user(home: Path, server: int) -> None:
    path = save_login_url(link(server))
    assert path == home / "login" / f"{server}.url"
    assert path.read_text() == link(server) + "\n"
    assert mode(path) == 0o600
    assert mode(path.parent) == 0o700


def test_existing_directory_is_made_private(server: int) -> None:
    login_links_dir().mkdir(parents=True, mode=0o755)
    login_links_dir().chmod(0o755)
    save_login_url(link(server))
    assert mode(login_links_dir()) == 0o700


def test_restart_replaces_the_link(server: int) -> None:
    path = save_login_url(link(server, "first"))
    path.chmod(0o644)  # A replaced file never keeps an old file's mode.
    assert save_login_url(link(server, "second")) == path
    assert path.read_text() == link(server, "second") + "\n"
    assert mode(path) == 0o600
    assert login_url(server) == link(server, "second")
    assert sorted(p.name for p in login_links_dir().iterdir()) == [path.name]


def test_stopping_an_old_server_keeps_the_new_link(server: int) -> None:
    save_login_url(link(server, "first"))
    path = save_login_url(link(server, "second"))
    remove_login_url(link(server, "first"))
    assert path.exists()
    remove_login_url(link(server, "second"))
    assert not path.exists()
    remove_login_url(link(server, "second"))  # Already gone is fine.


def test_symlinked_directory_is_refused(home: Path, tmp_path: Path) -> None:
    home.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (home / "login").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="symlink"):
        save_login_url(link(8765))


def test_default_is_the_newest_running_server(server: int) -> None:
    stopped = free_port()
    save_login_url(link(server, "running"))
    older = time.time() - 60
    os.utime(login_links_dir() / f"{server}.url", (older, older))
    # A newer file left by a server that crashed is skipped.
    save_login_url(link(stopped, "crashed"))
    assert login_url() == link(server, "running")
    with socket.socket() as newer:
        newer.bind(("127.0.0.1", 0))
        newer.listen()
        port = newer.getsockname()[1]
        save_login_url(link(port, "newest"))
        assert login_url() == link(port, "newest")


def test_missing_and_stale_links_are_explained() -> None:
    with pytest.raises(LoginLinkError, match="no clauz3 server is running"):
        login_url()
    with pytest.raises(LoginLinkError, match="no clauz3 server sign-in link is saved"):
        login_url(8765)
    port = free_port()
    save_login_url(link(port))
    with pytest.raises(LoginLinkError, match=f"on port {port} is not running"):
        login_url(port)
    with pytest.raises(LoginLinkError, match="no clauz3 server is running"):
        login_url()


def test_cli_prints_the_link(server: int, capsys: pytest.CaptureFixture[str]) -> None:
    save_login_url(link(server))
    assert main(["login-url"]) == 0
    assert capsys.readouterr().out == link(server) + "\n"


def test_cli_honours_port(server: int, capsys: pytest.CaptureFixture[str]) -> None:
    other = free_port()
    save_login_url(link(server, "chosen"))
    save_login_url(link(other, "other"))
    assert main(["login-url", "--port", str(server)]) == 0
    assert capsys.readouterr().out == link(server, "chosen") + "\n"
    assert main(["login-url", "--port", str(other)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"error: the clauz3 server on port {other} is not running" in captured.err
    assert "key=" not in captured.err


def test_cli_errors_without_a_server(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["login-url"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "error: no clauz3 server is running; start one with `clauz3 ui`\n"
    )


def test_cli_only_reads(server: int) -> None:
    path = save_login_url(link(server))
    before = path.stat()
    assert main(["login-url"]) == 0
    after = path.stat()
    assert (after.st_mtime_ns, after.st_mode) == (before.st_mtime_ns, before.st_mode)


def test_approval_service_saves_its_link_while_serving(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from clauz3.approvals.service import serve_approval_service

    seen: list[str] = []

    def serve(app: object, *, host: str, port: int) -> None:
        seen.append(_saved(port))

    monkeypatch.setattr("clauz3.approvals.service.uvicorn.run", serve)
    serve_approval_service(
        host="127.0.0.1", port=8799, auth_token="submit", decision_token="decide"
    )
    assert seen == [link(8799, "decide")]
    assert not (login_links_dir() / "8799.url").exists()
    out = capsys.readouterr().out
    assert "clauz3 login-url" in out
    assert out.count("key=decide") == 1


def _saved(port: int) -> str:
    path = login_links_dir() / f"{port}.url"
    assert mode(path) == 0o600
    return path.read_text().strip()
