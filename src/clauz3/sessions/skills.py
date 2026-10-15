"""Bounded, inert skill documents selected by the operator at startup."""

import hashlib
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_DOCUMENT_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTS = 128
MAX_ENTRIES = 4096
PREVIEW_CHARS = 1024


@dataclass(frozen=True)
class SkillDocument:
    id: str
    name: str
    preview: str
    content: str

    def record(self) -> dict[str, str]:
        return asdict(self)


def _document(path: Path, directory_fd: int) -> SkillDocument:
    # Nonblocking avoids hanging on a FIFO; O_NOFOLLOW rejects a replaced link.
    descriptor = os.open(
        "SKILL.md", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
    )
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"skill must be a regular file: {path}")
        data = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"skill exceeds {MAX_DOCUMENT_BYTES} bytes: {path}")
    content = data.decode("utf-8")
    lines = content.splitlines()
    # Return frontmatter verbatim: no YAML constructors, aliases, or interpolation.
    # Malformed/absent frontmatter falls back to the beginning of the document.
    preview = content.strip()
    if lines and lines[0] == "---" and "---" in lines[1:]:
        preview = "\n".join(lines[1 : lines.index("---", 1)]).strip()
    return SkillDocument(
        id=hashlib.sha256(str(path).encode()).hexdigest(),
        name=path.parent.name,
        preview=preview[:PREVIEW_CHARS],
        content=content,
    )


def snapshot_skills(folders: list[Path]) -> tuple[SkillDocument, ...]:
    """Read only SKILL.md files; never follow nested directory/file symlinks."""
    documents: dict[str, SkillDocument] = {}
    entries = total = 0
    for folder in folders:
        root = folder.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"skills directory is not a folder: {root}")

        def fail(error: OSError) -> None:
            raise error

        for directory, dirs, files, directory_fd in os.fwalk(
            root, follow_symlinks=False, onerror=fail
        ):
            entries += len(dirs) + len(files)
            if entries > MAX_ENTRIES:
                raise ValueError(f"skill selection exceeds {MAX_ENTRIES} entries")
            dirs.sort()
            for name in dirs:
                if (Path(directory) / name).is_symlink():
                    raise ValueError(
                        f"symlink in skills directory: {Path(directory) / name}"
                    )
            if "SKILL.md" not in files:
                continue
            path = Path(directory) / "SKILL.md"
            if path.is_symlink():
                raise ValueError(f"symlink in skills directory: {path}")
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"skill escapes selected directory: {path}")
            document = _document(path, directory_fd)
            if document.id in documents:
                continue
            documents[document.id] = document
            total += len(document.content.encode())
            if len(documents) > MAX_DOCUMENTS or total > MAX_TOTAL_BYTES:
                raise ValueError("skill selection exceeds document or total byte limit")
    return tuple(documents[key] for key in sorted(documents))
