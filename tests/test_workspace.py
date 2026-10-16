"""Workspace transport, credential separation, and real proof/approval workflow."""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from clauz3.approvals.authority import ApprovedLayer
from clauz3.approvals.client import APPROVAL_SERVICE_ENV, APPROVAL_TOKEN_ENV
from clauz3.approvals.profiles.store import ProfileStore
from clauz3.approvals.receipts import SIGNING_KEY_ENV, VERIFY_KEY_ENV, generate_keypair
from clauz3.approvals.service import create_approval_app
from clauz3.libraries.layout import expand_import_roots
from clauz3.sessions.chat import ChatConfig, Conversation, session_parameters

ROOT = Path(__file__).resolve().parents[1]
EMAIL = ROOT / "examples/email"


@pytest.fixture
def config(tmp_path: Path) -> ChatConfig:
    return ChatConfig(
        command=[sys.executable, str(ROOT / "tests/fixtures/workspace_agent.py")],
        cwd=str(EMAIL),
        bridge=[
            sys.executable,
            "-P",
            "-m",
            "clauz3.sessions.tools",
            str(EMAIL),
            str(EMAIL / "tools/email/trusted"),
        ],
        runner_env={},
        status_file=str(tmp_path / "runs.json"),
        libraries=["email"],
    )


@pytest.fixture
def client(config: ChatConfig, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAUZ3_APPROVAL_SIGNING_KEY", "must-not-reach-agent")
    monkeypatch.setenv("CLAUZ3_APPROVAL_DECISION_TOKEN", "must-not-reach-agent")
    app = create_approval_app(
        auth_token="submit", decision_token="decide", chat_config=config
    )
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)
    ) as client:
        yield client


def until(ws: WebSocketTestSession, predicate: Any) -> dict[str, Any]:
    for _ in range(100):
        message: dict[str, Any] = ws.receive_json()
        if predicate(message):
            return message
    raise AssertionError("Expected workspace message not received")


def ready(ws: WebSocketTestSession) -> None:
    until(ws, lambda m: m["method"] == "clauz3/state" and m["params"]["sessionId"])


def login(client: TestClient) -> None:
    assert client.get("/ui/login?key=decide").status_code == 200


def test_authentication_and_origin(client: TestClient) -> None:
    assert client.get("/ui").status_code == 401
    assert client.get("/api/chat/transcript").status_code == 401
    for headers in (
        {"Origin": "http://127.0.0.1"},
        {"Authorization": "Bearer submit", "Origin": "http://127.0.0.1"},
    ):
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                str(client.base_url).replace("http", "ws") + "/api/chat/acp",
                headers=headers,
            ) as rejected,
        ):
            rejected.receive_json()
    login(client)
    for origin in (None, "http://evil.example", "http://127.0.0.1:99"):
        headers = {"Origin": origin} if origin else {}
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                str(client.base_url).replace("http", "ws") + "/api/chat/acp",
                headers=headers,
            ) as rejected,
        ):
            rejected.receive_json()
    assert "Message agent" in client.get("/ui").text
    assert client.get("/ui/assets/workspace.js").status_code == 200
    assert client.get("/ui/assets/markdown.js").status_code == 200
    assert client.get("/ui/assets/chat.py").status_code == 404


