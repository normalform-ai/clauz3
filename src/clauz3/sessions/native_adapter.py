"""Small ACP transports for Antigravity stream-json and Pi RPC.

These use installed runtimes with existing authentication. Restricted Pi uses
the pinned SDK with an exact tool allowlist; other native CLI modes are unrestricted.
All configuration is private and temporary; no user/project settings are edited.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from clauz3.libraries.discovery import global_library_root
from clauz3.sessions.providers import PI_VERSION, pi_sdk

PI_TOOLS = [
    f"mcp__clauz3__{name}"
    for name in ("run", "status", "tools", "libraries", "list_skills", "read_skill")
]


def restricted_pi_environment() -> dict[str, str]:
    """Do not inherit Node preload hooks, Pi configuration or endpoint overrides."""
    allowed = {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "TMPDIR",
        "SYSTEMROOT",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_EXTRA_CA_CERTS",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        # The bridge's ``libraries`` tool resolves the global library root
        # from these, as it does under every other provider.
        "CLAUZ3_HOME",
        "XDG_DATA_HOME",
    }
    return {
        **{k: v for k, v in os.environ.items() if k in allowed or k.startswith("LC_")},
        "PI_OFFLINE": "1",
        "PI_SKIP_VERSION_CHECK": "1",
    }


def emit(message: dict[str, Any]) -> None:
    print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)


def session_path(identifier: str) -> Path:
    if str(UUID(identifier)) != identifier:
        raise ValueError("Invalid native session ID")
    directory = global_library_root() / "native-sessions"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / f"{identifier}.json"


class NativeAdapter:
    def __init__(self, agent: str, executable: str) -> None:
        self.agent = agent
        self.executable = executable
        self.process: asyncio.subprocess.Process | None = None
        self.directory = tempfile.TemporaryDirectory(
            prefix="clauz3-native-", dir=os.environ.get("CLAUZ3_NATIVE_DIRECTORY")
        )
        self.session_id = ""
        self.instructions = ""
        self.native_id: str | None = None
        self.turn = 0
        self.restricted = False

    async def send(self, value: dict[str, Any]) -> None:
        assert self.process and self.process.stdin
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        await self.process.stdin.drain()

    async def read(self) -> dict[str, Any]:
        assert self.process and self.process.stdout
        line = await self.process.stdout.readline()
        if not line:
            raise ValueError(
                f"{self.agent} exited. Check its installation and sign in using "
                f"{self.agent} in a terminal, then start a new conversation."
            )
        result = json.loads(line)
        if not isinstance(result, dict):
            raise ValueError("Invalid native agent response")
        return result

    def update(self, update: dict[str, Any]) -> None:
        emit(
            {
                "method": "session/update",
                "params": {
                    "sessionId": self.session_id,
                    "update": update,
                },
            }
        )

    def text(self, text: str) -> None:
        if text:
            self.update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": text},
                }
            )

    async def start(self, params: dict[str, Any], *, resume: bool) -> dict[str, Any]:
        if self.process:
            raise ValueError("Native adapter already has a session")
        self.session_id = params["sessionId"] if resume else str(uuid4())
        meta = params.get("_meta", {}).get("clauz3", {})
        self.restricted = self.agent == "pi" and meta.get("unrestricted") is not True
        path = session_path(self.session_id)
        if resume:
            state = json.loads(path.read_text())
            if state["agent"] != self.agent or state["cwd"] != params["cwd"]:
                raise ValueError(
                    "Native session belongs to a different agent or project"
                )
            if bool(state.get("restricted")) != self.restricted:
                raise ValueError(
                    "Start a new conversation to change Pi confinement mode"
                )
            self.native_id = state["native_id"]
            if not self.native_id:
                raise ValueError("This conversation has no resumable native session")
        self.instructions = meta.get("instructions", "")
        model = meta.get("model")
        servers = params["mcpServers"]
        if len(servers) != 1 or servers[0]["name"] != "clauz3":
            raise ValueError("Expected the Clauz3 MCP bridge")
        server = servers[0]
        bridge = {
            "command": server["command"],
            "args": server["args"],
            "env": {entry["name"]: entry["value"] for entry in server["env"]},
        }
        temporary = Path(self.directory.name)
        env = dict(os.environ)
        cwd = params["cwd"]
        if self.restricted:
            sdk = pi_sdk(self.executable)
            ready = temporary / "ready"
            agent_dir = Path(
                os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi/agent"))
            )
            auth_path = agent_dir / "auth.json"
            if auth_path.exists():
                credentials = json.loads(auth_path.read_text())
                if not isinstance(credentials, dict) or any(
                    isinstance(value, dict)
                    and isinstance(value.get("key"), str)
                    and value["key"].lstrip().startswith("!")
                    for value in credentials.values()
                ):
                    raise ValueError(
                        "Restricted Pi requires OAuth or literal API keys; "
                        "credential commands are disabled"
                    )
            settings_path = agent_dir / "settings.json"
            settings = (
                json.loads(settings_path.read_text()) if settings_path.exists() else {}
            )
            if not isinstance(settings, dict):
                raise ValueError("Invalid Pi settings")
            session_directory = global_library_root() / "native-sessions/pi"
            session_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            configuration = {
                "sdk": str(sdk),
                "authPath": str(auth_path),
                "cwd": params["cwd"],
                "privateDirectory": str(temporary),
                "sessionDirectory": str(session_directory),
                "resume": self.native_id,
                "instructions": self.instructions,
                "model": model,
                "bridge": bridge,
                "ready": str(ready),
                **{
                    k: v
                    for k in ("defaultProvider", "defaultModel")
                    if isinstance((v := settings.get(k)), str)
                },
            }
            config = temporary / "restricted.json"
            config.write_text(json.dumps(configuration))
            config.chmod(0o600)
            env = restricted_pi_environment()
            env["PI_CODING_AGENT_DIR"] = str(temporary)
            command = [
                str(shutil.which("node")),
                str(Path(__file__).with_name("restricted_pi.mjs")),
                str(config),
            ]
            cwd = str(temporary)
        elif self.agent == "pi":
            ready = temporary / "ready"
            bridge["ready"] = str(ready)
            config = temporary / "bridge.json"
            config.write_text(json.dumps(bridge))
            config.chmod(0o600)
            env["CLAUZ3_PI_BRIDGE"] = str(config)
            command = [
                self.executable,
                "--mode",
                "rpc",
                "--no-extensions",
                "--no-skills",
                "--no-prompt-templates",
                "--no-context-files",
                "--no-themes",
                "--no-approve",
                "--extension",
                str(Path(__file__).with_name("pi_bridge.mjs")),
                "--append-system-prompt",
                self.instructions,
            ]
            if resume:
                command += ["--session", str(self.native_id)]
            else:
                command += ["--session-id", self.session_id]
        else:
            # AGY has no per-invocation MCP config flag. A private workspace
            # carries its bridge while the bridge retains the actual project.
            cwd = str(temporary)
            agents = temporary / ".agents"
            agents.mkdir()
            config = agents / "mcp_config.json"
            config.write_text(json.dumps({"mcpServers": {"clauz3": bridge}}))
            config.chmod(0o600)
            command = [
                self.executable,
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--disable-slash-commands",
            ]
            if resume:
                command += ["--conversation", str(self.native_id)]
        if model and not self.restricted:
            command += ["--model", model]
        self.process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=2_000_000,
        )
        if self.agent == "pi":
            await self.send({"id": "state", "type": "get_state"})
            async with asyncio.timeout(45):
                while True:
                    result = await self.read()
                    if (
                        result.get("type") == "response"
                        and result.get("success") is False
                    ):
                        raise ValueError(str(result.get("error", "Pi startup failed")))
                    if result.get("id") == "state":
                        self.native_id = result.get("data", {}).get("sessionFile")
                        break
            if not ready.exists() or not self.native_id:
                raise ValueError(
                    "Pi did not load the Clauz3 bridge; update Pi and retry"
                )
            if self.restricted:
                attestation = json.loads(ready.read_text())
                if attestation.get("version") != PI_VERSION or sorted(
                    attestation.get("tools", [])
                ) != sorted(PI_TOOLS):
                    raise ValueError(
                        "Restricted Pi did not confirm the exact Clauz3 tool allowlist"
                    )
        self.save(params["cwd"])
        return {"sessionId": self.session_id}

    def save(self, cwd: str | None = None) -> None:
        path = session_path(self.session_id)
        if cwd is None:
            cwd = json.loads(path.read_text())["cwd"]
        path.write_text(
            json.dumps(
                {
                    "agent": self.agent,
                    "cwd": cwd,
                    "native_id": self.native_id,
                    "restricted": self.restricted,
                }
            )
        )
        path.chmod(0o600)

    async def prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("sessionId") != self.session_id:
            raise ValueError("Unknown session")
        text = "\n".join(
            block["text"] for block in params["prompt"] if block["type"] == "text"
        )
        self.turn += 1
        if self.agent == "agy":
            if self.turn == 1:
                text = self.instructions + "\nUser request:\n" + text
            await self.send({"event": "user", "message": {"content": text}})
        else:
            await self.send({"id": "prompt", "type": "prompt", "message": text})
        streamed = False
        while True:
            event = await self.read()
            if self.agent == "agy":
                if event.get("event") == "init":
                    self.native_id = event["conversation_id"]
                    self.save()
                elif event.get("event") == "step_update":
                    step = event["step_update"]
                    if step.get("step_type") == "agent_response":
                        self.text(step.get("text_delta", ""))
                        streamed |= bool(step.get("text_delta"))
                    elif step.get("step_type") == "tool":
                        self.update(
                            {
                                "sessionUpdate": "tool_call",
                                "toolCallId": f"{self.turn}:{step['step_index']}",
                                "title": step.get("tool_name", "Tool call"),
                                "status": "failed"
                                if step.get("tool_info", {}).get("error")
                                else "completed"
                                if step.get("state") == "DONE"
                                else "in_progress",
                            }
                        )
                elif event.get("event") == "result":
                    result = event["result"]
                    self.native_id = result.get("conversation_id", self.native_id)
                    self.save()
                    if result.get("denied_actions"):
                        raise ValueError(
                            "Antigravity denied a tool call. For Clauz3 MCP access, "
                            "add mcp(clauz3/run), mcp(clauz3/status), "
                            "mcp(clauz3/tools), "
                            "mcp(clauz3/libraries), mcp(clauz3/list_skills), and "
                            "mcp(clauz3/read_skill) to permissions.allow in "
                            "~/.gemini/antigravity-cli/settings.json. "
                            "Native tool permissions remain Antigravity settings."
                        )
                    if result.get("status") != "SUCCESS":
                        raise ValueError(
                            result.get("error")
                            or f"Antigravity: {result.get('status')}"
                        )
                    if not streamed:
                        self.text(result.get("response", ""))
                    return {"stopReason": "end_turn"}
            else:
                kind = event.get("type")
                if kind == "response" and not event.get("success", True):
                    raise ValueError(event.get("error", "Pi request failed"))
                if kind == "message_update":
                    chunk = event.get("assistantMessageEvent", {})
                    if chunk.get("type") == "text_delta":
                        self.text(chunk.get("delta", ""))
                elif kind == "tool_execution_start":
                    self.update(
                        {
                            "sessionUpdate": "tool_call",
                            "toolCallId": event["toolCallId"],
                            "title": event["toolName"],
                            "status": "in_progress",
                        }
                    )
                elif kind == "tool_execution_end":
                    self.update(
                        {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": event["toolCallId"],
                            "status": "failed" if event.get("isError") else "completed",
                        }
                    )
                elif kind == "extension_ui_request":
                    await self.send(
                        {
                            "type": "extension_ui_response",
                            "id": event["id"],
                            "cancelled": True,
                        }
                    )
                elif kind == "agent_end":
                    for message in event.get("messages", []):
                        if message.get("stopReason") == "error":
                            raise ValueError(
                                message.get("errorMessage", "Pi model request failed")
                            )
                    return {"stopReason": "end_turn"}

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.directory.cleanup()


async def main() -> None:
    adapter = NativeAdapter(*sys.argv[1:])
    reader = asyncio.StreamReader()
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )
    try:
        while line := await reader.readline():
            message = json.loads(line)
            identifier = message.get("id")
            try:
                method, params = message["method"], message.get("params", {})
                if method == "initialize":
                    result = {
                        "protocolVersion": 1,
                        "agentCapabilities": {"loadSession": True},
                    }
                elif method in {"session/new", "session/load"}:
                    result = await adapter.start(
                        params, resume=method == "session/load"
                    )
                elif method == "session/prompt":
                    result = await adapter.prompt(params)
                else:
                    raise ValueError("Unsupported native adapter operation")
                if identifier is not None:
                    emit({"id": identifier, "result": result})
            except Exception as exc:
                if identifier is not None:
                    emit(
                        {
                            "id": identifier,
                            "error": {"code": -32603, "message": str(exc)},
                        }
                    )
    finally:
        transport.close()
        with suppress(ProcessLookupError):
            await adapter.close()


if __name__ == "__main__":
    asyncio.run(main())
