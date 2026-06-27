"""Integration tests for SkillStore with ConsistencyGuard."""

from __future__ import annotations

import json

import pytest

from layerkg.butler.consistency.guard import ConsistencyGuard
from layerkg.butler.skills import SkillEntity, SkillLayer
from layerkg.butler.skills.store import SkillStore


@pytest.fixture
def tmp_path(tmp_path):
    """Temporary path fixture."""
    return tmp_path


async def test_skill_store_with_guard(tmp_path):
    """SkillStore 的写操作通过 Guard 记录审计日志。"""
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))
    store = SkillStore(db_path=str(tmp_path / "skills.db"), guard=guard)

    skill = SkillEntity(
        skill_id="s1",
        name="test",
        layer=SkillLayer.RULE,
        pattern={"k": "v"},
        action={"a": 1},
        confidence=0.7,
        source="inductor",
    )
    await store.create(skill)

    # Guard 应记录了 create 操作
    entries = await guard.query(target_type="skill", target_id="s1")
    assert len(entries) == 1
    assert entries[0].operation == "skill_create"
    assert json.loads(entries[0].after)["name"] == "test"

    await store.update("s1", status="active")
    entries = await guard.query(target_type="skill", target_id="s1")
    assert len(entries) == 2

    await store.delete("s1")
    entries = await guard.query(target_type="skill", target_id="s1")
    assert len(entries) == 3
    last = entries[-1]
    assert last.operation == "skill_delete"


async def test_skill_store_multiple_skills_with_guard(tmp_path):
    """多个技能操作都正确记录到 Guard。"""
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))
    store = SkillStore(db_path=str(tmp_path / "skills.db"), guard=guard)

    for i in range(3):
        skill = SkillEntity(
            skill_id=f"s{i}",
            name=f"skill_{i}",
            layer=SkillLayer.RULE,
            pattern={"index": i},
            action={},
            confidence=0.5 + i * 0.1,
            source="test",
        )
        await store.create(skill)

    # 每个技能都有一条 create 记录
    all_entries = await guard.query(target_type="skill")
    assert len(all_entries) == 3

    # 查询特定技能
    s1_entries = await guard.query(target_type="skill", target_id="s1")
    assert len(s1_entries) == 1
    assert s1_entries[0].operation == "skill_create"


async def test_skill_store_update_without_guard(tmp_path):
    """没有 guard 时 update 仍然正常工作。"""
    store = SkillStore(db_path=str(tmp_path / "skills.db"), guard=None)

    skill = SkillEntity(
        skill_id="s1",
        name="test",
        layer=SkillLayer.RULE,
        pattern={},
        action={},
        confidence=0.5,
        source="test",
    )
    await store.create(skill)

    ok = await store.update("s1", confidence=0.9, status="active")
    assert ok is True

    fetched = await store.get("s1")
    assert fetched.confidence == 0.9
    assert fetched.status == "active"


async def test_skill_store_get_last_operation(tmp_path):
    """get_last_operation 返回最近的技能操作。"""
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))
    store = SkillStore(db_path=str(tmp_path / "skills.db"), guard=guard)

    skill = SkillEntity(
        skill_id="s1",
        name="test",
        layer=SkillLayer.RULE,
        pattern={},
        action={},
        confidence=0.5,
        source="test",
    )
    await store.create(skill)
    await store.update("s1", status="active")
    await store.update("s1", confidence=0.8)

    last = await guard.get_last_operation("skill", "s1")
    assert last is not None
    assert last.operation == "skill_update"


async def test_skill_store_delete_with_guard_audit(tmp_path):
    """软删除时 Guard 记录完整审计信息。"""
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))
    store = SkillStore(db_path=str(tmp_path / "skills.db"), guard=guard)

    skill = SkillEntity(
        skill_id="s1",
        name="test",
        layer=SkillLayer.META,
        pattern={},
        action={},
        confidence=0.9,
        source="test",
    )
    await store.create(skill)

    # 删除前检查状态
    fetched = await store.get("s1")
    assert fetched.status == "candidate"

    # 软删除
    await store.delete("s1")

    # 确认状态已更新
    fetched = await store.get("s1")
    assert fetched.status == "deprecated"

    # 确认审计记录
    entries = await guard.query(target_type="skill", target_id="s1")
    assert len(entries) == 2  # create + delete
    assert entries[0].operation == "skill_create"
    assert entries[1].operation == "skill_delete"


async def test_skill_store_guard_operator_tracking(tmp_path):
    """Guard 正确记录不同 operator。"""
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))
    store = SkillStore(db_path=str(tmp_path / "skills.db"), guard=guard)

    # 用不同 source 创建技能
    skill1 = SkillEntity(
        skill_id="s1",
        name="auto",
        layer=SkillLayer.RULE,
        pattern={},
        action={},
        confidence=0.6,
        source="inductor",
    )
    await store.create(skill1)

    skill2 = SkillEntity(
        skill_id="s2",
        name="manual",
        layer=SkillLayer.RULE,
        pattern={},
        action={},
        confidence=0.7,
        source="human_admin",
    )
    await store.create(skill2)

    # 按目标查询
    s1_entries = await guard.query(target_type="skill", target_id="s1")
    assert s1_entries[0].operator == "inductor"

    s2_entries = await guard.query(target_type="skill", target_id="s2")
    assert s2_entries[0].operator == "human_admin"
