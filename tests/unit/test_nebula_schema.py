from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ontoagent.domain.schema import RELATION_TYPE_TO_NEO4J, VALID_ENTITY_LABELS
from ontoagent.store.nebula_schema import NebulaSchemaInitializer


@pytest.fixture
def mock_session() -> MagicMock:
    """mock nebula Session。execute 返回成功的 ResultSet。"""
    session = MagicMock()
    result = MagicMock()
    result.is_succeeded = MagicMock(return_value=True)
    result.error_msg = ""
    session.execute = MagicMock(return_value=result)
    return session


@pytest.mark.unit
class TestNebulaSchemaSpace:
    """Space 创建测试。"""

    def test_creates_space_with_correct_vid_type(self, mock_session: MagicMock) -> None:
        initializer = NebulaSchemaInitializer(mock_session, space_name="ontoagent")
        ok = initializer.ensure_space(vid_type="FIXED_STRING(36)")

        assert ok is True
        # 检查至少调用了 execute，且 DDL 包含 CREATE SPACE + FIXED_STRING(36)
        calls = [call.args[0] for call in mock_session.execute.call_args_list]
        assert any("CREATE SPACE" in c and "FIXED_STRING(36)" in c for c in calls)

    def test_space_name_appears_in_ddl(self, mock_session: MagicMock) -> None:
        initializer = NebulaSchemaInitializer(mock_session, space_name="my_space")
        initializer.ensure_space()

        calls = [call.args[0] for call in mock_session.execute.call_args_list]
        assert any("my_space" in c for c in calls)

    def test_ensure_space_is_idempotent(self, mock_session: MagicMock) -> None:
        """DDL 必须含 IF NOT EXISTS。"""
        initializer = NebulaSchemaInitializer(mock_session)
        initializer.ensure_space()

        calls = [call.args[0] for call in mock_session.execute.call_args_list]
        assert all("IF NOT EXISTS" in c for c in calls)

    def test_ensure_space_returns_false_on_failure(self, mock_session: MagicMock) -> None:
        result = MagicMock()
        result.is_succeeded = MagicMock(return_value=False)
        result.error_msg = "boom"
        mock_session.execute = MagicMock(return_value=result)

        initializer = NebulaSchemaInitializer(mock_session)
        ok = initializer.ensure_space()
        assert ok is False


@pytest.mark.unit
class TestNebulaSchemaTags:
    """Tag 创建测试。"""

    def test_creates_all_13_tags(self, mock_session: MagicMock) -> None:
        """13 个实体（VALID_ENTITY_LABELS）+ SchemaVersion 全部生成 Tag DDL。"""
        initializer = NebulaSchemaInitializer(mock_session)
        ddl_list = initializer.create_tags()

        # 验证每个 VALID_ENTITY_LABELS 都出现在 DDL 中
        for label in VALID_ENTITY_LABELS:
            assert any("CREATE TAG" in ddl and label in ddl for ddl in ddl_list), f"Missing CREATE TAG for {label}"
        # 数量匹配：13 实体 + 1 SchemaVersion
        assert len(ddl_list) == len(VALID_ENTITY_LABELS) + 1

    def test_creates_schema_version_tag(self, mock_session: MagicMock) -> None:
        """SchemaVersion Tag 必须含 version/description/applied_at 三个 string 字段。"""
        initializer = NebulaSchemaInitializer(mock_session)
        ddl_list = initializer.create_tags()

        sv_ddl = next(ddl for ddl in ddl_list if "SchemaVersion" in ddl)
        assert "CREATE TAG IF NOT EXISTS `SchemaVersion`" in sv_ddl
        assert "`version` string" in sv_ddl
        assert "`description` string" in sv_ddl
        assert "`applied_at` string" in sv_ddl

    def test_tag_ddl_uses_string_type(self, mock_session: MagicMock) -> None:
        """所有 Tag 属性用 string 类型（POC 简化策略）。"""
        initializer = NebulaSchemaInitializer(mock_session)
        ddl_list = initializer.create_tags()

        # 至少有一个 DDL 包含 "string" 类型（CodeEntity 有 filePath 等属性）
        assert any("string" in ddl for ddl in ddl_list)

    def test_timestamp_field_uses_backticks(self, mock_session: MagicMock) -> None:
        """LogEntity/AlertEntity 有 timestamp 字段（保留字），必须用反引号。"""
        initializer = NebulaSchemaInitializer(mock_session)
        ddl_list = initializer.create_tags()

        # 至少有一个 DDL 包含 `timestamp` 反引号
        assert any("`timestamp`" in ddl for ddl in ddl_list), "timestamp field must be backticked"

    def test_create_tags_is_idempotent(self, mock_session: MagicMock) -> None:
        """DDL 必须含 IF NOT EXISTS。"""
        initializer = NebulaSchemaInitializer(mock_session)
        ddl_list = initializer.create_tags()
        assert all("IF NOT EXISTS" in ddl for ddl in ddl_list)

    def test_tag_uses_camel_case_property_names(self, mock_session: MagicMock) -> None:
        """dataclass snake_case 字段必须转 camelCase（如 file_path → filePath）。"""
        initializer = NebulaSchemaInitializer(mock_session)
        ddl_list = initializer.create_tags()
        # CodeEntity 应包含 filePath（不是 file_path）
        code_entity_ddl = next(ddl for ddl in ddl_list if "CodeEntity" in ddl)
        assert "filePath" in code_entity_ddl
        assert "file_path" not in code_entity_ddl


