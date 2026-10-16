"""Operator-controlled library installation and per-conversation selection."""

import hashlib
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from clauz3.approvals.service import create_approval_app
from clauz3.libraries.workspace import WorkspaceLibraries
from clauz3.sessions import providers
from clauz3.sessions.chat import ChatConfig

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = {"Origin": "http://127.0.0.1"}


def until(
    ws: WebSocketTestSession, predicate: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    for _ in range(100):
        message = ws.receive_json()
        if predicate(message):
            return cast(dict[str, Any], message)
    raise AssertionError("Expected workspace event not received")


def ready(ws: WebSocketTestSession) -> None:
    until(
        ws, lambda m: bool(m["method"] == "clauz3/state" and m["params"]["sessionId"])
    )


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WorkspaceLibraries:
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "global"))
    project = tmp_path / "project"
    project.mkdir()
    return WorkspaceLibraries(project, tmp_path / "state", [])


def test_review_install_dependencies_and_selection(manager: WorkspaceLibraries) -> None:
    review = manager.prepare("install", "stdlib:find", "")
    assert not (manager.project / ".clauz3/tools").exists()
    assert set(review["domains"]) == {"find", "filesystem"}
    assert review["api"] and review["sourceCode"]
    assert "find executable" in review["requirements"]
    manager.apply(review["id"], [])
    assert manager.select(["find"]) == ["filesystem", "find"]
    assert WorkspaceLibraries(manager.project, manager.storage, []).selected == [
        "filesystem",
        "find",
    ]
    assert manager.inspect("stdlib:find")["api"]
    with pytest.raises(ValueError, match="depends"):
        manager.prepare("remove", "", "filesystem")


def test_update_and_remove_are_reviewed_and_backed_up(
    manager: WorkspaceLibraries,
) -> None:
    review = manager.prepare("install", "stdlib:env", "")
    manager.apply(review["id"], [])
    file = manager.project / ".clauz3/tools/env/trusted/effects.py"
    file.write_text(file.read_text() + "\n# My project edit\n")
    review = manager.prepare("update", "stdlib:env", "")
    assert "trusted/effects.py" in review["changes"][0]["changed"]
    with pytest.raises(ValueError, match="Stop"):
        manager.apply(review["id"], ["env"])
    assert "My project edit" in file.read_text()
    result = manager.apply(review["id"], [])
    assert "My project edit" not in file.read_text()
    assert (
        "My project edit"
        in (manager.project / result["backup"] / "env/trusted/effects.py").read_text()
    )
    manager.select(["env"])
    review = manager.prepare("remove", "", "env")
    result = manager.apply(review["id"], [])
    assert not file.exists()
    assert manager.selected == []
    assert (manager.project / result["backup"] / "env/trusted/effects.py").is_file()


def test_changes_after_review_and_symlink_destinations_are_refused(
    manager: WorkspaceLibraries,
) -> None:
    review = manager.prepare("install", "stdlib:env", "")
    target = manager.project / ".clauz3/tools/env/trusted"
    target.mkdir(parents=True)
    (target / "mine.py").write_text("# keep")
    with pytest.raises(ValueError, match="changed after review"):
        manager.apply(review["id"], [])
    shutil.rmtree(manager.project / ".clauz3/tools")
    (manager.project / ".clauz3/tools").symlink_to(
        manager.storage, target_is_directory=True
    )
    with pytest.raises(ValueError, match="Symbolic"):
        manager.apply(review["id"], [])
    assert not (manager.storage / "env").exists()


def test_staged_tampering_unknown_sources_and_ids_are_refused(
    manager: WorkspaceLibraries,
) -> None:
    with pytest.raises(ValueError, match="catalogue"):
        manager.prepare("install", "../../secret", "")
    review = manager.prepare("install", "stdlib:env", "")
    assert manager.staging
    file = Path(manager.staging.name) / ".clauz3/tools/env/trusted/effects.py"
    file.write_text("# changed")
    with pytest.raises(ValueError, match="Staged files changed"):
        manager.apply(review["id"], [])
    with pytest.raises(ValueError, match="expired"):
        manager.apply("not-the-review", [])
    manager.discard()


