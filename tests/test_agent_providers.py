"""Provider boundaries, native protocol translation, and terminal/UI routing."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clauz3.approvals.service import create_approval_app
from clauz3.cli import main
from clauz3.sessions import providers
from clauz3.sessions.chat import ChatConfig, Conversation, session_parameters
from clauz3.sessions.native_adapter import NativeAdapter
from clauz3.sessions.records import load_session

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/workspace_agent.py"


def config(tmp_path: Path, agent: str = "codex") -> ChatConfig:
    return ChatConfig(
        agent=agent,
        unrestricted=True,
        select_provider=True,
        command=[sys.executable, str(FIXTURE), "--generic"],
        cwd=str(tmp_path),
        bridge=[sys.executable, "bridge"],
        runner_env={"CLAUZ3_APPROVAL_TOKEN": "submit"},
        status_file=str(tmp_path / "runs.json"),
    )


@pytest.mark.parametrize("agent", ["codex", "agy", "opencode"])
@pytest.mark.parametrize("frontend", ["start", "ui"])
def test_explicit_unrestricted_required(agent: str, frontend: str, capsys: Any) -> None:
    assert main([frontend, agent, "--no-browser"]) == 2
    assert "--unrestricted" in capsys.readouterr().err


def test_claude_browser_rejects_unrestricted_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "home"))
    assert main(["ui", "claude", "--unrestricted", "--no-browser"]) == 2
    assert (
        "Claude browser sessions do not support --unrestricted"
        in capsys.readouterr().err
    )
    assert not (tmp_path / "home").exists()
    providers.check_mode("claude", True)  # Native terminal mode remains available.
    with pytest.raises(ValueError, match="Claude browser sessions"):
        session_parameters(config(tmp_path, "claude"))
    item = next(p for p in providers.inventory(True) if p["id"] == "claude")
    assert item["enabled"] is False
    assert item["modeHint"] == "restart without --unrestricted"


def test_claude_unrestricted_conversation_cannot_launch(tmp_path: Path) -> None:
    async def check() -> None:
        conversation = Conversation(config(tmp_path, "claude"))
        await conversation.start()
        assert conversation.process is None
        assert "Claude browser sessions" in conversation.error

    asyncio.run(check())


@pytest.mark.parametrize("agent", ["codex", "agy", "opencode", "pi"])
def test_missing_optional_runtime(agent: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clauz3.sessions.providers.shutil.which", lambda _: None)
    with pytest.raises(ValueError, match="not on PATH"):
        providers.command(agent)
    assert all(not p["available"] for p in providers.inventory(True))


@pytest.mark.parametrize("agent", ["codex", "agy", "opencode", "pi"])
def test_no_claude_options_for_other_providers(tmp_path: Path, agent: str) -> None:
    params = session_parameters(config(tmp_path, agent), "/private/lifetime")
    assert "claudeCode" not in params.get("_meta", {})
    bridge = params["mcpServers"][0]
    assert bridge["name"] == "clauz3"
    assert {v["name"] for v in bridge["env"]} == {
        "CLAUZ3_APPROVAL_TOKEN",
        "CLAUZ3_RUN_STATUS_FILE",
        "CLAUZ3_SESSION_LIFETIME_SOCKET",
    }


def test_codex_permission_requires_correlated_bridge_call(tmp_path: Path) -> None:
    conversation = Conversation(config(tmp_path))
    conversation.session_id = "session"
    request = {
        "sessionId": "session",
        "_meta": {"is_mcp_tool_approval": True},
        "toolCall": {"toolCallId": "call", "title": "mcp.clauz3.run"},
        "options": [
            {"kind": "allow_always", "optionId": "always"},
            {"kind": "allow_once", "optionId": "once"},
        ],
    }
    assert conversation.bridge_permission(request) == {"outcome": "cancelled"}
    conversation.bridge_calls.add("call")
    assert conversation.bridge_permission(request) == {
        "outcome": "selected",
        "optionId": "once",
    }
    for key, value in [("sessionId", "other"), ("_meta", {}), ("options", [])]:
        assert conversation.bridge_permission({**request, key: value}) == {
            "outcome": "cancelled"
        }
    conversation.config.unrestricted = False
    assert conversation.bridge_permission(request) == {"outcome": "cancelled"}


@pytest.mark.parametrize("malformed", [None, [], "invalid", 7])
def test_malformed_permission_is_cancelled(tmp_path: Path, malformed: Any) -> None:
    conversation = Conversation(config(tmp_path))
    conversation.session_id = "session"
    conversation.bridge_calls.add("call")
    valid: dict[str, Any] = {
        "sessionId": "session",
        "_meta": {"is_mcp_tool_approval": True},
        "toolCall": {"toolCallId": "call"},
        "options": [{"kind": "allow_once", "optionId": "once"}],
    }
    denied = {"outcome": "cancelled"}
    assert conversation.bridge_permission(malformed) == denied
    for field in ("_meta", "toolCall", "options"):
        assert conversation.bridge_permission({**valid, field: malformed}) == denied
    assert (
        conversation.bridge_permission({**valid, "toolCall": {"toolCallId": malformed}})
        == denied
    )
    assert conversation.bridge_permission({**valid, "options": [malformed]}) == denied


def test_malformed_acp_messages_do_not_end_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    malformed_params: list[Any] = [None, [], {"update": None}, {"update": []}]
    messages = [
        {"id": 1, "method": "session/request_permission", "params": None},
        *({"method": "session/update", "params": value} for value in malformed_params),
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "rawInput": None,
                    "content": {"type": "text", "text": "Still working"},
                }
            },
        },
    ]

    async def check() -> None:
        conversation = Conversation(config(tmp_path))
        replies: list[dict[str, Any]] = []

        async def capture(value: dict[str, Any]) -> None:
            replies.append(value)

        monkeypatch.setattr(conversation, "write", capture)
        conversation.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            f"print({''.join(json.dumps(m) + chr(10) for m in messages)!r}, end='')",
            stdout=asyncio.subprocess.PIPE,
        )
        # The scripted adapter intentionally closes stdout after its messages.
        conversation.ended = True
        await conversation.read_agent()
        await conversation.process.wait()
        assert conversation.error == ""
        assert replies[0]["result"]["outcome"] == {"outcome": "cancelled"}
        assert not conversation.bridge_calls
        assert conversation.history[-1] == messages[-1]

    asyncio.run(check())


@pytest.mark.parametrize("has_output", [False, True])
def test_response_notice_does_not_depend_on_retained_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, has_output: bool
) -> None:
    monkeypatch.setattr("clauz3.sessions.chat.MAX_HISTORY", 1)

    class ScriptedConversation(Conversation):
        async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            if has_output:
                self.publish(
                    {
                        "method": "session/update",
                        "params": {
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {
                                    "type": "text",
                                    "text": "Reply exceeds history limit",
                                },
                            }
                        },
                    }
                )
            return {"stopReason": "end_turn"}

    async def check() -> None:
        conversation = ScriptedConversation(config(tmp_path))
        conversation.subscriber = asyncio.Queue()
        await conversation.prompt("hello")
        received = []
        while not conversation.subscriber.empty():
            received.append(conversation.subscriber.get_nowait())
        assert ("returned no response" in json.dumps(received)) is not has_output
        assert not conversation.history

    asyncio.run(check())


def test_inventory_cached_and_not_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clauz3.sessions.history import ConversationStore

    calls = 0

    def inventory(unrestricted: bool) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        return [{"id": "codex", "available": True}]

    monkeypatch.setattr(providers, "inventory", inventory)
    store = ConversationStore(tmp_path / "conversations.sqlite", str(tmp_path))
    conversation = Conversation(config(tmp_path), store)
    for _ in range(3):
        assert conversation.state()["providers"] == [{"id": "codex", "available": True}]
        conversation.changed()
    assert calls == 1
    assert "providers" not in store.load(conversation.identifier)["state"]


def test_ui_switches_provider_and_restores_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    monkeypatch.setattr(providers, "command", lambda _: cfg.command)
    app = create_approval_app(
        auth_token="submit", decision_token="decide", chat_config=cfg
    )
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)
    ) as client:
        client.get("/ui/login?key=decide")
        with client.websocket_connect(
            "ws://127.0.0.1/api/chat/acp", headers={"Origin": "http://127.0.0.1"}
        ) as ws:

            def ready(agent: str) -> dict[str, Any]:
                for _ in range(80):
                    message = ws.receive_json()
                    state = message.get("params", {})
                    if (
                        message["method"] == "clauz3/state"
                        and state.get("sessionId")
                        and state.get("agent") == agent
                    ):
                        return dict(state)
                raise AssertionError("Provider did not start")

            original = ready("codex")
            ws.send_json({"method": "clauz3/new", "params": {"agent": "claude"}})
            for _ in range(80):
                message = ws.receive_json()
                if message.get("method") == "clauz3/notice":
                    assert "Claude browser sessions" in message["params"]["text"]
                    break
            else:
                raise AssertionError("Unrestricted Claude switch was not rejected")
            assert app.state.conversation.identifier == original["conversationId"]
            assert not app.state.conversation.closed
            ws.send_json({"method": "clauz3/new", "params": {"agent": "opencode"}})
            switched = ready("opencode")
            assert switched["conversationId"] != original["conversationId"]
            ws.send_json(
                {
                    "method": "clauz3/select",
                    "params": {"id": original["conversationId"]},
                }
            )
            assert ready("codex")["conversationId"] == original["conversationId"]


@pytest.mark.parametrize("stdin_kind", ["pipe", "file", "null"])
def test_terminal_launch_and_resume(tmp_path: Path, stdin_kind: str) -> None:
    executable = tmp_path / "codex-acp"
    executable.write_text(
        f"#!{sys.executable}\nimport runpy,sys\n"
        "sys.argv.append('--generic')\n"
        f"runpy.run_path({str(FIXTURE)!r},run_name='__main__')\n"
    )
    executable.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "CLAUZ3_HOME": str(tmp_path / "home"),
    }
    args = [
        sys.executable,
        "-m",
        "clauz3",
        "start",
        "codex",
        "--unrestricted",
        "--no-browser",
    ]
    for _ in range(2):
        with ExitStack() as stack:
            input_options: dict[str, Any] = {"input": "hello\n"}
            if stdin_kind != "pipe":
                input_path = tmp_path / "input.txt"
                input_path.write_text("hello\n")
                if stdin_kind == "null":
                    input_path = Path(os.devnull)
                    args += ["--prompt", "hello"]
                input_options = {"stdin": stack.enter_context(input_path.open())}
            result = subprocess.run(
                args,
                cwd=ROOT / "examples/email",
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                **input_options,
            )
        assert result.returncode == 0, result.stderr
        assert "Hello from the fixture" in result.stdout
        records = list((tmp_path / "home/sessions").glob("*.json"))
        assert len(records) == 1
        record = json.loads(records[0].read_text())
        assert record["agent"] == "codex"
        args = [
            sys.executable,
            "-m",
            "clauz3",
            "resume",
            record["session_id"],
            "--no-browser",
        ]


@pytest.mark.parametrize("unrestricted", [False, True])
def test_unavailable_saved_provider_preserves_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unrestricted: bool
) -> None:
    from clauz3.sessions.history import ConversationStore

    store = ConversationStore(tmp_path / "conversations.sqlite", str(tmp_path))
    identifier = store.create()
    store.save(
        identifier,
        {
            "agent": "codex",
            "unrestricted": True,
            "requestedModel": "saved-model",
            "libraries": ["email"],
            "libraryFingerprint": "saved-fingerprint",
            "sessionId": "saved-session",
        },
    )
    message = {"method": "clauz3/user", "params": {"text": "hello", "turn": 1}}
    store.append(identifier, message)
    monkeypatch.setattr("clauz3.sessions.providers.shutil.which", lambda _: None)
    cfg = config(tmp_path, "claude").model_copy(update={"unrestricted": unrestricted})
    app = create_approval_app(
        auth_token="submit", decision_token="decide", chat_config=cfg
    )
    with TestClient(app):
        conversation = app.state.conversation
        assert conversation.config.startup_error
        assert conversation.process is None
        conversation.persist()
    saved = store.load(identifier)
    assert saved["history"] == [message]
    assert saved["state"]["agent"] == "codex"
    assert saved["state"]["unrestricted"] is True
    assert saved["state"]["requestedModel"] == "saved-model"
    assert saved["state"]["libraries"] == ["email"]
    assert saved["state"]["libraryFingerprint"] == "saved-fingerprint"
    assert saved["state"]["sessionId"] == "saved-session"


@pytest.mark.parametrize("agent", ["future", "claude", "pi", "codex"])
def test_unavailable_or_unrestricted_chat_stays_readable_and_recoverable(
    tmp_path: Path, agent: str
) -> None:
    from clauz3.sessions.history import ConversationStore

    store = ConversationStore(tmp_path / "conversations.sqlite", str(tmp_path))
    identifier = store.create()
    store.save(
        identifier,
        {"agent": agent, "unrestricted": agent != "future", "sessionId": "saved"},
    )
    message = {"method": "clauz3/user", "params": {"text": "Saved text", "turn": 1}}
    store.append(identifier, message)
    cfg = config(tmp_path, "claude").model_copy(update={"unrestricted": False})
    cfg.command.remove("--generic")
    app = create_approval_app(
        auth_token="submit", decision_token="decide", chat_config=cfg
    )
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)
    ) as client:
        client.get("/ui/login?key=decide")
        conversation = app.state.conversation
        assert client.portal
        client.portal.call(conversation.start)
        assert conversation.process is None
        if agent == "future":
            assert "unsupported agent: future" in conversation.error
            assert conversation.state()["agentLabel"] == "future"
        else:
            assert conversation.error == (
                "This conversation requires --unrestricted; restart "
                "with that flag or start a new conversation."
            )
        conversation.persist()
        assert store.load(identifier)["history"] == [message]
        with client.websocket_connect(
            "ws://127.0.0.1/api/chat/acp", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            restored = ws.receive_json()
            assert restored["method"] == "clauz3/restore"
            assert restored["params"]["history"] == [message]
            ws.send_json({"method": "clauz3/new", "params": {"agent": "claude"}})
            for _ in range(80):
                response = ws.receive_json()
                if response["method"] == "clauz3/state":
                    assert not response["params"].get("error"), response
                if (
                    response["method"] == "clauz3/state"
                    and response["params"].get("sessionId") == "fixture"
                ):
                    assert response["params"]["conversationId"] != identifier
                    assert response["params"]["unrestricted"] is False
                    break
            else:
                raise AssertionError("Could not recover with a new conversation")


@pytest.mark.parametrize("agent", ["claude", "pi"])
@pytest.mark.parametrize("saved_mode", [{"unrestricted": False}, {}])
def test_restricted_chat_keeps_its_mode_on_an_unrestricted_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent: str,
    saved_mode: dict[str, bool],
) -> None:
    """An unrestricted server refuses to resume a restricted chat (including a
    pre-provider record without the key) and never relabels it unrestricted."""
    from clauz3.sessions.history import ConversationStore

    # The fixture agent stands in for the pinned Pi runtime.
    monkeypatch.setattr(providers, "check_runtime", lambda *_: None)
    store = ConversationStore(tmp_path / "conversations.sqlite", str(tmp_path))
    identifier = store.create()
    store.save(identifier, {"agent": agent, "sessionId": "saved", **saved_mode})
    message = {"method": "clauz3/user", "params": {"text": "Saved text", "turn": 1}}
    store.append(identifier, message)

    unrestricted = create_approval_app(
        auth_token="submit",
        decision_token="decide",
        chat_config=config(tmp_path, agent),
    )
    with TestClient(unrestricted) as client:
        conversation = unrestricted.state.conversation
        assert conversation.identifier == identifier
        assert client.portal
        client.portal.call(conversation.start)
        assert conversation.process is None
        assert conversation.error == (
            "This conversation was started in restricted mode; restart "
            "without --unrestricted, or start a new conversation."
        )
        assert conversation.state()["unrestricted"] is False
        conversation.persist()
    saved = store.load(identifier)
    assert saved["state"]["unrestricted"] is False
    assert saved["history"] == [message]

    cfg = config(tmp_path, agent).model_copy(update={"unrestricted": False})
    if agent == "claude":
        cfg.command.remove("--generic")
    restricted = create_approval_app(
        auth_token="submit", decision_token="decide", chat_config=cfg
    )
    with TestClient(restricted) as client:
        conversation = restricted.state.conversation
        assert conversation.identifier == identifier
        assert not conversation.config.startup_error
        assert client.portal
        client.portal.call(conversation.start)
        assert not conversation.error
        assert conversation.session_id == "saved"
        assert conversation.state()["unrestricted"] is False
        client.portal.call(conversation.close)
    assert store.load(identifier)["state"]["unrestricted"] is False


def test_new_unrestricted_conversation_is_labelled_unrestricted(
    tmp_path: Path,
) -> None:
    async def check() -> None:
        conversation = Conversation(config(tmp_path))
        assert conversation.state()["unrestricted"] is True
        await conversation.start()
        assert conversation.session_id
        assert conversation.state()["unrestricted"] is True
        await conversation.close()

    asyncio.run(check())


def test_claude_switch_checks_adapter_off_loop_before_closing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrong Claude adapter refuses the switch and leaves the chat open."""
    import shutil

    from clauz3.sessions.history import ConversationStore

    problem = "UI requires claude-agent-acp 0.0.0-test"
    checked: list[str] = []

    def adapter_problem(executable: str) -> str:
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()  # Runs in a worker thread, not the loop.
        checked.append(executable)
        return problem

    which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: (
            "/fake/claude-agent-acp" if name == "claude-agent-acp" else which(name)
        ),
    )
    monkeypatch.setattr("clauz3.sessions.chat.adapter_problem", adapter_problem)
    monkeypatch.setattr(providers, "check_runtime", lambda *_: None)
    cfg = config(tmp_path, "pi").model_copy(update={"unrestricted": False})
    monkeypatch.setattr(providers, "command", lambda _: cfg.command)
    app = create_approval_app(
        auth_token="submit",
        decision_token="decide",
        chat_config=cfg,
    )
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)
    ) as client:
        client.get("/ui/login?key=decide")
        with client.websocket_connect(
            "ws://127.0.0.1/api/chat/acp", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            for _ in range(80):
                message = ws.receive_json()
                if message["method"] == "clauz3/state" and message["params"].get(
                    "sessionId"
                ):
                    break
            else:
                raise AssertionError("Pi fixture did not start")
            original = app.state.conversation
            assert original.config.agent == "pi"
            ws.send_json({"method": "clauz3/new", "params": {"agent": "claude"}})
            for _ in range(80):
                message = ws.receive_json()
                if message["method"] == "clauz3/notice":
                    assert message["params"]["text"] == problem
                    break
            else:
                raise AssertionError("Wrong adapter version was not reported")
            assert checked == ["/fake/claude-agent-acp"]
            assert app.state.conversation is original
            assert not original.closed and original.process
            # Selecting a saved Claude chat keeps its transcript readable.
            store = ConversationStore(tmp_path / "conversations.sqlite", str(tmp_path))
            saved_claude = store.create()
            store.save(saved_claude, {"agent": "claude", "sessionId": "saved"})
            transcript = {
                "method": "clauz3/user",
                "params": {"text": "Saved text", "turn": 1},
            }
            store.append(saved_claude, transcript)
            ws.send_json({"method": "clauz3/select", "params": {"id": saved_claude}})
            for _ in range(80):
                message = ws.receive_json()
                if message["method"] == "clauz3/restore":
                    break
            else:
                raise AssertionError("Saved Claude chat was not restored")
            assert message["params"]["conversationId"] == saved_claude
            assert message["params"]["history"] == [transcript]
            assert len(checked) == 2
            assert app.state.conversation.config.startup_error == problem


def test_provider_resume_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    from clauz3.sessions.records import SessionRecord, save_session

    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path))
    record = SessionRecord(str(uuid4()), str(tmp_path), "pi", None, True, None, [], {})
    save_session(record)
    assert load_session(record.session_id) == record


