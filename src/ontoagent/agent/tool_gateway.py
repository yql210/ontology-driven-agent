from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "tool_gateway.yaml"


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def _build_pattern(keywords: list[str]) -> re.Pattern:
    escaped = [re.escape(kw) for kw in keywords]
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


_config = _load_config()
_ENABLED = _config.get("enabled", True)
_KEYWORDS = _config.get("blocked_keywords", [])
if not _KEYWORDS:
    _KEYWORDS = ["SET", "DELETE", "REMOVE", "CREATE", "MERGE", "DROP", "DETACH DELETE", "FOREACH", "CALL apoc"]

_WRITE_KEYWORDS = _build_pattern(_KEYWORDS)


def is_write_cypher(cypher: str) -> bool:
    if not _ENABLED:
        return False
    return bool(_WRITE_KEYWORDS.search(cypher))


def validate_graph_query(cypher: str) -> tuple[bool, str]:
    if not _ENABLED:
        return True, "ok"
    if is_write_cypher(cypher):
        return False, "Cypher 写操作被拦截。写操作请使用 express_intent。"
    return True, "ok"
