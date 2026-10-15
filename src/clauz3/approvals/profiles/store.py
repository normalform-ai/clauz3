"""Transactional profile rules and decision history (never execution receipts).

This file has the same trust requirement as an operator's policy file: agents
and approved effects must not be allowed to modify it. Unix permissions protect
against other users, not arbitrary processes running as the operator.
"""

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import cast
from uuid import uuid4


class ProfileStore:
    def __init__(self, path: Path, *, project: Path) -> None:
        self.path = path.absolute()
        self.project = str(project.resolve())
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"unsupported approval profile schema: {version}")
            db.execute("""CREATE TABLE IF NOT EXISTS history (
                id TEXT PRIMARY KEY, project TEXT NOT NULL, created REAL NOT NULL,
                signature TEXT, basis TEXT, record TEXT NOT NULL,
                decision TEXT, rule_id TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS rules (
                id TEXT PRIMARY KEY, project TEXT NOT NULL, signature TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('allow', 'deny')),
                enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                history_id TEXT NOT NULL UNIQUE, created REAL NOT NULL)""")
            db.execute("PRAGMA user_version = 1")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def record(self, record: dict[str, object], basis: dict[str, object] | None) -> str:
        event = uuid4().hex
        # Explicit allowlist: no client extras, auth material, or receipts.
        saved = {
            key: record.get(key)
            for key in (
                "request_id",
                "program",
                "program_sha256",
                "target",
                "guarantees",
                "contract_documentation",
                "contract_descriptions",
                "coverage",
                "trusted_roots",
                "import_roots",
                "proofs",
            )
        }
        with self._connection() as db:
            db.execute(
                "INSERT INTO history VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
                (
                    event,
                    self.project,
                    time.time(),
                    basis["signature"] if basis else None,
                    json.dumps(basis) if basis else None,
                    json.dumps(saved),
                ),
            )
        return event

    def decide(
        self,
        event: str,
        decision: str,
        rule_id: str | None = None,
        *,
        remember: bool = False,
    ) -> None:
        with self._connection() as db:
            db.execute(
                "UPDATE history SET decision=?, rule_id=? WHERE id=? AND project=?",
                (decision, rule_id, event, self.project),
            )
            if remember:
                self._remember(db, event)

    def remember(self, event: str) -> str:
        """Promote a recorded human decision, without changing that decision."""
        with self._connection() as db:
            return self._remember(db, event)

    def _remember(self, db: sqlite3.Connection, event: str) -> str:
        row = db.execute(
            "SELECT * FROM history WHERE id=? AND project=?",
            (event, self.project),
        ).fetchone()
        if row is None or not row["signature"]:
            raise ValueError("this request has no verified profile signature")
        decision = row["decision"]
        if decision in {"approved_once", "approved_remember"}:
            action = "allow"
        elif decision in {"rejected", "rejected_contract"}:
            action = "deny"
        else:
            raise ValueError("only a human approval or rejection can be remembered")
        rule = uuid4().hex
        db.execute(
            """INSERT INTO rules VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(history_id) DO UPDATE SET enabled=1""",
            (rule, self.project, row["signature"], action, event, time.time()),
        )
        return str(
            db.execute("SELECT id FROM rules WHERE history_id=?", (event,)).fetchone()[
                0
            ]
        )

    def disable(self, rule: str) -> None:
        with self._connection() as db:
            result = db.execute(
                "UPDATE rules SET enabled=0 WHERE id=? AND project=?",
                (rule, self.project),
            )
            if result.rowcount != 1:
                raise ValueError("rule not found in this project")

    def match(self, signature: str) -> dict[str, object] | None:
        with self._connection() as db:
            row = db.execute(
                """SELECT * FROM rules WHERE project=? AND signature=? AND enabled=1
                ORDER BY CASE decision WHEN 'deny' THEN 0 ELSE 1 END, created, id
                LIMIT 1""",
                (self.project, signature),
            ).fetchone()
            return dict(row) if row is not None else None

    def snapshot(self) -> dict[str, object]:
        with self._connection() as db:
            rules = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM rules WHERE project=? ORDER BY created DESC",
                    (self.project,),
                )
            ]
            history = []
            for row in db.execute(
                "SELECT * FROM history WHERE project=? ORDER BY created DESC",
                (self.project,),
            ):
                item = dict(row)
                item["record"] = json.loads(item["record"])
                item["basis"] = json.loads(item["basis"]) if item["basis"] else None
                history.append(item)
            return {"project": self.project, "rules": rules, "history": history}


def objects(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value)