@pytest.fixture
def client(
    manager: WorkspaceLibraries,
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    agent = getattr(request, "param", "claude")
    config = ChatConfig(
        command=[
            sys.executable,
            str(ROOT / "tests/fixtures/workspace_agent.py"),
            *(["--generic"] if agent != "claude" else []),
        ],
        agent=agent,
        unrestricted=agent != "claude",
        select_provider=True,
        cwd=str(manager.project),
        bridge=[],
        runner_env={},
        status_file=str(tmp_path / "runs.json"),
        manage_libraries=True,
    )
    if agent != "claude":
        monkeypatch.setattr(providers, "command", lambda _: config.command)
    app = create_approval_app(
        auth_token="submit", decision_token="decide", chat_config=config
    )
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)
    ) as client:
        yield client


def post(client: TestClient, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/api/workspace/libraries/" + action, json=payload, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def test_installer_authentication(client: TestClient) -> None:
    url = "/api/workspace/libraries"
    assert client.get(url).status_code == 401
    assert (
        client.post(
            url + "/prepare",
            json={"source": "stdlib:env", "action": "install"},
            headers={"Authorization": "Bearer submit", **ORIGIN},
        ).status_code
        == 401
    )
    client.get("/ui/login?key=decide")
    for headers in (
        {},
        {"Origin": "http://evil.example"},
        {"Sec-Fetch-Site": "cross-site", **ORIGIN},
    ):
        assert (
            client.post(
                url + "/select", json={"domains": []}, headers=headers
            ).status_code
            == 401
        )
    assert client.get(url).status_code == 200
    assert client.get("/ui/libraries").status_code == 200


def test_selection_applies_only_to_new_conversation(client: TestClient) -> None:
    client.get("/ui/login?key=decide")
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        ready(ws)
        app = cast(FastAPI, client.app)
        first = app.state.conversation.identifier
        review = post(client, "prepare", {"source": "stdlib:env", "action": "install"})
        post(client, "apply", {"id": review["id"]})
        post(client, "select", {"domains": ["env"]})
        assert app.state.conversation.config.libraries == []
        ws.send_json({"method": "clauz3/new"})
        until(ws, lambda m: m["method"] == "clauz3/restore")
        ready(ws)
        assert app.state.conversation.config.libraries == ["env"]
        assert (
            str(Path(app.state.conversation.config.cwd) / ".clauz3/tools/env/trusted")
            in app.state.conversation.config.bridge
        )
        ws.send_json({"method": "clauz3/select", "params": {"id": first}})
        until(ws, lambda m: m["method"] == "clauz3/restore")
        ready(ws)
        assert app.state.conversation.config.libraries == []


def test_changed_libraries_keep_old_conversations_read_only(client: TestClient) -> None:
    client.get("/ui/login?key=decide")
    review = post(client, "prepare", {"source": "stdlib:env", "action": "install"})
    post(client, "apply", {"id": review["id"]})
    post(client, "select", {"domains": ["env"]})
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        ready(ws)
        ws.send_json({"method": "clauz3/new"})
        until(ws, lambda m: m["method"] == "clauz3/restore")
        ready(ws)
        app = cast(FastAPI, client.app)
        old = app.state.conversation.identifier
        original_fingerprint = app.state.conversation.config.library_fingerprint
        ws.send_json(
            {"method": "session/prompt", "params": {"prompt": "Keep this chat"}}
        )
        until(ws, lambda m: m["method"] == "clauz3/turn_end")
        ws.send_json({"method": "session/cancel"})
        until(
            ws, lambda m: bool(m["method"] == "clauz3/state" and m["params"]["ended"])
        )
        file = (
            Path(app.state.conversation.config.cwd)
            / ".clauz3/tools/env/trusted/effects.py"
        )
        file.write_text(file.read_text() + "\n# changed version\n")
        for _ in range(2):
            ws.send_json({"method": "clauz3/select", "params": {"id": old}})
            restored = until(ws, lambda m: m["method"] == "clauz3/restore")
            assert "Keep this chat" in str(restored)
            until(
                ws,
                lambda m: bool(m["method"] == "clauz3/state" and m["params"]["ended"]),
            )
            assert "libraries changed" in app.state.conversation.error
            assert (
                app.state.conversation.config.library_fingerprint
                == original_fingerprint
            )


def test_failed_install_rolls_back_all_domains(
    manager: WorkspaceLibraries, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = manager.prepare("install", "stdlib:find", "")
    real_rename = Path.rename

    def fail_second(path: Path, target: str | Path) -> Path:
        if path.name == "find" and path.parent.name == "tools":
            raise OSError("disk write failed")
        return real_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_second)
    with pytest.raises(OSError, match="disk write failed"):
        manager.apply(review["id"], [])
    assert manager.installed() == []


def test_layout_conflict_reports_startup_error_without_authorizing(
    manager: WorkspaceLibraries, tmp_path: Path
) -> None:
    from clauz3.libraries.install import install_layer

    install_layer("stdlib:env", into=manager.project)
    shutil.copytree(
        manager.project / ".clauz3/tools/env", manager.project / "tools/env"
    )
    config = ChatConfig(
        command=[sys.executable, str(ROOT / "tests/fixtures/workspace_agent.py")],
        cwd=str(manager.project),
        bridge=[],
        runner_env={},
        status_file=str(tmp_path / "runs.json"),
        libraries=["env"],
        manage_libraries=True,
    )
    app = create_approval_app(
        auth_token="submit", decision_token="decide", chat_config=config
    )
    # Startup reports the unusable layout instead of crashing in the fallback.
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)):
        error = app.state.conversation.config.startup_error
        assert error is not None and "provided by both" in error


