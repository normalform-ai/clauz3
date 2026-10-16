"""Line-oriented terminal frontend for the same conversation host as the UI."""

import asyncio
import json
import os
import stat
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from clauz3.sessions.chat import TERMINAL_PHASES, ChatConfig, Conversation
from clauz3.sessions.history import ConversationStore
from clauz3.sessions.records import _path


class TerminalConversation(Conversation):
    def publish(self, message: dict[str, Any], *, retain: bool = True) -> None:
        super().publish(message, retain=retain)
        if message["method"] == "session/update":
            update = message["params"].get("update", {})
            if update.get("sessionUpdate") == "agent_message_chunk":
                print(update.get("content", {}).get("text", ""), end="", flush=True)
            elif update.get("sessionUpdate") == "tool_call":
                print(f"\n[{update.get('title', 'Tool call')}]", file=sys.stderr)
        elif message["method"] == "clauz3/turn_end":
            print(flush=True)
        elif message["method"] == "clauz3/notice":
            print(message["params"].get("text", ""), file=sys.stderr)


async def run(config: ChatConfig, session_id: str, resume: bool = False) -> int:
    store = ConversationStore(_path(session_id, "sqlite"), config.cwd)
    entries = store.list()
    saved = store.load(entries[0]["id"]) if resume and entries else None
    if resume and saved is None:
        raise ValueError("This session has no saved agent conversation")
    conversation = TerminalConversation(config, store, saved)
    interactive = sys.stdin.isatty()
    transport: asyncio.ReadTransport | None = None
    input_mode = os.fstat(sys.stdin.fileno()).st_mode
    if stat.S_ISREG(input_mode) or (stat.S_ISCHR(input_mode) and not interactive):
        # Event loops cannot register regular files or /dev/null as pipes.
        async def read_line() -> bytes:
            return await asyncio.to_thread(sys.stdin.buffer.readline)
    else:
        reader = asyncio.StreamReader()
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )

        async def read_line() -> bytes:
            return await reader.readline()

    agent_exit: asyncio.Task[int] | None = None
    line: asyncio.Task[bytes] | None = None
    try:
        await conversation.start()
        if conversation.process and not conversation.ended:
            agent_exit = asyncio.create_task(conversation.process.wait())
        while not conversation.ended:
            while conversation.busy or any(
                r.get("phase") not in TERMINAL_PHASES for r in conversation.runs()
            ):
                conversation.persist()
                await asyncio.sleep(0.1)
            if conversation.error:
                break
            if interactive:
                print("\nYou> ", end="", flush=True)
            line = asyncio.create_task(read_line())
            if agent_exit:
                done, _ = await asyncio.wait(
                    {line, agent_exit}, return_when=asyncio.FIRST_COMPLETED
                )
                if agent_exit in done:
                    conversation.error = conversation.error or "Agent disconnected"
                    break
            text = await line
            if not text or text.strip() in {b"/exit", b"/quit"}:
                break
            if not text.strip():
                continue
            try:
                conversation.submit(text.decode().rstrip("\n"))
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
        if conversation.error:
            print(conversation.error, file=sys.stderr)
            return 2
        return 0
    finally:
        for task in (line, agent_exit):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        if transport:
            transport.close()
        await conversation.close()
        conversation.persist()


def main() -> int:
    config_path, session_id, *flags = sys.argv[1:]
    config = ChatConfig.model_validate(json.loads(Path(config_path).read_text()))
    try:
        return asyncio.run(run(config, session_id, "--resume" in flags))
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
