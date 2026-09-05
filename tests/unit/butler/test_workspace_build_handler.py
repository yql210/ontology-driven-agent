"""Butler vertical slice for durable workspace graph builds."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ontoagent.butler.engine import ButlerEngine
from ontoagent.butler.event_bus import ButlerEvent
from ontoagent.butler.handlers.base import HandlerContext
from ontoagent.config import OntoAgentConfig
from ontoagent.parsing.service_graph.workspace.models import WorkspaceGenerationState


@dataclass(frozen=True)
class _Submission:
    task_id: str = "task-1"
    workspace_id: str = "workspace-1"
    generation_id: str = "generation-1"
    scheduled: bool = True
    request: object | None = object()


@dataclass(frozen=True)
class _Status:
    task_id: str = "task-1"
    workspace_id: str = "workspace-1"
    generation_id: str = "generation-1"
    state: WorkspaceGenerationState = WorkspaceGenerationState.ACTIVE


def _event(**overrides: object) -> ButlerEvent:
    payload: dict[str, object] = {
        "workspace_id": "workspace-1",
        "generation_id": "generation-1",
        "idempotency_key": "request-1",
        "manifest_dir": "/workspace",
        "manifest": {
            "workspace_id": "workspace-1",
            "name": "Workspace",
            "repositories": [
                {"repo_id": "one", "path": "one", "branch": "main", "source_revision": "a", "languages": ["java"]},
                {"repo_id": "two", "path": "two", "branch": "main", "source_revision": "b", "languages": ["java"]},
                {"repo_id": "three", "path": "three", "branch": "main", "source_revision": "c", "languages": ["java"]},
            ],
        },
    }
    payload.update(overrides)
    return ButlerEvent(event_type="workspace.build.requested", payload=payload, source="test")


class TestWorkspaceBuildHandler:
    def test_handler_has_dedicated_event_contract(self) -> None:
        from ontoagent.butler.handlers.workspace_build import WorkspaceBuildHandler

        handler = WorkspaceBuildHandler(MagicMock())

        assert handler.handler_id == "workspace.build"
        assert handler.event_types == ["workspace.build.requested"]

    @pytest.mark.asyncio
    async def test_event_routes_to_workspace_build_application_service(self) -> None:
        from ontoagent.butler.handlers.workspace_build import WorkspaceBuildHandler

        service = MagicMock()
        service.submit.return_value = _Submission()
        service.get_task_status.return_value = _Status()
        handler = WorkspaceBuildHandler(service)

        result = await handler.handle(_event(), HandlerContext(OntoAgentConfig()))

        assert result.success is True
        service.submit.assert_called_once_with(
            _event().payload["manifest"], Path("/workspace"), "request-1", "generation-1", None
        )
        service.run.assert_called_once_with(service.submit.return_value.request)
        service.get_task_status.assert_called_once_with("workspace-1", "task-1")
        assert result.data == {
            "task_id": "task-1",
            "workspace_id": "workspace-1",
            "generation_id": "generation-1",
            "status": "ACTIVE",
        }
        assert [event.event_type for event in result.events] == ["workspace.build.task.active"]

    @pytest.mark.asyncio
    async def test_validation_failure_does_not_schedule_or_publish(self) -> None:
        from ontoagent.butler.handlers.workspace_build import WorkspaceBuildHandler

        service = MagicMock()
        service.submit.side_effect = ValueError("repository one revision mismatch")
        handler = WorkspaceBuildHandler(service)

        result = await handler.handle(_event(), HandlerContext(OntoAgentConfig()))

        assert result.success is False
        assert result.events == []
        service.run.assert_not_called()
        service.get_task_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_idempotent_task_is_read_back_without_republishing(self) -> None:
        from ontoagent.butler.handlers.workspace_build import WorkspaceBuildHandler

        service = MagicMock()
        service.submit.return_value = _Submission(scheduled=False, request=None)
        service.get_task_status.return_value = _Status()

        result = await WorkspaceBuildHandler(service).handle(_event(), HandlerContext(OntoAgentConfig()))

        assert result.success is True
        service.run.assert_not_called()
        service.get_task_status.assert_called_once_with("workspace-1", "task-1")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("state", "event_type"),
        [
            (WorkspaceGenerationState.ACTIVE, "workspace.build.task.active"),
            (WorkspaceGenerationState.FAILED, "workspace.build.task.failed"),
            (WorkspaceGenerationState.BLOCKED, "workspace.build.task.blocked"),
        ],
    )
    async def test_persisted_terminal_state_maps_to_truthful_status_event(
        self, state: WorkspaceGenerationState, event_type: str
    ) -> None:
        from ontoagent.butler.handlers.workspace_build import WorkspaceBuildHandler

        service = MagicMock()
        service.submit.return_value = _Submission()
        service.get_task_status.return_value = _Status(state=state)

        result = await WorkspaceBuildHandler(service).handle(_event(), HandlerContext(OntoAgentConfig()))

        assert result.success is (state is WorkspaceGenerationState.ACTIVE)
        assert result.data["status"] == state.name
        assert result.events[-1].event_type == event_type
        assert result.events[-1].payload == result.data

    def test_handler_does_not_import_legacy_publisher_or_generic_graph_store(self) -> None:
        import ontoagent.butler.handlers.workspace_build as module

        source = inspect.getsource(module)

        assert "parsing.service_graph.publish_orchestrator" not in source
        assert "store.graph_store" not in source
        assert "get_graph_store" not in source


@pytest.mark.asyncio
async def test_engine_routes_workspace_event_and_publishes_status_events(tmp_path: Path) -> None:
    from ontoagent.butler.handlers.workspace_build import WorkspaceBuildHandler

    config = OntoAgentConfig()
    config.data_dir = str(tmp_path / ".ontoagent")
    service = MagicMock()
    service.submit.return_value = _Submission()
    service.get_task_status.return_value = _Status()
    engine = ButlerEngine(config)
    engine.register_handler(WorkspaceBuildHandler(service))
    received: list[ButlerEvent] = []
    subscription_id = engine._bus.subscribe("workspace.build.task.active", received.append)
    try:
        await engine.start()
        results = await engine.submit_event(_event())
        await asyncio.sleep(0)

        assert len(results) == 1
        assert results[0].success is True
        assert [event.payload for event in received] == [
            {"task_id": "task-1", "workspace_id": "workspace-1", "generation_id": "generation-1", "status": "ACTIVE"}
        ]
    finally:
        engine._bus.unsubscribe(subscription_id)
        await engine.stop()
