"""Tests for build API router."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from ontoagent.api.web import app as app_module


@pytest.fixture
def test_client() -> TestClient:
    """Create a TestClient with mocked graph_store and pre-initialized build_tasks.

    不走 lifespan（避免触发真实 Neo4j 连接），手动初始化 ``app.state``。
    """
    app = app_module.create_app()
    # 手动初始化 lifespan 中本应设置的 state（不走 TestClient context manager）
    app.state.graph_store = MagicMock()
    app.state.build_tasks = {}
    app.state.build_asyncio_tasks = {}
    return TestClient(app)


@pytest.mark.unit
def test_build_returns_202_with_task_id(test_client: TestClient):
    """POST /api/build 返回 202 + task_id（本地路径，不触发 clone）。"""
    response = test_client.post(
        "/api/build",
        json={"repo_url": "./nonexistent-local-path-for-test"},
    )
    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "accepted"
    # task_id 是 uuid4().hex（32 字符）
    assert len(data["task_id"]) == 32


@pytest.mark.unit
def test_build_status_returns_state(test_client: TestClient):
    """GET /api/build/status/{task_id} 返回 200 + 状态字段。"""
    # 触发一次构建
    post = test_client.post(
        "/api/build",
        json={"repo_url": "./nonexistent-local-path-for-test"},
    )
    task_id = post.json()["task_id"]

    # 查询状态
    response = test_client.get(f"/api/build/status/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == task_id
    # 状态可能是 pending/cloning/building/success/failed（取决于后台任务是否已运行）
    assert data["status"] in {"pending", "cloning", "building", "success", "failed"}
    assert "repo_id" in data


@pytest.mark.unit
def test_build_status_404_for_unknown_task(test_client: TestClient):
    """GET /api/build/status/{unknown} 返回 404。"""
    response = test_client.get("/api/build/status/nonexistent-task-id")
    assert response.status_code == 404
    assert "nonexistent-task-id" in response.json()["detail"]


@pytest.mark.unit
def test_build_empty_repo_url_returns_422(test_client: TestClient):
    """POST /api/build 空 repo_url 返回 422（Pydantic 校验失败）。"""
    response = test_client.post("/api/build", json={"repo_url": ""})
    assert response.status_code == 422


@pytest.mark.unit
def test_build_invalid_remote_url_returns_400(test_client: TestClient):
    """POST /api/build 远程 URL 校验失败返回 400。"""
    response = test_client.post("/api/build", json={"repo_url": "not-a-valid-url"})
    assert response.status_code == 400
