from __future__ import annotations

from ontoagent.config import OntoAgentConfig
from ontoagent.store.graph_store import GraphStore
from ontoagent.store.nebula_store import NebulaGraphStore
from ontoagent.store.neo4j_store import Neo4jGraphStore


def create_graph_store(config: OntoAgentConfig) -> GraphStore:
    """根据配置创建 GraphStore 实例。

    Args:
        config: OntoAgentConfig，graph_backend 字段决定后端类型。

    Returns:
        GraphStore 实例（Neo4jGraphStore 或 NebulaGraphStore）。

    Raises:
        ValueError: 当 config.graph_backend 不是 "neo4j" 或 "nebula"。
    """
    backend = config.graph_backend
    if backend == "neo4j":
        return Neo4jGraphStore(
            uri=config.neo4j_uri,
            user=config.neo4j_user,
            password=config.neo4j_password,
        )
    if backend == "nebula":
        return NebulaGraphStore(
            host=config.nebula_host,
            port=config.nebula_port,
            user=config.nebula_user,
            password=config.nebula_password,
            space=config.nebula_space,
        )
    msg = f"Unsupported graph_backend: {backend!r} (expected 'neo4j' or 'nebula')"
    raise ValueError(msg)


def create_graph_store_from_env() -> GraphStore:
    """从环境变量加载配置并创建 GraphStore 实例。

    Returns:
        GraphStore 实例。
    """
    return create_graph_store(OntoAgentConfig.from_env())
