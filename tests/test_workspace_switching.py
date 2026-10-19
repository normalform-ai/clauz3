"""Workspace transitions preserve ownership, authority and tutorial isolation."""

import hashlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from clauz3._vendor.deal_solver import Conclusion
from clauz3.approvals.client import APPROVAL_TOKEN_ENV
from clauz3.approvals.decisions import Decision
from clauz3.approvals.profiles.store import ProfileStore
from clauz3.approvals.service import create_approval_app
from clauz3.libraries.layout import expand_import_roots
from clauz3.libraries.manifest import read_manifest
from clauz3.libraries.mock_metadata import mock_declarations
from clauz3.prover import prove_text
from clauz3.sessions.chat import ChatConfig
from clauz3.sessions.history import ConversationStore
from clauz3.sessions.workspaces import Workspaces

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = {"Origin": "http://127.0.0.1"}


def until(ws: WebSocketTestSession, method: str) -> dict[str, Any]:
    for _ in range(100):
        message = ws.receive_json()
        if message["method"] == method:
            return dict(message["params"])
    raise AssertionError(f"Missing {method}")


def ready(ws: WebSocketTestSession) -> dict[str, Any]:
    for _ in range(100):
        state = until(ws, "clauz3/state")
        if state["sessionId"]:
            return state
    raise AssertionError("Agent not ready")


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[tuple[TestClient, Path, Path]]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "original.txt").write_text("Original workspace")
    config = ChatConfig(
        cwd=str(project),
        command=[sys.executable, str(ROOT / "tests/fixtures/workspace_agent.py")],
        bridge=[],
        runner_env={APPROVAL_TOKEN_ENV: "submit"},
        status_file=str(tmp_path / "state/runs.json"),
        history_file=str(tmp_path / "state/conversations.sqlite"),
        manage_libraries=True,
    )
    app = create_approval_app(
        auth_token="submit",
        decision_token="decide",
        chat_config=config,
        profile=ProfileStore(
            tmp_path / "state/approval-history.sqlite", project=project
        ),
    )
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)
    ) as client:
        client.get("/ui/login?key=decide")
        yield client, project, tmp_path / "state"


def switch(ws: WebSocketTestSession, **params: Any) -> dict[str, Any]:
    ws.send_json({"method": "clauz3/workspace", "params": params})
    until(ws, "clauz3/workspace_changed")
    return until(ws, "clauz3/restore")