def install(client: TestClient, source: str) -> None:
    review = post(client, "prepare", {"source": source, "action": "install"})
    post(client, "apply", {"id": review["id"]})


def prompt(ws: WebSocketTestSession, text: str) -> None:
    ws.send_json({"method": "session/prompt", "params": {"prompt": text}})
    until(ws, lambda m: m["method"] == "clauz3/turn_end")


def change(
    ws: WebSocketTestSession, domains: list[str], *, confirm: bool = False
) -> dict[str, Any] | None:
    """Apply a library change; return its transcript marker, if any."""
    ws.send_json(
        {
            "method": "clauz3/libraries",
            "params": {"domains": domains, "confirm": confirm},
        }
    )
    until(ws, lambda m: m["method"] == "clauz3/restore")
    marker = None
    for _ in range(100):
        message = ws.receive_json()
        if message["method"] == "clauz3/libraries_changed":
            marker = message
        if message["method"] == "clauz3/state":
            assert not message["params"]["error"], message["params"]["error"]
            if message["params"]["sessionId"]:
                return marker
    raise AssertionError("The conversation did not restart")


def test_libraries_change_freely_before_the_first_message(client: TestClient) -> None:
    client.get("/ui/login?key=decide")
    install(client, "stdlib:env")
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        ready(ws)
        app = cast(FastAPI, client.app)
        first = app.state.conversation.identifier
        change(ws, ["env"])
        conversation = app.state.conversation
        assert conversation.identifier == first
        assert conversation.config.libraries == ["env"]
        assert not conversation.config.approvals_paused
        assert conversation.history == []
        # The saved default for new conversations is untouched.
        assert client.get("/api/workspace/libraries").json()["selected"] == []


@pytest.mark.parametrize("client", ["claude", "codex", "opencode"], indirect=True)
def test_adding_libraries_mid_conversation_needs_confirmation_and_pauses(
    client: TestClient,
) -> None:
    client.get("/ui/login?key=decide")
    install(client, "stdlib:env")
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        ready(ws)
        app = cast(FastAPI, client.app)
        identifier = app.state.conversation.identifier
        agent = app.state.conversation.config.agent
        prompt(ws, "Started")
        ws.send_json({"method": "clauz3/libraries", "params": {"domains": ["env"]}})
        asked = until(ws, lambda m: m["method"] == "clauz3/confirm_libraries")
        assert asked["params"]["added"] == ["env"]
        assert app.state.conversation.config.libraries == []
        marker = change(ws, ["env"], confirm=True)
        assert marker is not None and marker["params"] == {
            "added": ["env"],
            "removed": [],
            "updated": False,
            "paused": True,
            "turn": 1,
        }
        conversation = app.state.conversation
        assert conversation.identifier == identifier
        assert conversation.config.agent == agent
        assert conversation.config.approvals_paused
        assert conversation.state()["approvalsPaused"]
        # The agent hears about the change once, before the next prompt.
        prompt(ws, "Count files")
        reply = [
            m
            for m in conversation.history
            if m["method"] == "session/update"
            and "added env" in str(m["params"]["update"]["content"])
        ]
        assert len(reply) == 1
        assert conversation.library_changes == []
        assert "[clauz3]" not in str(
            [m for m in conversation.history if m["method"] == "clauz3/user"]
        )
        # Reopening keeps the pause.
        ws.send_json({"method": "clauz3/new"})
        until(ws, lambda m: m["method"] == "clauz3/restore")
        ready(ws)
        assert not app.state.conversation.config.approvals_paused
        ws.send_json({"method": "clauz3/select", "params": {"id": identifier}})
        until(ws, lambda m: m["method"] == "clauz3/restore")
        ready(ws)
        assert app.state.conversation.config.approvals_paused
        assert app.state.conversation.config.agent == agent


