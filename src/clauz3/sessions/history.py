"""Operator-owned conversation history, separate from approval authority."""

import fcntl
import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4


class ConversationStore:
    def __init__(self, path: Path, project: str) -> None:
        self.path = path
        self.project = str(Path(project).resolve())
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
                updated REAL NOT NULL, state TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS messages (
                sequence INTEGER PRIMARY KEY, conversation TEXT NOT NULL,
                message TEXT NOT NULL)""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS messages_chat ON messages(conversation)"
            )

    @contextmanager
    def lease(self) -> Iterator[None]:
        """Only one UI service may resume this project's agent sessions at once."""
        digest = sha256(self.project.encode()).hexdigest()
        path = self.path.with_name(f"workspace-{digest}.lock")
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(
                    "This project already has a running workspace UI"
                ) from None
            yield
        finally:
            os.close(descriptor)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def create(self) -> str:
        identifier = uuid4().hex
        with self.connection() as db:
            db.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, '{}')",
                (identifier, self.project, "New conversation", time.time()),
            )
        return identifier

    def list(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id, title, updated FROM conversations WHERE project=? "
                    "ORDER BY updated DESC",
                    (self.project,),
                )
            ]

    def load(self, identifier: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM conversations WHERE id=? AND project=?",
                (identifier, self.project),
            ).fetchone()
            if row is None:
                raise ValueError("Conversation not found in this workspace")
            return {
                **dict(row),
                "state": json.loads(row["state"]),
                "history": [
                    json.loads(item[0])
                    for item in db.execute(
                        "SELECT message FROM messages WHERE conversation=? "
                        "ORDER BY sequence",
                        (identifier,),
                    )
                ],
            }

    def append(self, identifier: str, message: dict[str, Any]) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO messages(conversation, message) VALUES (?, ?)",
                (identifier, json.dumps(message)),
            )
            db.execute(
                "UPDATE conversations SET updated=? WHERE id=? AND project=?",
                (time.time(), identifier, self.project),
            )
            if message["method"] == "clauz3/user" and message["params"]["turn"] == 1:
                title = " ".join(message["params"]["text"].split())[:80]
                db.execute(
                    "UPDATE conversations SET title=? WHERE id=? AND project=?",
                    (title, identifier, self.project),
                )

    def save(self, identifier: str, state: dict[str, Any]) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE conversations SET state=? WHERE id=? AND project=?",
                (json.dumps(state), identifier, self.project),
            )

    def rename(self, identifier: str, title: str) -> None:
        if not title.strip() or len(title) > 120:
            raise ValueError("Enter a conversation title of 1–120 characters")
        with self.connection() as db:
            if (
                db.execute(
                    "UPDATE conversations SET title=? WHERE id=? AND project=?",
                    (title.strip(), identifier, self.project),
                ).rowcount
                != 1
            ):
                raise ValueError("Conversation not found in this workspace")
