from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ontoagent.domain.schema import ModuleEntity
from ontoagent.pipeline.module_clustering import ModuleCluster, ModuleClustering
from ontoagent.store.neo4j_store import Neo4jGraphStore

# =============================================================================
# Task 1: ModuleCluster dataclass (2 tests)
# =============================================================================


class TestModuleCluster:
    """测试 ModuleCluster dataclass。"""

    def test_create_module_cluster_successfully(self) -> None:
        """测试正常创建 ModuleCluster。"""
        module = ModuleEntity(name="test_module")
        entity_ids = ["id1", "id2", "id3"]

        cluster = ModuleCluster(
            module=module,
            entity_ids=entity_ids,
            cohesion=0.8,
            entity_count=3,
        )

        assert cluster.module is module
        assert cluster.entity_ids == entity_ids
        assert cluster.cohesion == 0.8
        assert cluster.entity_count == 3

    def test_entity_count_must_match_entity_ids_length(self) -> None:
        """测试 entity_count 必须等于 len(entity_ids)。"""
        module = ModuleEntity(name="test_module")
        entity_ids = ["id1", "id2", "id3"]

        with pytest.raises(AssertionError):
            ModuleCluster(
                module=module,
                entity_ids=entity_ids,
                cohesion=0.8,
                entity_count=5,  # 不匹配
            )


# =============================================================================
# Task 2: ModuleClustering 构造函数 (2 tests)
# =============================================================================


class TestModuleClusteringInit:
    """测试 ModuleClustering 构造函数。"""

    def test_init_successfully(self) -> None:
        """测试正常初始化。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)
        clustering = ModuleClustering(mock_store, algorithm="label_propagation")

        assert clustering._neo4j_store is mock_store
        assert clustering._algorithm == "label_propagation"

    def test_init_invalid_algorithm_raises_value_error(self) -> None:
        """测试不支持的算法抛 ValueError。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)

        with pytest.raises(ValueError, match="Unsupported algorithm"):
            ModuleClustering(mock_store, algorithm="unknown_algo")


# =============================================================================
# Task 3: _load_graph (3 tests)
# =============================================================================