@pytest.mark.parametrize("client", ["codex"], indirect=True)
def test_provider_switch_and_library_changes_preserve_identity(
    client: TestClient,
) -> None:
    client.get("/ui/login?key=decide")
    install(client, "stdlib:env")
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        ready(ws)
        app = cast(FastAPI, client.app)
        original = app.state.conversation.identifier
        # Even an empty chat with the same libraries must switch providers.
        ws.send_json(
            {"method": "clauz3/new", "params": {"agent": "opencode", "domains": []}}
        )
        until(ws, lambda m: m["method"] == "clauz3/restore")
        ready(ws)
        identifier = app.state.conversation.identifier
        assert identifier != original
        assert app.state.conversation.config.agent == "opencode"
        prompt(ws, "Started")
        change(ws, ["env"], confirm=True)
        assert app.state.conversation.config.agent == "opencode"
        assert app.state.conversation.identifier == identifier
        assert app.state.conversation.config.approvals_paused
        prompt(ws, "Continue")
        assert "added env" in str(app.state.conversation.history)
        ws.send_json({"method": "clauz3/select", "params": {"id": original}})
        until(ws, lambda m: m["method"] == "clauz3/restore")
        ready(ws)
        assert app.state.conversation.config.agent == "codex"
        ws.send_json({"method": "clauz3/select", "params": {"id": identifier}})
        until(ws, lambda m: m["method"] == "clauz3/restore")
        ready(ws)
        assert app.state.conversation.config.agent == "opencode"
        assert app.state.conversation.config.libraries == ["env"]
        assert app.state.conversation.config.approvals_paused


def test_the_agent_hears_every_change_since_its_last_prompt(
    client: TestClient,
) -> None:
    client.get("/ui/login?key=decide")
    install(client, "stdlib:env")
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        ready(ws)
        prompt(ws, "Started")
        change(ws, ["env"], confirm=True)
        change(ws, [])
        conversation = cast(FastAPI, client.app).state.conversation
        assert conversation.library_changes == ["added env", "removed env"]
        prompt(ws, "Count files")
        assert "added env; removed env" in str(conversation.history)
        assert conversation.library_changes == []


def test_removing_libraries_mid_conversation_applies_without_pausing(
    client: TestClient,
) -> None:
    client.get("/ui/login?key=decide")
    install(client, "stdlib:find")
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        ready(ws)
        ws.send_json({"method": "clauz3/new", "params": {"domains": ["find"]}})
        until(ws, lambda m: m["method"] == "clauz3/restore")
        ready(ws)
        app = cast(FastAPI, client.app)
        # Dependencies come along, and the saved default is untouched.
        assert app.state.conversation.config.libraries == ["filesystem", "find"]
        assert client.get("/api/workspace/libraries").json()["selected"] == []
        prompt(ws, "Started")
        marker = change(ws, ["filesystem"])
        assert marker is not None and marker["params"]["removed"] == ["find"]
        assert not marker["params"]["paused"]
        assert app.state.conversation.config.libraries == ["filesystem"]
        assert not app.state.conversation.config.approvals_paused


def test_changed_libraries_can_continue_after_confirmation(client: TestClient) -> None:
    client.get("/ui/login?key=decide")
    install(client, "stdlib:env")
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        ready(ws)
        ws.send_json({"method": "clauz3/new", "params": {"domains": ["env"]}})
        until(ws, lambda m: m["method"] == "clauz3/restore")
        ready(ws)
        app = cast(FastAPI, client.app)
        old = app.state.conversation.identifier
        prompt(ws, "Keep this chat")
        ws.send_json({"method": "clauz3/new"})
        until(ws, lambda m: m["method"] == "clauz3/restore")
        ready(ws)
        file = (
            Path(app.state.conversation.config.cwd)
            / ".clauz3/tools/env/trusted/effects.py"
        )
        file.write_text(file.read_text() + "\n# changed version\n")
        ws.send_json({"method": "clauz3/select", "params": {"id": old}})
        restored = until(ws, lambda m: m["method"] == "clauz3/restore")
        assert restored["params"]["librariesChanged"]
        ws.send_json({"method": "clauz3/libraries", "params": {"domains": ["env"]}})
        asked = until(ws, lambda m: m["method"] == "clauz3/confirm_libraries")
        assert asked["params"]["updated"] and asked["params"]["added"] == []
        change(ws, ["env"], confirm=True)
        conversation = app.state.conversation
        assert conversation.identifier == old
        assert not conversation.config.startup_error
        assert conversation.config.approvals_paused
        assert "Keep this chat" in str(conversation.history)


