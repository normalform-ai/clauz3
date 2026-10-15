"""Bounded, read-only browsing under the server-selected workspace root."""

import codecs
import errno
import os
import stat
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Any, TypedDict

from fastapi import HTTPException

MAX_PREVIEW = 256 * 1024
MAX_ENTRIES = 2000


class FileEntry(TypedDict):
    name: str
    kind: str
    size: int


def browse(root: Path, path: str, *, hidden: bool = False) -> dict[str, Any]:
    """Walk open directory descriptors so swapped symlinks cannot escape root."""
    parts = path.split("/") if path else []
    if any(part in {"", ".", ".."} for part in parts) or "\x00" in path:
        raise HTTPException(400, "Choose a path inside this workspace.")
    try:
        with ExitStack() as stack:
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, descriptor)
            for index, part in enumerate(parts):
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if index < len(parts) - 1:
                    flags |= os.O_DIRECTORY
                descriptor = os.open(part, flags, dir_fd=descriptor)
                stack.callback(os.close, descriptor)
            info = os.fstat(descriptor)
            if stat.S_ISDIR(info.st_mode):
                entries: list[FileEntry] = []
                truncated = False
                with os.scandir(descriptor) as listing:
                    for entry in listing:
                        if not hidden and entry.name.startswith("."):
                            continue
                        if len(entries) == MAX_ENTRIES:
                            truncated = True
                            break
                        try:
                            metadata = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue  # A worker may be updating this directory.
                        kind = (
                            "directory"
                            if stat.S_ISDIR(metadata.st_mode)
                            else "file"
                            if stat.S_ISREG(metadata.st_mode)
                            else "unavailable"
                        )
                        name = entry.name
                        try:
                            name.encode("utf-8")
                        except UnicodeEncodeError:
                            # POSIX permits byte names that JSON/URLs cannot represent.
                            name = name.encode("utf-8", errors="replace").decode(
                                "utf-8"
                            )
                            kind = "unavailable"
                        entries.append(
                            {"name": name, "kind": kind, "size": metadata.st_size}
                        )
                entries.sort(
                    key=lambda e: (e["kind"] != "directory", e["name"].lower())
                )
                return {
                    "kind": "directory",
                    "path": path,
                    "entries": entries,
                    "truncated": truncated,
                }
            if not stat.S_ISREG(info.st_mode):
                raise HTTPException(415, "Only regular files can be previewed.")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                data = stream.read(MAX_PREVIEW + 1)
            truncated = len(data) > MAX_PREVIEW
            content = None
            if b"\x00" not in data:
                with suppress(UnicodeDecodeError):
                    # An incremental decoder tolerates a cut-off final UTF-8 codepoint.
                    content = codecs.getincrementaldecoder("utf-8")().decode(
                        data[:MAX_PREVIEW], final=not truncated
                    )
            return {
                "kind": "file",
                "path": path,
                "size": info.st_size,
                "content": content,
                "truncated": truncated,
            }
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise HTTPException(404, "This file or folder no longer exists.") from None
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise HTTPException(403, "Symbolic links cannot be opened.") from None
        raise HTTPException(403, "This file or folder cannot be read.") from None
