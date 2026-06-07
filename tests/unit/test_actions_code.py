"""Code Action Function 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from layerkg.actions.code import (
    extract_interface,
    generate_api_doc,
    reduce_complexity,
    split_large_function,
    trace_call_chain,
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
    def test_reduce_complexity_not_implemented(self, mock_graph_store: MagicMock) -> None:
        with pytest.raises(NotImplementedError):
            reduce_complexity("test-id", {}, mock_graph_store)


# --- extract_interface 测试 ---


class TestExtractInterface:
    def test_extract_interface_analysis(self, mock_graph_store: MagicMock) -> None:
        """提取接口建议，返回公开方法列表。"""
        result = extract_interface(
            entity_id="UserService.login",
            context={"class_methods": ["login", "logout", "_hash_password", "get_profile"]},
            graph_store=mock_graph_store,
        )
        assert result["success"] is True
        assert result["entity_id"] == "UserService.login"
        assert result["side_effects"] == []

        analysis = result["analysis"]
        # _hash_password 应被过滤掉
        assert "_hash_password" not in analysis["public_methods"]
        assert "login" in analysis["public_methods"]
        assert "logout" in analysis["public_methods"]
        assert "get_profile" in analysis["public_methods"]
        assert analysis["interface_name"] == "IUserService.login"
        assert "suggested_interface" in analysis

    def test_extract_interface_entity_not_found(self, mock_graph_store: MagicMock) -> None:
        """实体不存在抛 ValueError。"""
        mock_graph_store.get_node.return_value = None
        with pytest.raises(ValueError, match="Entity not found"):
            extract_interface("nonexistent", {"class_methods": ["foo"]}, mock_graph_store)

    def test_extract_interface_no_public_methods(self, mock_graph_store: MagicMock) -> None:
        """所有方法都是私有的，返回空列表。"""
        result = extract_interface(
            entity_id="UserService.login",
            context={"class_methods": ["_init", "_internal"]},
            graph_store=mock_graph_store,
        )
        assert result["analysis"]["public_methods"] == []


# --- trace_call_chain 测试 ---


class TestTraceCallChain:
    def test_trace_call_chain_analysis(self, mock_graph_store: MagicMock) -> None:
        """追踪调用链，返回调用树。"""
        mock_graph_store.query.return_value = [
            {"id": "func-a", "name": "validate", "entity_type": "function"},
            {"id": "func-b", "name": "authenticate", "entity_type": "function"},
        ]
        result = trace_call_chain(
            entity_id="UserService.login",
            context={"depth": 3},
            graph_store=mock_graph_store,
        )
        assert result["success"] is True
        assert result["side_effects"] == []
        assert result["analysis"]["depth"] == 3
        assert len(result["analysis"]["call_tree"]) == 2
        assert result["analysis"]["call_tree"][0]["name"] == "validate"

    def test_trace_call_chain_default_depth(self, mock_graph_store: MagicMock) -> None:
        """默认追踪深度为 3。"""
        mock_graph_store.query.return_value = []
        result = trace_call_chain(
            entity_id="UserService.login",
            context={},
            graph_store=mock_graph_store,
        )
        assert result["analysis"]["depth"] == 3

    def test_trace_call_chain_entity_not_found(self, mock_graph_store: MagicMock) -> None:
        """实体不存在抛 ValueError。"""
        mock_graph_store.get_node.return_value = None
        with pytest.raises(ValueError, match="Entity not found"):
            trace_call_chain("nonexistent", {"depth": 3}, mock_graph_store)

    def test_trace_call_chain_empty_result(self, mock_graph_store: MagicMock) -> None:
        """无调用关系时返回空树。"""
        mock_graph_store.query.return_value = []
        result = trace_call_chain("UserService.login", {"depth": 5}, mock_graph_store)
        assert result["analysis"]["call_tree"] == []
        assert result["analysis"]["depth"] == 5


# --- generate_api_doc 测试 ---


class TestGenerateApiDoc:
    def test_generate_api_doc_analysis(self, mock_graph_store: MagicMock) -> None:
        """生成 API 文档，返回 Markdown。"""
        mock_graph_store.get_node.return_value = {
            "id": "func-001",
            "name": "UserService.login",
            "labels": ["CodeEntity"],
            "entityType": "function",
            "params": ["username", "password"],
            "return_type": "Token",
            "docstring": "用户登录接口",
        }
        result = generate_api_doc(
            entity_id="func-001",
            context={},
            graph_store=mock_graph_store,
        )
        assert result["success"] is True
        assert result["side_effects"] == []
        assert result["analysis"]["entity_name"] == "UserService.login"
        assert result["analysis"]["entity_type"] == "function"

        doc = result["analysis"]["doc_markdown"]
        assert "## `UserService.login`" in doc
        assert "`username`" in doc
        assert "`password`" in doc
        assert "`Token`" in doc
        assert "用户登录接口" in doc

    def test_generate_api_doc_entity_not_found(self, mock_graph_store: MagicMock) -> None:
        """实体不存在抛 ValueError。"""
        mock_graph_store.get_node.return_value = None
        with pytest.raises(ValueError, match="Entity not found"):
            generate_api_doc("nonexistent", {}, mock_graph_store)

    def test_generate_api_doc_minimal_info(self, mock_graph_store: MagicMock) -> None:
        """节点信息最少时也能生成文档。"""
        mock_graph_store.get_node.return_value = {
            "id": "func-002",
            "name": "simple_func",
        }
        result = generate_api_doc("func-002", {}, mock_graph_store)
        assert result["success"] is True
        assert "## `simple_func`" in result["analysis"]["doc_markdown"]

    def test_generate_api_doc_with_context_params(self, mock_graph_store: MagicMock) -> None:
        """节点无 params 时从 context 降级读取。"""
        mock_graph_store.get_node.return_value = {
            "id": "func-003",
            "name": "my_func",
            "entityType": "function",
        }
        result = generate_api_doc(
            entity_id="func-003",
            context={"params": ["arg1", "arg2"]},
            graph_store=mock_graph_store,
        )
        doc = result["analysis"]["doc_markdown"]
        assert "`arg1`" in doc
        assert "`arg2`" in doc
