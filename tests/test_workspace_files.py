"""Workspace file browsing uses the operator session and cannot follow links."""

import errno
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clauz3.approvals.service import create_approval_app
from clauz3.sessions.chat import ChatConfig
from clauz3.sessions.files import MAX_ENTRIES, MAX_PREVIEW


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "nested").mkdir()
    (root / "nested/read me.py").write_text('<script>alert("hello")</script>\nλ')
    (root / ".env").write_text("hidden")
    (root / "binary").write_bytes(b"\x00\xff")
    (root / "latin1").write_bytes(b"\xff")
    (root / "large").write_text("a" * (MAX_PREVIEW - 1) + "λ-tail")
    (tmp_path / "outside").write_text("private")
    (root / "escape").symlink_to(tmp_path / "outside")
    (root / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    (root / "internal-link").symlink_to(root / "nested/read me.py")
    os.mkfifo(root / "fifo")
    config = ChatConfig(
        command=[],
        cwd=str(root),
        bridge=[],
        runner_env={},
        status_file=str(tmp_path / "runs.json"),
    )
    app = create_approval_app(
        auth_token="submit", decision_token="decide", chat_config=config
    )
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1))


def login(client: TestClient) -> None:
    assert client.get("/ui/login?key=decide").status_code == 200


def test_file_browser_requires_operator_and_same_origin(client: TestClient) -> None:
    url = "/api/workspace/files"
    assert client.get(url).status_code == 401
    assert (
        client.get(url, headers={"Authorization": "Bearer submit"}).status_code == 401
    )
    login(client)
    for headers in (
        {"Origin": "http://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Host": "localhost"},
    ):
        assert client.get(url, headers=headers).status_code == 401
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.post(url).status_code == 405


def test_directory_navigation_and_hidden_files(client: TestClient) -> None:
    login(client)
    entries = client.get("/api/workspace/files").json()["entries"]
    assert entries[0]["name"] == "nested"
    assert ".env" not in {entry["name"] for entry in entries}
    assert all(
        entry["kind"] == "unavailable"
        for entry in entries
        if entry["name"] in {"escape", "linked-dir", "fifo"}
    )
    entries = client.get("/api/workspace/files?hidden=true").json()["entries"]
    assert ".env" in {entry["name"] for entry in entries}
    entries = client.get("/api/workspace/files?path=nested").json()["entries"]
    assert [entry["name"] for entry in entries] == ["read me.py"]


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/etc/passwd",
        "nested/../../outside",
        "nested/../.env",
        "nested//file",
        "\x00",
    ],
)
def test_rejects_non_workspace_paths(client: TestClient, path: str) -> None:
    login(client)
    assert client.get("/api/workspace/files", params={"path": path}).status_code == 400


@pytest.mark.parametrize("path", ["escape", "linked-dir/outside", "internal-link"])
def test_does_not_follow_symlinks(client: TestClient, path: str) -> None:
    login(client)
    assert client.get("/api/workspace/files", params={"path": path}).status_code == 403


def test_bounded_text_preview_and_nonregular_files(client: TestClient) -> None:
    login(client)
    url = "/api/workspace/files"
    result = client.get(url, params={"path": "nested/read me.py"}).json()
    assert result["content"] == '<script>alert("hello")</script>\nλ'
    assert result["truncated"] is False
    for path in ("binary", "latin1"):
        assert client.get(url, params={"path": path}).json()["content"] is None
    result = client.get(url, params={"path": "large"}).json()
    assert result["truncated"] is True
    assert result["content"] == "a" * (MAX_PREVIEW - 1)
    assert client.get(url, params={"path": "fifo"}).status_code == 415
    assert client.get(url, params={"path": "missing"}).status_code == 404


def test_directory_limit(client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "workspace/nested"
    for index in range(MAX_ENTRIES):
        (folder / str(index)).touch()
    login(client)
    result = client.get("/api/workspace/files?path=nested").json()
    assert result["truncated"] is True
    assert len(result["entries"]) == MAX_ENTRIES


def test_directory_swapped_to_link_during_open_is_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    real_open = os.open
    root = tmp_path / "workspace"

    def swap(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == "nested":
            (root / "nested").rename(root / "original")
            (root / "nested").symlink_to(tmp_path, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    login(client)
    monkeypatch.setattr(os, "open", swap)
    response = client.get("/api/workspace/files", params={"path": "nested/outside"})
    assert response.status_code == 403
    assert "private" not in response.text


def test_non_utf8_filename_does_not_break_listing(
    client: TestClient, tmp_path: Path
) -> None:
    name = os.fsencode(tmp_path / "workspace") + b"/invalid-\xff"
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError as exc:
        if exc.errno == errno.EILSEQ:
            pytest.skip("This filesystem requires UTF-8 filenames")
        raise
    os.close(descriptor)
    login(client)
    result = client.get("/api/workspace/files")
    assert result.status_code == 200
    entry = next(
        e for e in result.json()["entries"] if e["name"].startswith("invalid-")
    )
    assert entry["kind"] == "unavailable"