def test_switch_tutorial_and_back_preserves_histories_and_rotates_authority(
    workspace: tuple[TestClient, Path, Path],
) -> None:
    client, project, storage = workspace
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        original = until(ws, "clauz3/restore")
        ready(ws)
        state = switch(ws, tutorial="email")
        tutorial = Path(state["cwd"])
        assert tutorial == storage / "tutorials/email"
        assert state["libraries"] == ["email"]
        ready(ws)
        snapshot = client.get("/api/workspaces").json()
        assert snapshot["tutorial"]
        assert snapshot["recent"][0]["path"] == str(project)
        assert (
            client.get("/api/workspace/libraries").json()["mockDeclarations"]["email"][
                "kind"
            ]
            == "mock"
        )
        assert (
            client.get(
                "/api/workspace/files", params={"path": "original.txt"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/requests", headers={"Authorization": "Bearer submit"}, json={}
            ).status_code
            == 401
        )
        assert (
            cast(FastAPI, client.app).state.conversation.config.runner_env[
                APPROVAL_TOKEN_ENV
            ]
            != "submit"
        )
        back = switch(ws, path=str(project))
        assert back["cwd"] == str(project)
        assert back["conversationId"] == original["conversationId"]
        assert back["libraries"] == []
        ready(ws)
        assert not client.get("/api/workspaces").json()["tutorial"]
        again = switch(ws, tutorial="email")
        assert again["conversationId"] == state["conversationId"]


def test_busy_requires_explicit_stop_and_destination_lease(
    workspace: tuple[TestClient, Path, Path],
) -> None:
    client, project, storage = workspace
    other = project.parent / "other"
    other.mkdir()
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        until(ws, "clauz3/restore")
        ready(ws)
        ws.send_json({"method": "session/prompt", "params": {"prompt": "wait"}})
        until(ws, "clauz3/user")
        ws.send_json({"method": "clauz3/workspace", "params": {"path": str(other)}})
        confirmation = until(ws, "clauz3/confirm_workspace")
        assert confirmation["path"] == str(other)
        assert client.get("/api/workspaces").json()["current"] == str(project)
        # A destination already owned by another UI must not stop this agent.
        with ConversationStore(storage / "conversations.sqlite", str(other)).lease():
            ws.send_json(
                {
                    "method": "clauz3/workspace",
                    "params": {"path": str(other), "stop": True},
                }
            )
            assert "already has" in until(ws, "clauz3/notice")["text"]
            assert cast(FastAPI, client.app).state.conversation.busy
        switched = switch(ws, path=str(other), stop=True)
        assert switched["cwd"] == str(other)
        ready(ws)
        # Source lease released after the switch; destination still held.
        with ConversationStore(storage / "conversations.sqlite", str(project)).lease():
            pass
        with (
            pytest.raises(ValueError, match="already has"),
            ConversationStore(storage / "conversations.sqlite", str(other)).lease(),
        ):
            pass


def test_reset_scoped_to_tutorial(workspace: tuple[TestClient, Path, Path]) -> None:
    client, project, storage = workspace
    original_profile = ProfileStore(
        storage / "approval-history.sqlite", project=project
    )
    original_profile.record({"request_id": "keep"}, None)
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        original = until(ws, "clauz3/restore")
        ready(ws)
        first = switch(ws, tutorial="email")
        ready(ws)
        tutorial = Path(first["cwd"])
        (tutorial / "scratch.txt").write_text("remove")
        profile = ProfileStore(storage / "approval-history.sqlite", project=tutorial)
        event = profile.record(
            {"request_id": "discard"}, {"signature": "tutorial-rule"}
        )
        profile.decide(event, Decision.APPROVED_REMEMBER, remember=True)
        assert profile.match("tutorial-rule")
        preferences = (
            storage
            / "workspaces"
            / hashlib.sha256(str(tutorial).encode()).hexdigest()
            / "libraries.json"
        )
        preferences.write_text("[]")
        ws.send_json(
            {
                "method": "clauz3/workspace",
                "params": {"tutorial": "email", "reset": True},
            }
        )
        assert "Confirm" in until(ws, "clauz3/notice")["text"]
        assert (tutorial / "scratch.txt").exists()
        fresh = switch(ws, tutorial="email", reset=True, confirmReset=True, stop=True)
        assert fresh["conversationId"] != first["conversationId"]
        assert fresh["libraries"] == ["email"]
        with (
            pytest.raises(ValueError, match="already has"),
            ConversationStore(storage / "conversations.sqlite", str(tutorial)).lease(),
        ):
            pass
        assert not (tutorial / "scratch.txt").exists()
        assert (
            len(
                ConversationStore(
                    storage / "conversations.sqlite", str(tutorial)
                ).list()
            )
            == 1
        )
        assert (
            len(
                ConversationStore(storage / "conversations.sqlite", str(project)).list()
            )
            == 1
        )
        assert ConversationStore(storage / "conversations.sqlite", str(project)).load(
            original["conversationId"]
        )
        assert profile.snapshot()["history"] == []
        assert profile.snapshot()["rules"] == []
        assert len(cast(list[Any], original_profile.snapshot()["history"])) == 1
        assert (project / "original.txt").exists()


def test_registry_and_template_paths_are_bounded(tmp_path: Path) -> None:
    registry = Workspaces(tmp_path / "data")
    with pytest.raises(ValueError, match="absolute"):
        registry.target({"path": "."})
    with pytest.raises(ValueError, match="Unknown"):
        registry.target({"tutorial": "../../elsewhere"})
    tutorial = registry.prepare_email()
    (tutorial / "keep.txt").write_text("keep")
    assert registry.prepare_email() == tutorial
    assert (tutorial / "keep.txt").exists()
    with pytest.raises(ValueError, match="Tutorials list"):
        registry.target({"path": str(tutorial)})
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tutorial / "link"
    link.symlink_to(outside, target_is_directory=True)
    registry.prepare_email(reset=True)
    assert outside.is_dir()
    assert not link.exists()
    registry.email.rename(tmp_path / "moved")
    registry.email.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        registry.prepare_email(reset=True)


@pytest.mark.parametrize(
    "recipient,expected",
    [("bob@example.com", Conclusion.OK), ("alice@example.com", Conclusion.FAIL)],
)
def test_bundled_tutorial_proofs(
    tmp_path: Path, recipient: str, expected: Conclusion
) -> None:
    project = Workspaces(tmp_path / "state").prepare_email()
    roots = [project / "tools/email/trusted"]
    source = f"""import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

@clauz3.guarantee(emails.only(["bob@example.com"]))
@clauz3.guarantee(emails.at_most(1))
@clauz3.guarantee(emails.unique_recipients())
def main() -> None:
    send_email({recipient!r}, "Hello from ClauZ3")
"""
    proofs = prove_text(source, trusted_roots=roots, import_paths=[project])
    assert [proof.proof.conclusion for proof in proofs] == [expected]
    assert mock_declarations(project, roots)["kind"] == "mock"
    manifest = read_manifest(project / "tools/email", "email")
    assert manifest and manifest["title"] == "Mock email"


def test_tutorial_startup_and_reopen_keep_copy(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    storage = tmp_path / "state"
    config = ChatConfig(
        cwd=str(project),
        tutorial=True,
        command=[sys.executable, str(ROOT / "tests/fixtures/workspace_agent.py")],
        bridge=["unused", "--skills-snapshot", "/launch-workspace-only.json"],
        runner_env={},
        status_file=str(storage / "runs.json"),
        manage_libraries=True,
    )
    identifier = ""
    for iteration in range(2):
        app = create_approval_app(
            auth_token="submit", decision_token="decide", chat_config=config
        )
        with TestClient(
            app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)
        ) as client:
            client.get("/ui/login?key=decide")
            with client.websocket_connect(
                "ws://127.0.0.1/api/chat/acp", headers=ORIGIN
            ) as ws:
                state = until(ws, "clauz3/restore")
                ready(ws)
                assert state["libraries"] == ["email"]
                assert state["cwd"] == str(storage / "tutorials/email")
                assert "--skills-snapshot" not in app.state.conversation.config.bridge
                if iteration:
                    assert state["conversationId"] == identifier
                    assert (Path(state["cwd"]) / "saved.txt").read_text() == "survives"
                identifier = state["conversationId"]
                (Path(state["cwd"]) / "saved.txt").write_text("survives")
    assert list(project.iterdir()) == []


def test_switch_cancels_request_finishing_verification_in_previous_workspace(
    workspace: tuple[TestClient, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import concurrent.futures
    import threading

    from clauz3.approvals import service
    from clauz3.runner import build_approval_request

    client, project, storage = workspace
    entered, release = threading.Event(), threading.Event()

    def verifying(*args: Any, **kwargs: Any) -> None:
        entered.set()
        assert release.wait(10)
        return None

    monkeypatch.setattr(service, "verify_profile", verifying)
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        until(ws, "clauz3/restore")
        ready(ws)
        state = switch(ws, tutorial="email")
        ready(ws)
        root = Path(state["cwd"])
        source = """import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email
@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    send_email("bob@example.com", "Hello")
"""
        roots = [root / "tools/email/trusted"]
        proofs = prove_text(source, trusted_roots=roots, import_paths=[root])
        payload = build_approval_request(
            source=source,
            source_name="<test>",
            target="main",
            proofs=proofs,
            trusted_roots=roots,
            import_roots=expand_import_roots([root]),
        )
        token = cast(FastAPI, client.app).state.conversation.config.runner_env[
            APPROVAL_TOKEN_ENV
        ]
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = pool.submit(
                client.post,
                "/api/requests",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            try:
                assert entered.wait(10), result.result(timeout=1).text
                switch(ws, path=str(project), stop=True)
            finally:
                release.set()
            response = result.result(timeout=10)
        assert response.status_code == 409
        assert "Workspace changed" in response.text
        assert client.get("/api/requests").json() == []
        assert client.get("/api/profile").json()["history"] == []
        assert (
            ProfileStore(storage / "approval-history.sqlite", project=root).snapshot()[
                "history"
            ]
            == []
        )


def test_storage_aliases_have_one_canonical_tutorial(tmp_path: Path) -> None:
    storage = tmp_path / "data"
    storage.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(storage, target_is_directory=True)
    registry = Workspaces(alias)
    assert registry.target({"tutorial": "email"}) == storage / "tutorials/email"
    assert registry.prepare_email() == storage / "tutorials/email"
