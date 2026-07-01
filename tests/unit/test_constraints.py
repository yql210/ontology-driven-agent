from __future__ import annotations

import dataclasses

import pytest

from ontoagent.domain.exceptions import ConstraintViolationError
from ontoagent.domain.schema import (
    RELATION_CONSTRAINTS,
    VALID_RELATION_TYPES,
    RelationConstraint,
    validate_relation_constraint,
)

# =============================================================================
# A. 约束定义完整性（4 个测试）
# =============================================================================


@pytest.mark.unit
def test_all_relation_types_have_constraints():
    """每种 VALID_RELATION_TYPES 都有对应的 RELATION_CONSTRAINTS 条目。"""
    for rel_type in VALID_RELATION_TYPES:
        assert rel_type in RELATION_CONSTRAINTS, f"关系类型 '{rel_type}' 在 RELATION_CONSTRAINTS 中缺失约束定义"


@pytest.mark.unit
def test_all_constraint_domains_are_valid_entity_labels():
    """每个约束的 domain 值都是合法的实体标签名。"""
    valid_entity_labels = {
        "CodeEntity",
        "ConceptEntity",
        "DocEntity",
        "ResourceEntity",
        "ModuleEntity",
        "ChangeSetEntity",
        "LogEntity",
        "AlertEntity",
        "ServiceEntity",
        "DataAsset",
        "ComplianceItem",
        "CapabilityEntity",
        "ProcessEntity",
    }

    for rel_type, constraint in RELATION_CONSTRAINTS.items():
        domain = constraint.domain
        allowed_domain = {domain} if isinstance(domain, str) else domain
        for label in allowed_domain:
            assert label in valid_entity_labels, f"关系 '{rel_type}' 的 domain 包含非法实体标签: '{label}'"


@pytest.mark.unit
def test_all_constraint_ranges_are_valid_entity_labels():
    """每个约束的 range 值都是合法的实体标签名。"""
    valid_entity_labels = {
        "CodeEntity",
        "ConceptEntity",
        "DocEntity",
        "ResourceEntity",
        "ModuleEntity",
        "ChangeSetEntity",
        "LogEntity",
        "AlertEntity",
        "ServiceEntity",
        "DataAsset",
        "ComplianceItem",
        "CapabilityEntity",
        "ProcessEntity",
    }

    for rel_type, constraint in RELATION_CONSTRAINTS.items():
        range_val = constraint.range
        allowed_range = {range_val} if isinstance(range_val, str) else range_val
        for label in allowed_range:
            assert label in valid_entity_labels, f"关系 '{rel_type}' 的 range 包含非法实体标签: '{label}'"


@pytest.mark.unit
def test_relation_constraint_is_dataclass():
    """RelationConstraint 是 dataclass。"""
    assert dataclasses.is_dataclass(RelationConstraint)


# =============================================================================
# B. domain/range 校验（8 个测试）
# =============================================================================


@pytest.mark.unit
def test_calls_codeentity_to_codeentity_passes():
    """calls: CodeEntity→CodeEntity 通过校验。"""
    # 不应抛出异常
    validate_relation_constraint("calls", "CodeEntity", "CodeEntity")


@pytest.mark.unit
def test_calls_docentity_to_codeentity_fails():
    """calls: DocEntity→CodeEntity 失败（domain 不匹配）。"""
    with pytest.raises(ConstraintViolationError) as exc_info:
        validate_relation_constraint("calls", "DocEntity", "CodeEntity")
    assert "源实体必须是 {'CodeEntity'}" in str(exc_info.value)
    assert "实际为 'DocEntity'" in str(exc_info.value)


@pytest.mark.unit
def test_contains_moduleentity_to_codeentity_passes():
    """contains: ModuleEntity→CodeEntity 通过校验。"""
    validate_relation_constraint("contains", "ModuleEntity", "CodeEntity")


@pytest.mark.unit
def test_contains_moduleentity_to_changesetentity_fails():
    """contains: ModuleEntity→ChangeSetEntity 失败（range 不匹配）。"""
    with pytest.raises(ConstraintViolationError) as exc_info:
        validate_relation_constraint("contains", "ModuleEntity", "ChangeSetEntity")
    assert "目标实体必须是" in str(exc_info.value)
    assert "实际为 'ChangeSetEntity'" in str(exc_info.value)


@pytest.mark.unit
def test_changed_in_codeentity_to_changesetentity_passes():
    """changed_in: CodeEntity→ChangeSetEntity 通过校验。"""
    validate_relation_constraint("changed_in", "CodeEntity", "ChangeSetEntity")


@pytest.mark.unit
def test_changed_in_changesetentity_to_codeentity_fails():
    """changed_in: ChangeSetEntity→CodeEntity 失败（方向反了，domain 不匹配）。"""
    with pytest.raises(ConstraintViolationError) as exc_info:
        validate_relation_constraint("changed_in", "ChangeSetEntity", "CodeEntity")
    assert "源实体必须是" in str(exc_info.value)
    assert "实际为 'ChangeSetEntity'" in str(exc_info.value)


@pytest.mark.unit
def test_describes_docentity_to_codeentity_passes():
    """describes: DocEntity→CodeEntity 通过校验。"""
    validate_relation_constraint("describes", "DocEntity", "CodeEntity")


@pytest.mark.unit
def test_affects_changesetentity_to_conceptentity_passes():
    """affects: ChangeSetEntity→ConceptEntity 通过校验。"""
    validate_relation_constraint("affects", "ChangeSetEntity", "ConceptEntity")


# =============================================================================
# C. 边界和向后兼容（4 个测试）
# =============================================================================


@pytest.mark.unit
def test_unknown_relation_type_does_not_raise():
    """未知关系类型不抛异常（向后兼容）。"""
    # "future_relation" 不在 RELATION_CONSTRAINTS 中，不应抛出异常
    validate_relation_constraint("future_relation", "AnyEntity", "AnyOtherEntity")


@pytest.mark.unit
def test_empty_source_label_raises_constraint_error():
    """source_label 为空字符串时抛出 ConstraintViolationError。"""
    with pytest.raises(ConstraintViolationError) as exc_info:
        validate_relation_constraint("calls", "", "CodeEntity")
    assert "实际为 ''" in str(exc_info.value)


@pytest.mark.unit
def test_empty_target_label_raises_constraint_error():
    """target_label 为空字符串时抛出 ConstraintViolationError。"""
    with pytest.raises(ConstraintViolationError) as exc_info:
        validate_relation_constraint("calls", "CodeEntity", "")
    assert "实际为 ''" in str(exc_info.value)


@pytest.mark.unit
def test_domain_and_range_handle_string_and_set():
    """domain/range 为单字符串 vs 集合都能正确处理。"""
    # 单字符串 domain/range（calls 的 domain 和 range 都是单字符串）
    validate_relation_constraint("calls", "CodeEntity", "CodeEntity")

    # 集合 domain/range（contains 的 domain 和 range 都是集合）
    validate_relation_constraint("contains", "ModuleEntity", "DocEntity")
    validate_relation_constraint("contains", "CodeEntity", "ResourceEntity")

    # 混合：domain 是集合，range 是单字符串
    validate_relation_constraint("changed_in", "ResourceEntity", "ChangeSetEntity")

    # 混合：domain 是单字符串，range 是集合
    validate_relation_constraint("affects", "ChangeSetEntity", "DocEntity")
