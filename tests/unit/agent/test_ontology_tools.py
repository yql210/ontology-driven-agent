"""V4 ontology 工具测试 — explore_ontology / explain_constraint / suggest_alternatives。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_shapes() -> list:
    """构造两个 mock ConstraintShape 用于测试。"""
    shape1 = MagicMock()
    shape1.id = "shape:sensitive_data"
    shape1.name = "敏感数据保护"
    shape1.description = "禁止处理 restricted 数据的写入"
    shape1.enabled = True

    shape2 = MagicMock()
    shape2.id = "shape:public_read"
    shape2.name = "公开数据读取"
    shape2.description = "允许读取公开数据"
    shape2.enabled = True
    return [shape1, shape2]


def test_explore_ontology_returns_summaries(mock_shapes: list) -> None:
    """explore_ontology 返回所有 enabled Shape 的 id+name+description 摘要。"""
    mock_registry = MagicMock()
    mock_registry.all_shapes.return_value = mock_shapes
    mock_registry.__len__ = lambda self: 2

    with patch("ontoagent.agent.tools._get_shape_registry", return_value=mock_registry):
        from ontoagent.agent.tools import explore_ontology

        result = explore_ontology.invoke({})
        data = json.loads(result)

    assert data["count"] == 2
    assert len(data["shapes"]) == 2
    summary_keys = {s["id"] for s in data["shapes"]}
    assert summary_keys == {"shape:sensitive_data", "shape:public_read"}
    for item in data["shapes"]:
        assert {"id", "name", "description"} <= set(item.keys())


def test_explore_ontology_filters_disabled(mock_shapes: list) -> None:
    """explore_ontology 跳过 enabled=False 的 Shape。"""
    mock_shapes[1].enabled = False
    mock_registry = MagicMock()
    mock_registry.all_shapes.return_value = mock_shapes

    with patch("ontoagent.agent.tools._get_shape_registry", return_value=mock_registry):
        from ontoagent.agent.tools import explore_ontology

        result = explore_ontology.invoke({})
        data = json.loads(result)

    assert data["count"] == 1
    assert data["shapes"][0]["id"] == "shape:sensitive_data"


def test_explore_ontology_registry_unavailable() -> None:
    """ShapeRegistry 未启用（None）时返回友好提示。"""
    with patch("ontoagent.agent.tools._get_shape_registry", return_value=None):
        from ontoagent.agent.tools import explore_ontology

        result = explore_ontology.invoke({})
        data = json.loads(result)

    assert "info" in data or "error" in data
    assert data.get("count", 0) == 0


def test_explain_constraint_returns_full_shape(mock_shapes: list) -> None:
    """explain_constraint 返回单个 Shape 的完整信息。"""
    from ontoagent.domain.shapes import Operation, Severity, ShapeKind

    target = MagicMock()
    target.entry_type = "CodeEntity"
    target.operation = Operation.UPDATE
    target.field_filter = None

    path = MagicMock()
    path.raw = "PROCESSES_DATA -> DataAsset"
    path.is_self.return_value = False
    path.target_label = "DataAsset"
    path.max_depth = 3

    constraint = MagicMock()
    constraint.field = "sensitivity"
    constraint.operator = "in"
    constraint.value = ["restricted"]

    mock_shapes[0].target = target
    mock_shapes[0].path = path
    mock_shapes[0].constraint = constraint
    mock_shapes[0].kind = ShapeKind.OPERATIONAL
    mock_shapes[0].severity = Severity.BLOCK
    mock_shapes[0].priority = 10
    mock_shapes[0].tags = ["data-safety"]
    mock_shapes[0].suggestion = "降低敏感度后再操作"

    mock_registry = MagicMock()
    mock_registry.all_shapes.return_value = mock_shapes
    mock_registry.__contains__ = lambda self, key: key == "shape:sensitive_data"

    with patch("ontoagent.agent.tools._get_shape_registry", return_value=mock_registry):
        from ontoagent.agent.tools import explain_constraint

        result = explain_constraint.invoke({"shape_id": "shape:sensitive_data"})
        data = json.loads(result)

    assert data["id"] == "shape:sensitive_data"
    assert data["name"] == "敏感数据保护"
    assert data["target"]["entry_type"] == "CodeEntity"
    assert data["target"]["operation"] == "UPDATE"
    assert data["constraint"]["field"] == "sensitivity"
    assert data["severity"] == "block"
    assert data["path"]["raw"] == "PROCESSES_DATA -> DataAsset"
    assert data["path"]["target_label"] == "DataAsset"


def test_explain_constraint_not_found() -> None:
    """不存在的 shape_id 返回错误。"""
    mock_registry = MagicMock()
    mock_registry.all_shapes.return_value = []
    mock_registry.__contains__ = lambda self, key: False

    with patch("ontoagent.agent.tools._get_shape_registry", return_value=mock_registry):
        from ontoagent.agent.tools import explain_constraint

        result = explain_constraint.invoke({"shape_id": "shape:nonexistent"})
        data = json.loads(result)

    assert "error" in data
    assert "shape:nonexistent" in data["error"]


def test_explain_constraint_registry_unavailable() -> None:
    """ShapeRegistry 未启用时返回错误。"""
    with patch("ontoagent.agent.tools._get_shape_registry", return_value=None):
        from ontoagent.agent.tools import explain_constraint

        result = explain_constraint.invoke({"shape_id": "any"})
        data = json.loads(result)

    assert "error" in data or "info" in data


def test_suggest_alternatives_returns_same_bind_to() -> None:
    """suggest_alternatives 返回同 bind_to 的其他 intent。"""
    from ontoagent.execution.action_types import ActionConfig

    intent_map = {
        "refactor": ActionConfig(
            name="refactor",
            intent_type="refactor",
            trigger_hint="重构代码",
            bind_to="code_entity",
        ),
        "document": ActionConfig(
            name="document",
            intent_type="document",
            trigger_hint="补全文档",
            bind_to="code_entity",
        ),
        "compliance_check": ActionConfig(
            name="compliance_check",
            intent_type="compliance_check",
            trigger_hint="合规检查",
            bind_to="code_entity",
        ),
        "diagnose_alert": ActionConfig(
            name="diagnose_alert",
            intent_type="diagnose_alert",
            trigger_hint="告警诊断",
            bind_to="alert_entity",
        ),
    }

    with patch("ontoagent.agent.tools._get_intent_map", return_value=intent_map):
        from ontoagent.agent.tools import suggest_alternatives

        result = suggest_alternatives.invoke({"intent_type": "refactor", "target": "foo"})
        data = json.loads(result)

    assert data["source_intent"] == "refactor"
    assert data["resource_type"] == "code_entity"
    alt_ids = {a["intent_type"] for a in data["alternatives"]}
    assert alt_ids == {"document", "compliance_check"}
    assert "refactor" not in alt_ids
    assert "diagnose_alert" not in alt_ids


def test_suggest_alternatives_unknown_intent() -> None:
    """未知 intent_type 返回错误。"""
    with patch("ontoagent.agent.tools._get_intent_map", return_value={}):
        from ontoagent.agent.tools import suggest_alternatives

        result = suggest_alternatives.invoke({"intent_type": "unknown", "target": "foo"})
        data = json.loads(result)

    assert "error" in data


def test_suggest_alternatives_no_siblings() -> None:
    """bind_to 独占时返回空 alternatives。"""
    from ontoagent.execution.action_types import ActionConfig

    intent_map = {
        "refactor": ActionConfig(
            name="refactor",
            intent_type="refactor",
            trigger_hint="重构",
            bind_to="code_entity",
        ),
        "diagnose": ActionConfig(
            name="diagnose",
            intent_type="diagnose",
            trigger_hint="诊断",
            bind_to="alert_entity",
        ),
    }

    with patch("ontoagent.agent.tools._get_intent_map", return_value=intent_map):
        from ontoagent.agent.tools import suggest_alternatives

        result = suggest_alternatives.invoke({"intent_type": "refactor", "target": "foo"})
        data = json.loads(result)

    assert data["alternatives"] == []
    assert data["count"] == 0
