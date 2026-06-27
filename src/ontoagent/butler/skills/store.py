"""Skill store for managing skill patterns."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from ontoagent.butler.consistency.guard import ConsistencyGuard


class SkillLayer(Enum):
    """Skill layer levels."""

    RULE = "rule"
    META = "meta"
    HARNESS = "harness"


@dataclass
class SkillEntity:
    """Skill pattern entity."""

    skill_id: str
    name: str
    layer: SkillLayer
    pattern: dict
    action: dict
    confidence: float
    source: str
    status: str = "candidate"
    hit_count: int = 0
    version: int = 1
    parent_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        """Validate confidence range."""
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {self.confidence}")
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()
        if not self.updated_at:
            self.updated_at = datetime.now(UTC).isoformat()


class SkillStore:
    """Store for skill patterns."""

    def __init__(
        self,
        db_path: str,
        guard: ConsistencyGuard | None = None,
    ) -> None:
        """Initialize with database path and optional guard."""
        self._db_path = Path(db_path)
        self._guard = guard
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
                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    pattern_json TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'candidate',
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    parent_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (confidence >= 0.0 AND confidence <= 1.0)
                )
            """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_skills_layer
                ON skills(layer, status)
            """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_skills_status
                ON skills(status, confidence)
            """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_skills_created
                ON skills(created_at DESC)
            """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_skills_parent
                ON skills(parent_id)
            """
            )
            db.commit()
        finally:
            db.close()

    async def create(self, skill: SkillEntity) -> str:
        """Create a new skill and return skill_id."""
        if self._guard:
            await self._guard.log_operation(
                op="skill_create",
                target_type="skill",
                target_id=skill.skill_id,
                before=None,
                after={
                    "name": skill.name,
                    "layer": skill.layer.value,
                    "pattern": skill.pattern,
                    "action": skill.action,
                    "confidence": skill.confidence,
                },
                operator=skill.source,
            )

        def _write() -> None:
            db = self._get_db()
            try:
                db.execute(
                    """
                    INSERT INTO skills
                    (skill_id, name, layer, pattern_json, action_json, confidence, source, status, hit_count, version, parent_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        skill.skill_id,
                        skill.name,
                        skill.layer.value,
                        json.dumps(skill.pattern),
                        json.dumps(skill.action),
                        skill.confidence,
                        skill.source,
                        skill.status,
                        skill.hit_count,
                        skill.version,
                        skill.parent_id,
                        skill.created_at,
                        skill.updated_at,
                    ),
                )
                db.commit()
            finally:
                db.close()

        await asyncio.to_thread(_write)
        return skill.skill_id

    async def get(self, skill_id: str) -> SkillEntity | None:
        """Get a skill by skill_id."""

        def _read() -> SkillEntity | None:
            db = self._get_db()
            try:
                row = db.execute(
                    """
                    SELECT skill_id, name, layer, pattern_json, action_json, confidence, source, status, hit_count, version, parent_id, created_at, updated_at
                    FROM skills
                    WHERE skill_id = ?
                """,
                    (skill_id,),
                ).fetchone()

                if not row:
                    return None

                return SkillEntity(
                    skill_id=row[0],
                    name=row[1],
                    layer=SkillLayer(row[2]),
                    pattern=json.loads(row[3]),
                    action=json.loads(row[4]),
                    confidence=row[5],
                    source=row[6],
                    status=row[7],
                    hit_count=row[8],
                    version=row[9],
                    parent_id=row[10],
                    created_at=row[11],
                    updated_at=row[12],
                )
            finally:
                db.close()

        return await asyncio.to_thread(_read)

    async def update(self, skill_id: str, **kwargs) -> bool:
        """Update specified fields of a skill."""
        valid_fields = {"name", "confidence", "status", "hit_count", "version", "parent_id"}
        updates = {}
        set_parts = []
        values = []

        for k, v in kwargs.items():
            if k == "pattern":
                set_parts.append("pattern_json = ?")
                values.append(json.dumps(v))
                updates["pattern"] = v
            elif k == "action":
                set_parts.append("action_json = ?")
                values.append(json.dumps(v))
                updates["action"] = v
            elif k in valid_fields:
                set_parts.append(f"{k} = ?")
                values.append(v)
                updates[k] = v

        if not set_parts:
            return False

        set_clause = ", ".join(set_parts)
        values.append(datetime.now(UTC).isoformat())  # updated_at
        values.append(skill_id)

        def _write() -> bool:
            db = self._get_db()
            try:
                cursor = db.execute(
                    f"UPDATE skills SET {set_clause}, updated_at = ? WHERE skill_id = ?",
                    values,
                )
                db.commit()
                return cursor.rowcount > 0
            finally:
                db.close()

        ok = await asyncio.to_thread(_write)
        if ok and self._guard:
            fetched = await self.get(skill_id)
            if fetched:
                await self._guard.log_operation(
                    op="skill_update",
                    target_type="skill",
                    target_id=skill_id,
                    before=None,
                    after={"updates": updates},
                    operator="system",
                )
        return ok

    async def delete(self, skill_id: str) -> bool:
        """Soft delete a skill by setting status to deprecated."""

        def _write() -> bool:
            db = self._get_db()
            try:
                cursor = db.execute(
                    "UPDATE skills SET status = 'deprecated', updated_at = ? WHERE skill_id = ?",
                    (datetime.now(UTC).isoformat(), skill_id),
                )
                db.commit()
                return cursor.rowcount > 0
            finally:
                db.close()

        ok = await asyncio.to_thread(_write)
        if ok and self._guard:
            await self._guard.log_operation(
                op="skill_delete",
                target_type="skill",
                target_id=skill_id,
                before=None,
                after={"status": "deprecated"},
                operator="system",
            )
        return ok

    async def list_by_layer(
        self,
        layer: SkillLayer,
        status: str | None = None,
    ) -> list[SkillEntity]:
        """List skills by layer and optional status filter."""

        def _query() -> list[SkillEntity]:
            db = self._get_db()
            try:
                if status:
                    rows = db.execute(
                        """
                        SELECT skill_id, name, layer, pattern_json, action_json, confidence, source, status, hit_count, version, parent_id, created_at, updated_at
                        FROM skills
                        WHERE layer = ? AND status = ?
                        ORDER BY created_at DESC
                    """,
                        (layer.value, status),
                    ).fetchall()
                else:
                    rows = db.execute(
                        """
                        SELECT skill_id, name, layer, pattern_json, action_json, confidence, source, status, hit_count, version, parent_id, created_at, updated_at
                        FROM skills
                        WHERE layer = ?
                        ORDER BY created_at DESC
                    """,
                        (layer.value,),
                    ).fetchall()

                return [
                    SkillEntity(
                        skill_id=row[0],
                        name=row[1],
                        layer=SkillLayer(row[2]),
                        pattern=json.loads(row[3]),
                        action=json.loads(row[4]),
                        confidence=row[5],
                        source=row[6],
                        status=row[7],
                        hit_count=row[8],
                        version=row[9],
                        parent_id=row[10],
                        created_at=row[11],
                        updated_at=row[12],
                    )
                    for row in rows
                ]
            finally:
                db.close()

        return await asyncio.to_thread(_query)

    async def count_by_layer(self) -> dict[SkillLayer, int]:
        """Return count of skills per layer."""

        def _query() -> dict[SkillLayer, int]:
            db = self._get_db()
            try:
                rows = db.execute(
                    """
                    SELECT layer, COUNT(*) as count
                    FROM skills
                    GROUP BY layer
                """
                ).fetchall()

                counts: dict[SkillLayer, int] = {
                    SkillLayer.RULE: 0,
                    SkillLayer.META: 0,
                    SkillLayer.HARNESS: 0,
                }
                for layer_str, count in rows:
                    counts[SkillLayer(layer_str)] = count
                return counts
            finally:
                db.close()

        return await asyncio.to_thread(_query)

    async def search_by_pattern(
        self,
        key: str,
        value: str | int | float,
    ) -> list[SkillEntity]:
        """Search skills by pattern field using json_extract."""

        def _query() -> list[SkillEntity]:
            db = self._get_db()
            try:
                rows = db.execute(
                    """
                    SELECT skill_id, name, layer, pattern_json, action_json, confidence, source, status, hit_count, version, parent_id, created_at, updated_at
                    FROM skills
                    WHERE json_extract(pattern_json, '$.' || ?) = ?
                    ORDER BY created_at DESC
                """,
                    (key, value),
                ).fetchall()

                return [
                    SkillEntity(
                        skill_id=row[0],
                        name=row[1],
                        layer=SkillLayer(row[2]),
                        pattern=json.loads(row[3]),
                        action=json.loads(row[4]),
                        confidence=row[5],
                        source=row[6],
                        status=row[7],
                        hit_count=row[8],
                        version=row[9],
                        parent_id=row[10],
                        created_at=row[11],
                        updated_at=row[12],
                    )
                    for row in rows
                ]
            finally:
                db.close()

        return await asyncio.to_thread(_query)

    async def get_candidates(self, min_confidence: float = 0.5) -> list[SkillEntity]:
        """Get candidate skills with confidence >= min_confidence."""

        def _query() -> list[SkillEntity]:
            db = self._get_db()
            try:
                rows = db.execute(
                    """
                    SELECT skill_id, name, layer, pattern_json, action_json, confidence, source, status, hit_count, version, parent_id, created_at, updated_at
                    FROM skills
                    WHERE status = 'candidate' AND confidence >= ?
                    ORDER BY confidence DESC
                """,
                    (min_confidence,),
                ).fetchall()

                return [
                    SkillEntity(
                        skill_id=row[0],
                        name=row[1],
                        layer=SkillLayer(row[2]),
                        pattern=json.loads(row[3]),
                        action=json.loads(row[4]),
                        confidence=row[5],
                        source=row[6],
                        status=row[7],
                        hit_count=row[8],
                        version=row[9],
                        parent_id=row[10],
                        created_at=row[11],
                        updated_at=row[12],
                    )
                    for row in rows
                ]
            finally:
                db.close()

        return await asyncio.to_thread(_query)

    async def increment_hit_count(self, skill_id: str) -> bool:
        """Atomically increment hit_count for a skill."""

        def _write() -> bool:
            db = self._get_db()
            try:
                cursor = db.execute(
                    "UPDATE skills SET hit_count = hit_count + 1 WHERE skill_id = ?",
                    (skill_id,),
                )
                db.commit()
                return cursor.rowcount > 0
            finally:
                db.close()

        return await asyncio.to_thread(_write)
