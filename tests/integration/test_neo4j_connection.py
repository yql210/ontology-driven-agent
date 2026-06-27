from __future__ import annotations

import os

import pytest

from ontoagent.config import OntoAgentConfig


@pytest.mark.integration
def test_config_defaults():
    """Test OntoAgentConfig default values."""
    config = OntoAgentConfig()
    assert config.neo4j_uri == "bolt://localhost:7687"
    assert config.neo4j_user == "neo4j"
    assert config.chroma_persist_dir == ".chroma"
    assert config.ollama_base_url == "http://localhost:11434"
    assert config.embedding_model == "qwen2.5-coder:0.5b"


@pytest.mark.integration
def test_config_from_env(monkeypatch):
    """Test OntoAgentConfig.from_env reads from environment."""
    monkeypatch.setenv("ONTOAGENT_NEO4J_URI", "bolt://custom:7687")
    monkeypatch.setenv("ONTOAGENT_NEO4J_PASSWORD", "secret")
    config = OntoAgentConfig.from_env()
    assert config.neo4j_uri == "bolt://custom:7687"
    assert config.neo4j_password == "secret"


@pytest.mark.integration
def test_neo4j_real_connection():
    """Test real Neo4j connection.

    需要运行 Neo4j 服务，通过 ONTOAGENT_NEO4J_URI 等环境变量配置连接。
    """
    from neo4j import GraphDatabase

    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER", "neo4j")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")

    if not uri or not password:
        pytest.skip("需要设置 ONTOAGENT_NEO4J_URI 和 ONTOAGENT_NEO4J_PASSWORD 环境变量")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            result = session.run("RETURN 1 AS check")
            record = result.single()
            assert record is not None
            assert record["check"] == 1
    finally:
        driver.close()
