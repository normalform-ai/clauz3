"""Single-conversation ACP host for the local, operator-owned workspace.

Transport/lifecycle follows AmiGO's WebSocket/stdio architecture. The browser
never supplies process configuration or agent permissions. Approval decisions
still go through the existing authority and signed-receipt API.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack, asynccontextmanager, suppress
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from clauz3.libraries.install import InstallError
from clauz3.libraries.workspace import WorkspaceLibraries
from clauz3.sessions import providers
from clauz3.sessions.files import browse
from clauz3.sessions.history import ConversationStore
from clauz3.sessions.runs import STATUS_FILE_ENV
from clauz3.sessions.workspaces import Workspaces
from clauz3.source import SourceError

ADAPTER_VERSION = "0.89.1"
MAX_MESSAGE = 2_000_000
MAX_HISTORY = 8_000_000
HISTORY_NOTICE = (
    "Transcript storage limit reached. This action can finish, but later output "
    "will not be saved. Start a new conversation before sending another prompt."
)
SESSION_TOOLS = tuple(
    f"mcp__clauz3__{name}"
    for name in ("run", "status", "tools", "libraries", "list_skills", "read_skill")
)
TERMINAL_PHASES = {"completed", "failed", "refused", "cancelled"}
LIBRARIES_CHANGED = (
    "This conversation's libraries changed since it last ran. Its history is "
    "saved; continue with the current libraries or start a new conversation."
)


class ChatConfig(BaseModel):
    tutorial: bool = False
    agent: str = "claude"
    unrestricted: bool = False
    select_provider: bool = False
    command: list[str]
    cwd: str
    bridge: list[str]
    runner_env: dict[str, str]
    status_file: str
    history_file: str | None = None
    library_fingerprint: str = ""
    startup_error: str = ""
    # Automatic approvals stay paused once a started conversation gains code.
    approvals_paused: bool = False
    libraries_changed: bool = False
    # Defaults dropped because they are no longer installed; reported once.
    dropped_libraries: list[str] = []
    manage_libraries: bool = False
    show_libraries: bool = False
    libraries: list[str] = []
    model: str | None = None
    prompt: str | None = None


def adapter_problem(executable: str) -> str:
    """Why this adapter cannot be trusted, or an empty string if it can.

    Unknown releases must be reviewed before trusting their SDK forwarding.
    """
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            env=agent_environment(),
        )
    except subprocess.TimeoutExpired:
        return "ACP adapter version check timed out"
    if result.returncode or result.stdout.strip() != ADAPTER_VERSION:
        return (
            f"UI requires claude-agent-acp {ADAPTER_VERSION}; install with "
            f"npm install -g @agentclientprotocol/claude-agent-acp@{ADAPTER_VERSION}"
        )
    return ""


def check_adapter(executable: str) -> None:
    if problem := adapter_problem(executable):
        raise ValueError(problem)


def agent_environment() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CLAUZ3_APPROVAL")
        and key
        not in {"CLAUZ3_UI_CONFIG", STATUS_FILE_ENV, "CLAUZ3_SESSION_LIFETIME_SOCKET"}
    }
    env["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"
    return env


def session_parameters(
    config: ChatConfig, lifetime_socket: str | None = None
) -> dict[str, Any]:
    from clauz3.sessions.launcher import SESSION_INSTRUCTIONS

    providers.check_mode(config.agent, config.unrestricted, browser_ui=True)
    options: dict[str, Any] = {
        "tools": [],
        "allowedTools": list(SESSION_TOOLS),
        "settingSources": [],
        "strictMcpConfig": True,
        "allowDangerouslySkipPermissions": False,
        "plugins": [],
        "settings": {
            "disableAllHooks": True,
            "disableClaudeAiConnectors": True,
            "enabledPlugins": {},
        },
        "extraArgs": {"disable-slash-commands": None, "no-chrome": None},
        "systemPrompt": {
            "type": "preset",
            "preset": "claude_code",
            "append": SESSION_INSTRUCTIONS
            + "Agent slash commands are disabled in this browser workspace. "
            "If the bridge is unavailable, ask the user to exit and restart "
            "clauz3 ui. All effects go through clauz3 run.",
        },
    }
    if config.model:
        options["model"] = config.model
    parameters: dict[str, Any] = {
        "cwd": config.cwd,
        "mcpServers": [
            {
                "name": "clauz3",
                "command": config.bridge[0],
                "args": config.bridge[1:],
                "env": [
                    {"name": k, "value": v}
                    for k, v in {
                        **config.runner_env,
                        STATUS_FILE_ENV: config.status_file,
                        **(
                            {"CLAUZ3_SESSION_LIFETIME_SOCKET": lifetime_socket}
                            if lifetime_socket
                            else {}
                        ),
                    }.items()
                ],
            }
        ],
    }
    if config.agent == "claude":
        # The existing Claude browser contract remains restricted.
        parameters["_meta"] = {"claudeCode": {"options": options}}
    elif config.agent in {"agy", "pi"}:
        parameters["_meta"] = {
            "clauz3": {
                "model": config.model,
                "instructions": SESSION_INSTRUCTIONS,
                "unrestricted": config.unrestricted,
            }
        }
    return parameters


class Conversation:
    def __init__(
        self,
        config: ChatConfig,
        store: ConversationStore | None = None,
        saved: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.provider_inventory = (
            providers.inventory(config.unrestricted) if config.select_provider else []
        )
        self.store = store
        self.identifier = saved["id"] if saved else store.create() if store else ""
        self.resume_id: str | None = None
        self.loading = False
        # The conversation's own mode, not the server's: a saved chat keeps its
        # mode until a fresh agent session starts under this server's flag.
        self.unrestricted = (
            bool(saved["state"].get("unrestricted")) if saved else config.unrestricted
        )
        self.saved_state = ""
        self.process: asyncio.subprocess.Process | None = None
        self.tasks: list[asyncio.Task[None]] = []
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.bridge_calls: set[str] = set()
        self.history: list[dict[str, Any]] = []
        self.history_bytes = 0
        self.history_limited = bool(saved and saved["state"].get("historyLimited"))
        # Library changes the agent has not heard about yet; reported as a note
        # prepended to the next prompt.
        self.library_changes: list[str] = (
            list(saved["state"].get("libraryChanges", [])) if saved else []
        )
        self.subscriber: asyncio.Queue[dict[str, Any]] | None = None
        self.next_id = 0
        self.session_id: str | None = None
        self.turn = 0
        self.saw_turn_output = False
        self.busy = False
        self.ended = False
        self.closed = False
        self.close_lock = asyncio.Lock()
        self.error = ""
        self.resume_error = ""
        self.lock = asyncio.Lock()
        self.writer_lock = asyncio.Lock()
        self.run_turns: dict[str, int] = {}
        self.last_runs: list[dict[str, Any]] = []
        self.archived_runs: list[dict[str, Any]] = []
        self.lifetime: int | None = None
        self.lifetime_server: asyncio.AbstractServer | None = None
        self.lifetime_directory: tempfile.TemporaryDirectory[str] | None = None
        self.bridges: set[asyncio.StreamWriter] = set()
        if saved:
            self.history = saved["history"]
            self.history_bytes = sum(len(json.dumps(m).encode()) for m in self.history)
            self.turn = max(
                [saved["state"].get("turn", 0)]
                + [
                    m["params"]["turn"]
                    for m in self.history
                    if m["method"] == "clauz3/user"
                ]
            )
            # Startup notifications (e.g. available commands) are history too,
            # but adapters may not persist a session until its first prompt.
            if self.turn:
                self.resume_id = saved["state"].get("sessionId")
            self.last_runs = saved["state"].get("runs", [])
            self.archived_runs = self.last_runs.copy()
            for run in self.last_runs:
                self.run_turns[run["run_id"]] = run.get("turn", 0)
                if run.get("phase") not in TERMINAL_PHASES:
                    run["phase"] = "cancelled"

    def persist(self) -> None:
        if self.store:
            state = self.state()
            state.pop("providers")
            state["sessionId"] = self.session_id or self.resume_id
            state["libraryChanges"] = self.library_changes
            encoded = json.dumps(state)
            if encoded != self.saved_state:
                self.store.save(self.identifier, state)
                self.saved_state = encoded

    def runs(self) -> list[dict[str, Any]]:
        try:
            raw = Path(self.config.status_file).read_text()
            records = json.loads(raw) if len(raw) <= MAX_MESSAGE else []
        except (OSError, ValueError):
            records = self.last_runs
        if not isinstance(records, list):
            records = []
        for record in records:
            run_id = str(record.get("run_id", ""))
            record["turn"] = self.run_turns.setdefault(run_id, self.turn)
            if self.ended and record.get("phase") not in TERMINAL_PHASES:
                record["phase"] = "cancelled"
        self.last_runs = records
        # Older turns belong to this conversation even after a new bridge starts.
        current_ids = {r["run_id"] for r in records}
        records = [
            r for r in self.archived_runs if r["run_id"] not in current_ids
        ] + records
        return records

    def state(self) -> dict[str, Any]:
        selected = providers.PROVIDERS.get(self.config.agent)
        return {
            "agent": self.config.agent,
            "agentLabel": selected.label if selected else self.config.agent,
            "unrestricted": self.unrestricted,
            "providers": self.provider_inventory,
            "conversationId": self.identifier,
            "sessionId": self.session_id,
            "busy": self.busy,
            "historyLimited": self.history_limited,
            "ended": self.closed,
            "stopping": self.ended and not self.closed,
            "error": self.error,
            "resumeError": self.resume_error,
            "turn": self.turn,
            "cwd": self.config.cwd,
            "libraries": self.config.libraries,
            "libraryFingerprint": self.config.library_fingerprint,
            "librariesChanged": self.config.libraries_changed,
            "approvalsPaused": self.config.approvals_paused,
            "showLibraries": self.config.show_libraries,
            "requestedModel": self.config.model,
            "model": self.config.model or "Agent default",
            "runs": self.runs(),
        }

    def publish(self, message: dict[str, Any], *, retain: bool = True) -> None:
        if message.get("method") == "session/update":
            params = message.get("params")
            update = params.get("update") if isinstance(params, dict) else None
            if isinstance(update, dict) and update.get("sessionUpdate") in (
                "agent_message_chunk",
                "tool_call",
            ):
                self.saw_turn_output = True
        if retain:
            size = len(json.dumps(message).encode())
            if self.history_limited or self.history_bytes + size > MAX_HISTORY:
                if not self.history_limited:
                    self.history_limited = True
                    self.publish(
                        {"method": "clauz3/notice", "params": {"text": HISTORY_NOTICE}},
                        retain=False,
                    )
            else:
                self.history_bytes += size
                if self.store:
                    self.store.append(self.identifier, message)
                self.history.append(message)
        self.persist()
        if self.subscriber is not None:
            if self.subscriber.full():
                # Reconnect reconstructs from retained history; never block the agent.
                while not self.subscriber.empty():
                    self.subscriber.get_nowait()
                self.subscriber.put_nowait(
                    {
                        "method": "clauz3/restore",
                        "params": {
                            "history": list(self.history),
                            **self.state(),
                        },
                    }
                )
            else:
                self.subscriber.put_nowait(message)

    def changed(self) -> None:
        self.publish({"method": "clauz3/state", "params": self.state()}, retain=False)

    async def write(self, message: dict[str, Any]) -> None:
        async with self.writer_lock:
            if self.process is None or self.process.stdin is None or self.ended:
                raise ValueError("Agent session is not running")
            self.process.stdin.write((json.dumps(message) + "\n").encode())
            await self.process.stdin.drain()

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        identifier = self.next_id
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self.pending[identifier] = future
        try:
            await self.write(
                {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
            )
            return await future
        finally:
            self.pending.pop(identifier, None)

    def bridge_permission(self, params: object) -> dict[str, str]:
        # Codex correlates MCP approvals with previously announced tool calls.
        # Never grant native permissions or infer identity from display titles.
        if not isinstance(params, dict):
            return {"outcome": "cancelled"}
        meta, call, options = (
            params.get("_meta"),
            params.get("toolCall"),
            params.get("options"),
        )
        if (
            self.config.agent == "codex"
            and self.config.unrestricted
            and params.get("sessionId") == self.session_id
            and isinstance(meta, dict)
            and meta.get("is_mcp_tool_approval") is True
            and isinstance(call, dict)
            and isinstance(call.get("toolCallId"), str)
            and call["toolCallId"] in self.bridge_calls
            and isinstance(options, list)
        ):
            for option in options:
                if (
                    isinstance(option, dict)
                    and option.get("kind") == "allow_once"
                    and isinstance(option.get("optionId"), str)
                ):
                    return {"outcome": "selected", "optionId": option["optionId"]}
        return {"outcome": "cancelled"}

    async def read_agent(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Invalid ACP response")
                method = message.get("method")
                identifier = message.get("id")
                if method and identifier is not None:
                    permission = self.bridge_permission(message.get("params", {}))
                    # The six MCP tools are preallowed at SDK configuration time.
                    # Unexpected permission requests never become approval grants.
                    response = (
                        {"result": {"outcome": permission}}
                        if method == "session/request_permission"
                        else {
                            "error": {
                                "code": -32601,
                                "message": "Client capability disabled",
                            }
                        }
                    )
                    await self.write({"jsonrpc": "2.0", "id": identifier, **response})
                    if (
                        method == "session/request_permission"
                        and permission["outcome"] == "cancelled"
                    ):
                        self.publish(
                            {
                                "method": "clauz3/notice",
                                "params": {
                                    "text": (
                                        "Unexpected agent tool permission refused. "
                                        "Actions must use the selected Clauz3 tools."
                                    ),
                                },
                            }
                        )
                elif method == "session/update":
                    params = message.get("params")
                    update = params.get("update") if isinstance(params, dict) else None
                    if not isinstance(update, dict):
                        continue
                    raw = update.get("rawInput", {})
                    if (
                        self.config.agent == "codex"
                        and isinstance(raw, dict)
                        and raw.get("server") == "clauz3"
                        and f"mcp__clauz3__{raw.get('tool')}" in SESSION_TOOLS
                        and isinstance(update.get("toolCallId"), str)
                    ):
                        self.bridge_calls.add(update["toolCallId"])
                    if not self.loading:
                        self.publish(message)
                elif type(identifier) is int and identifier in self.pending:
                    future = self.pending[identifier]
                    if not future.done():
                        if "error" in message:
                            error = message["error"]
                            if not isinstance(error, dict):
                                raise ValueError("Invalid ACP error response")
                            future.set_exception(
                                ValueError(str(error.get("message", "Agent error")))
                            )
                        else:
                            result = message.get("result")
                            if not isinstance(result, dict):
                                raise ValueError(
                                    "Invalid ACP result: expected an object"
                                )
                            future.set_result(result)
            if not self.ended:
                raise ValueError(
                    "Agent disconnected. Start a new conversation to retry."
                )
        except Exception as exc:
            self.error = self.error or str(exc) or "Invalid ACP response"
            await self.close()

    async def drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        # Drain continuously without placing credentials or adapter logs in the UI.
        while await self.process.stderr.read(8192):
            pass

    async def start(self) -> None:
        async with self.lock:
            if self.process or self.ended:
                return
            if self.config.startup_error:
                self.error = self.config.startup_error
                await self.close()
                return
            try:
                providers.check_mode(
                    self.config.agent, self.config.unrestricted, browser_ui=True
                )
                providers.check_runtime(
                    self.config.agent, self.config.command, self.config.unrestricted
                )
            except ValueError as exc:
                self.error = str(exc)
                await self.close()
                return
            env = agent_environment()
            if self.config.agent == "opencode":
                env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
                    {"permission": {"clauz3_*": "allow"}}
                )
            read_fd, self.lifetime = os.pipe()
            try:
                self.lifetime_directory = tempfile.TemporaryDirectory(
                    prefix="clz-life-"
                )
                # AF_UNIX paths are limited to about 104 bytes on macOS. Honour
                # TMPDIR when it fits, otherwise use a short private directory.
                if (
                    len(os.fsencode(str(Path(self.lifetime_directory.name) / "bridge")))
                    > 100
                ):
                    self.lifetime_directory.cleanup()
                    self.lifetime_directory = tempfile.TemporaryDirectory(
                        prefix="clz-life-", dir="/tmp"
                    )
                lifetime_socket = str(Path(self.lifetime_directory.name) / "bridge")

                async def bridge_connected(
                    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
                ) -> None:
                    self.bridges.add(writer)
                    try:
                        await reader.read()
                    finally:
                        self.bridges.discard(writer)
                        writer.close()

                self.lifetime_server = await asyncio.start_unix_server(
                    bridge_connected, path=lifetime_socket
                )
                if self.config.agent in {"agy", "pi"}:
                    # The host removes private bridge files even when Stop kills
                    # the adapter before its own cleanup can run.
                    env["CLAUZ3_NATIVE_DIRECTORY"] = self.lifetime_directory.name
                if self.config.agent == "codex":
                    from clauz3.sessions.launcher import SESSION_INSTRUCTIONS

                    bridge = session_parameters(self.config, lifetime_socket)[
                        "mcpServers"
                    ][0]
                    env["CODEX_CONFIG"] = json.dumps(
                        {
                            "developer_instructions": SESSION_INSTRUCTIONS,
                            "mcp_servers": {
                                "clauz3": {
                                    "command": bridge["command"],
                                    "args": bridge["args"],
                                    "env": {
                                        item["name"]: item["value"]
                                        for item in bridge["env"]
                                    },
                                }
                            },
                        }
                    )
                    env["INITIAL_AGENT_MODE"] = "read-only"
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-P",
                    "-m",
                    "clauz3.sessions.acp_worker",
                    str(read_fd),
                    *self.config.command,
                    cwd=self.config.cwd,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                    limit=MAX_MESSAGE,
                    pass_fds=(read_fd,),
                )
                os.close(read_fd)
                read_fd = -1
                self.tasks = [
                    asyncio.create_task(self.read_agent()),
                    asyncio.create_task(self.drain_stderr()),
                ]
                async with asyncio.timeout(60):
                    result = await self.call(
                        "initialize",
                        {
                            "protocolVersion": 1,
                            "clientCapabilities": {},
                            "clientInfo": {"name": "clauz3", "version": "0.1.2"},
                        },
                    )
                    if result.get("protocolVersion") != 1:
                        raise ValueError("Agent must support ACP v1")
                    capabilities = result.get("agentCapabilities", {})
                    if not isinstance(capabilities, dict):
                        raise ValueError("Invalid ACP agent capabilities")
                    parameters = session_parameters(self.config, lifetime_socket)
                    if self.resume_id:
                        self.loading = True
                        try:
                            if not capabilities.get("loadSession"):
                                raise ValueError(
                                    "This adapter cannot resume saved conversations"
                                )
                            await self.call(
                                "session/load",
                                {**parameters, "sessionId": self.resume_id},
                            )
                        except Exception as exc:
                            # A local transcript can outlive the adapter's session.
                            # Never silently give a fresh agent the old chat's identity.
                            self.resume_error = str(exc) or "Session loading timed out"
                            self.error = (
                                "Couldn’t resume this conversation. "
                                "Your transcript is saved. Start a new conversation "
                                "to continue."
                            )
                            raise
                        finally:
                            self.loading = False
                        self.session_id = self.resume_id
                    else:
                        session = await self.call("session/new", parameters)
                        if (
                            not isinstance(session.get("sessionId"), str)
                            or not session["sessionId"]
                        ):
                            raise ValueError("Invalid ACP session ID")
                        self.session_id = session["sessionId"]
                        # Only a fresh agent session takes the server's mode;
                        # selected_config refuses to resume across modes.
                        self.unrestricted = self.config.unrestricted
                if self.config.model and self.config.agent in {"codex", "opencode"}:
                    async with asyncio.timeout(30):
                        await self.call(
                            "session/set_model",
                            {
                                "sessionId": self.session_id,
                                "modelId": self.config.model,
                            },
                        )
                self.changed()
                if dropped := self.config.dropped_libraries:
                    it = "it" if len(dropped) == 1 else "them"
                    self.publish(
                        {
                            "method": "clauz3/notice",
                            "params": {
                                "text": f"{', '.join(dropped)} "
                                + ("isn't" if len(dropped) == 1 else "aren't")
                                + f" installed in this project, so this conversation "
                                f"starts without {it}. Install from Libraries to use "
                                f"{it} again."
                            },
                        },
                        retain=False,
                    )
                if self.config.prompt:
                    self.submit(self.config.prompt)
            except Exception as exc:
                self.error = self.error or (
                    str(exc) or "Agent startup timed out. Check agent authentication."
                )
                await self.close()
            finally:
                if read_fd != -1:
                    os.close(read_fd)

    def submit(self, text: str) -> None:
        if not self.session_id or self.ended:
            raise ValueError("Agent is not ready")
        if self.busy or any(r.get("phase") not in TERMINAL_PHASES for r in self.runs()):
            raise ValueError(
                "An action is still running; wait or stop it before sending"
            )
        if not text.strip() or len(text) > 20_000:
            raise ValueError("Enter a prompt of 1–20,000 characters")
        if text.lstrip().startswith("/"):
            raise ValueError("Agent slash commands are disabled in this workspace")
        message = {
            "method": "clauz3/user",
            "params": {"text": text, "turn": self.turn + 1},
        }
        if (
            self.history_limited
            or self.history_bytes + len(json.dumps(message).encode()) > MAX_HISTORY
        ):
            self.history_limited = True
            self.changed()
            raise ValueError(HISTORY_NOTICE)
        self.publish(message)
        self.turn += 1
        self.saw_turn_output = False
        self.bridge_calls.clear()
        self.busy = True
        self.changed()
        self.tasks.append(asyncio.create_task(self.prompt(text)))

    async def prompt(self, text: str) -> None:
        if self.config.agent == "opencode" and self.turn == 1:
            from clauz3.sessions.launcher import SESSION_INSTRUCTIONS

            text = SESSION_INSTRUCTIONS + "\nUser request:\n" + text
        note = (
            "[clauz3] Your Clauz3 libraries changed: "
            + "; ".join(self.library_changes)
            + ". Call the tools tool again before writing programs; earlier tool "
            "listings are out of date."
            if self.library_changes
            else ""
        )
        self.library_changes = []
        try:
            result = await self.call(
                "session/prompt",
                {
                    "sessionId": self.session_id,
                    "prompt": [
                        {"type": "text", "text": f"{note}\n\n{text}" if note else text}
                    ],
                },
            )
            if self.config.agent != "claude" and not self.saw_turn_output:
                self.publish(
                    {
                        "method": "clauz3/notice",
                        "params": {
                            "text": (
                                "The agent returned no response. Check its "
                                "authentication and model selection; use --model "
                                "to select an available model."
                            )
                        },
                    }
                )
            self.publish({"method": "clauz3/turn_end", "params": result})
        except (OSError, ValueError) as exc:
            self.error = str(exc)
        finally:
            self.busy = False
            self.changed()

    async def close(self) -> None:
        async with self.close_lock:
            if not self.closed:
                await self._close()

    async def _close(self) -> None:
        # Stop ends this conversation. Killing the MCP bridge also closes each
        # runner's lifetime pipe, stopping workers in their own process groups.
        self.ended = True
        self.busy = False
        for bridge in tuple(self.bridges):
            bridge.close()
        if self.lifetime_server is not None:
            self.lifetime_server.close()
            await self.lifetime_server.wait_closed()
            self.lifetime_server = None
        if self.lifetime is not None:
            os.close(self.lifetime)
            self.lifetime = None
        if self.process is not None and self.process.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
            await self.process.wait()
        if self.lifetime_directory is not None:
            self.lifetime_directory.cleanup()
            self.lifetime_directory = None
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ValueError("Conversation stopped"))
        for task in self.tasks:
            if task is not asyncio.current_task() and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in self.tasks if t is not asyncio.current_task()),
            return_exceptions=True,
        )
        self.closed = True
        self.changed()


def render_workspace() -> str:
    from clauz3.approvals.workspace import profile_component

    return (
        (Path(__file__).parent / "web" / "index.html")
        .read_text()
        .replace("<!-- APPROVALS -->", profile_component())
    )


def install_chat(
    app: FastAPI,
    config: ChatConfig,
    is_approver: Callable[[str | None], bool],
    activate_libraries: Callable[[list[Path], bool], None] | None = None,
    activate_workspace: Callable[[Path, bool], dict[str, str]] | None = None,
) -> None:
    from clauz3.approvals.service import APPROVER_SESSION_COOKIE

    config = config.model_copy(update={"cwd": str(Path(config.cwd).resolve())})
    store = ConversationStore(
        Path(config.history_file)
        if config.history_file
        else Path(config.status_file).with_name("conversations.sqlite"),
        config.cwd,
    )
    launch_project, launch_bridge = config.cwd, config.bridge.copy()
    workspaces = Workspaces(store.path.parent)
    leases = ExitStack()
    switching = False
    manager = (
        WorkspaceLibraries(
            Path(config.cwd),
            store.path.parent
            / "workspaces"
            / hashlib.sha256(config.cwd.encode()).hexdigest(),
            config.libraries,
        )
        if config.manage_libraries
        else None
    )

    def selected_config(
        saved: dict[str, Any] | None,
        domains: list[str] | None = None,
        *,
        agent: str | None = None,
        accept_changes: bool = False,
        pause: bool = False,
        adapter: str | None = None,
    ) -> ChatConfig:
        """Build the config for a new or saved conversation.

        ``adapter`` is the Claude adapter check already run off the event loop
        by ``adapter_check``; ``None`` checks here, before the UI is serving.
        """
        selected_agent = (
            saved["state"].get("agent", "claude") if saved else agent or config.agent
        )
        base = config
        if saved:
            base = config.model_copy(
                update={
                    "agent": selected_agent,
                    "model": saved["state"].get("requestedModel"),
                    "approvals_paused": bool(saved["state"].get("approvalsPaused")),
                    "libraries": saved["state"].get("libraries", config.libraries),
                    "library_fingerprint": saved["state"].get("libraryFingerprint", ""),
                }
            )
        try:
            if saved and saved["state"].get("unrestricted") and not config.unrestricted:
                raise ValueError(
                    "This conversation requires --unrestricted; restart "
                    "with that flag or start a new conversation."
                )
            if saved and not saved["state"].get("unrestricted") and config.unrestricted:
                # Symmetric: never resume a restricted chat under unrestricted
                # agent tools, and never relabel it (a missing key is restricted).
                raise ValueError(
                    "This conversation was started in restricted mode; restart "
                    "without --unrestricted, or start a new conversation."
                )
            providers.check_mode(selected_agent, config.unrestricted, browser_ui=True)
            if selected_agent != config.agent:
                if not config.select_provider:
                    raise ValueError("Provider switching is unavailable")
                command = providers.command(selected_agent)
                providers.check_runtime(selected_agent, command, config.unrestricted)
                if selected_agent == "claude":
                    if adapter is None:
                        check_adapter(command[0])
                    elif adapter:
                        raise ValueError(adapter)
                base = base.model_copy(
                    update={
                        "agent": selected_agent,
                        "command": command,
                        "model": base.model if saved else None,
                    }
                )
            if saved:
                base = base.model_copy(
                    update={"model": saved["state"].get("requestedModel")}
                )
        except (OSError, ValueError) as exc:
            if not saved:
                raise
            return base.model_copy(
                update={
                    "agent": selected_agent,
                    "startup_error": str(exc),
                    "prompt": None,
                }
            )
        if manager is None:
            return base.model_copy(update={"prompt": None}) if saved else base
        state = saved["state"] if saved else {}
        paused = pause or bool(state.get("approvalsPaused"))
        previous = state.get("libraryFingerprint")
        dropped: list[str] = []
        try:
            if domains is None:
                if saved is None:
                    dropped = manager.prune()
                domains = (
                    state.get("libraries", config.libraries)
                    if saved
                    else manager.selected
                )
            if saved and not accept_changes and not unchanged(domains, previous):
                if activate_libraries:
                    activate_libraries([], False)
                return base.model_copy(
                    update={
                        "libraries": domains,
                        "startup_error": LIBRARIES_CHANGED,
                        "libraries_changed": True,
                        "library_fingerprint": previous or "",
                        "approvals_paused": paused,
                        "prompt": None,
                    }
                )
            roots = manager.roots(domains)
            fingerprint = manager.fingerprint(domains)
            bridge = [
                sys.executable,
                "-P",
                "-m",
                "clauz3.sessions.tools",
                config.cwd,
                *map(str, roots),
            ]
            if "--skills-snapshot" in config.bridge:
                index = config.bridge.index("--skills-snapshot")
                bridge += config.bridge[index : index + 2]
            if activate_libraries:
                activate_libraries(roots, paused)
            return base.model_copy(
                update={
                    "libraries": sorted(domains),
                    "library_fingerprint": fingerprint,
                    "approvals_paused": paused,
                    "dropped_libraries": dropped,
                    "bridge": bridge,
                    "prompt": None if saved else config.prompt,
                }
            )
        except (OSError, ValueError) as exc:
            if activate_libraries:
                activate_libraries([], False)
            return base.model_copy(
                update={
                    "libraries": domains or [],
                    "startup_error": str(exc),
                    "library_fingerprint": previous or "",
                    "approvals_paused": paused,
                    "prompt": None,
                }
            )

    async def adapter_check(agent: str) -> str | None:
        """Check the Claude adapter in a thread before switching to it."""
        if agent != "claude" or agent == config.agent or not config.select_provider:
            return None
        executable = shutil.which(providers.provider(agent).executable)
        if executable is None:
            return None  # selected_config reports the missing runtime.
        return await asyncio.to_thread(adapter_problem, executable)

    def unchanged(domains: list[str], fingerprint: str | None) -> bool:
        """Whether every library is still installed with the recorded files."""
        assert manager is not None
        installed = manager.installed()
        return all(d in installed for d in domains) and (
            not fingerprint or manager.fingerprint(domains) == fingerprint
        )

    # No conversation writes or status-file resets before the project lease.
    conversation = Conversation(config)  # Unpersisted until lifespan owns the lease.

    def authorized(connection: Request | WebSocket, *, mutation: bool = False) -> bool:
        host = connection.headers.get("host", "")
        origin = connection.headers.get("origin")
        return bool(
            is_approver(connection.cookies.get(APPROVER_SESSION_COOKIE))
            and urlsplit("http://" + host).hostname == "127.0.0.1"
            and connection.client
            and connection.client.host == "127.0.0.1"
            and connection.headers.get("sec-fetch-site") != "cross-site"
            and (not mutation or origin)
            and (not origin or origin == "http://" + host)
        )

    @app.get("/api/workspaces")
    def workspace_list(request: Request) -> Response:
        if not authorized(request):
            raise HTTPException(401)
        return JSONResponse(
            {
                **workspaces.snapshot(config.cwd),
                "enabled": manager is not None and activate_workspace is not None,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/workspace/libraries")
    def libraries(request: Request) -> Response:
        if not authorized(request):
            raise HTTPException(401)
        if manager is None:
            raise HTTPException(
                404, "Library management is unavailable for this service"
            )
        try:
            return JSONResponse(
                {
                    **manager.snapshot(),
                    "active": []
                    if conversation.closed
                    else conversation.config.libraries,
                },
                headers={"Cache-Control": "no-store"},
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/workspace/libraries/{action}")
    async def library_action(
        action: str, request: Request, payload: dict[str, Any]
    ) -> Response:
        if not authorized(request, mutation=True):
            raise HTTPException(401)
        if manager is None:
            raise HTTPException(
                404, "Library management is unavailable for this service"
            )
        if manager.busy or switching:
            raise HTTPException(
                409, "A workspace or library operation is already in progress"
            )
        manager.busy = True
        try:
            if action == "inspect":
                result = await asyncio.to_thread(
                    manager.inspect, str(payload.get("source", ""))
                )
            elif action == "prepare":
                result = await asyncio.to_thread(
                    manager.prepare,
                    str(payload.get("action", "")),
                    str(payload.get("source", "")),
                    str(payload.get("domain", "")),
                )
            elif action == "apply":
                if conversation.busy or any(
                    r.get("phase") not in TERMINAL_PHASES for r in conversation.runs()
                ):
                    raise ValueError(
                        "Wait for the current action to finish or press Stop "
                        "before applying library changes."
                    )
                active = [] if conversation.closed else conversation.config.libraries
                result = await asyncio.to_thread(
                    manager.apply, str(payload.get("id", "")), active
                )
            elif action == "select":
                domains = payload.get("domains")
                if not isinstance(domains, list) or not all(
                    isinstance(d, str) for d in domains
                ):
                    raise ValueError("Choose library names from the installed list")
                result = {"selected": manager.select(domains)}
            elif action == "discard":
                manager.discard()
                result = {"ok": True}
            else:
                raise HTTPException(404)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except (InstallError, SourceError, OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            manager.busy = False

    @app.get("/api/chat/conversations")
    def conversations(request: Request) -> Response:
        if not authorized(request):
            raise HTTPException(401)
        return JSONResponse(
            {"conversations": store.list()}, headers={"Cache-Control": "no-store"}
        )

    @app.get("/api/chat/transcript")
    async def transcript(request: Request) -> Response:
        if not authorized(request):
            raise HTTPException(401)
        return Response(
            json.dumps({"history": conversation.history, **conversation.state()}),
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    'attachment; filename="clauz3-conversation.json"'
                ),
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/chat/handoff")
    async def handoff(request: Request, payload: dict[str, Any]) -> Response:
        if not authorized(request, mutation=True):
            raise HTTPException(401)
        identifier = payload.get("conversationId")
        if not isinstance(identifier, str) or not identifier:
            raise HTTPException(400, "Choose a conversation to copy")
        # Pin the requested ID: switching chats during a click must not export
        # a different conversation. No awaits until the snapshot is complete.
        try:
            if identifier == conversation.identifier:
                conversation.persist()
            result = store.handoff(identifier)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                500, "Could not save the conversation snapshot"
            ) from exc
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/api/workspace/files")
    def files(request: Request, path: str = "", hidden: bool = False) -> Response:
        if not authorized(request):
            raise HTTPException(401)
        return JSONResponse(
            browse(Path(config.cwd), path, hidden=hidden),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[Any]:
        nonlocal conversation, config, store, manager
        async with previous_lifespan(app) as state:
            try:
                workspaces.remember(Path(config.cwd))
                if config.tutorial:
                    if config.unrestricted:
                        raise ValueError("Start the tutorial without --unrestricted")
                    target = workspaces.target({"tutorial": "email"})
                    store = ConversationStore(store.path, str(target))
                    leases.enter_context(store.lease())
                    workspaces.prepare_email()
                    config = config.model_copy(
                        update={
                            "cwd": str(target),
                            "prompt": None,
                            "show_libraries": False,
                            "bridge": [],
                        }
                    )
                    manager = WorkspaceLibraries(
                        target,
                        store.path.parent
                        / "workspaces"
                        / hashlib.sha256(config.cwd.encode()).hexdigest(),
                        ["email"],
                    )
                    if activate_workspace:
                        config = config.model_copy(
                            update={
                                "runner_env": {
                                    **config.runner_env,
                                    **activate_workspace(target, False),
                                }
                            }
                        )
                else:
                    leases.enter_context(store.lease())
                existing = store.list()
                saved = (
                    store.load(existing[0]["id"])
                    if existing and not config.prompt
                    else None
                )
                Path(config.status_file).unlink(missing_ok=True)
                conversation = Conversation(selected_config(saved), store, saved)
                app.state.conversation = conversation
                yield state
            finally:
                await conversation.close()
                if manager and not manager.busy:
                    manager.discard()
                leases.close()

    app.router.lifespan_context = lifespan

    @app.websocket("/api/chat/acp")
    async def socket(websocket: WebSocket) -> None:
        nonlocal conversation, config, store, manager, leases, switching
        if not authorized(websocket, mutation=True):
            # Accept only to deliver the close code; never send workspace data.
            await websocket.accept()
            await websocket.close(code=1008)
            return
        if conversation.subscriber is not None:
            await websocket.accept()
            await websocket.close(code=1013, reason="Workspace open in another tab")
            return
        await websocket.accept()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        conversation.subscriber = queue
        await websocket.send_json(
            {
                "method": "clauz3/restore",
                "params": {
                    "history": conversation.history,
                    **conversation.state(),
                },
            }
        )

        def start() -> asyncio.Task[None]:
            task = asyncio.create_task(conversation.start())
            task.add_done_callback(
                lambda task: None if task.cancelled() else task.exception()
            )
            return task

        startup = start()

        def idle() -> None:
            if manager and manager.busy:
                raise ValueError("Wait for the library operation to finish")
            if not startup.done():
                raise ValueError("Wait for agent startup to finish")
            if conversation.busy or any(
                r.get("phase") not in TERMINAL_PHASES for r in conversation.runs()
            ):
                raise ValueError(
                    "Wait for the action to finish or press Stop "
                    "before switching conversations"
                )

        def installed_with_dependencies(domains: object) -> list[str]:
            assert manager is not None
            if not isinstance(domains, list) or not all(
                isinstance(d, str) for d in domains
            ):
                raise ValueError("Choose library names from the installed list")
            # Libraries removed since the conversation last ran are dropped.
            return manager.usable(domains)

        async def replace(
            identifier: str | None,
            configure: Callable[[dict[str, Any] | None], ChatConfig],
        ) -> None:
            nonlocal conversation, startup
            await conversation.close()
            # Load after closing so the record includes the final saved state.
            saved = store.load(identifier) if identifier else None
            Path(config.status_file).unlink(missing_ok=True)
            while not queue.empty():
                queue.get_nowait()
            conversation = Conversation(
                configure(saved).model_copy(update={"prompt": None}), store, saved
            )
            app.state.conversation = conversation
            conversation.subscriber = queue
            await websocket.send_json(
                {
                    "method": "clauz3/restore",
                    "params": {"history": conversation.history, **conversation.state()},
                }
            )
            startup = start()

        async def send() -> None:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=0.5)
                except TimeoutError:
                    conversation.persist()
                    message = {"method": "clauz3/state", "params": conversation.state()}
                await websocket.send_json(message)

        sender = asyncio.create_task(send())
        try:
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode()) > 100_000:
                    raise ValueError("Message too large")
                try:
                    message = json.loads(raw)
                    method = message.get("method")
                    if method == "session/prompt":
                        if manager and manager.busy:
                            raise ValueError("Wait for the library operation to finish")
                        prompt = message.get("params", {}).get("prompt")
                        if not isinstance(prompt, str):
                            raise ValueError("Expected a text prompt")
                        conversation.submit(prompt)
                    elif method == "session/cancel":
                        await conversation.close()
                    elif method == "clauz3/workspace":
                        if manager is None or activate_workspace is None:
                            raise ValueError("Workspace switching is unavailable")
                        if manager.busy or switching:
                            raise ValueError("Wait for the library operation to finish")
                        params = message.get("params", {})
                        destination = workspaces.target(params)
                        tutorial = workspaces.is_tutorial(destination)
                        reset = params.get("reset") is True
                        if reset and (
                            not tutorial or params.get("confirmReset") is not True
                        ):
                            raise ValueError("Confirm resetting the email tutorial")
                        if tutorial and config.unrestricted:
                            raise ValueError(
                                "Start the tutorial without --unrestricted"
                            )
                        if str(destination) == config.cwd and not reset:
                            await websocket.send_json(
                                {
                                    "method": "clauz3/workspace_changed",
                                    "params": workspaces.snapshot(config.cwd),
                                }
                            )
                            continue
                        active = (
                            not startup.done()
                            or conversation.busy
                            or any(
                                r.get("phase") not in TERMINAL_PHASES
                                for r in conversation.runs()
                            )
                        )
                        if active and params.get("stop") is not True:
                            await websocket.send_json(
                                {"method": "clauz3/confirm_workspace", "params": params}
                            )
                            continue
                        next_store = ConversationStore(store.path, str(destination))
                        next_leases = ExitStack()
                        if str(destination) != config.cwd:
                            next_leases.enter_context(next_store.lease())
                        switching = True
                        try:
                            # Acquire the destination before stopping the source.
                            # An already-open destination leaves the source running.
                            if not startup.done():
                                startup.cancel()
                                await asyncio.gather(startup, return_exceptions=True)
                            await conversation.close()
                            manager.discard()
                            if tutorial:
                                workspaces.prepare_email(reset=reset)
                            storage = (
                                store.path.parent
                                / "workspaces"
                                / hashlib.sha256(str(destination).encode()).hexdigest()
                            )
                            if reset:
                                next_store.clear()
                                (storage / "libraries.json").unlink(missing_ok=True)
                            next_manager = WorkspaceLibraries(
                                destination, storage, ["email"] if tutorial else []
                            )
                            if not tutorial and not next_manager.preferences.exists():
                                next_manager.select(next_manager.installed())
                            runner_env = {
                                **config.runner_env,
                                **activate_workspace(destination, reset),
                            }
                            # Custom skills and startup prompts belong to the
                            # launching workspace.
                            config = config.model_copy(
                                update={
                                    "cwd": str(destination),
                                    "runner_env": runner_env,
                                    "status_file": str(
                                        Path(config.status_file).with_name(
                                            f"runs-{uuid4().hex}.json"
                                        )
                                    ),
                                    "bridge": launch_bridge
                                    if str(destination) == launch_project
                                    else [],
                                    "libraries": next_manager.selected,
                                    "prompt": None,
                                    "startup_error": "",
                                    "show_libraries": False,
                                }
                            )
                            if str(destination) != store.project:
                                leases.close()
                                leases = next_leases.pop_all()
                            store, manager = next_store, next_manager
                            workspaces.remember(destination)
                            existing = store.list()
                            saved = store.load(existing[0]["id"]) if existing else None
                            Path(config.status_file).unlink(missing_ok=True)
                            while not queue.empty():
                                queue.get_nowait()
                            conversation = Conversation(
                                selected_config(saved), store, saved
                            )
                            app.state.conversation = conversation
                            conversation.subscriber = queue
                            await websocket.send_json(
                                {
                                    "method": "clauz3/workspace_changed",
                                    "params": workspaces.snapshot(config.cwd),
                                }
                            )
                            await websocket.send_json(
                                {
                                    "method": "clauz3/restore",
                                    "params": {
                                        "history": conversation.history,
                                        **conversation.state(),
                                    },
                                }
                            )
                            startup = start()
                        finally:
                            switching = False
                            next_leases.close()
                    elif method == "clauz3/rename":
                        params = message.get("params", {})
                        store.rename(
                            str(params.get("id", "")), str(params.get("title", ""))
                        )
                        await websocket.send_json(
                            {
                                "method": "clauz3/conversations",
                                "params": {"conversations": store.list()},
                            }
                        )
                    elif method in {"clauz3/new", "clauz3/select"}:
                        idle()
                        params = message.get("params", {})
                        if method == "clauz3/select":
                            identifier = str(params.get("id", ""))
                            if (
                                identifier == conversation.identifier
                                and not conversation.closed
                            ):
                                continue
                            selected = store.load(identifier)
                            adapter = await adapter_check(
                                selected["state"].get("agent", "claude")
                            )
                            await replace(
                                identifier, partial(selected_config, adapter=adapter)
                            )
                            continue
                        requested_agent = params.get("agent", config.agent)
                        providers.provider(requested_agent)
                        providers.check_mode(
                            requested_agent, config.unrestricted, browser_ui=True
                        )
                        domains = (
                            None
                            if manager is None or params.get("domains") is None
                            else installed_with_dependencies(params["domains"])
                        )
                        # What this New would start with, stale defaults dropped.
                        target = (
                            None
                            if manager is None
                            else manager.usable(manager.selected)
                            if domains is None
                            else domains
                        )
                        if (
                            requested_agent == conversation.config.agent
                            and not conversation.turn
                            and not conversation.ended
                            # A chat that cannot start (e.g. a mode refusal)
                            # is never reused, so New always recovers.
                            and not conversation.config.startup_error
                            and not conversation.history_limited
                            and (
                                manager is None
                                or target is not None
                                and target == conversation.config.libraries
                                and manager.fingerprint(target)
                                == conversation.config.library_fingerprint
                            )
                        ):
                            # Repeated New clicks should not fill the sidebar
                            # with unused chats. A changed selection still starts
                            # a new agent with freshly constructed authority.
                            continue
                        if requested_agent != config.agent:
                            # Refuse an unavailable runtime before stopping this chat.
                            candidate = providers.command(requested_agent)
                            providers.check_runtime(
                                requested_agent, candidate, config.unrestricted
                            )
                        adapter = await adapter_check(requested_agent)
                        if adapter:
                            raise ValueError(adapter)
                        await replace(
                            None,
                            partial(
                                selected_config,
                                domains=domains,
                                agent=requested_agent,
                                adapter=adapter,
                            ),
                        )
                    elif method == "clauz3/libraries":
                        idle()
                        if manager is None:
                            raise ValueError("Library management is unavailable")
                        params = message.get("params", {})
                        domains = installed_with_dependencies(params.get("domains"))
                        current = conversation.config.libraries
                        files_unchanged = (
                            not conversation.config.libraries_changed
                            and unchanged(
                                current, conversation.config.library_fingerprint
                            )
                        )
                        added = sorted(set(domains) - set(current))
                        removed = sorted(set(current) - set(domains))
                        if not added and not removed and files_unchanged:
                            continue
                        started = conversation.turn > 0
                        widening = bool(added) or not files_unchanged
                        if started and widening and params.get("confirm") is not True:
                            await websocket.send_json(
                                {
                                    "method": "clauz3/confirm_libraries",
                                    "params": {
                                        "domains": domains,
                                        "added": added,
                                        "removed": removed,
                                        "updated": not files_unchanged,
                                    },
                                }
                            )
                            continue
                        adapter = await adapter_check(conversation.config.agent)
                        await replace(
                            conversation.identifier,
                            partial(
                                selected_config,
                                domains=domains,
                                accept_changes=True,
                                pause=started and widening,
                                adapter=adapter,
                            ),
                        )
                        if started:
                            changes = [
                                *(f"added {d}" for d in added),
                                *(f"removed {d}" for d in removed),
                                *(
                                    ["updated library files"]
                                    if not files_unchanged
                                    else []
                                ),
                            ]
                            conversation.library_changes += changes
                            conversation.publish(
                                {
                                    "method": "clauz3/libraries_changed",
                                    "params": {
                                        "added": added,
                                        "removed": removed,
                                        "updated": not files_unchanged,
                                        "paused": conversation.config.approvals_paused,
                                        "turn": conversation.turn,
                                    },
                                }
                            )
                    else:
                        raise ValueError("Unsupported workspace operation")
                except (OSError, ValueError, AttributeError, TypeError) as exc:
                    await websocket.send_json(
                        {"method": "clauz3/notice", "params": {"text": str(exc)}}
                    )
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            conversation.subscriber = None
            sender.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await sender
