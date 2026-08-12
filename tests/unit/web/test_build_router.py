"""Tests for build API router."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ontoagent.api.web import app as app_module
from ontoagent.api.web.router.build import BuildStatusResponse, _update_repo_status
from ontoagent.pipeline.builder import BuildResult


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


@pytest.mark.unit
def test_stream_build_status_returns_sse(test_client: TestClient):
    """GET /api/build/stream/{task_id} 返回 text/event-stream。"""
    task_id = "test-sse-task-id"
    # 预置终态状态：SSE 推一次后立即关闭，避免测试阻塞
    test_client.app.state.build_tasks[task_id] = BuildStatusResponse(task_id=task_id, status="success", repo_id="test")

    response = test_client.get(f"/api/build/stream/{task_id}")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")


@pytest.mark.unit
def test_stream_build_status_404_for_unknown_task(test_client: TestClient):
    """GET /api/build/stream/{unknown} 返回 404。"""
    response = test_client.get("/api/build/stream/nonexistent-task-id")
    assert response.status_code == 404


def _wait_for_status(test_client: TestClient, task_id: str, *, timeout: float = 5.0) -> dict:
    """轮询任务状态直到终态（success/failed），避免后台任务未完成时断言。"""
    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        resp = test_client.get(f"/api/build/status/{task_id}")
        last = resp.json()
        if last["status"] in {"success", "failed"}:
            return last
        time.sleep(0.01)
    pytest.fail(f"task {task_id} did not finish within {timeout}s (last: {last})")


def _wait_for_merge_node(graph_store, *, timeout: float = 5.0) -> None:
    """轮询等待 merge_node 被调用。

    后台任务先把 task 状态置为终态，随后才回写图库，故终态到达不代表回写已完成。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if graph_store.merge_node.called:
            return
        time.sleep(0.01)
    pytest.fail(f"merge_node was not called within {timeout}s")


@pytest.mark.unit
def test_build_progress_updates_stage_and_logs(test_client: TestClient, tmp_path: Path) -> None:
    """mock builder.build 模拟 progress_callback → 状态中 stage/stage_detail/logs 更新。"""

    # Arrange
    def fake_build(
        repo_path,
        *,
        repo_id: str = "default",
        skip_semantic: bool = False,
        skip_clustering: bool = False,
        clear: bool = False,
        progress_callback=None,
    ):
        logging.getLogger("ontoagent.pipeline").info("building started")
        assert progress_callback is not None
        progress_callback("parse", "Parsed 1 entities, Resolved 1 relations")
        progress_callback("structural_write", "Wrote 1 relations")
        return BuildResult(files_scanned=1, entities_created=1, relations_created=1)

    with patch("ontoagent.pipeline.builder.OntoAgentBuilder") as mock_builder_cls:
        mock_builder_cls.return_value.build.side_effect = fake_build
        # Act
        post = test_client.post("/api/build", json={"repo_url": str(tmp_path)})
    assert post.status_code == 202
    task_id = post.json()["task_id"]

    # 轮询到终态
    data = _wait_for_status(test_client, task_id)
    assert data["status"] == "success"
    assert data["stage"] == "structural_write"
    assert data["stage_detail"] == "Wrote 1 relations"
    assert data["logs"]


@pytest.mark.unit
def test_build_failure_records_logs_and_message(test_client: TestClient, tmp_path: Path) -> None:
    """mock build 抛 RuntimeError + 记录日志 → status=failed + message + logs。"""

    # Arrange
    def fake_build(*args, **kwargs):
        logging.getLogger("ontoagent.pipeline").error("stage 2 failed")
        raise RuntimeError("simulated build failure")

    with patch("ontoagent.pipeline.builder.OntoAgentBuilder") as mock_builder_cls:
        mock_builder_cls.return_value.build.side_effect = fake_build
        # Act
        post = test_client.post("/api/build", json={"repo_url": str(tmp_path)})
    task_id = post.json()["task_id"]

    # 轮询到终态
    data = _wait_for_status(test_client, task_id)
    assert data["status"] == "failed"
    assert "RuntimeError" in data["message"]
    assert "simulated build failure" in data["message"]
    assert "stage 2 failed" in data["logs"]


@pytest.mark.unit
def test_build_success_returns_result_and_frozen_logs(test_client: TestClient, tmp_path: Path) -> None:
    """mock build 正常返回 BuildResult → status=success + result 完整 + logs 冻结。"""

    # Arrange
    def fake_build(*args, **kwargs):
        logging.getLogger("ontoagent.pipeline").info("stage 2 complete")
        return BuildResult(files_scanned=2, entities_created=5, relations_created=3)

    with patch("ontoagent.pipeline.builder.OntoAgentBuilder") as mock_builder_cls:
        mock_builder_cls.return_value.build.side_effect = fake_build
        # Act
        post = test_client.post("/api/build", json={"repo_url": str(tmp_path)})
    task_id = post.json()["task_id"]

    # 轮询到终态
    data = _wait_for_status(test_client, task_id)
    assert data["status"] == "success"
    assert data["result"]["files_scanned"] == 2
    assert data["result"]["entities_created"] == 5
    assert data["result"]["relations_created"] == 3
    assert data["logs"]


