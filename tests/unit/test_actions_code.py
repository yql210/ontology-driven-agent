"""Code Action Function 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from layerkg.actions.code import (
    extract_interface,
    reduce_complexity,
    split_large_function,
)


# --- Fixtures ---


@pytest.fixture
def mock_graph_store() -> MagicMock:
    """创建 mock GraphStore，返回一个大型函数节点。"""
    store = MagicMock()
    store.get_node.return_value = {
        "id": "UserService.login",
        "name": "UserService.login",
        "labels": ["CodeEntity"],
        "entityType": "function",
        "lines": 150,
        "branches": 20,
    }
    return store


# --- split_large_function 测试 ---


class TestSplitLargeFunction:
    def test_split_large_function_analysis(self, mock_graph_store: MagicMock) -> None:
        """大函数分析成功，返回拆分建议。"""
        result = split_large_function(
            entity_id="UserService.login",
            context={"lines": 150, "branches": 20, "max_lines": 100},
            graph_store=mock_graph_store,
        )

        assert result["success"] is True
        assert result["entity_id"] == "UserService.login"
        assert result["side_effects"] == []

        analysis = result["analysis"]
        assert analysis["total_lines"] == 150
        assert analysis["branch_count"] == 20
        assert analysis["complexity"] > 0.0
        assert len(analysis["suggested_splits"]) >= 1

        # 150 lines + 20 branches 应该产生至少 2 个建议
        split_names = [s["name"] for s in analysis["suggested_splits"]]
        assert "_validate_inputs" in split_names
        assert "_process_core" in split_names

    def test_split_large_function_entity_not_found(self, mock_graph_store: MagicMock) -> None:
        """实体不存在抛 ValueError。"""
        mock_graph_store.get_node.return_value = None
        with pytest.raises(ValueError, match="Entity not found"):
            split_large_function(
                entity_id="nonexistent",
                context={"lines": 150, "branches": 20},
                graph_store=mock_graph_store,
            )

    def test_split_large_function_no_split_needed(self, mock_graph_store: MagicMock) -> None:
        """小函数不需要拆分，抛 ValueError。"""
        with pytest.raises(ValueError, match="does not exceed max_lines"):
            split_large_function(
                entity_id="UserService.login",
                context={"lines": 50, "branches": 5, "max_lines": 100},
                graph_store=mock_graph_store,
            )

    def test_split_large_function_reads_from_node(self, mock_graph_store: MagicMock) -> None:
        """context 中没有 lines/branches 时从节点属性读取。"""
        result = split_large_function(
            entity_id="UserService.login",
            context={"max_lines": 50},  # 不提供 lines，从 node 读
            graph_store=mock_graph_store,
        )
        assert result["success"] is True
        assert result["analysis"]["total_lines"] == 150  # 来自 node

    def test_split_large_function_complexity_range(self, mock_graph_store: MagicMock) -> None:
        """复杂度在 0-1 范围内。"""
        for lines, branches in [(120, 5), (200, 30), (150, 15)]:
            result = split_large_function(
                entity_id="UserService.login",
                context={"lines": lines, "branches": branches, "max_lines": 100},
                graph_store=mock_graph_store,
            )
            complexity = result["analysis"]["complexity"]
            assert 0.0 <= complexity <= 1.0, f"complexity {complexity} out of range for lines={lines}, branches={branches}"

    def test_split_large_function_large_fn_more_splits(self, mock_graph_store: MagicMock) -> None:
        """更大的函数产生更多拆分建议。"""
        result = split_large_function(
            entity_id="UserService.login",
            context={"lines": 250, "branches": 25, "max_lines": 100},
            graph_store=mock_graph_store,
        )
        # 250 lines 应该触发 3 个建议（validate + core + error handling）
        assert len(result["analysis"]["suggested_splits"]) >= 2


# --- 空壳 Function 测试 ---


class TestNotImplementedStubs:
    def test_extract_interface_not_implemented(self, mock_graph_store: MagicMock) -> None:
        with pytest.raises(NotImplementedError):
            extract_interface("test-id", {}, mock_graph_store)

    def test_reduce_complexity_not_implemented(self, mock_graph_store: MagicMock) -> None:
        with pytest.raises(NotImplementedError):
            reduce_complexity("test-id", {}, mock_graph_store)
