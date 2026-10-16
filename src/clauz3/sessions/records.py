"""Non-secret resume metadata and an exclusive lease for each conversation."""

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

from clauz3.libraries.discovery import global_library_root
from clauz3.libraries.layout import tools_dirs
from clauz3.sessions.providers import PROVIDERS
from clauz3.sessions.skills import SkillDocument


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    cwd: str
    agent: str
    model: str | None
    unrestricted: bool
    policy: str | None
    skills_dirs: list[str]
    selection: dict[str, str]


def _path(session_id: str, suffix: str) -> Path:
    if str(UUID(session_id)) != session_id:
        raise ValueError("session ID must be a canonical UUID")
    directory = global_library_root() / "sessions"
    if directory.is_symlink():
        raise ValueError("session metadata directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / f"{session_id}.{suffix}"


@contextmanager
def session_lease(session_id: str) -> Iterator[None]:
    import fcntl

    descriptor = os.open(
        _path(session_id, "lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "session is already running; exit it before resuming"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def save_session(record: SessionRecord) -> None:
    # A record is created once, before the harness starts, under its lease.
    descriptor = os.open(
        _path(record.session_id, "json"),
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"version": 1, **asdict(record)}, stream, indent=2)


def load_session(session_id: str) -> SessionRecord:
    path = _path(session_id, "json")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor) as stream:
            data = json.loads(stream.read(65537))
    except FileNotFoundError as exc:
        raise ValueError(f"unknown clauz3 session: {session_id}") from exc
    if not isinstance(data, dict) or data.pop("version", None) != 1:
        raise ValueError("invalid session metadata version")
    fields = {
        "session_id",
        "cwd",
        "agent",
        "model",
        "unrestricted",
        "policy",
        "skills_dirs",
        "selection",
    }
    if set(data) != fields:
        raise ValueError("invalid session metadata fields")
    if (
        data["session_id"] != session_id
        or not isinstance(data["agent"], str)
        or data["agent"] not in PROVIDERS
        or not isinstance(data["cwd"], str)
        or not Path(data["cwd"]).is_absolute()
        or type(data["unrestricted"]) is not bool
        or any(
            data[key] is not None and not isinstance(data[key], str)
            for key in ("model", "policy")
        )
        or not isinstance(data["skills_dirs"], list)
        or not all(
            isinstance(item, str) and Path(item).is_absolute()
            for item in data["skills_dirs"]
        )
        or not isinstance(data["selection"], dict)
        or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in data["selection"].items()
        )
    ):
        raise ValueError("invalid session metadata values")
    return SessionRecord(**data)


def selection_fingerprints(
    cwd: Path, roots: list[Path], policy: Path | None, skills: tuple[SkillDocument, ...]
) -> dict[str, str]:
    files: dict[str, str] = {}
    # Include package initializers outside individual trusted roots as well as
    # the trusted source. Adding/removing a library changes the root inventory.
    sources = {path for tools in tools_dirs(cwd) for path in tools.rglob("*.py")}
    for root in roots:
        if not root.resolve().is_relative_to(cwd):
            raise ValueError(f"library root escapes project: {root}")
        sources.update(root.rglob("*.py"))
        for parent in root.parents:
            if not parent.is_relative_to(cwd):
                break
            initializer = parent / "__init__.py"
            if initializer.is_file():
                sources.add(initializer)
    for path in sorted(sources):
        if not path.resolve().is_relative_to(cwd):
            raise ValueError(f"library source escapes project: {path}")
        files[str(path.relative_to(cwd))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    tools = json.dumps(
        {"roots": {str(root): str(root.resolve()) for root in roots}, "files": files},
        sort_keys=True,
    )
    documents = json.dumps([s.record() for s in skills], sort_keys=True)
    return {
        "libraries": hashlib.sha256(tools.encode()).hexdigest(),
        "policy": hashlib.sha256(policy.read_bytes()).hexdigest() if policy else "none",
        "skill documents": hashlib.sha256(documents.encode()).hexdigest(),
    }