@pytest.mark.unit
def test_build_success_writes_repo_status(test_client: TestClient, tmp_path: Path) -> None:
    """构建成功 → RepositoryEntity.status 回写为 success。"""

    # Arrange
    graph_store = test_client.app.state.graph_store
    graph_store.get_nodes_by_label.return_value = [{"id": "repo-uuid", "name": "test-repo", "status": "building"}]

    def fake_build(*args, **kwargs):
        return BuildResult(files_scanned=1, entities_created=1, relations_created=1)

    with patch("ontoagent.pipeline.builder.OntoAgentBuilder") as mock_builder_cls:
        mock_builder_cls.return_value.build.side_effect = fake_build
        # Act
        post = test_client.post(
            "/api/build",
            json={"repo_url": str(tmp_path), "repo_id": "test-repo"},
        )
    task_id = post.json()["task_id"]

    # 轮询到终态，再等图库回写完成（状态置为终态后才调用 _update_repo_status）
    data = _wait_for_status(test_client, task_id)
    assert data["status"] == "success"
    _wait_for_merge_node(graph_store)
    graph_store.merge_node.assert_called_once_with(
        "RepositoryEntity",
        {"id": "repo-uuid", "name": "test-repo", "status": "success"},
    )


@pytest.mark.unit
def test_build_failure_writes_repo_status(test_client: TestClient, tmp_path: Path) -> None:
    """构建失败 → RepositoryEntity.status 回写为 failed。"""

    # Arrange
    graph_store = test_client.app.state.graph_store
    graph_store.get_nodes_by_label.return_value = [{"id": "repo-uuid", "name": "test-repo", "status": "building"}]

    def fake_build(*args, **kwargs):
        raise RuntimeError("simulated build failure")

    with patch("ontoagent.pipeline.builder.OntoAgentBuilder") as mock_builder_cls:
        mock_builder_cls.return_value.build.side_effect = fake_build
        # Act
        post = test_client.post(
            "/api/build",
            json={"repo_url": str(tmp_path), "repo_id": "test-repo"},
        )
    task_id = post.json()["task_id"]

    data = _wait_for_status(test_client, task_id)
    assert data["status"] == "failed"
    _wait_for_merge_node(graph_store)
    graph_store.merge_node.assert_called_once_with(
        "RepositoryEntity",
        {"id": "repo-uuid", "name": "test-repo", "status": "failed"},
    )


@pytest.mark.unit
def test_update_repo_status_no_nodes_does_not_merge() -> None:
    """get_nodes_by_label 返回空列表 → 不抛异常、merge_node 不被调用。"""

    # Arrange
    graph_store = MagicMock()
    graph_store.get_nodes_by_label.return_value = []
    request = MagicMock()
    request.app.state.graph_store = graph_store

    # Act
    _update_repo_status(request, "test-repo", "success")

    # Assert
    graph_store.get_nodes_by_label.assert_called_once_with("RepositoryEntity", ["id", "name", "status"])
    graph_store.merge_node.assert_not_called()


@pytest.mark.unit
def test_update_repo_status_none_id_skips_merge() -> None:
    """节点 id 为 None → 跳过（merge_node 不被调用）。"""

    # Arrange
    graph_store = MagicMock()
    graph_store.get_nodes_by_label.return_value = [{"id": None, "name": "test-repo"}]
    request = MagicMock()
    request.app.state.graph_store = graph_store

    # Act
    _update_repo_status(request, "test-repo", "success")

    # Assert
    graph_store.merge_node.assert_not_called()


@pytest.mark.unit
def test_update_repo_status_store_error_logs_warning(caplog) -> None:
    """graph_store 抛异常 → 仅记 warning，不向调用方传播异常。"""

    # Arrange
    graph_store = MagicMock()
    graph_store.get_nodes_by_label.side_effect = RuntimeError("boom")
    request = MagicMock()
    request.app.state.graph_store = graph_store

    # Act
    with caplog.at_level(logging.WARNING, logger="ontoagent.api.web.router.build"):
        _update_repo_status(request, "test-repo", "success")

    # Assert
    graph_store.merge_node.assert_not_called()
    assert any("Failed to update repo status" in r.message for r in caplog.records)
