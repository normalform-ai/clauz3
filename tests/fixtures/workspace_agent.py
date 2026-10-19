"""Deterministic ACP agent; optional prompt runs a real Clauz3 MCP call."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from clauz3.mcp import MCPClient


def emit(value: dict[str, Any]) -> None:
    print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)


async def main() -> None:
    bridge: dict[str, Any] = {}
    active: set[asyncio.Task[None]] = set()

    async def answer(message: dict[str, Any]) -> None:
        prompt = message["params"]["prompt"][0]["text"]
        if prompt == "crash":
            os._exit(1)
        if prompt == "wait":
            await asyncio.sleep(60)
        if prompt == "permission":
            emit(
                {
                    "id": "permission",
                    "method": "session/request_permission",
                    "params": {
                        "options": [{"optionId": "allow", "kind": "allow_once"}],
                    },
                }
            )
            return
        if prompt.startswith("run:"):
            source = Path(prompt[4:]).read_text()
            emit(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "fixture",
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "run",
                            "title": "Run proved program",
                            "status": "in_progress",
                            "rawInput": {"source": source},
                        },
                    },
                }
            )

            def run() -> str:
                if "--pi-sdk" in sys.argv:
                    from clauz3.sessions.providers import pi_sdk

                    executable = sys.argv[sys.argv.index("--pi-sdk") + 1]
                    result = subprocess.run(
                        ["node", str(Path(__file__).with_name("pi_proof.mjs"))],
                        input=json.dumps(
                            {
                                "sdk": str(pi_sdk(executable)),
                                "source": source,
                                "bridge": {
                                    **bridge,
                                    "env": {
                                        e["name"]: e["value"] for e in bridge["env"]
                                    },
                                },
                            }
                        ),
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=True,
                    )
                    return result.stdout
                with MCPClient(
                    command=bridge["command"],
                    args=bridge["args"],
                    env={
                        **os.environ,
                        **{e["name"]: e["value"] for e in bridge["env"]},
                    },
                ) as client:
                    return str(client.call("run", source=source))

            output = await asyncio.to_thread(run)
            emit(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "fixture",
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "run",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "content",
                                    "content": {"type": "text", "text": output},
                                }
                            ],
                        },
                    },
                }
            )
        # Chunks split Markdown mid-token, as streamed agent text does.
        chunks = (
            ("Use **both", " forms:**\n\n- `a`\n  ``", "`\n  x <b>y</b>\n  ```\n")
            if prompt == "markdown"
            else (prompt.split("\n\n")[0],)
            if prompt.startswith("[clauz3]")
            else ("Hello ", "from the fixture. <script>bad()</script>")
        )
        for chunk in chunks:
            emit(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "fixture",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": chunk},
                        },
                    },
                }
            )
        emit({"id": message["id"], "result": {"stopReason": "end_turn"}})

    while line := await asyncio.to_thread(sys.stdin.readline):
        message = json.loads(line)
        method = message.get("method")
        if method == "initialize":
            assert message["params"]["clientCapabilities"] == {}
            assert "CLAUZ3_APPROVAL_SIGNING_KEY" not in os.environ
            assert "CLAUZ3_APPROVAL_DECISION_TOKEN" not in os.environ
            emit(
                {
                    "id": message["id"],
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {"loadSession": True},
                    },
                }
            )
        elif method in {"session/new", "session/load"}:
            params = message["params"]
            if "--generic" in sys.argv:
                assert "claudeCode" not in params.get("_meta", {})
            else:
                options = params["_meta"]["claudeCode"]["options"]
                assert options["tools"] == [] and options["settingSources"] == []
                assert options["strictMcpConfig"] is True
                assert options["settings"]["disableAllHooks"] is True
                assert options["allowDangerouslySkipPermissions"] is False
            assert len(params["mcpServers"]) == 1
            bridge = params["mcpServers"][0]
            assert not any(
                "SIGNING" in e["name"] or "DECISION" in e["name"] for e in bridge["env"]
            )
            if method == "session/load":
                if "--missing-session" in sys.argv or os.environ.get(
                    "CLAUZ3_FIXTURE_EMPTY_SESSION"
                ):
                    emit(
                        {
                            "id": message["id"],
                            "error": {
                                "code": -32002,
                                "message": "Resource not found: " + params["sessionId"],
                            },
                        }
                    )
                    continue
                emit(
                    {
                        "method": "session/update",
                        "params": {
                            "sessionId": "fixture",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {
                                    "type": "text",
                                    "text": "REPLAY_MUST_NOT_DUPLICATE",
                                },
                            },
                        },
                    }
                )
            if os.environ.get("CLAUZ3_FIXTURE_EMPTY_SESSION"):
                # Real adapters announce commands before the user sends a
                # prompt, when there is no persisted agent session to load yet.
                emit(
                    {
                        "method": "session/update",
                        "params": {
                            "sessionId": "fixture",
                            "update": {
                                "sessionUpdate": "available_commands_update",
                                "availableCommands": [],
                            },
                        },
                    }
                )
            emit({"id": message["id"], "result": {"sessionId": "fixture"}})
        elif method == "session/set_model":
            emit({"id": message["id"], "result": {}})
        elif method == "session/prompt":
            task = asyncio.create_task(answer(message))
            active.add(task)
            task.add_done_callback(active.discard)
        elif message.get("id") == "permission":
            assert message["result"]["outcome"]["outcome"] == "cancelled"
            emit(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "fixture",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "Permission refused"},
                        },
                    },
                }
            )


if __name__ == "__main__":
    asyncio.run(main())