class TestLoadGraph:
    """测试 _load_graph 方法。"""

    def test_load_graph_returns_adj_and_entity_data(self) -> None:
        """测试正常加载图结构。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)

        # Mock 实体查询返回
        mock_store.query.side_effect = [
            # 第一次调用：获取实体
            [
                {"id": "e1", "name": "Entity1", "file_path": "src/module1/file1.py"},
                {"id": "e2", "name": "Entity2", "file_path": "src/module1/file2.py"},
                {"id": "e3", "name": "Entity3", "file_path": "src/module2/file3.py"},
            ],
            # 第二次调用：获取关系
            [
                {"source": "e1", "target": "e2"},
                {"source": "e2", "target": "e3"},
            ],
        ]

        clustering = ModuleClustering(mock_store)
        adj, entity_data = clustering._load_graph()

        assert adj == {
            "e1": {"e2"},
            "e2": {"e1", "e3"},
            "e3": {"e2"},
        }
        assert entity_data == {
            "e1": {"name": "Entity1", "file_path": "src/module1/file1.py"},
            "e2": {"name": "Entity2", "file_path": "src/module1/file2.py"},
            "e3": {"name": "Entity3", "file_path": "src/module2/file3.py"},
        }
        assert mock_store.query.call_count == 2

    def test_load_graph_empty_graph(self) -> None:
        """测试空图（无实体）。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)
        mock_store.query.side_effect = [[], []]  # 无实体，无关系

        clustering = ModuleClustering(mock_store)
        adj, entity_data = clustering._load_graph()

        assert adj == {}
        assert entity_data == {}

    def test_load_graph_with_isolated_nodes(self) -> None:
        """测试孤立节点（有实体无关系）。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)

        mock_store.query.side_effect = [
            [
                {"id": "e1", "name": "Entity1", "file_path": "src/file1.py"},
                {"id": "e2", "name": "Entity2", "file_path": "src/file2.py"},
            ],
            [],  # 无关系
        ]

        clustering = ModuleClustering(mock_store)
        adj, entity_data = clustering._load_graph()

        assert adj == {
            "e1": set(),
            "e2": set(),
        }
        assert entity_data == {
            "e1": {"name": "Entity1", "file_path": "src/file1.py"},
            "e2": {"name": "Entity2", "file_path": "src/file2.py"},
        }

    def test_load_graph_with_same_file_entities(self) -> None:
        """测试同文件实体两两互连（虚拟边）。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)

        # 同文件 src/module1.py 有 3 个实体：e1, e2, e3
        # 不同文件 src/module2.py 有 1 个实体：e4
        # 无结构关系，全靠虚拟边连接
        mock_store.query.side_effect = [
            [
                {"id": "e1", "name": "Entity1", "file_path": "src/module1.py"},
                {"id": "e2", "name": "Entity2", "file_path": "src/module1.py"},
                {"id": "e3", "name": "Entity3", "file_path": "src/module1.py"},
                {"id": "e4", "name": "Entity4", "file_path": "src/module2.py"},
            ],
            [],  # 无结构关系
        ]

        clustering = ModuleClustering(mock_store)
        adj, _entity_data = clustering._load_graph()

        # 同文件的 e1, e2, e3 应该全连接（虚拟边）
        # e1 与 e2, e3 相连
        assert "e2" in adj["e1"]
        assert "e3" in adj["e1"]
        assert len(adj["e1"]) == 2

        # e2 与 e1, e3 相连
        assert "e1" in adj["e2"]
        assert "e3" in adj["e2"]
        assert len(adj["e2"]) == 2

        # e3 与 e1, e2 相连
        assert "e1" in adj["e3"]
        assert "e2" in adj["e3"]
        assert len(adj["e3"]) == 2

        # e4 孤立（单文件无同文件邻居）
        assert len(adj["e4"]) == 0

    def test_load_graph_combines_virtual_and_structural_edges(self) -> None:
        """测试虚拟边和结构边合并。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)

        # e1, e2 同文件 src/file1.py（虚拟边）
        # e1 -> e3 有结构关系 CALLS
        # e3 在不同文件 src/file2.py
        mock_store.query.side_effect = [
            [
                {"id": "e1", "name": "Entity1", "file_path": "src/file1.py"},
                {"id": "e2", "name": "Entity2", "file_path": "src/file1.py"},
                {"id": "e3", "name": "Entity3", "file_path": "src/file2.py"},
            ],
            [
                {"source": "e1", "target": "e3"},  # 结构边
            ],
        ]

        clustering = ModuleClustering(mock_store)
        adj, _entity_data = clustering._load_graph()

        # e1 通过虚拟边连 e2，通过结构边连 e3
        assert adj["e1"] == {"e2", "e3"}
        # e2 通过虚拟边连 e1
        assert adj["e2"] == {"e1"}
        # e3 通过结构边连 e1
        assert adj["e3"] == {"e1"}


# =============================================================================
# Task 4: _label_propagation (5 tests)
# =============================================================================


class TestLabelPropagation:
    """测试 _label_propagation 算法。"""

    def test_triangle_graph_converges_to_one_community(self) -> None:
        """测试三角形图（A-B, B-C, A-C）→ 所有节点同一社区。"""
        adj = {
            "A": {"B", "C"},
            "B": {"A", "C"},
            "C": {"A", "B"},
        }

        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))
        labels = clustering._label_propagation(adj)

        # 所有节点应该属于同一个社区
        communities = set(labels.values())
        assert len(communities) == 1
        assert labels["A"] == labels["B"] == labels["C"]

    def test_two_disconnected_components(self) -> None:
        """测试两个分离组件（A-B, C-D）→ 2 个社区。"""
        adj = {
            "A": {"B"},
            "B": {"A"},
            "C": {"D"},
            "D": {"C"},
        }

        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))
        labels = clustering._label_propagation(adj)

        # A 和 B 同社区，C 和 D 同社区，但两个社区不同
        assert labels["A"] == labels["B"]
        assert labels["C"] == labels["D"]
        assert labels["A"] != labels["C"]

    def test_empty_graph_returns_empty_dict(self) -> None:
        """测试空图 {} → 返回 {}。"""
        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))
        labels = clustering._label_propagation({})

        assert labels == {}

    def test_chain_graph_converges_within_two_iterations(self) -> None:
        """测试链式 A-B-C 收敛（2 轮内）。"""
        adj = {
            "A": {"B"},
            "B": {"A", "C"},
            "C": {"B"},
        }

        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))
        labels = clustering._label_propagation(adj, max_iterations=10)

        # 链式图会收敛到单一社区
        communities = set(labels.values())
        assert len(communities) == 1

    def test_isolated_node_keeps_own_label(self) -> None:
        """测试孤立节点（A 无邻居）→ A 保持自身标签。"""
        adj = {
            "A": set(),  # 孤立
            "B": {"C"},
            "C": {"B"},
        }

        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))
        labels = clustering._label_propagation(adj)

        # A 应该保持自身标签（无邻居，无法传播）
        assert labels["A"] == "A"
        # B 和 C 应该形成社区
        assert labels["B"] == labels["C"]


# =============================================================================
# Task 5: _compute_cohesion (3 tests)
# =============================================================================


class TestComputeCohesion:
    """测试 _compute_cohesion 方法。"""

    def test_fully_connected_graph(self) -> None:
        """测试完全连通 3 节点（3条内部边，max=3）→ 1.0。"""
        adj = {
            "A": {"B", "C"},
            "B": {"A", "C"},
            "C": {"A", "B"},
        }
        entity_ids = ["A", "B", "C"]

        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))
        cohesion = clustering._compute_cohesion(entity_ids, adj)

        # 完全连通图：3 条边，最大可能边数 = 3*(3-1)/2 = 3
        assert cohesion == 1.0

    def test_chain_graph(self) -> None:
        """测试链式 A-B-C（2条内部边，max=3）→ 2/3 ≈ 0.667。"""
        adj = {
            "A": {"B"},
            "B": {"A", "C"},
            "C": {"B"},
        }
        entity_ids = ["A", "B", "C"]

        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))
        cohesion = clustering._compute_cohesion(entity_ids, adj)

        # 链式：A-B, B-C = 2 条边，max = 3
        assert cohesion == pytest.approx(2.0 / 3.0)

    def test_single_node_returns_zero(self) -> None:
        """测试单节点 → 0.0。"""
        adj = {"A": set()}
        entity_ids = ["A"]

        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))
        cohesion = clustering._compute_cohesion(entity_ids, adj)

        assert cohesion == 0.0

    def test_empty_entity_ids_returns_zero(self) -> None:
        """测试空实体列表 → 0.0。"""
        adj = {}
        entity_ids: list[str] = []

        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))
        cohesion = clustering._compute_cohesion(entity_ids, adj)

        assert cohesion == 0.0


# =============================================================================
# Task 6: _generate_module_name (3 tests)
# =============================================================================


class TestGenerateModuleName:
    """测试 _generate_module_name 方法。"""

    def test_common_path_prefix_generates_name(self) -> None:
        """测试有公共路径前缀 → 返回最后一段。"""
        entity_data = {
            "e1": {"file_path": "src/ontoagent/parser/file1.py"},
            "e2": {"file_path": "src/ontoagent/parser/file2.py"},
            "e3": {"file_path": "src/ontoagent/parser/subdir/file3.py"},
        }
        entity_ids = ["e1", "e2", "e3"]

        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))
        name = clustering._generate_module_name(entity_ids, entity_data)

        assert name == "parser"

    def test_no_file_path_returns_module_n(self) -> None:
        """测试无 file_path → 'module_0'。"""
        entity_data = {
            "e1": {"file_path": None},
            "e2": {"file_path": None},
        }
        entity_ids = ["e1", "e2"]

        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))
        name = clustering._generate_module_name(entity_ids, entity_data)

        assert name == "module_0"

    def test_mixed_paths_no_common_prefix_returns_module_n(self) -> None:
        """测试混合路径无公共前缀 → 'module_0'（首次调用）。"""
        entity_data = {
            "e1": {"file_path": "src/module1/file.py"},
            "e2": {"file_path": "tests/test_file.py"},
            "e3": {"file_path": "/absolutely/different/path.py"},
        }
        entity_ids = ["e1", "e2", "e3"]

        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))
        name = clustering._generate_module_name(entity_ids, entity_data)

        assert name == "module_0"

    def test_counter_increments_on_multiple_calls(self) -> None:
        """测试计数器在多次调用时递增。"""
        clustering = ModuleClustering(MagicMock(spec=Neo4jGraphStore))

        # 第一次调用：无 file_path
        name1 = clustering._generate_module_name(["e1"], {"e1": {"file_path": None}})
        assert name1 == "module_0"

        # 第二次调用：无公共前缀
        entity_data = {
            "e1": {"file_path": "src/module1/file.py"},
            "e2": {"file_path": "tests/test_file.py"},
        }
        name2 = clustering._generate_module_name(["e1", "e2"], entity_data)
        assert name2 == "module_1"


# =============================================================================
# Task 7: detect_modules 完整流程 (2 tests)
# =============================================================================


class TestDetectModules:
    """测试 detect_modules 主入口方法。"""

    def test_detect_modules_returns_two_clusters(self) -> None:
        """测试 4 节点 2 组件图 → 返回 2 个 ModuleCluster。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)

        # Mock _load_graph 返回两个分离的组件
        adj = {
            "e1": {"e2"},
            "e2": {"e1"},
            "e3": {"e4"},
            "e4": {"e3"},
        }
        entity_data = {
            "e1": {"name": "Entity1", "file_path": "src/module1/a.py"},
            "e2": {"name": "Entity2", "file_path": "src/module1/b.py"},
            "e3": {"name": "Entity3", "file_path": "src/module2/c.py"},
            "e4": {"name": "Entity4", "file_path": "src/module2/d.py"},
        }

        clustering = ModuleClustering(mock_store)
        # Mock _load_graph 方法
        clustering._load_graph = MagicMock(return_value=(adj, entity_data))

        clusters = clustering.detect_modules()

        assert len(clusters) == 2
        # 验证每个 cluster 的属性
        for cluster in clusters:
            assert isinstance(cluster, ModuleCluster)
            assert isinstance(cluster.module, ModuleEntity)
            assert cluster.entity_count == len(cluster.entity_ids)
            assert cluster.cohesion == 1.0  # 2 节点单边完全连通
            # 验证模块名来自公共路径
            assert cluster.module.name in ("module1", "module2")

    def test_detect_modules_empty_graph_returns_empty_list(self) -> None:
        """测试空图 → 返回空列表。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)

        clustering = ModuleClustering(mock_store)
        clustering._load_graph = MagicMock(return_value=({}, {}))

        clusters = clustering.detect_modules()

        assert clusters == []


# =============================================================================
# Task 8: save_modules 保存到 Neo4j (2 tests)
# =============================================================================


class TestSaveModules:
    """测试 save_modules 方法。"""

    def test_save_modules_creates_nodes_and_relations(self) -> None:
        """测试保存模块 → 正确调用 merge_node 和 merge_relation。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)
        clustering = ModuleClustering(mock_store)

        module1 = ModuleEntity(name="module1")
        cluster1 = ModuleCluster(
            module=module1,
            entity_ids=["e1", "e2"],
            cohesion=0.8,
            entity_count=2,
        )

        module2 = ModuleEntity(name="module2")
        cluster2 = ModuleCluster(
            module=module2,
            entity_ids=["e3"],
            cohesion=1.0,
            entity_count=1,
        )

        count = clustering.save_modules([cluster1, cluster2])

        assert count == 2
        # 验证 merge_node 被调用 2 次
        assert mock_store.merge_node.call_count == 2
        # 验证 merge_relation 被调用 3 次 (2 + 1)
        assert mock_store.merge_relation.call_count == 3

        # 验证第一次 merge_node 的参数
        first_call = mock_store.merge_node.call_args_list[0]
        assert first_call[0][0] == "ModuleEntity"
        assert first_call[0][1]["id"] == module1.id
        assert first_call[0][1]["name"] == "module1"

    def test_save_modules_empty_list_returns_zero(self) -> None:
        """测试空列表 → 返回 0。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)
        clustering = ModuleClustering(mock_store)

        count = clustering.save_modules([])

        assert count == 0
        mock_store.merge_node.assert_not_called()
        mock_store.merge_relation.assert_not_called()


# =============================================================================
# Task 9: get_module_tree 层次结构 (2 tests)
# =============================================================================


class TestGetModuleTree:
    """测试 get_module_tree 方法。"""

    def test_get_module_tree_returns_correct_structure(self) -> None:
        """测试返回正确的层次结构。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)

        clustering = ModuleClustering(mock_store)

        # Mock detect_modules 返回固定结果
        module1 = ModuleEntity(name="module1")
        cluster1 = ModuleCluster(
            module=module1,
            entity_ids=["e1", "e2"],
            cohesion=0.8,
            entity_count=2,
        )

        clustering.detect_modules = MagicMock(return_value=[cluster1])

        tree = clustering.get_module_tree()

        assert tree == {
            "module1": {
                "entities": ["e1", "e2"],
                "cohesion": 0.8,
                "entity_count": 2,
            }
        }

    def test_get_module_tree_empty_returns_empty_dict(self) -> None:
        """测试空模块列表 → 返回 {}。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)

        clustering = ModuleClustering(mock_store)
        clustering.detect_modules = MagicMock(return_value=[])

        tree = clustering.get_module_tree()

        assert tree == {}


# =============================================================================
# Task 10: 边界测试 (1 test)
# =============================================================================


class TestBoundaryCases:
    """测试边界情况。"""

    def test_single_node_graph_returns_one_module(self) -> None:
        """测试单节点图 → 1 个模块，cohesion=0.0，entity_count=1。"""
        mock_store = MagicMock(spec=Neo4jGraphStore)

        adj = {"e1": set()}
        entity_data = {"e1": {"name": "Entity1", "file_path": "src/file.py"}}

        clustering = ModuleClustering(mock_store)
        clustering._load_graph = MagicMock(return_value=(adj, entity_data))

        clusters = clustering.detect_modules()

        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster.entity_count == 1
        assert cluster.entity_ids == ["e1"]
        assert cluster.cohesion == 0.0  # 单节点无内聚
        assert cluster.module.name == "file.py"  # 文件名作为模块名
