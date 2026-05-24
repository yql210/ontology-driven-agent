from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(env_path: Path | None = None) -> None:
    """Load .env file into os.environ (no external dependency)."""
    path = env_path or Path(__file__).resolve().parent.parent.parent / ".env"
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


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
        agent_llm_provider: Agent LLM 提供商。
        agent_llm_model: Agent LLM 模型名称。
        agent_api_key: Agent API 密钥。
        agent_base_url: Agent API 基础 URL。
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
    build_doc_max_length: int = 2000
    build_skip_dirs: set[str] = field(
        default_factory=lambda: {
            "__pycache__",
            ".git",
            ".mypy_cache",
            ".ruff_cache",
            ".pytest_cache",
            "node_modules",
            ".venv",
            "venv",
            "site",
            ".tox",
            "dist",
            "build",
            "*.egg-info",
            # Java 常见多版本目录（Guava 特定）
            "android",
            "guava-gwt",
            "guava-testlib",
            "guava-tests",
            # 通用测试源码目录（Maven/Gradle 标准）
            "testlib",
        }
    )

    # Agent/LLM 配置（Phase 3 新增）
    agent_llm_provider: str = "zhipu"
    agent_llm_model: str = "glm-4-flash"
    agent_api_key: str = ""
    agent_base_url: str = "https://open.bigmodel.cn/api/anthropic"

    @classmethod
    def from_env(cls) -> LayerKGConfig:
        """从环境变量创建配置（自动加载 .env 文件）。

        支持的环境变量：
            LAYERKG_NEO4J_URI, LAYERKG_NEO4J_USER, LAYERKG_NEO4J_PASSWORD,
            LAYERKG_CHROMA_DIR, LAYERKG_OLLAMA_URL, LAYERKG_EMBEDDING_MODEL,
            LAYERKG_LLM_MODEL, LAYERKG_BUILD_INCLUDE_DOCS,
            LAYERKG_BUILD_DOC_EXTENSIONS, LAYERKG_BUILD_SKIP_DIRS,
            LAYERKG_BUILD_DOC_MAX_LENGTH, LAYERKG_AGENT_LLM_PROVIDER,
            LAYERKG_AGENT_LLM_MODEL, LAYERKG_AGENT_API_KEY,
            LAYERKG_AGENT_BASE_URL
        """
        _load_dotenv()

        # 解析 build_doc_extensions
        doc_ext_str = os.getenv("LAYERKG_BUILD_DOC_EXTENSIONS")
        build_doc_extensions = (
            [e.strip() for e in doc_ext_str.split(",") if e.strip()]
            if doc_ext_str
            else list(cls.__dataclass_fields__["build_doc_extensions"].default_factory())
        )

        # 解析 build_skip_dirs
        skip_dirs_str = os.getenv("LAYERKG_BUILD_SKIP_DIRS")
        build_skip_dirs = (
            {d.strip() for d in skip_dirs_str.split(",") if d.strip()}
            if skip_dirs_str
            else cls.__dataclass_fields__["build_skip_dirs"].default_factory()
        )

        return cls(
            neo4j_uri=os.getenv("LAYERKG_NEO4J_URI", cls.neo4j_uri),
            neo4j_user=os.getenv("LAYERKG_NEO4J_USER", cls.neo4j_user),
            neo4j_password=os.getenv("LAYERKG_NEO4J_PASSWORD", cls.neo4j_password),
            chroma_persist_dir=os.getenv("LAYERKG_CHROMA_DIR", cls.chroma_persist_dir),
            ollama_base_url=os.getenv("LAYERKG_OLLAMA_URL", cls.ollama_base_url),
            embedding_model=os.getenv("LAYERKG_EMBEDDING_MODEL", cls.embedding_model),
            llm_model=os.getenv("LAYERKG_LLM_MODEL", cls.llm_model),
            build_include_docs=os.getenv("LAYERKG_BUILD_INCLUDE_DOCS", "true").lower() == "true",
            build_doc_extensions=build_doc_extensions,
            build_skip_dirs=build_skip_dirs,
            build_doc_max_length=int(os.getenv("LAYERKG_BUILD_DOC_MAX_LENGTH", "2000")),
            agent_llm_provider=os.getenv("LAYERKG_AGENT_LLM_PROVIDER", cls.agent_llm_provider),
            agent_llm_model=os.getenv("LAYERKG_AGENT_LLM_MODEL", cls.agent_llm_model),
            agent_api_key=os.getenv("LAYERKG_AGENT_API_KEY", cls.agent_api_key),
            agent_base_url=os.getenv("LAYERKG_AGENT_BASE_URL", cls.agent_base_url),
        )
