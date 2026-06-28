from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Cypher 写操作关键词
_WRITE_KEYWORDS = re.compile(
    r"\b(SET|DELETE|REMOVE|CREATE|MERGE|DROP|DETACH\s+DELETE|FOREACH|CALL\s+apoc)\b",
    re.IGNORECASE,
)


def is_write_cypher(cypher: str) -> bool:
    """检测 Cypher 是否包含写操作。"""
    return bool(_WRITE_KEYWORDS.search(cypher))


def validate_graph_query(cypher: str) -> tuple[bool, str]:
    """验证 graph_query 的 Cypher 语句。

    Returns:
        (is_allowed, reason)
    """
    if is_write_cypher(cypher):
        return False, "Cypher 写操作被拦截: 检测到 SET/DELETE/CREATE/MERGE/DROP。写操作请使用 express_intent。"
    return True, "ok"
