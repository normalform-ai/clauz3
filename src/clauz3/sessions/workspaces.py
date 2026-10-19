"""Operator-owned recent folders and bundled, disposable tutorial workspaces."""

import json
import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from importlib.resources import files
from pathlib import Path
from typing import Any


class Workspaces:
    def __init__(self, storage: Path) -> None:
        self.storage = storage.resolve()
        self.storage.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.storage / "recent-workspaces.sqlite"
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS workspaces "
                "(path TEXT PRIMARY KEY, used REAL NOT NULL)"
            )

    @property
    def email(self) -> Path:
        return self.storage / "tutorials" / "email"

    def is_tutorial(self, path: Path) -> bool:
        return path.absolute() == self.email.absolute()

    def remember(self, path: Path) -> None:
        if self.is_tutorial(path):
            return
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO workspaces VALUES (?, ?)",
                (str(path.resolve()), time.time()),
            )

    def snapshot(self, current: str) -> dict[str, Any]:
        with closing(sqlite3.connect(self.path)) as db:
            recent = [
                row[0]
                for row in db.execute(
                    "SELECT path FROM workspaces ORDER BY used DESC LIMIT 30"
                )
            ]
        return {
            "current": current,
            "recent": [
                {
                    "path": path,
                    "name": Path(path).name,
                    "available": Path(path).is_dir(),
                }
                for path in recent
            ],
            "tutorial": self.is_tutorial(Path(current)),
            "tutorials": [{"id": "email", "name": "Email approvals"}],
        }

    def target(self, payload: dict[str, Any]) -> Path:
        if payload.get("tutorial") == "email":
            # Never follow links when creating or resetting our managed workspace.
            for path in (self.storage, self.email.parent, self.email):
                if path.is_symlink():
                    raise ValueError("Tutorial directories must not be symbolic links")
            return self.email.absolute()
        if payload.get("tutorial"):
            raise ValueError("Unknown tutorial")
        raw = payload.get("path")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Enter an existing folder's absolute path")
        path = Path(raw).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("Choose an existing folder using its absolute path")
        path = path.resolve()
        # Tutorial paths must go through the managed template workflow.
        if path.is_relative_to(self.email.parent.resolve()):
            raise ValueError("Choose the tutorial from the Tutorials list")
        return path

    def prepare_email(self, *, reset: bool = False) -> Path:
        """Called only while holding the tutorial's project lease."""
        target = self.target({"tutorial": "email"})
        if target.exists() and not reset:
            return target
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Build first so a copy failure leaves the existing tutorial intact.
        with tempfile.TemporaryDirectory(prefix=".email-", dir=target.parent) as temp:
            staging = Path(temp) / "workspace"
            source = files("clauz3") / "tutorials/email"
            shutil.copytree(
                str(source),
                staging,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            (staging / ".clauz3-tutorial.json").write_text(
                json.dumps({"id": "email", "version": 1}) + "\n"
            )
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
        return target
