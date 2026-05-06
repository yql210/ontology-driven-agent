from __future__ import annotations

import os

import pytest

from layerkg.config import LayerKGConfig


@pytest.mark.integration
def test_config_defaults():
    """Test LayerKGConfig default values."""
    config = LayerKGConfig()
    assert config.neo4j_uri == "bolt://localhost:7687"
    assert config.neo4j_user == "neo4j"
    assert config.chroma_persist_dir == ".chroma"
    assert config.ollama_base_url == "http://localhost:11434"
    assert config.embedding_model == "qwen2.5-coder:0.5b"


@pytest.mark.integration
def test_config_from_env(monkeypatch):
    """Test LayerKGConfig.from_env reads from environment."""
    monkeypatch.setenv("LAYERKG_NEO4J_URI", "bolt://custom:7687")
    monkeypatch.setenv("LAYERKG_NEO4J_PASSWORD", "secret")
    config = LayerKGConfig.from_env()
    assert config.neo4j_uri == "bolt://custom:7687"
    assert config.neo4j_password == "secret"


@pytest.mark.integration
def test_neo4j_real_connection():
    """Test real Neo4j connection.

    需要运行 Neo4j 服务，默认连接 bolt://REDACTED_IP:7687。
    """
    from neo4j import GraphDatabase

    uri = os.getenv("LAYERKG_NEO4J_URI", "bolt://REDACTED_IP:7687")
    user = os.getenv("LAYERKG_NEO4J_USER", "neo4j")
    password = os.getenv("LAYERKG_NEO4J_PASSWORD", "REDACTED_PASSWORD")

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