@pytest.mark.parametrize("name", ["logo.svg", "favicon.svg"])
def test_brand_assets_match_docs(client: TestClient, name: str) -> None:
    """The UI serves the same logo files as the docs site."""
    response = client.get(f"/ui/assets/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    docs = Path(__file__).parents[1] / "docs" / "assets" / name
    assert response.text == docs.read_text()
    login(client)
    assert f"/ui/assets/{name}" in client.get("/ui").text


def test_stream_reconnect_draft_and_followup(client: TestClient) -> None:
    login(client)
    headers = {"Origin": "http://127.0.0.1"}
    with client.websocket_connect(
        str(client.base_url).replace("http", "ws") + "/api/chat/acp", headers=headers
    ) as ws:
        ready(ws)
        process = cast(FastAPI, client.app).state.conversation.process
        ws.send_json({"method": "session/prompt", "params": {"prompt": "hello"}})
        until(ws, lambda m: m["method"] == "clauz3/turn_end")
    with client.websocket_connect(
        str(client.base_url).replace("http", "ws") + "/api/chat/acp", headers=headers
    ) as ws:
        snapshot = ws.receive_json()
        assert snapshot["params"]["sessionId"] == "fixture"
        assert any(
            m["method"] == "session/update" for m in snapshot["params"]["history"]
        )
        assert cast(FastAPI, client.app).state.conversation.process is process
        ws.send_json({"method": "session/prompt", "params": {"prompt": "follow up"}})
        until(ws, lambda m: m["method"] == "clauz3/turn_end")
        ws.send_json(
            {"method": "session/new", "params": {"cwd": "/", "mcpServers": []}}
        )
        assert (
            "Unsupported"
            in until(ws, lambda m: m["method"] == "clauz3/notice")["params"]["text"]
        )
    transcript = client.get("/api/chat/transcript").json()
    assert transcript["turn"] == 2
    assert "must-not-reach-agent" not in json.dumps(transcript)


def test_stop_new_and_unexpected_permissions(client: TestClient) -> None:
    login(client)
    with client.websocket_connect(
        str(client.base_url).replace("http", "ws") + "/api/chat/acp",
        headers={"Origin": "http://127.0.0.1"},
    ) as ws:
        ready(ws)
        ws.send_json({"method": "session/prompt", "params": {"prompt": "/mcp"}})
        assert (
            "slash"
            in until(ws, lambda m: m["method"] == "clauz3/notice")["params"]["text"]
        )
        ws.send_json({"method": "session/prompt", "params": {"prompt": "permission"}})
        until(
            ws,
            lambda m: (
                m["method"] == "session/update"
                and m["params"]["update"].get("content", {}).get("text")
                == "Permission refused"
            ),
        )
        process = cast(FastAPI, client.app).state.conversation.process
        ws.send_json({"method": "session/cancel"})
        until(ws, lambda m: m["method"] == "clauz3/state" and m["params"]["ended"])
        assert process.returncode is not None
        ws.send_json({"method": "clauz3/new"})
        until(ws, lambda m: m["method"] == "clauz3/restore")
        ready(ws)
        assert cast(FastAPI, client.app).state.conversation.process.pid != process.pid


def test_agent_crash_ends_session(client: TestClient) -> None:
    login(client)
    with client.websocket_connect(
        str(client.base_url).replace("http", "ws") + "/api/chat/acp",
        headers={"Origin": "http://127.0.0.1"},
    ) as ws:
        ready(ws)
        ws.send_json({"method": "session/prompt", "params": {"prompt": "crash"}})
        result = until(
            ws, lambda m: m["method"] == "clauz3/state" and m["params"]["ended"]
        )
        assert "disconnected" in result["params"]["error"]


def test_restricted_sdk_options(config: ChatConfig) -> None:
    parameters = session_parameters(config)
    options = parameters["_meta"]["claudeCode"]["options"]
    assert options["tools"] == []
    assert options["settingSources"] == []
    assert options["strictMcpConfig"] is True
    assert options["settings"]["disableAllHooks"] is True
    assert options["extraArgs"] == {"disable-slash-commands": None, "no-chrome": None}
    instructions = options["systemPrompt"]["append"]
    assert "slash commands are disabled" in instructions
    assert "restart clauz3 ui" in instructions
    assert "/mcp" not in instructions
    assert "/status" not in instructions


@pytest.mark.parametrize(
    "decision,mode",
    [
        ("approved_once", "normal"),
        ("rejected", "normal"),
        ("stop", "normal"),
        ("approved_once", "libraries"),
        ("approved_once", "history_limit"),
        ("approved_once", "codex"),
        ("rejected", "codex"),
        ("approved_once", "pi"),
        ("rejected", "pi"),
        ("stop", "pi"),
    ],
)
def test_real_mcp_proof_inline_approval_and_receipt(
    config: ChatConfig,
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
    mode: str,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    shutil.copytree(EMAIL / "tools", project / "tools")
    marker = project / "effect-completed"
    effect = project / "tools/email/trusted/effects.py"
    effect.write_text(
        effect.read_text().replace(
            "    pass",
            "    import time\n    from pathlib import Path\n"
            "    time.sleep(2)\n    Path('effect-completed').touch()",
        )
    )
    worker_pid = tmp_path / "worker.pid"
    monkeypatch.setenv("WORKSPACE_TEST_WORKER_PID", str(worker_pid))
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "global"))
    config.cwd = str(project)
    config.bridge = [
        sys.executable,
        str(ROOT / "tests/fixtures/workspace_bridge.py"),
        str(project),
        str(project / "tools/email/trusted"),
    ]
    if mode == "codex":
        config.agent = "codex"
        config.unrestricted = True
        config.command.append("--generic")
    if mode == "pi":
        from clauz3.sessions import providers

        executable = shutil.which("pi")
        if executable is None:
            pytest.skip("Optional pinned Pi runtime is not installed")
        try:
            providers.pi_sdk(executable)
        except ValueError:
            pytest.skip("Optional pinned Pi runtime is not installed")
        # Only replace the ACP transport/model, not the SDK, bridge or approvals.
        # pi_proof.mjs invokes the real restricted SDK with a scripted model.
        monkeypatch.setattr(providers, "check_runtime", lambda *args: None)
        config.agent = "pi"
        config.command.extend(["--generic", "--pi-sdk", executable])
    config.manage_libraries = mode == "libraries"
    config.libraries = [] if config.manage_libraries else ["email"]
    signing, verifying = generate_keypair()
    monkeypatch.setenv(SIGNING_KEY_ENV, signing)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        config.runner_env = {
            APPROVAL_SERVICE_ENV: url,
            APPROVAL_TOKEN_ENV: "submit",
            VERIFY_KEY_ENV: verifying,
        }
        app = create_approval_app(
            auth_token="submit",
            decision_token="decide",
            chat_config=config,
            profile=ProfileStore(tmp_path / "profile.sqlite", project=project),
            authorized_layers=(
                ApprovedLayer(
                    trusted_roots=(project / "tools/email/trusted",),
                    # As sessions/service.py authorizes the worker's roots.
                    import_roots=tuple(expand_import_roots([project])),
                ),
            ),
        )
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        thread = threading.Thread(
            target=lambda: server.run(sockets=[listener]), daemon=True
        )
        thread.start()
        try:
            with TestClient(app, base_url=url, client=("127.0.0.1", 12345)) as client:
                login(client)
                with client.websocket_connect(
                    str(client.base_url).replace("http", "ws") + "/api/chat/acp",
                    headers={"Origin": url},
                ) as ws:
                    ready(ws)
                    if mode == "libraries":
                        response = client.post(
                            "/api/workspace/libraries/select",
                            json={"domains": ["email"]},
                            headers={"Origin": url},
                        )
                        assert response.status_code == 200
                        ws.send_json({"method": "clauz3/new"})
                        until(ws, lambda m: m["method"] == "clauz3/restore")
                        ready(ws)
                    ws.send_json(
                        {
                            "method": "session/prompt",
                            "params": {
                                "prompt": "run:" + str(EMAIL / "cases/only_bob_pass.py")
                            },
                        }
                    )
                    result = until(
                        ws,
                        lambda m: (
                            m["method"] == "clauz3/state"
                            and any(
                                r.get("phase") == "awaiting_approval"
                                for r in m["params"]["runs"]
                            )
                        ),
                    )
                    run = result["params"]["runs"][0]
                    assert run["turn"] == 1
                    request_id = run["request_id"]
                    original_request = client.get(f"/requests/{request_id}").json()
                    assert original_request["trusted_roots"] == [
                        str(project / "tools/email/trusted")
                    ]
                    assert request_id in client.get("/ui/rows").text
                    if decision == "stop":
                        ws.send_json({"method": "session/cancel"})
                        stopped = until(
                            ws,
                            lambda m: (
                                m["method"] == "clauz3/state" and m["params"]["ended"]
                            ),
                        )
                        assert stopped["params"]["runs"][0]["phase"] == "cancelled"
                    assert (
                        client.post(
                            f"/api/requests/{request_id}/decision",
                            json={
                                "decision": "approved_once"
                                if decision == "stop"
                                else decision
                            },
                        ).status_code
                        == 200
                    )
                    if decision == "stop":
                        pid = int(worker_pid.read_text())
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            status = subprocess.run(
                                ["ps", "-p", str(pid), "-o", "stat="],
                                capture_output=True,
                                text=True,
                            ).stdout.strip()
                            if not status or status.startswith("Z"):
                                break
                            time.sleep(0.05)
                        else:
                            pytest.fail("Stopped workspace left its worker running")
                        assert not marker.exists()
                    if mode == "history_limit":
                        until(
                            ws,
                            lambda m: (
                                m["method"] == "clauz3/state"
                                and any(
                                    r.get("phase") == "executing"
                                    for r in m["params"]["runs"]
                                )
                            ),
                        )
                        monkeypatch.setattr("clauz3.sessions.chat.MAX_HISTORY", 1)
                        # Publish on the service event loop while the real worker
                        # is inside the delayed, approved effect.
                        assert client.portal
                        client.portal.call(
                            app.state.conversation.publish,
                            {
                                "method": "clauz3/notice",
                                "params": {"text": "large output"},
                            },
                        )
                        assert not app.state.conversation.closed
                    if decision != "stop":
                        until(ws, lambda m: m["method"] == "clauz3/turn_end")
                    record = json.loads(Path(config.status_file).read_text())[0]
                    transcript = client.get("/api/chat/transcript").text
                    if decision == "approved_once":
                        assert record["phase"] == "completed"
                        assert record["exit_code"] == 0
                        if mode != "history_limit":
                            assert "receipt=clz2." in transcript
                        else:
                            assert client.get("/api/chat/transcript").json()[
                                "historyLimited"
                            ]
                            assert app.state.conversation.process.returncode is None
                            ws.send_json({"method": "session/cancel"})
                            until(
                                ws,
                                lambda m: (
                                    m["method"] == "clauz3/state"
                                    and m["params"]["ended"]
                                ),
                            )
                        assert marker.exists()
                        if mode == "libraries":
                            assert (
                                client.post(
                                    "/api/workspace/libraries/select",
                                    json={"domains": []},
                                    headers={"Origin": url},
                                ).status_code
                                == 200
                            )
                            ws.send_json({"method": "clauz3/new"})
                            until(ws, lambda m: m["method"] == "clauz3/restore")
                            ready(ws)
                            refused = client.post(
                                "/requests",
                                json=original_request,
                                headers={"Authorization": "Bearer submit"},
                            )
                            assert refused.status_code == 403
                            assert "authorized layer" in refused.text
                    else:
                        assert record["phase"] != "completed"
                        assert "receipt=clz2." not in transcript
                        if decision == "rejected":
                            assert record["phase"] == "refused"
                            assert record["exit_code"] == 3
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def test_close_reaps_process(config: ChatConfig) -> None:
    async def check() -> None:
        conversation = Conversation(config)
        await conversation.start()
        assert conversation.session_id == "fixture"
        conversation.submit("wait")
        await conversation.close()
        assert conversation.process and conversation.process.returncode is not None
        assert all(task.done() for task in conversation.tasks)

    asyncio.run(check())