def email_workspace(
    manager: WorkspaceLibraries, tmp_path: Path, **options: Any
) -> tuple[FastAPI, Callable[[TestClient, str, str], Any]]:
    """A workspace with the example email library and env installed.

    Returns the app and ``submit(api, recipient, body)``, which proves an email
    program against the email+env layer and submits it for approval.
    """
    from clauz3.approvals.authority import ApprovedLayer
    from clauz3.libraries.install import install_library
    from clauz3.libraries.layout import expand_import_roots
    from clauz3.prover import prove_text
    from clauz3.runner import build_approval_request

    project = manager.project
    shutil.copytree(
        ROOT / "examples/email/tools/email", project / ".clauz3/tools/email"
    )
    install_library("stdlib:env", into=project)
    config = ChatConfig(
        command=[sys.executable, str(ROOT / "tests/fixtures/workspace_agent.py")],
        cwd=str(project),
        bridge=[],
        runner_env={},
        status_file=str(tmp_path / "runs.json"),
        manage_libraries=True,
    )
    app = create_approval_app(
        auth_token="submit",
        decision_token="decide",
        chat_config=config,
        max_wait_seconds=0.01,
        **options,
    )
    resolved = project.resolve()
    layer = ApprovedLayer(
        (
            resolved / ".clauz3/tools/email/trusted",
            resolved / ".clauz3/tools/env/trusted",
        ),
        tuple(expand_import_roots([resolved])),
    )

    def submit(api: TestClient, recipient: str, body: str) -> Any:
        source = (
            "import clauz3\n"
            "from tools.email.trusted import contracts as emails\n"
            "from tools.email.trusted.effects import send_email\n\n"
            f'@clauz3.guarantee(emails.only(["{recipient}"]))\n'
            "@clauz3.guarantee(emails.at_most(1))\n"
            "def main() -> None:\n"
            f'    send_email("{recipient}", "{body}")\n'
        )
        payload = build_approval_request(
            source=source,
            source_name="<test>",
            target="main",
            proofs=prove_text(
                source,
                trusted_roots=layer.trusted_roots,
                import_paths=layer.import_roots,
            ),
            trusted_roots=layer.trusted_roots,
            import_roots=layer.import_roots,
        )
        response = api.post(
            "/requests", json=payload, headers={"Authorization": "Bearer submit"}
        )
        return response, payload

    return app, submit


def pause_by_adding_env(ws: WebSocketTestSession) -> None:
    """Reach the email+env layer by adding env to a started conversation."""
    ws.send_json({"method": "clauz3/new", "params": {"domains": ["email"]}})
    until(ws, lambda m: m["method"] == "clauz3/restore")
    ready(ws)
    prompt(ws, "Started")
    change(ws, ["email", "env"], confirm=True)


def test_paused_conversations_ignore_remembered_approvals_but_not_denials(
    manager: WorkspaceLibraries, tmp_path: Path
) -> None:
    from clauz3.approvals.profiles.store import ProfileStore

    app, submit = email_workspace(
        manager,
        tmp_path,
        profile=ProfileStore(tmp_path / "profile.sqlite", project=manager.project),
    )

    def decide(api: TestClient, payload: dict[str, Any], decision: str) -> None:
        response = api.post(
            f"/api/requests/{payload['request_id']}/decision",
            json={"decision": decision, "remember": True},
            headers=ORIGIN,
        )
        assert response.status_code == 200, response.text

    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as api:
        api.get("/ui/login?key=decide")
        with api.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
            ready(ws)
            ws.send_json(
                {"method": "clauz3/new", "params": {"domains": ["email", "env"]}}
            )
            until(ws, lambda m: m["method"] == "clauz3/restore")
            ready(ws)
            response, payload = submit(api, "bob@example.com", "first")
            assert response.status_code == 503
            decide(api, payload, "approved_once")
            response, _ = submit(api, "bob@example.com", "second")
            assert response.json()["decision"] == "auto_approved"

            pause_by_adding_env(ws)
            response, payload = submit(api, "bob@example.com", "third")
            assert response.status_code == 503
            record = api.get(f"/requests/{payload['request_id']}", headers=ORIGIN)
            assert record.json()["approvals_paused"] is True
            assert "automatic approvals are paused" in api.get("/ui/rows").text
            decide(api, payload, "rejected")
            response, _ = submit(api, "bob@example.com", "fourth")
            assert response.json()["decision"] == "auto_rejected"


