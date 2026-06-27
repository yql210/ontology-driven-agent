"""Tests for SkillStore."""

from __future__ import annotations

import pytest

from layerkg.butler.skills.store import SkillEntity, SkillLayer, SkillStore


@pytest.fixture
def tmp_path(tmp_path):
    """Temporary path fixture."""
    return tmp_path


async def test_skill_create_and_get(tmp_path):
    """create writes skill, get reads it back."""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    skill = SkillEntity(
        skill_id="s1",
        name="detect_import_cycle",
        layer=SkillLayer.RULE,
        pattern={"tool_sequence": ["graph_query", "search_code"]},
        action={"action_type": "query_refactor"},
        confidence=0.85,
        source="inductor",
    )
    sid = await store.create(skill)
    assert sid == "s1"

    fetched = await store.get("s1")
    assert fetched is not None
    assert fetched.name == "detect_import_cycle"
    assert fetched.layer == SkillLayer.RULE
    assert fetched.confidence == 0.85
    assert fetched.hit_count == 0
    assert fetched.status == "candidate"


async def test_skill_confidence_validation(tmp_path):
    """confidence out of range raises ValueError."""
    with pytest.raises(ValueError):
        SkillEntity(
            skill_id="s1",
            name="bad",
            layer=SkillLayer.RULE,
            pattern={},
            action={},
            confidence=1.5,
            source="test",
        )


async def test_skill_update(tmp_path):
    """update modifies specified fields."""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
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


async def test_skill_soft_delete(tmp_path):
    """delete is soft delete, status -> deprecated."""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
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

    ok = await store.delete("s1")
    assert ok is True
    fetched = await store.get("s1")
    assert fetched.status == "deprecated"


async def test_list_by_layer(tmp_path):
    """list_by_layer filters by layer and status."""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    for i, layer in enumerate([SkillLayer.RULE, SkillLayer.RULE, SkillLayer.META]):
        s = SkillEntity(
            skill_id=f"s{i}",
            name=f"t{i}",
            layer=layer,
            pattern={},
            action={},
            confidence=0.5,
            source="test",
            status="active" if i < 2 else "candidate",
        )
        await store.create(s)

    rules = await store.list_by_layer(SkillLayer.RULE, status="active")
    assert len(rules) == 2


async def test_count_by_layer(tmp_path):
    """count_by_layer returns count per layer."""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    for i in range(3):
        s = SkillEntity(
            skill_id=f"s{i}",
            name=f"t{i}",
            layer=SkillLayer.RULE,
            pattern={},
            action={},
            confidence=0.5,
            source="test",
        )
        await store.create(s)
    s = SkillEntity(
        skill_id="s3",
        name="t3",
        layer=SkillLayer.META,
        pattern={},
        action={},
        confidence=0.7,
        source="test",
    )
    await store.create(s)

    counts = await store.count_by_layer()
    assert counts[SkillLayer.RULE] == 3
    assert counts[SkillLayer.META] == 1


async def test_search_by_pattern(tmp_path):
    """search_by_pattern uses json_extract to query pattern field."""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    s1 = SkillEntity(
        skill_id="s1",
        name="t1",
        layer=SkillLayer.RULE,
        pattern={"tool": "graph_query"},
        action={},
        confidence=0.5,
        source="test",
    )
    s2 = SkillEntity(
        skill_id="s2",
        name="t2",
        layer=SkillLayer.RULE,
        pattern={"tool": "search_code"},
        action={},
        confidence=0.5,
        source="test",
    )
    await store.create(s1)
    await store.create(s2)

    results = await store.search_by_pattern("tool", "graph_query")
    assert len(results) == 1
    assert results[0].skill_id == "s1"


async def test_get_candidates(tmp_path):
    """get_candidates returns skills with confidence >= min_confidence and status = candidate."""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    for i, conf in enumerate([0.3, 0.6, 0.8]):
        s = SkillEntity(
            skill_id=f"s{i}",
            name=f"t{i}",
            layer=SkillLayer.RULE,
            pattern={},
            action={},
            confidence=conf,
            source="test",
        )
        await store.create(s)

    candidates = await store.get_candidates(min_confidence=0.5)
    assert len(candidates) == 2


async def test_increment_hit_count(tmp_path):
    """increment_hit_count atomically increments hit_count."""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    s = SkillEntity(
        skill_id="s1",
        name="t1",
        layer=SkillLayer.RULE,
        pattern={},
        action={},
        confidence=0.5,
        source="test",
    )
    await store.create(s)

    await store.increment_hit_count("s1")
    await store.increment_hit_count("s1")
    fetched = await store.get("s1")
    assert fetched.hit_count == 2
