from __future__ import annotations

import os
from dataclasses import dataclass, field


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
        build_include_docs: 是否包含文档文件。
        build_doc_extensions: 文档文件扩展名列表。
        build_skip_dirs: 跳过的目录集合。
    """

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    chroma_persist_dir: str = ".chroma"
    ollama_base_url: str = "http://localhost:11434"
    embedding_model: str = "qwen2.5-coder:0.5b"
    llm_model: str = "qwen3.5:9b"

    # Build 配置
    build_include_docs: bool = True
    build_doc_extensions: list[str] = field(default_factory=lambda: [".md", ".rst"])
    build_skip_dirs: set[str] = field(
        default_factory=lambda: {
            "__pycache__",
            ".git",
            ".mypy_cache",
            ".ruff_cache",
            "node_modules",
            ".venv",
            "site",
            ".tox",
            "dist",
            "build",
            "*.egg-info",
        }
    )

    @classmethod
    def from_env(cls) -> LayerKGConfig:
        """从环境变量创建配置。

        支持的环境变量：
            LAYERKG_NEO4J_URI, LAYERKG_NEO4J_USER, LAYERKG_NEO4J_PASSWORD,
            LAYERKG_CHROMA_DIR, LAYERKG_OLLAMA_URL, LAYERKG_EMBEDDING_MODEL,
            LAYERKG_LLM_MODEL, LAYERKG_BUILD_INCLUDE_DOCS
        """
        return cls(
            neo4j_uri=os.getenv("LAYERKG_NEO4J_URI", cls.neo4j_uri),
            neo4j_user=os.getenv("LAYERKG_NEO4J_USER", cls.neo4j_user),
            neo4j_password=os.getenv("LAYERKG_NEO4J_PASSWORD", cls.neo4j_password),
            chroma_persist_dir=os.getenv("LAYERKG_CHROMA_DIR", cls.chroma_persist_dir),
            ollama_base_url=os.getenv("LAYERKG_OLLAMA_URL", cls.ollama_base_url),
            embedding_model=os.getenv("LAYERKG_EMBEDDING_MODEL", cls.embedding_model),
            llm_model=os.getenv("LAYERKG_LLM_MODEL", cls.llm_model),
            build_include_docs=os.getenv("LAYERKG_BUILD_INCLUDE_DOCS", "true").lower() == "true",
        )