@pytest.mark.unit
class TestNebulaSchemaEdges:
    """Edge 创建测试。"""

    def test_creates_all_26_edges(self, mock_session: MagicMock) -> None:
        """26 个关系（RELATION_TYPE_TO_NEO4J values）全部生成 Edge DDL。"""
        initializer = NebulaSchemaInitializer(mock_session)
        ddl_list = initializer.create_edges()

        for rel_type in RELATION_TYPE_TO_NEO4J.values():
            assert any("CREATE EDGE" in ddl and rel_type in ddl for ddl in ddl_list), (
                f"Missing CREATE EDGE for {rel_type}"
            )
        assert len(ddl_list) == len(RELATION_TYPE_TO_NEO4J)

    def test_create_edges_have_common_props(self, mock_session: MagicMock) -> None:
        """Edge type 包含通用溯源和权重属性（Phase 6.1）。"""
        initializer = NebulaSchemaInitializer(mock_session)
        ddl_list = initializer.create_edges()
        for ddl in ddl_list:
            assert "CREATE EDGE IF NOT EXISTS" in ddl
            # 5 个通用属性必须存在
            assert "weight" in ddl
            assert "affectScore" in ddl
            assert "provenanceSource" in ddl
            assert "confidence" in ddl
            assert "extractedAt" in ddl

    def test_create_edges_is_idempotent(self, mock_session: MagicMock) -> None:
        initializer = NebulaSchemaInitializer(mock_session)
        ddl_list = initializer.create_edges()
        assert all("IF NOT EXISTS" in ddl for ddl in ddl_list)


@pytest.mark.unit
class TestNebulaSchemaIndexes:
    """Tag index 创建测试。"""

    def test_creates_index_for_each_tag(self, mock_session: MagicMock) -> None:
        initializer = NebulaSchemaInitializer(mock_session)
        ddl_list = initializer.create_indexes()

        # 每个标签都应该有对应的索引
        for label in VALID_ENTITY_LABELS:
            assert any("TAG INDEX" in ddl and label in ddl for ddl in ddl_list), f"Missing TAG INDEX for {label}"
        assert len(ddl_list) == len(VALID_ENTITY_LABELS)

    def test_create_indexes_is_idempotent(self, mock_session: MagicMock) -> None:
        initializer = NebulaSchemaInitializer(mock_session)
        ddl_list = initializer.create_indexes()
        assert all("IF NOT EXISTS" in ddl for ddl in ddl_list)


@pytest.mark.unit
class TestNebulaSchemaInitialize:
    """initialize 集成测试。"""

    def test_initialize_executes_space_tags_edges_indexes(self, mock_session: MagicMock) -> None:
        initializer = NebulaSchemaInitializer(mock_session)
        ok = initializer.initialize(vid_type="FIXED_STRING(36)")
        assert ok is True

        calls = [call.args[0] for call in mock_session.execute.call_args_list]
        # 至少 1 个 CREATE SPACE
        assert any("CREATE SPACE" in c for c in calls)
        # 13 个 CREATE TAG（不含 TAG INDEX — 用精确字符串边界）+ 1 SchemaVersion
        tag_calls = [c for c in calls if "CREATE TAG IF NOT EXISTS" in c and "INDEX" not in c]
        assert len(tag_calls) == len(VALID_ENTITY_LABELS) + 1
        # 26 个 CREATE EDGE
        edge_calls = [c for c in calls if "CREATE EDGE IF NOT EXISTS" in c]
        assert len(edge_calls) == len(RELATION_TYPE_TO_NEO4J)
        # 13 个 TAG INDEX
        idx_calls = [c for c in calls if "CREATE TAG INDEX" in c]
        assert len(idx_calls) == len(VALID_ENTITY_LABELS)