def test_service_lifetime_pipe_stops_agent(config: ChatConfig) -> None:
    async def check() -> None:
        conversation = Conversation(config)
        await conversation.start()
        assert conversation.lifetime is not None
        assert conversation.process is not None
        # Simulate the service disappearing: the guardian sees EOF even without
        # an orderly session close. Its ACP process group must not survive.
        import os

        os.close(conversation.lifetime)
        conversation.lifetime = None
        async with asyncio.timeout(5):
            await conversation.process.wait()
        await conversation.close()
        assert conversation.process.returncode != 0

    asyncio.run(check())


def test_history_switch_resume_and_server_restart(config: ChatConfig) -> None:
    def application() -> FastAPI:
        return create_approval_app(
            auth_token="submit", decision_token="decide", chat_config=config
        )

    def browser(app: FastAPI) -> TestClient:
        return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1))

    with browser(application()) as client:
        assert client.get("/api/chat/conversations").status_code == 401
        login(client)
        assert (
            client.get(
                "/api/chat/conversations", headers={"Origin": "http://evil.example"}
            ).status_code
            == 401
        )
        with client.websocket_connect(
            "ws://127.0.0.1/api/chat/acp", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            ready(ws)
            first = cast(FastAPI, client.app).state.conversation.identifier
            ws.send_json(
                {"method": "session/prompt", "params": {"prompt": "First task"}}
            )
            until(ws, lambda m: m["method"] == "clauz3/turn_end")
            ws.send_json({"method": "clauz3/new"})
            until(ws, lambda m: m["method"] == "clauz3/restore")
            ready(ws)
            second = cast(FastAPI, client.app).state.conversation.identifier
            assert first != second
            ws.send_json(
                {"method": "session/prompt", "params": {"prompt": "Second task"}}
            )
            until(ws, lambda m: m["method"] == "clauz3/turn_end")
            ws.send_json(
                {
                    "method": "clauz3/select",
                    "params": {"id": first, "cwd": "/", "mcpServers": []},
                }
            )
            restored = until(ws, lambda m: m["method"] == "clauz3/restore")
            assert "First task" in json.dumps(restored)
            assert "Second task" not in json.dumps(restored)
            ready(ws)
            ws.send_json(
                {"method": "session/prompt", "params": {"prompt": "Follow-up"}}
            )
            until(ws, lambda m: m["method"] == "clauz3/turn_end")
            ws.send_json(
                {
                    "method": "clauz3/rename",
                    "params": {"id": first, "title": "My first task"},
                }
            )
            until(ws, lambda m: m["method"] == "clauz3/conversations")
            transcript = client.get("/api/chat/transcript").json()
            assert "REPLAY_MUST_NOT_DUPLICATE" not in json.dumps(transcript)
            assert transcript["turn"] == 2
            assert (
                len([m for m in transcript["history"] if m["method"] == "clauz3/user"])
                == 2
            )
    with browser(application()) as client:
        login(client)
        entries = client.get("/api/chat/conversations").json()["conversations"]
        assert {c["title"] for c in entries} == {"My first task", "Second task"}
        with client.websocket_connect(
            "ws://127.0.0.1/api/chat/acp", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            snapshot = until(ws, lambda m: m["method"] == "clauz3/restore")
            assert snapshot["params"]["conversationId"] == first
            assert "Follow-up" in json.dumps(snapshot)
            ready(ws)
            ws.send_json(
                {"method": "session/prompt", "params": {"prompt": "After restart"}}
            )
            until(ws, lambda m: m["method"] == "clauz3/turn_end")
            assert client.get("/api/chat/transcript").json()["turn"] == 3


def test_history_store_isolates_projects_and_keeps_events(tmp_path: Path) -> None:
    from clauz3.sessions.history import ConversationStore

    file = tmp_path / "history.sqlite"
    one = ConversationStore(file, str(tmp_path / "one"))
    two = ConversationStore(file, str(tmp_path / "two"))
    identifier = one.create()
    message = {"method": "clauz3/user", "params": {"text": "Hello", "turn": 1}}
    one.append(identifier, message)
    one.save(identifier, {"turn": 1, "sessionId": "saved-agent-session"})
    assert two.list() == []
    with pytest.raises(ValueError, match="not found"):
        two.load(identifier)
    with pytest.raises(ValueError, match="not found"):
        two.rename(identifier, "wrong project")
    restored = ConversationStore(file, str(tmp_path / "one")).load(identifier)
    assert restored["history"] == [message]
    assert restored["state"]["sessionId"] == "saved-agent-session"
    assert file.stat().st_mode & 0o077 == 0


def test_conversation_store_lease_releases_after_exit(tmp_path: Path) -> None:
    from clauz3.sessions.history import ConversationStore

    first = ConversationStore(tmp_path / "history.sqlite", str(tmp_path))
    second = ConversationStore(first.path, str(tmp_path))
    with first.lease(), pytest.raises(ValueError, match="already has"), second.lease():
        raise AssertionError("Concurrent lease was granted")
    with second.lease():
        assert second.list() == []


def test_queue_overflow_uses_a_history_snapshot(config: ChatConfig) -> None:
    conversation = Conversation(config)
    conversation.subscriber = asyncio.Queue(maxsize=2)
    for index in range(3):
        conversation.publish({"method": "test", "params": {"index": index}})
    restore = conversation.subscriber.get_nowait()
    conversation.publish({"method": "test", "params": {"index": 3}})
    assert [m["params"]["index"] for m in restore["params"]["history"]] == [0, 1, 2]
    assert conversation.subscriber.get_nowait()["params"]["index"] == 3


def test_prompt_over_limit_does_not_advance_or_wedge(
    config: ChatConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = Conversation(config)
    conversation.session_id = "fixture"
    monkeypatch.setattr("clauz3.sessions.chat.MAX_HISTORY", 1)
    with pytest.raises(ValueError, match="storage limit"):
        conversation.submit("hello")
    assert conversation.turn == 0
    assert not conversation.busy
    assert conversation.history_limited
    assert not conversation.tasks
    assert not conversation.closed


@pytest.mark.parametrize(
    "response",
    [
        {"result": []},
        {"error": "boom"},
        {"result": {"protocolVersion": 1, "agentCapabilities": []}},
        {"result": {"sessionId": []}},
        {"error": {"message": "Adapter authentication failed"}},
    ],
)
def test_malformed_adapter_startup_is_visible_and_reaped(
    config: ChatConfig,
    response: dict[str, Any],
) -> None:
    script = "import json, sys, time\nfirst=json.loads(sys.stdin.readline())\n"
    if "sessionId" in response.get("result", {}):
        script += (
            "print(json.dumps({'id':first['id'],'result':{'protocolVersion':1}}),flush=True)\n"
            "first=json.loads(sys.stdin.readline())\n"
        )
    script += (
        f"print(json.dumps({response!r} | {{'id':first['id']}}),flush=True)\n"
        "time.sleep(60)\n"
    )
    config.command = [sys.executable, "-c", script]

    async def check() -> None:
        conversation = Conversation(config)
        await asyncio.wait_for(conversation.start(), 5)
        assert conversation.closed
        assert conversation.process and conversation.process.returncode is not None
        assert (
            "Invalid ACP" in conversation.error
            or "authentication failed" in conversation.error
        )
        assert all(task.done() for task in conversation.tasks)

    asyncio.run(check())


def test_detached_bridge_exits_on_workspace_lifetime_socket(config: ChatConfig) -> None:
    async def check() -> None:
        conversation = Conversation(config)
        await conversation.start()
        assert conversation.lifetime_directory and conversation.process
        bridge = await asyncio.create_subprocess_exec(
            *config.bridge,
            env={
                **os.environ,
                "CLAUZ3_SESSION_LIFETIME_SOCKET": str(
                    Path(conversation.lifetime_directory.name) / "bridge"
                ),
            },
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(5):
                while not conversation.bridges:
                    await asyncio.sleep(0.01)
            assert os.getpgid(bridge.pid) != os.getpgid(conversation.process.pid)
            await conversation.close()
            assert await asyncio.wait_for(bridge.wait(), 5) == 143
        finally:
            if bridge.returncode is None:
                bridge.kill()
                await bridge.wait()
            await conversation.close()

    asyncio.run(check())


def test_second_tab_has_retryable_close_code(client: TestClient) -> None:
    login(client)
    url = "ws://127.0.0.1/api/chat/acp"
    headers = {"Origin": "http://127.0.0.1"}
    with client.websocket_connect(url, headers=headers) as first:
        ready(first)
        with client.websocket_connect(url, headers=headers) as second:
            with pytest.raises(WebSocketDisconnect) as rejected:
                second.receive_json()
            assert rejected.value.code == 1013
    with client.websocket_connect(url, headers=headers) as reloaded:
        assert reloaded.receive_json()["params"]["sessionId"] == "fixture"


def test_new_reuses_an_unused_conversation(client: TestClient) -> None:
    login(client)
    with client.websocket_connect(
        "ws://127.0.0.1/api/chat/acp", headers={"Origin": "http://127.0.0.1"}
    ) as ws:
        ready(ws)
        initial = client.get("/api/chat/conversations").json()["conversations"]
        for _ in range(3):
            ws.send_json({"method": "clauz3/new"})
        # A later prompt is an ordered barrier for the preceding New messages.
        ws.send_json({"method": "session/prompt", "params": {"prompt": "hello"}})
        until(ws, lambda m: m["method"] == "clauz3/turn_end")
        current = client.get("/api/chat/conversations").json()["conversations"]
        assert [row["id"] for row in current] == [row["id"] for row in initial]


def test_missing_adapter_session_preserves_history_and_allows_new(
    config: ChatConfig,
) -> None:
    config = config.model_copy(
        update={"command": [*config.command, "--missing-session"]}
    )

    def browser() -> TestClient:
        return TestClient(
            create_approval_app(
                auth_token="submit", decision_token="decide", chat_config=config
            ),
            base_url="http://127.0.0.1",
            client=("127.0.0.1", 1),
        )

    with browser() as client:
        login(client)
        with client.websocket_connect(
            "ws://127.0.0.1/api/chat/acp", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            ready(ws)
            ws.send_json(
                {"method": "session/prompt", "params": {"prompt": "Keep this task"}}
            )
            until(ws, lambda m: m["method"] == "clauz3/turn_end")
            original = client.get("/api/chat/transcript").json()

    with browser() as client:
        login(client)
        with client.websocket_connect(
            "ws://127.0.0.1/api/chat/acp", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            until(ws, lambda m: m["method"] == "clauz3/state" and m["params"]["ended"])
            failed = client.get("/api/chat/transcript").json()
            assert failed["conversationId"] == original["conversationId"]
            assert failed["history"] == original["history"]
            assert failed["sessionId"] is None
            assert "Your transcript is saved" in failed["error"]
            assert "Resource not found" not in failed["error"]
            assert failed["resumeError"] == "Resource not found: fixture"
            assert (
                cast(FastAPI, client.app).state.conversation.process.returncode
                is not None
            )
            ws.send_json({"method": "clauz3/new"})
            until(ws, lambda m: m["method"] == "clauz3/restore")
            ready(ws)
            fresh = client.get("/api/chat/transcript").json()
            assert fresh["conversationId"] != original["conversationId"]
            assert fresh["history"] == []
            assert fresh["error"] == fresh["resumeError"] == ""
            ws.send_json({"method": "session/prompt", "params": {"prompt": "New task"}})
            until(ws, lambda m: m["method"] == "clauz3/turn_end")
            ws.send_json(
                {
                    "method": "clauz3/select",
                    "params": {"id": original["conversationId"]},
                }
            )
            restored = until(ws, lambda m: m["method"] == "clauz3/restore")
            assert restored["params"]["history"] == original["history"]
            until(ws, lambda m: m["method"] == "clauz3/state" and m["params"]["ended"])