def test_paused_conversations_ignore_policy_approvals_but_not_rejections(
    manager: WorkspaceLibraries, tmp_path: Path
) -> None:
    from clauz3.approvals.policy import approval_policy_from_dict

    policy = approval_policy_from_dict(
        {
            "version": 1,
            "imports": {"pol": "tools.email.trusted.contracts"},
            "rules": [
                {
                    "name": "block-ceo",
                    "decision": "auto_rejected",
                    "unless_proven": ["pol.recipient_at_most('ceo@example.com', 0)"],
                },
                {
                    "name": "allow-bob",
                    "decision": "auto_approved",
                    "when_proven": ["pol.only(['bob@example.com'])", "pol.at_most(1)"],
                },
            ],
        }
    )
    app, submit = email_workspace(manager, tmp_path, policy=policy)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as api:
        api.get("/ui/login?key=decide")
        with api.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
            ready(ws)
            ws.send_json(
                {"method": "clauz3/new", "params": {"domains": ["email", "env"]}}
            )
            until(ws, lambda m: m["method"] == "clauz3/restore")
            ready(ws)
            response, _ = submit(api, "bob@example.com", "first")
            assert response.json()["decision"] == "auto_approved"

            pause_by_adding_env(ws)
            response, payload = submit(api, "bob@example.com", "second")
            assert response.status_code == 503
            record = api.get(f"/requests/{payload['request_id']}", headers=ORIGIN)
            assert record.json()["approvals_paused"] is True
            response, _ = submit(api, "ceo@example.com", "third")
            assert response.json()["decision"] == "auto_rejected"


@pytest.mark.parametrize(
    ("source", "deleted", "dropped"),
    [
        ("stdlib:env", "env", "env isn't installed"),
        # find depends on filesystem, so it cannot stay without it.
        ("stdlib:find", "filesystem", "filesystem, find aren't installed"),
    ],
)
def test_uninstalled_defaults_are_dropped_with_a_notice(
    client: TestClient,
    manager: WorkspaceLibraries,
    source: str,
    deleted: str,
    dropped: str,
) -> None:
    client.get("/ui/login?key=decide")
    install(client, source)
    post(client, "select", {"domains": [source.removeprefix("stdlib:")]})
    # Removed outside the Libraries page, e.g. deleted by hand.
    shutil.rmtree(manager.project / ".clauz3/tools" / deleted)
    with client.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
        ready(ws)
        prompt(ws, "Started")
        ws.send_json({"method": "clauz3/new"})
        notice = until(ws, lambda m: m["method"] == "clauz3/notice")
        assert dropped in notice["params"]["text"]
        app = cast(FastAPI, client.app)
        assert app.state.conversation.config.libraries == []
        assert not app.state.conversation.config.startup_error
    assert client.get("/api/workspace/libraries").json()["selected"] == []


def test_uninstalled_defaults_do_not_stop_startup(
    manager: WorkspaceLibraries, tmp_path: Path
) -> None:
    from clauz3.libraries.install import install_library

    install_library("stdlib:find", into=manager.project)
    # The workspace keeps its default next to the conversation store.
    cwd = str(manager.project.resolve())
    storage = tmp_path / "workspaces" / hashlib.sha256(cwd.encode()).hexdigest()
    assert WorkspaceLibraries(manager.project, storage, []).select(["find"]) == [
        "filesystem",
        "find",
    ]
    shutil.rmtree(manager.project / ".clauz3/tools/filesystem")
    config = ChatConfig(
        command=[sys.executable, str(ROOT / "tests/fixtures/workspace_agent.py")],
        cwd=str(manager.project),
        bridge=[],
        runner_env={},
        status_file=str(tmp_path / "runs.json"),
        manage_libraries=True,
    )
    app = create_approval_app(
        auth_token="submit", decision_token="decide", chat_config=config
    )
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as api:
        assert app.state.conversation.config.libraries == []
        assert not app.state.conversation.config.startup_error
        api.get("/ui/login?key=decide")
        with api.websocket_connect("ws://127.0.0.1/api/chat/acp", headers=ORIGIN) as ws:
            notice = until(ws, lambda m: m["method"] == "clauz3/notice")
            assert "filesystem, find aren't installed" in notice["params"]["text"]
    assert WorkspaceLibraries(manager.project, storage, []).selected == []
