from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from layerkg.store.neo4j_store import Neo4jGraphStore


@pytest.fixture
def mock_session() -> MagicMock:
    """创建 mock session，支持 context manager。"""
    session = MagicMock()
    # 支持 with session as s: 语法
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


@pytest.fixture
def mock_driver(mock_session: MagicMock) -> MagicMock:
    """创建 mock driver，返回 mock session。"""
    driver = MagicMock()
    driver.session = MagicMock(return_value=mock_session)
    return driver


@pytest.mark.unit
class TestNeo4jGraphStoreMergeNode:
    """测试 merge_node 方法。"""

    def test_merge_node_creates_new(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 merge_node 创建新节点。"""
        # Arrange
        mock_result = MagicMock()
        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        label = "CodeEntity"
        properties = {"id": "test-123", "name": "foo", "entityType": "function"}

        # Act
        result = store.merge_node(label, properties)

        # Assert
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        assert "MERGE" in call_args[0][0]
        assert call_args[1]["id"] == "test-123"
        assert call_args[1]["name"] == "foo"
        assert result == properties

    def test_merge_node_updates_existing(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 merge_node 更新已存在的节点。"""
        # Arrange
        mock_result = MagicMock()
        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        label = "CodeEntity"
        properties = {"id": "test-123", "name": "bar", "entityType": "class"}

        # Act
        result = store.merge_node(label, properties)

        # Assert
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        assert "SET" in call_args[0][0]
        assert call_args[1]["name"] == "bar"
        assert result == properties

    def test_merge_node_missing_id_raises(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 merge_node 缺少 id 时抛出 ValueError。"""
        # Arrange
        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act & Assert
        with pytest.raises(ValueError, match="must contain 'id'"):
            store.merge_node("CodeEntity", {"name": "foo"})


@pytest.mark.unit
class TestNeo4jGraphStoreGetNode:
    """测试 get_node 方法。"""

    def test_get_node_found(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 get_node 找到节点。"""

        # Arrange - 使用 dict 子类模拟 Neo4j Node 对象
        class MockNode(dict):
            """继承 dict 使其可被 dict() 正确转换。"""

            pass

        node_data = {"id": "test-123", "name": "foo"}
        mock_node = MockNode(node_data)

        mock_record = MagicMock()
        mock_record.get = MagicMock(return_value=mock_node)

        mock_result = MagicMock()
        mock_result.single = MagicMock(return_value=mock_record)

        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.get_node("test-123")

        # Assert
        assert result is not None
        assert result["id"] == "test-123"
        assert result["name"] == "foo"

    def test_get_node_not_found(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 get_node 未找到节点返回 None。"""
        # Arrange
        mock_result = MagicMock()
        mock_result.single = MagicMock(return_value=None)
        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.get_node("nonexistent")

        # Assert
        assert result is None


@pytest.mark.unit
class TestNeo4jGraphStoreDeleteNode:
    """测试 delete_node 方法。"""

    def test_delete_node_success(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 delete_node 成功删除节点。"""
        # Arrange - mock Neo4j ResultSummary
        mock_counters = MagicMock()
        mock_counters.nodes_deleted = 1
        mock_summary = MagicMock()
        mock_summary.counters = mock_counters

        mock_result = MagicMock()
        mock_result.consume = MagicMock(return_value=mock_summary)
        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.delete_node("test-123")

        # Assert
        assert result is True
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        assert "DETACH DELETE" in call_args[0][0]
        assert call_args[1]["id"] == "test-123"

    def test_delete_node_not_found(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 delete_node 节点不存在返回 False。"""
        # Arrange - mock Neo4j ResultSummary，无节点删除
        mock_counters = MagicMock()
        mock_counters.nodes_deleted = 0
        mock_summary = MagicMock()
        mock_summary.counters = mock_counters

        mock_result = MagicMock()
        mock_result.consume = MagicMock(return_value=mock_summary)
        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.delete_node("nonexistent")

        # Assert
        assert result is False


@pytest.mark.unit
class TestNeo4jGraphStoreContextManager:
    """测试 context manager 支持。"""

    def test_close(self, mock_driver: MagicMock):
        """测试 close 方法关闭 driver。"""
        # Arrange
        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        store.close()

        # Assert
        mock_driver.close.assert_called_once()

    def test_context_manager(self):
        """测试 __enter__ 和 __exit__。"""
        # Arrange
        with patch("layerkg.store.neo4j_store.GraphDatabase") as mock_gd:
            mock_driver = MagicMock()
            mock_gd.driver.return_value = mock_driver

            # Act
            with Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password") as store:
                assert store is not None

            # Assert
            mock_driver.close.assert_called_once()


@pytest.mark.unit
class TestNeo4jGraphStoreMergeRelation:
    """测试 merge_relation 方法。"""

    def test_merge_relation_without_properties(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 merge_relation 不带属性。"""
        # Arrange
        mock_result = MagicMock()
        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.merge_relation("src-123", "tgt-456", "CALLS")

        # Assert
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        assert "MERGE" in cypher
        assert "CALLS" in cypher
        assert "$source_id" in cypher
        assert "$target_id" in cypher
        assert call_args[1]["source_id"] == "src-123"
        assert call_args[1]["target_id"] == "tgt-456"
        assert result == {}

    def test_merge_relation_with_properties(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 merge_relation 带属性。"""
        # Arrange
        mock_result = MagicMock()
        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        properties = {"weight": 0.8, "created_at": "2025-01-01"}

        # Act
        result = store.merge_relation("src-123", "tgt-456", "CALLS", properties)

        # Assert
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        assert "MERGE" in cypher
        assert "SET" in cypher
        assert call_args[1]["source_id"] == "src-123"
        assert call_args[1]["target_id"] == "tgt-456"
        assert call_args[1]["weight"] == 0.8
        assert result == properties

    def test_merge_relation_with_snake_case_rel_type(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 merge_relation 使用 snake_case 关系类型自动转换。"""
        # Arrange
        mock_result = MagicMock()
        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.merge_relation("src-123", "tgt-456", "semantic_impact")

        # Assert
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        assert "SEMANTIC_IMPACT" in cypher
        assert result == {}

    def test_merge_relation_with_labels_uses_labels(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 merge_relation 带 label 时使用正确标签创建节点。"""
        # Arrange
        mock_result = MagicMock()
        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        store.merge_relation("src-123", "tgt-456", "CALLS", source_label="CodeEntity", target_label="CodeEntity")

        # Assert
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        assert "source:CodeEntity" in cypher
        assert "target:CodeEntity" in cypher

    def test_merge_relation_invalid_label_raises(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 merge_relation 非法 label 抛出 ValueError。"""
        # Arrange
        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act & Assert - 测试包含空格的 label
        with pytest.raises(ValueError, match="Invalid source_label"):
            store.merge_relation("src-123", "tgt-456", "CALLS", source_label="Invalid Label")

        # Act & Assert - 测试包含特殊字符的 label
        with pytest.raises(ValueError, match="Invalid target_label"):
            store.merge_relation("src-123", "tgt-456", "CALLS", target_label="Label;DROP TABLE")

    def test_merge_relation_invalid_rel_type_raises(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 merge_relation 非法关系类型抛出 ValueError。"""
        # Arrange
        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act & Assert - 测试包含非法字符的关系类型
        with pytest.raises(ValueError, match="Invalid relation type"):
            store.merge_relation("src-123", "tgt-456", "INVALID;REL")


@pytest.mark.unit
class TestNeo4jGraphStoreDeleteRelation:
    """测试 delete_relation 方法。"""

    def test_delete_relation_success(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 delete_relation 成功删除关系。"""
        # Arrange
        mock_counters = MagicMock()
        mock_counters.relationships_deleted = 1
        mock_summary = MagicMock()
        mock_summary.counters = mock_counters

        mock_result = MagicMock()
        mock_result.consume = MagicMock(return_value=mock_summary)
        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.delete_relation("src-123", "tgt-456", "CALLS")

        # Assert
        assert result is True
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        assert "DELETE" in cypher
        assert call_args[1]["source_id"] == "src-123"
        assert call_args[1]["target_id"] == "tgt-456"

    def test_delete_relation_not_found(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 delete_relation 关系不存在返回 False。"""
        # Arrange
        mock_counters = MagicMock()
        mock_counters.relationships_deleted = 0
        mock_summary = MagicMock()
        mock_summary.counters = mock_counters

        mock_result = MagicMock()
        mock_result.consume = MagicMock(return_value=mock_summary)
        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.delete_relation("src-123", "tgt-456", "CALLS")

        # Assert
        assert result is False


@pytest.mark.unit
class TestNeo4jGraphStoreGetRelations:
    """测试 get_relations 方法。"""

    def test_get_relations_by_source(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 get_relations 按源节点查询。"""
        # Arrange
        expected_data = {
            "source_id": "src-123",
            "target_id": "tgt-456",
            "rel_type": "CALLS",
            "properties": {"weight": 0.5},
        }

        mock_record = MagicMock()
        mock_record.data = MagicMock(return_value=expected_data)

        # 使 result 可迭代
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([mock_record]))

        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.get_relations(source_id="src-123")

        # Assert
        assert len(result) == 1
        assert result[0]["source_id"] == "src-123"
        assert result[0]["target_id"] == "tgt-456"

    def test_get_relations_by_type(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 get_relations 按关系类型查询。"""
        # Arrange
        mock_result = MagicMock()
        mock_result.data = MagicMock(return_value=[])

        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.get_relations(rel_type="CALLS")

        # Assert
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        assert "CALLS" in cypher
        assert result == []

    def test_get_relations_no_filters(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 get_relations 无过滤条件返回所有关系。"""
        # Arrange
        mock_result = MagicMock()
        mock_result.data = MagicMock(return_value=[])

        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.get_relations()

        # Assert
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        assert "MATCH" in cypher
        assert "WHERE" not in cypher
        assert result == []


@pytest.mark.unit
class TestNeo4jGraphStoreQuery:
    """测试 query 方法。"""

    def test_query_returns_results(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 query 返回查询结果。"""
        # Arrange
        expected_data = {"n": {"id": "123", "name": "foo"}}

        mock_record = MagicMock()
        mock_record.data = MagicMock(return_value=expected_data)

        # 使 result 可迭代
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([mock_record]))

        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.query("MATCH (n) RETURN n")

        # Assert
        assert len(result) == 1
        assert result[0]["n"]["id"] == "123"

    def test_query_with_params(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 query 传递参数。"""
        # Arrange
        mock_result = MagicMock()
        mock_result.data = MagicMock(return_value=[])

        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        result = store.query("MATCH (n {id: $id}) RETURN n", {"id": "test-123"})

        # Assert
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        assert call_args[0][0] == "MATCH (n {id: $id}) RETURN n"
        assert call_args[1]["id"] == "test-123"
        assert result == []


@pytest.mark.unit
class TestNeo4jGraphStoreConstraints:
    """测试 ensure_constraints 方法。"""

    def test_ensure_constraints_creates_all(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 ensure_constraints 创建 7 个唯一约束 + 注册 schema 版本。"""
        # Arrange
        mock_result = MagicMock()
        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        store.ensure_constraints()

        # Assert: 6 实体 + 1 SchemaVersion = 7 约束 + 1 register_schema_version query = 8 calls
        assert mock_session.run.call_count == 8
        calls = mock_session.run.call_args_list
        cyphers = [call[0][0] for call in calls]

        # 验证 6 个实体标签的约束都被创建
        labels = [
            "CodeEntity",
            "ConceptEntity",
            "DocEntity",
            "ResourceEntity",
            "ModuleEntity",
            "ChangeSetEntity",
        ]
        for label in labels:
            assert any(label in cypher for cypher in cyphers)
        # 验证 SchemaVersion 约束被创建（使用 n.version 而非 n.id）
        assert any("SchemaVersion" in cypher and "REQUIRE n.version IS UNIQUE" in cypher for cypher in cyphers)
        # 验证每个约束都是 CREATE CONSTRAINT
        constraint_cyphers = [c for c in cyphers if "CREATE CONSTRAINT" in c]
        assert len(constraint_cyphers) == 7
        # 验证实体约束都有 REQUIRE n.id IS UNIQUE
        for cypher in constraint_cyphers[:6]:  # 前 6 个是实体约束
            assert "REQUIRE n.id IS UNIQUE" in cypher
        # 验证 register_schema_version 的 MERGE 语句
        assert any("MERGE" in cypher and "SchemaVersion" in cypher for cypher in cyphers)


@pytest.mark.unit
class TestNeo4jGraphStoreCleanupOrphanNodes:
    """测试 cleanup_orphan_nodes 方法。"""

    def test_cleanup_orphan_nodes(self, mock_driver: MagicMock, mock_session: MagicMock):
        """测试 cleanup_orphan_nodes 返回删除计数。"""
        # Arrange - 模拟返回删除计数
        mock_record = MagicMock()
        mock_record.__getitem__ = MagicMock(return_value=3)

        mock_result = MagicMock()
        mock_result.single = MagicMock(return_value=mock_record)

        mock_session.run = MagicMock(return_value=mock_result)

        store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
        store._driver = mock_driver

        # Act
        count = store.cleanup_orphan_nodes()

        # Assert
        assert count == 3
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        assert "WHERE labels(n) = []" in cypher
        assert "DETACH DELETE" in cypher
