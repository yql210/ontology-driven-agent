"""Tests for repo API router."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from ontoagent.api.web import app as app_module


@pytest.fixture
def mock_store() -> MagicMock:
    """Mock graph_store for repo router tests."""
    store = MagicMock()
    store.get_nodes_by_label.return_value = []
    store.merge_node.return_value = {"id": "stub", "name": "stub"}
    return store


@pytest.fixture
def test_client(mock_store: MagicMock) -> TestClient:
    """Create a TestClient with mocked graph_store.

    不走 lifespan（避免触发真实 Neo4j 连接），手动注入 mock graph_store。
    """
    app = app_module.create_app()
    app.state.graph_store = mock_store
    app.state.build_tasks = {}
    app.state.build_asyncio_tasks = {}
    return TestClient(app)


@pytest.mark.unit
def test_list_repos_empty(test_client: TestClient, mock_store: MagicMock):
    """GET /api/repos 空列表。"""
    mock_store.get_nodes_by_label.return_value = []
    response = test_client.get("/api/repos")
    assert response.status_code == 200
    assert response.json() == {"repos": []}
    mock_store.get_nodes_by_label.assert_called_once_with("RepositoryEntity", ["id", "name", "url", "status"])


@pytest.mark.unit
def test_list_repos_returns_list(test_client: TestClient, mock_store: MagicMock):
    """GET /api/repos 返回仓库列表。"""
    mock_store.get_nodes_by_label.return_value = [
        {"id": "repo-1", "name": "alpha", "url": "https://example.com/alpha.git", "status": "success"},
        {"id": "repo-2", "name": "beta", "url": "https://example.com/beta.git", "status": "pending"},
    ]
    response = test_client.get("/api/repos")
    assert response.status_code == 200
    repos = response.json()["repos"]
    assert len(repos) == 2
    assert repos[0]["name"] == "alpha"
    assert repos[1]["status"] == "pending"


@pytest.mark.unit
def test_register_repo_writes_node(test_client: TestClient, mock_store: MagicMock):
    """POST /api/repos 注册仓库并写入 RepositoryEntity。"""
    response = test_client.post(
        "/api/repos",
        json={"name": "new-repo", "url": "https://example.com/new.git", "branch": "dev"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "new-repo"
    assert data["url"] == "https://example.com/new.git"
    assert data["branch"] == "dev"
    assert data["status"] == "pending"
    # 校验 merge_node 被调用，label 正确
    mock_store.merge_node.assert_called_once()
    args, _ = mock_store.merge_node.call_args
    assert args[0] == "RepositoryEntity"
    props = args[1]
    assert props["name"] == "new-repo"
    assert props["url"] == "https://example.com/new.git"
    assert props["branch"] == "dev"
    assert props["status"] == "pending"
    assert props["id"]


@pytest.mark.unit
def test_register_repo_empty_name_returns_422(test_client: TestClient):
    """POST /api/repos 空 name 返回 422。"""
    response = test_client.post("/api/repos", json={"name": "", "url": "", "branch": "main"})
    assert response.status_code == 422


@pytest.mark.unit
def test_register_repo_minimal_payload(test_client: TestClient, mock_store: MagicMock):
    """POST /api/repos 最小 payload（只有 name）。"""
    response = test_client.post("/api/repos", json={"name": "minimal"})
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "minimal"
    assert data["url"] == ""
    assert data["branch"] == "main"
    assert data["status"] == "pending"
