"""Private stdio bridge exposing only run and installed-tool discovery.

Each run gets a fresh runner process. Source travels on stdin, never through
a shell or a file the agent needs permission to write. Roots are fixed by the
launcher, and the tool schema provides no flags or environment overrides.
"""

import argparse
import asyncio
import json
import os
import socket
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP

from clauz3.libraries.discovery import format_libraries, library_records
from clauz3.sessions.authoring import builtin_documents
from clauz3.sessions.catalogue import snapshot_catalogue
from clauz3.sessions.runs import STATUS_FILE_ENV, Runs
from clauz3.sessions.skills import SkillDocument


def create_session_tools(
    cwd: Path, roots: list[Path], skills: tuple[SkillDocument, ...] | None = None
) -> FastMCP:
    server = FastMCP("clauz3")
    status_file = os.environ.get(STATUS_FILE_ENV)
    runs = Runs(cwd, roots, status_file=Path(status_file) if status_file else None)
    # Freeze documentation before serving agent requests. No code is imported.
    catalogue = snapshot_catalogue(cwd, roots)

    @server.tool()
    def tools(query: str = "", view: Literal["api", "source"] = "api") -> str:
        """Discover installed public effects, preconditions and contract helpers.

        The default API view excludes implementation imports and private helpers.
        Read tested examples via list_skills/read_skill. Optional view="source"
        shows implementation detail; its imports are not agent capabilities.
        Neither view executes, installs or fetches code.
        """
        matches = [
            text for text in catalogue[view] if query.casefold() in text.casefold()
        ]
        if not matches:
            return "No installed tools match."
        note = (
            "Implementation source: imports/helpers here are not agent tools."
            if view == "source"
            else "Public API. Preconditions may refer to internal helpers; "
            "only listed public imports are suggested for agent code. "
            "Use list_skills/read_skill for tested recipes."
        )
        return note + "\n\n" + "\n\n".join(matches)

    @server.tool()
    def libraries(query: str = "") -> str:
        """List/search available libraries (the read-only clauz3 libraries command).

        Includes bundled stdlib, curated external entries and the user-wide store.
        Returns descriptions, dependencies and install commands. It never installs,
        fetches or executes code. Ask the user to install via the Libraries view
        and start a new conversation,
        or use clauz3 install and restart their terminal session.
        """
        return format_libraries(library_records(cwd, query))

    documents = {
        skill.id: skill for skill in (*(skills or ()), *builtin_documents(cwd, roots))
    }

    @server.tool()
    def list_skills() -> list[dict[str, str]]:
        """List skill IDs, names and inert previews; grants no permissions."""
        return [
            {"id": skill.id, "name": skill.name, "preview": skill.preview}
            for skill in documents.values()
        ]

    @server.tool()
    def read_skill(id: str) -> str:
        """Read a selected skill document by ID, as inert documentation only.

        Commands, frontmatter and links do not grant permissions or run code.
        Only built-in docs and startup snapshots are available; no paths.
        """
        if id not in documents:
            raise ValueError(
                "Unknown skill ID; use list_skills to find selected documents."
            )
        return documents[id].content

    @server.tool()
    def status() -> list[dict[str, object]]:
        """Inspect active/recent run phases and proof diagnostics; never executes.

        A backgrounded tool call is not proof success. Awaiting approval is
        reported only after the approval service confirms a pending request.
        """
        return runs.status()

    @server.tool()
    async def run(source: str, ctx: Context[Any, Any]) -> str:
        """Prove Python source, request approval, then execute with a signed receipt.

        Define main() with guarantees using installed contract helpers. Proof
        time is bounded by the operator. Use status if a call is backgrounded;
        never infer that approval is pending just because the call takes time.
        """
        if not roots:
            return (
                "exit code: 2\nNo project libraries selected. "
                "Ask the user to install a library and restart the session."
            )

        async def notify(record: dict[str, object]) -> None:
            # In-process callers may have no MCP request; status still works.
            with suppress(ValueError, RuntimeError):
                await ctx.report_progress(
                    progress=float(str(record.get("obligation", 0))),
                    message=json.dumps(record),
                )

        return await runs.run(source, notify)

    return server


if __name__ == "__main__":
    lifetime_socket = os.environ.get("CLAUZ3_SESSION_LIFETIME_SOCKET")
    if lifetime_socket:
        # MCP adapters may detach the bridge into another process group. Tie it
        # directly to the UI service so Stop and service death also stop workers.
        lifetime = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        lifetime.connect(lifetime_socket)

        def disconnected() -> None:
            try:
                lifetime.recv(1)
            finally:
                os._exit(143)

        threading.Thread(target=disconnected, daemon=True).start()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--skills-snapshot", type=Path)
    parser.add_argument("cwd", type=Path)
    parser.add_argument("roots", nargs="*", type=Path)
    args = parser.parse_args()
    skills = None
    if args.skills_snapshot is not None:
        skills = tuple(
            SkillDocument(**item)
            for item in json.loads(args.skills_snapshot.read_text())
        )
    server = create_session_tools(args.cwd, args.roots, skills)
    if args.check:
        print(json.dumps([tool.name for tool in asyncio.run(server.list_tools())]))
    else:
        server.run()
