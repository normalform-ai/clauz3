"""Private sign-in link files and the ``clauz3 login-url`` command."""

import json
import os
import socket
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import uvicorn
from fastapi.testclient import TestClient

from clauz3.approvals.login_links import (
    LoginLinkError,
    login_links_dir,
    login_url,
    remove_login_url,
    save_login_url,
)
from clauz3.approvals.service import create_approval_app, serve_approval_service
from clauz3.cli import main


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@contextmanager
def http_server(body: bytes) -> Iterator[int]:
    """A local HTTP server answering every GET with ``body``."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield int(httpd.server_address[1])
        httpd.shutdown()


@pytest.fixture
def server() -> Iterator[int]:
    """A port answering ``GET /health`` like a running clauz3 server."""
    with http_server(json.dumps({"ok": True}).encode()) as port:
        yield port


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
    with http_server(json.dumps({"ok": True}).encode()) as port:
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


def test_a_port_reused_by_another_program_is_not_running(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with http_server(b"<html>someone else</html>") as port:
        save_login_url(link(port))
        with pytest.raises(LoginLinkError, match=f"on port {port} is not running"):
            login_url(port)
        with pytest.raises(LoginLinkError, match="no clauz3 server is running"):
            login_url()
    with socket.socket() as silent:
        # A bare TCP listener that never answers HTTP is not a clauz3 server.
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        port = silent.getsockname()[1]
        save_login_url(link(port))
        with pytest.raises(LoginLinkError, match="is not running"):
            login_url(port)


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


def fake_serve(monkeypatch: pytest.MonkeyPatch, during: Any) -> list[int]:
    """Replace uvicorn's serving loop; record the port of each bound socket."""
    ports: list[int] = []

    def run(self: uvicorn.Server, sockets: list[socket.socket]) -> None:
        [listener] = sockets
        ports.append(listener.getsockname()[1])
        during(ports[-1])

    monkeypatch.setattr(uvicorn.Server, "run", run)
    return ports


def test_approval_service_saves_its_link_while_serving(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []
    ports = fake_serve(monkeypatch, lambda port: seen.append(_saved(port)))
    serve_approval_service(
        host="127.0.0.1", port=0, auth_token="submit", decision_token="decide"
    )
    [port] = ports
    assert seen == [link(port, "decide")]
    assert not (login_links_dir() / f"{port}.url").exists()
    captured = capsys.readouterr()
    assert "clauz3 login-url" in captured.out
    assert captured.out.count("key=decide") == 1
    assert captured.err == ""


def test_printed_link_signs_in_whatever_the_token_contains(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # An operator-chosen token may hold characters a URL gives a meaning to.
    token = "a+b&c#d e/é?=%"
    seen: list[str] = []
    fake_serve(monkeypatch, lambda port: seen.append(_saved(port)))
    serve_approval_service(
        host="127.0.0.1", port=0, auth_token="submit", decision_token=token
    )
    [printed] = [
        line.split(": ", 1)[1]
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("approver UI")
    ]
    assert seen == [printed]
    # A browser sends the path and query, never the fragment.
    parts = urlsplit(printed)
    assert parts.fragment == ""
    client = TestClient(create_approval_app(auth_token="submit", decision_token=token))
    response = client.get(f"{parts.path}?{parts.query}", follow_redirects=False)
    assert response.status_code == 303, response.text
    assert client.get("/ui").status_code == 200


def test_a_busy_port_leaves_the_running_servers_link(
    server: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = save_login_url(link(server, "running"))
    before = path.read_bytes()
    ports = fake_serve(monkeypatch, lambda port: None)
    with pytest.raises(OSError):
        serve_approval_service(
            host="127.0.0.1", port=server, auth_token="submit", decision_token="other"
        )
    assert ports == []
    assert path.read_bytes() == before
    assert login_url(server) == link(server, "running")
    assert "key=other" not in capsys.readouterr().out


def test_cli_reports_a_busy_port(
    server: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = save_login_url(link(server, "running"))
    before = path.read_bytes()
    fake_serve(monkeypatch, lambda port: None)
    assert main(["approval-service", "--port", str(server)]) == 2
    assert path.read_bytes() == before
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert "key=" not in captured.out + captured.err


@pytest.mark.parametrize("store", ["file", "read-only"])
def test_unwritable_store_only_warns(
    store: str,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if store == "file":
        home.write_text("not a directory")
    else:
        home.mkdir()
        home.chmod(0o500)
    served: list[int] = []
    ports = fake_serve(monkeypatch, served.append)
    try:
        serve_approval_service(
            host="127.0.0.1", port=0, auth_token="submit", decision_token="decide"
        )
    finally:
        if store == "read-only":
            home.chmod(0o700)
    assert served == ports and len(ports) == 1
    captured = capsys.readouterr()
    # The link is still printed once, in the usual private line only.
    assert captured.out.count("key=decide") == 1
    [warning] = captured.err.splitlines()
    assert warning.startswith("warning: could not save the sign-in link")
    assert "key=" not in warning


def test_keeping_and_forgetting_links_only_warn(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from clauz3.approvals.login_links import forget_link, keep_link

    assert keep_link(link(8765, "decide")) is True
    login_links_dir().chmod(0o500)
    try:
        forget_link(link(8765, "decide"))
    finally:
        login_links_dir().chmod(0o700)
    assert (login_links_dir() / "8765.url").exists()
    (login_links_dir() / "8765.url").unlink()
    login_links_dir().rmdir()
    home.rmdir()
    home.write_text("not a directory")
    assert keep_link(link(8765, "decide")) is False
    warnings = capsys.readouterr().err.splitlines()
    assert [w.split(":")[0] for w in warnings] == ["warning", "warning"]
    assert warnings[0].startswith("warning: could not remove the sign-in link")
    assert warnings[1].startswith("warning: could not save the sign-in link")
    assert not any("key=" in w for w in warnings)


def _ipv6_available() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


@pytest.mark.skipif(not _ipv6_available(), reason="no IPv6 loopback")
def test_ipv6_host_saves_a_bracketed_link(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    servers: list[uvicorn.Server] = []
    original = uvicorn.Server.run

    def run(self: uvicorn.Server, sockets: list[socket.socket]) -> None:
        servers.append(self)
        original(self, sockets=sockets)

    monkeypatch.setattr(uvicorn.Server, "run", run)
    service = threading.Thread(
        target=serve_approval_service,
        kwargs={
            "host": "::1",
            "port": 0,
            "auth_token": "submit",
            "decision_token": "decide",
        },
    )
    service.start()
    try:
        deadline = time.monotonic() + 10
        while not (servers and servers[0].started):
            assert time.monotonic() < deadline, "service did not start"
            time.sleep(0.02)
        [path] = login_links_dir().glob("*.url")
        port = int(path.stem)
        expected = f"http://[::1]:{port}/ui/login?key=decide"
        assert path.read_text() == expected + "\n"
        assert main(["login-url", "--port", str(port)]) == 0
        assert capsys.readouterr().out.splitlines()[-1] == expected
    finally:
        if servers:
            servers[0].should_exit = True
        service.join(timeout=10)
    assert not service.is_alive()
    assert list(login_links_dir().glob("*.url")) == []


def _saved(port: int) -> str:
    path = login_links_dir() / f"{port}.url"
    assert mode(path) == 0o600
    return path.read_text().strip()
