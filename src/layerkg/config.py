from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class LayerKGConfig:
    """LayerKG 配置。

    Attributes:
        neo4j_uri: Neo4j 连接 URI。
        neo4j_user: Neo4j 用户名。
        neo4j_password: Neo4j 密码。
        chroma_persist_dir: ChromaDB 持久化目录。
        ollama_base_url: Ollama 服务地址。
        embedding_model: 嵌入模型名称。
        llm_model: LLM 模型名称。
    """

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    chroma_persist_dir: str = ".chroma"
    ollama_base_url: str = "http://localhost:11434"
    embedding_model: str = "qwen2.5-coder:0.5b"
    llm_model: str = "qwen3.5:9b"

    @classmethod
    def from_env(cls) -> LayerKGConfig:
        """从环境变量创建配置。

        支持的环境变量：
            LAYERKG_NEO4J_URI, LAYERKG_NEO4J_USER, LAYERKG_NEO4J_PASSWORD,
            LAYERKG_CHROMA_DIR, LAYERKG_OLLAMA_URL, LAYERKG_EMBEDDING_MODEL,
            LAYERKG_LLM_MODEL
        """
        return cls(
            neo4j_uri=os.getenv("LAYERKG_NEO4J_URI", cls.neo4j_uri),
            neo4j_user=os.getenv("LAYERKG_NEO4J_USER", cls.neo4j_user),
            neo4j_password=os.getenv("LAYERKG_NEO4J_PASSWORD", cls.neo4j_password),
            chroma_persist_dir=os.getenv("LAYERKG_CHROMA_DIR", cls.chroma_persist_dir),
            ollama_base_url=os.getenv("LAYERKG_OLLAMA_URL", cls.ollama_base_url),
            embedding_model=os.getenv("LAYERKG_EMBEDDING_MODEL", cls.embedding_model),
            llm_model=os.getenv("LAYERKG_LLM_MODEL", cls.llm_model),
        )
