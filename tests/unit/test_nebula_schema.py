from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ontoagent.domain.schema import RELATION_TYPE_TO_NEO4J, VALID_ENTITY_LABELS
from ontoagent.store.nebula_schema import NebulaSchemaInitializer


def _make_show_spaces_result(space_names: list[str]) -> MagicMock:
    """构造 SHOW SPACES 的 ResultSet mock（贴近真实 nebula3 SDK 行为）。

    nebula3 SDK 中:
    - SHOW SPACES 返回单列 ``Name``。
    - ``result.column_values("Name")`` 是方法，返回 ``list[ValueWrapper]``。
    - ``ValueWrapper.as_string()`` 是方法，返回字符串值。

    重要：mock 必须模拟这些方法的 callable 特性，不能用属性赋值。
    之前用 result.rows = list 掩盖了 rows 是方法的事实，导致真实 SDK 下静默失败。
    """
    result = MagicMock()
    result.is_succeeded = MagicMock(return_value=True)
    result.error_msg = ""

    # 构造 ValueWrapper mock: as_string() 是方法返回字符串
    values = []
    for name in space_names:
        vw = MagicMock()
        vw.as_string = MagicMock(return_value=name)
        values.append(vw)

    # column_values 是方法，不是属性
    result.column_values = MagicMock(return_value=values)
    return result


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
        """CREATE SPACE DDL 必须含 IF NOT EXISTS。"""
        initializer = NebulaSchemaInitializer(mock_session)
        initializer.ensure_space()

        calls = [call.args[0] for call in mock_session.execute.call_args_list]
        # 过滤 CREATE 语句（排除 SHOW SPACES 检测语句）
        create_calls = [c for c in calls if "CREATE" in c]
        assert create_calls, "Expected at least one CREATE call"
        assert all("IF NOT EXISTS" in c for c in create_calls)

    def test_ensure_space_returns_false_on_failure(self, mock_session: MagicMock) -> None:
        result = MagicMock()
        result.is_succeeded = MagicMock(return_value=False)
        result.error_msg = "boom"
        mock_session.execute = MagicMock(return_value=result)

        initializer = NebulaSchemaInitializer(mock_session)
        ok = initializer.ensure_space()
        assert ok is False

    def test_ensure_space_skips_create_if_exists(self, mock_session: MagicMock) -> None:
        """space 已存在时（SHOW SPACES 包含目标 space），不应执行 CREATE SPACE。

        共享集群场景：普通用户对 CREATE SPACE 无权限，但 SHOW SPACES 普通用户可执行。
        先 SHOW 检测已存在 → 直接返回 True，跳过 CREATE。
        """
        initializer = NebulaSchemaInitializer(mock_session, space_name="ontoagent")

        # 构造 SHOW SPACES 返回值：包含 "ontoagent"
        show_result = _make_show_spaces_result(["ontoagent", "other_space"])
        # CREATE 不会被执行，但若被执行返回失败模拟权限错误
        create_result = MagicMock()
        create_result.is_succeeded = MagicMock(return_value=False)
        create_result.error_msg = "PermissionError"

        def execute_side_effect(stmt: str):
            if "SHOW SPACES" in stmt:
                return show_result
            return create_result

        mock_session.execute = MagicMock(side_effect=execute_side_effect)

        ok = initializer.ensure_space()
        assert ok is True

        # 验证没有 CREATE SPACE 被执行
        calls = [call.args[0] for call in mock_session.execute.call_args_list]
        assert not any("CREATE SPACE" in c for c in calls), (
            f"CREATE SPACE should not be executed when space exists: {calls}"
        )

    def test_ensure_space_creates_if_not_exists(self, mock_session: MagicMock) -> None:
        """space 不在 SHOW SPACES 列表中时，执行 CREATE SPACE。"""
        initializer = NebulaSchemaInitializer(mock_session, space_name="ontoagent")

        show_result = _make_show_spaces_result(["other_space"])  # 不含 ontoagent
        create_result = MagicMock()
        create_result.is_succeeded = MagicMock(return_value=True)
        create_result.error_msg = ""

        def execute_side_effect(stmt: str):
            if "SHOW SPACES" in stmt:
                return show_result
            return create_result

        mock_session.execute = MagicMock(side_effect=execute_side_effect)

        ok = initializer.ensure_space()
        assert ok is True

        # 验证 CREATE SPACE 被执行
        calls = [call.args[0] for call in mock_session.execute.call_args_list]
        assert any("CREATE SPACE" in c for c in calls), f"CREATE SPACE should be executed: {calls}"

    def test_ensure_space_creates_when_show_spaces_fails(self, mock_session: MagicMock) -> None:
        """SHOW SPACES 执行失败时，降级到直接 CREATE SPACE（保持原有行为）。"""
        initializer = NebulaSchemaInitializer(mock_session, space_name="ontoagent")

        show_result = MagicMock()
        show_result.is_succeeded = MagicMock(return_value=False)
        show_result.error_msg = "permission denied for SHOW"
        create_result = MagicMock()
        create_result.is_succeeded = MagicMock(return_value=True)
        create_result.error_msg = ""

        def execute_side_effect(stmt: str):
            if "SHOW SPACES" in stmt:
                return show_result
            return create_result

        mock_session.execute = MagicMock(side_effect=execute_side_effect)

        ok = initializer.ensure_space()
        assert ok is True

        # 验证 CREATE SPACE 被执行（降级路径）
        calls = [call.args[0] for call in mock_session.execute.call_args_list]
        assert any("CREATE SPACE" in c for c in calls)

    def test_ensure_space_creates_when_show_spaces_decode_fails(self, mock_session: MagicMock) -> None:
        """SHOW SPACES 返回值结构无法解析时，降级到直接 CREATE SPACE。"""
        initializer = NebulaSchemaInitializer(mock_session, space_name="ontoagent")

        # 返回 succeeded=True 但 rows 结构异常（解析抛异常）
        show_result = MagicMock()
        show_result.is_succeeded = MagicMock(return_value=True)
        show_result.rows = MagicMock(side_effect=RuntimeError("decode failed"))
        create_result = MagicMock()
        create_result.is_succeeded = MagicMock(return_value=True)

        def execute_side_effect(stmt: str):
            if "SHOW SPACES" in stmt:
                return show_result
            return create_result

        mock_session.execute = MagicMock(side_effect=execute_side_effect)

        ok = initializer.ensure_space()
        assert ok is True

        # 验证 CREATE SPACE 被执行（解析失败降级）
        calls = [call.args[0] for call in mock_session.execute.call_args_list]
        assert any("CREATE SPACE" in c for c in calls)

    def test_ensure_space_accepts_custom_vid_type(self, mock_session: MagicMock) -> None:
        """ensure_space(vid_type=...) 透传到 CREATE SPACE DDL。"""
        initializer = NebulaSchemaInitializer(mock_session, space_name="ontoagent")
        # SHOW SPACES 返回空 → 走 CREATE 路径
        show_result = _make_show_spaces_result([])
        create_result = MagicMock()
        create_result.is_succeeded = MagicMock(return_value=True)

        def execute_side_effect(stmt: str):
            if "SHOW SPACES" in stmt:
                return show_result
            return create_result

        mock_session.execute = MagicMock(side_effect=execute_side_effect)

        ok = initializer.ensure_space(vid_type="FIXED_STRING(64)")
        assert ok is True

        calls = [call.args[0] for call in mock_session.execute.call_args_list]
        create_call = next(c for c in calls if "CREATE SPACE" in c)
        assert "FIXED_STRING(64)" in create_call

    def test_initializer_accepts_vid_type_param(self, mock_session: MagicMock) -> None:
        """NebulaSchemaInitializer 接收 vid_type，initialize() 使用该值生成 CREATE SPACE。

        增强功能：vid_type 可通过构造器注入，避免每次调用 ensure_space 都重复传参。
        """
        initializer = NebulaSchemaInitializer(mock_session, space_name="ontoagent", vid_type="FIXED_STRING(64)")
        # SHOW SPACES 返回空 → 走 CREATE 路径
        show_result = _make_show_spaces_result([])
        create_result = MagicMock()
        create_result.is_succeeded = MagicMock(return_value=True)

        def execute_side_effect(stmt: str):
            if "SHOW SPACES" in stmt:
                return show_result
            return create_result

        mock_session.execute = MagicMock(side_effect=execute_side_effect)

        ok = initializer.ensure_space()
        assert ok is True

        calls = [call.args[0] for call in mock_session.execute.call_args_list]
        create_call = next(c for c in calls if "CREATE SPACE" in c)
        assert "FIXED_STRING(64)" in create_call

    def test_initializer_default_vid_type_unchanged(self, mock_session: MagicMock) -> None:
        """无 vid_type 参数时，默认值仍为 FIXED_STRING(36)（保持向后兼容）。"""
        initializer = NebulaSchemaInitializer(mock_session, space_name="ontoagent")
        show_result = _make_show_spaces_result([])
        create_result = MagicMock()
        create_result.is_succeeded = MagicMock(return_value=True)

        def execute_side_effect(stmt: str):
            if "SHOW SPACES" in stmt:
                return show_result
            return create_result

        mock_session.execute = MagicMock(side_effect=execute_side_effect)

        initializer.ensure_space()

        calls = [call.args[0] for call in mock_session.execute.call_args_list]
        create_call = next(c for c in calls if "CREATE SPACE" in c)
        assert "FIXED_STRING(36)" in create_call


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
        code_entity_ddl = next(ddl for ddl in ddl_list if "CREATE TAG IF NOT EXISTS `CodeEntity`" in ddl)
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

        # 每个标签都应有 name 和 repoId 两个索引（多仓库 P1-Task 1-3）
        for label in VALID_ENTITY_LABELS:
            name_idx = f"idx_{label}_name"
            repo_idx = f"idx_{label}_repoId"
            assert any("TAG INDEX" in ddl and name_idx in ddl for ddl in ddl_list), f"Missing TAG INDEX {name_idx}"
            assert any("TAG INDEX" in ddl and repo_idx in ddl for ddl in ddl_list), f"Missing TAG INDEX {repo_idx}"
        assert len(ddl_list) == len(VALID_ENTITY_LABELS) * 2

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
        # TAG INDEX：每个 label 两个（name + repoId）
        idx_calls = [c for c in calls if "CREATE TAG INDEX" in c]
        assert len(idx_calls) == len(VALID_ENTITY_LABELS) * 2
