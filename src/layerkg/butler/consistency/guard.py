"""Consistency guard with audit logging."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


@dataclass
class AuditEntry:
    """Audit log entry."""

    entry_id: str
    operation: str
    target_type: str
    target_id: str
    before: str | None
    after: str | None
    operator: str
    timestamp: str


class ConsistencyGuard:
    """Consistency guard with audit logging."""

    def __init__(self, db_path: str) -> None:
        """Initialize with database path."""
        self._db_path = Path(db_path)
        self._init_db()

    def _get_db(self) -> sqlite3.Connection:
        """Get a new SQLite connection with WAL mode."""
        db = sqlite3.connect(str(self._db_path))
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _init_db(self) -> None:
        """Initialize database schema."""
        db = self._get_db()
        try:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    entry_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    operator TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_target
                ON audit_log(target_type, target_id)
            """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON audit_log(timestamp DESC)
            """
            )
            db.commit()
        finally:
            db.close()

    async def log_operation(
        self,
        op: str,
        target_type: str,
        target_id: str,
        before: dict | None,
        after: dict | None,
        operator: str,
    ) -> str:
        """Log an operation and return entry_id."""
        entry_id = str(uuid4())
        timestamp = datetime.now(UTC).isoformat()

        def _write() -> None:
            db = self._get_db()
            try:
                db.execute(
                    """
                    INSERT INTO audit_log
                    (entry_id, operation, target_type, target_id, before_json, after_json, operator, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        entry_id,
                        op,
                        target_type,
                        target_id,
                        json.dumps(before) if before is not None else None,
                        json.dumps(after) if after is not None else None,
                        operator,
                        timestamp,
                    ),
                )
                db.commit()
            finally:
                db.close()

        await asyncio.to_thread(_write)
        return entry_id

    async def query(
        self,
        target_type: str,
        target_id: str | None = None,
    ) -> list[AuditEntry]:
        """Query audit log by target_type and optionally target_id."""

        def _query() -> list[AuditEntry]:
            db = self._get_db()
            try:
                if target_id:
                    rows = db.execute(
                        """
                        SELECT entry_id, operation, target_type, target_id, before_json, after_json, operator, timestamp
                        FROM audit_log
                        WHERE target_type = ? AND target_id = ?
                        ORDER BY rowid ASC
                    """,
                        (target_type, target_id),
                    ).fetchall()
                else:
                    rows = db.execute(
                        """
                        SELECT entry_id, operation, target_type, target_id, before_json, after_json, operator, timestamp
                        FROM audit_log
                        WHERE target_type = ?
                        ORDER BY rowid ASC
                    """,
                        (target_type,),
                    ).fetchall()

                return [
                    AuditEntry(
                        entry_id=row[0],
                        operation=row[1],
                        target_type=row[2],
                        target_id=row[3],
                        before=row[4],
                        after=row[5],
                        operator=row[6],
                        timestamp=row[7],
                    )
                    for row in rows
                ]
            finally:
                db.close()

        return await asyncio.to_thread(_query)

    async def get_last_operation(
        self,
        target_type: str,
        target_id: str,
    ) -> AuditEntry | None:
        """Get the most recent operation for a target."""

        def _query() -> AuditEntry | None:
            db = self._get_db()
            try:
                row = db.execute(
                    """
                    SELECT entry_id, operation, target_type, target_id, before_json, after_json, operator, timestamp
                    FROM audit_log
                    WHERE target_type = ? AND target_id = ?
                    ORDER BY rowid DESC
                    LIMIT 1
                """,
                    (target_type, target_id),
                ).fetchone()

                if not row:
                    return None

                return AuditEntry(
                    entry_id=row[0],
                    operation=row[1],
                    target_type=row[2],
                    target_id=row[3],
                    before=row[4],
                    after=row[5],
                    operator=row[6],
                    timestamp=row[7],
                )
            finally:
                db.close()

        return await asyncio.to_thread(_query)
