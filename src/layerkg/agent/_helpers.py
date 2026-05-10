"""Agent 共享辅助函数 — lazy init 单例模式"""

from __future__ import annotations

from typing import TYPE_CHECKING

from layerkg.config import LayerKGConfig

if TYPE_CHECKING:
    from layerkg.chroma_store import ChromaStore
    from layerkg.neo4j_store import Neo4jGraphStore

_config: LayerKGConfig | None = None
_neo4j: Neo4jGraphStore | None = None
_chroma: ChromaStore | None = None


def get_config() -> LayerKGConfig:
    global _config
    if _config is None:
        _config = LayerKGConfig.from_env()
    return _config


def get_neo4j() -> Neo4jGraphStore:
    global _neo4j
    if _neo4j is None:
        from layerkg.neo4j_store import Neo4jGraphStore
        cfg = get_config()
        _neo4j = Neo4jGraphStore(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password)
    return _neo4j


def get_chroma() -> ChromaStore:
    global _chroma
    if _chroma is None:
        from layerkg.chroma_store import ChromaStore
        cfg = get_config()
        _chroma = ChromaStore(cfg.chroma_persist_dir, cfg.ollama_base_url, cfg.embedding_model)
    return _chroma