@pytest.mark.parametrize("agent", ["agy", "pi"])
def test_native_stream_translation(
    tmp_path: Path, agent: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[dict[str, Any]] = (
        [
            {
                "event": "step_update",
                "step_update": {"step_type": "agent_response", "text_delta": "hello"},
            },
            {"event": "result", "result": {"status": "SUCCESS", "response": "hello"}},
        ]
        if agent == "agy"
        else [
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "hello"},
            },
            {"type": "agent_end", "messages": []},
        ]
    )
    output: list[dict[str, Any]] = []
    monkeypatch.setattr("clauz3.sessions.native_adapter.emit", output.append)
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path))

    class ScriptedAdapter(NativeAdapter):
        async def send(self, value: dict[str, Any]) -> None:
            pass

        async def read(self) -> dict[str, Any]:
            return events.pop(0)

        def save(self, cwd: str | None = None) -> None:
            pass

    async def check() -> None:
        adapter = ScriptedAdapter(agent, "unused")
        adapter.session_id = "session"
        try:
            assert await adapter.prompt(
                {"sessionId": "session", "prompt": [{"type": "text", "text": "hi"}]}
            ) == {"stopReason": "end_turn"}
            assert len(output) == 1  # Final response must not duplicate deltas.
            assert output[0]["params"]["update"]["content"]["text"] == "hello"
            if agent == "agy":
                events.append(
                    {
                        "event": "result",
                        "result": {
                            "status": "SUCCESS",
                            "denied_actions": [{"action": "mcp"}],
                        },
                    }
                )
                with pytest.raises(ValueError, match="permissions.allow"):
                    await adapter.prompt({"sessionId": "session", "prompt": []})
        finally:
            await adapter.close()

    asyncio.run(check())
