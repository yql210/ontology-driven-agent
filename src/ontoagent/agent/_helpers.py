"""Agent 共享辅助函数 — lazy init 单例模式"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ontoagent.config import OntoAgentConfig

if TYPE_CHECKING:
    from ontoagent.pipeline.aligner import ConceptAligner
    from ontoagent.pipeline.impact_propagator import ImpactPropagator
    from ontoagent.pipeline.module_clustering import ModuleClustering
    from ontoagent.store.chroma_store import ChromaStore
    from ontoagent.store.graph_store import GraphStore

_config: OntoAgentConfig | None = None
_graph_store: GraphStore | None = None
_chroma: ChromaStore | None = None
_aligner: ConceptAligner | None = None
_clustering: ModuleClustering | None = None
_impact_propagator: ImpactPropagator | None = None


def get_config() -> OntoAgentConfig:
    global _config
    if _config is None:
        _config = OntoAgentConfig.from_env()
    return _config


def get_graph_store() -> GraphStore:
    """获取或创建 GraphStore 单例（由 factory 根据 config.graph_backend 路由后端）。"""
    global _graph_store
    if _graph_store is None:
        from ontoagent.store.factory import create_graph_store

        _graph_store = create_graph_store(get_config())
    return _graph_store


def get_neo4j() -> GraphStore:
    """向后兼容别名：返回当前后端的 GraphStore（可能是 Neo4j 或 Nebula）。

    历史调用方使用 ``get_neo4j()`` 拿存储实例；多后端改造后改为 ``get_graph_store()``。
    保留此函数避免上游破坏，内部委托给 ``get_graph_store``。
    """
    return get_graph_store()


def get_chroma() -> ChromaStore:
    global _chroma
    if _chroma is None:
        from ontoagent.store.chroma_store import ChromaStore

        cfg = get_config()
        _chroma = ChromaStore(cfg.chroma_persist_dir, cfg.ollama_base_url, cfg.embedding_model)
    return _chroma


def get_aligner() -> ConceptAligner:
    """获取 ConceptAligner 单例（从 Neo4j 加载已有概念）。"""
    global _aligner
    if _aligner is None:
        graph_store = get_graph_store()
        # 从图数据库加载概念，否则 list_concepts() 返回空列表
        results = graph_store.query(
            "MATCH (c:ConceptEntity) RETURN c.id AS id, c.name AS name, "
            "c.entity_type AS type, c.description AS description, c.aliases AS aliases"
        )
        from ontoagent.domain.schema import ConceptEntity
        from ontoagent.pipeline.aligner import ConceptAligner

        concepts = []
        for r in results:
            aliases = r.get("aliases") or []
            concepts.append(
                ConceptEntity(
                    id=r["id"],
                    name=r["name"],
                    entity_type=r["type"],
                    description=r.get("description"),
                    aliases=set(aliases),
                )
            )
        _aligner = ConceptAligner(
            chroma_store=get_chroma(),
            neo4j_store=graph_store,
            concepts=concepts,
        )
    return _aligner


def get_clustering() -> ModuleClustering:
    """获取 ModuleClustering 单例。"""
    global _clustering
    if _clustering is None:
        from ontoagent.pipeline.module_clustering import ModuleClustering

        _clustering = ModuleClustering(neo4j_store=get_graph_store())
    return _clustering


def get_impact_propagator() -> ImpactPropagator:
    """获取 ImpactPropagator 单例。"""
    global _impact_propagator
    if _impact_propagator is None:
        from ontoagent.pipeline.impact_propagator import ImpactPropagator

        _impact_propagator = ImpactPropagator(graph_store=get_graph_store())
    return _impact_propagator
