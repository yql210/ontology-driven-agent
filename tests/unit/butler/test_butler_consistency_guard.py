"""Tests for ConsistencyGuard."""

from __future__ import annotations

import json

import pytest

from ontoagent.butler.consistency.guard import ConsistencyGuard


@pytest.fixture
def tmp_path(tmp_path):
    """Temporary path fixture."""
    return tmp_path


async def test_log_operation(tmp_path):
    """log_operation writes audit log and returns entry_id."""
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))
    entry_id = await guard.log_operation(
        op="skill_create",
        target_type="skill",
        target_id="skill-123",
        before=None,
        after={"name": "test_skill", "layer": "rule"},
        operator="inductor",
    )
    assert isinstance(entry_id, str)
    assert len(entry_id) > 0


async def test_query_by_target(tmp_path):
    """query filters by target_type and target_id."""
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))
    await guard.log_operation("create", "skill", "s1", None, {"v": 1}, "butler")
    await guard.log_operation("update", "skill", "s1", {"v": 1}, {"v": 2}, "butler")
    await guard.log_operation("create", "skill", "s2", None, {"v": 1}, "butler")

    entries = await guard.query(target_type="skill", target_id="s1")
    assert len(entries) == 2
    assert entries[0].operation == "create"


async def test_get_last_operation(tmp_path):
    """get_last_operation returns the most recent operation."""
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))
    await guard.log_operation("create", "skill", "s1", None, {"v": 1}, "butler")
    await guard.log_operation("update", "skill", "s1", {"v": 1}, {"v": 2}, "butler")

    last = await guard.get_last_operation("skill", "s1")
    assert last.operation == "update"
    assert json.loads(last.after) == {"v": 2}
