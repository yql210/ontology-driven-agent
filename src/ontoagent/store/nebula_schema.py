from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ontoagent.domain.schema import (
    RELATION_TYPE_TO_NEO4J,
    VALID_ENTITY_LABELS,
    entity_field_names,
)

if TYPE_CHECKING:
    from nebula3.gclient.net.SessionPool import Session

logger = logging.getLogger(__name__)


# NebulaGraph 保留字（部分），属性名出现时需用反引号包裹
# 实测在 NebulaGraph 3.7.0 上，以下字段名会导致 SyntaxError（必须反引号）：
#   steps, order, timestamp, path, rank, source
# 注意：name/config/type/key/label/value 实测不需要反引号
_RESERVED_WORDS: frozenset[str] = frozenset(
    {
        "path",
        "rank",
        "source",
        "timestamp",
        "steps",
        "order",
        "tag",
        "edge",
        "vertex",
        "step",
        "depth",
        "user",
        "password",
        "space",
        "config",
        "job",
    }
)


def _escape_prop_name(name: str) -> str:
    """属性名若是 NebulaGraph 保留字，用反引号包裹。

    Args:
        name: 属性名（camelCase）。

    Returns:
        原名或 ```name```。
    """
    return f"`{name}`" if name.lower() in _RESERVED_WORDS else name


class NebulaSchemaInitializer:
    """从 OntoAgent schema 自动创建 NebulaGraph Space + Tag + Edge + 索引。

    DDL 全部幂等（IF NOT EXISTS），属性统一用 string 类型（POC 简化策略）。
    保留字（timestamp、path 等）自动加反引号。
    """

    def __init__(self, session: Session, space_name: str = "ontoagent") -> None:
        """初始化 schema 创建器。

        Args:
            session: nebula3 Session 对象（已登录）。
            space_name: 目标 Space 名称，默认 ``ontoagent``。
        """
        self._session = session
        self._space_name = space_name

    def ensure_space(self, vid_type: str = "FIXED_STRING(36)") -> bool:
        """创建或确认 Space 存在。

        Args:
            vid_type: VID 类型，默认 ``FIXED_STRING(36)`` 匹配 OntoAgent UUID。

        Returns:
            是否执行成功。
        """
        ddl = (
            f"CREATE SPACE IF NOT EXISTS `{self._space_name}` "
            f"(vid_type={vid_type}, partition_num=10, replica_factor=1);"
        )
        result = self._session.execute(ddl)
        if not result.is_succeeded():
            logger.error("[NebulaSchema] create space failed: %s", result.error_msg)
            return False
        logger.info("[NebulaSchema] space '%s' ensured (vid_type=%s)", self._space_name, vid_type)
        return True

    def create_tags(self) -> list[str]:
        """为 13 个实体创建 Tag DDL（不执行，仅返回语句列表）。

        属性从 ``entity_field_names(label)`` 反射获取，全部使用 ``string`` 类型。
        额外追加 builder/pipeline 实际写入的通用字段：

        - provenance: ``provenanceSource``、``confidence``、``extractedAt``（来自 ``add_provenance()``）
        - ``codeParameters``（``entity_to_dict`` 将 ``entity.parameters`` 映射到此 key，
          与 schema 的 ``parameters`` 字段命名不同，需单独声明）

        所有字段统一用 ``string`` 类型，避免 ``_format_value`` 的类型不匹配错误。
        """
        common_fields = {
            "provenanceSource", "confidence", "extractedAt",
            "codeParameters",  # entity_to_dict 产出的 key（不同于 schema.parameters）
        }
        ddl_list: list[str] = []
        for label in VALID_ENTITY_LABELS:
            field_names = sorted(set(entity_field_names(label)) | common_fields)
            props = ", ".join(f"{_escape_prop_name(f)} string" for f in field_names)
            ddl = f"CREATE TAG IF NOT EXISTS `{label}` ({props});"
            ddl_list.append(ddl)
        return ddl_list

    def create_edges(self) -> list[str]:
        """为 26 个关系创建 Edge type DDL（不执行，仅返回语句列表）。

        Edge 无属性（POC 简化）。
        """
        ddl_list: list[str] = []
        for edge_type in RELATION_TYPE_TO_NEO4J.values():
            ddl_list.append(f"CREATE EDGE IF NOT EXISTS `{edge_type}` ();")
        return ddl_list

    def create_indexes(self) -> list[str]:
        """为每个 Tag 的 ``name`` 属性创建 Tag Index DDL（不执行，仅返回语句列表）。"""
        ddl_list: list[str] = []
        for label in VALID_ENTITY_LABELS:
            # name(64) — 字符串索引长度 64（足够覆盖大多数业务标识符）
            ddl = f"CREATE TAG INDEX IF NOT EXISTS `idx_{label}_name` ON `{label}`(`name`(64));"
            ddl_list.append(ddl)
        return ddl_list

    def initialize(self, vid_type: str = "FIXED_STRING(36)") -> bool:
        """完整初始化：Space + Tag + Edge + Index。

        DDL 全部幂等。注意 NebulaGraph DDL 是异步的，调用方需等待 ~20s 生效
        （本方法不在内部 sleep，避免单元测试阻塞；由调用方负责等待）。

        Args:
            vid_type: VID 类型，默认 ``FIXED_STRING(36)``。

        Returns:
            是否全部执行成功。
        """
        if not self.ensure_space(vid_type=vid_type):
            return False

        all_ddls: list[str] = []
        all_ddls.extend(self.create_tags())
        all_ddls.extend(self.create_edges())
        all_ddls.extend(self.create_indexes())

        # 注意：DDL 执行时不能 USE SPACE（创建 Space 后需等待生效）
        # 这里直接执行所有 DDL（NebulaGraph 会按 Space 名称解析）
        for ddl in all_ddls:
            full_stmt = f"USE `{self._space_name}`; {ddl}"
            result = self._session.execute(full_stmt)
            if not result.is_succeeded():
                logger.error("[NebulaSchema] DDL failed: %s | stmt=%s", result.error_msg, ddl)
                return False

        logger.info(
            "[NebulaSchema] initialized space '%s' (tags=%d, edges=%d, indexes=%d)",
            self._space_name,
            len(VALID_ENTITY_LABELS),
            len(RELATION_TYPE_TO_NEO4J),
            len(VALID_ENTITY_LABELS),
        )
        return True
