"""Dedicated Butler handler for workspace-scoped service graph builds."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ontoagent.butler.event_bus import ButlerEvent
from ontoagent.butler.handlers.base import BaseHandler, HandlerContext, HandlerResult
from ontoagent.parsing.service_graph.workspace.build_application_service import (
    WorkspaceBuildApplicationService,
    WorkspaceBuildTaskStatus,
    create_workspace_build_service,
)
from ontoagent.parsing.service_graph.workspace.models import WorkspaceGenerationState

if TYPE_CHECKING:
    from ontoagent.config import OntoAgentConfig


class WorkspaceBuildHandler(BaseHandler):
    """Run a validated local workspace build through the workspace application service."""

    handler_id = "workspace.build"
    event_types = ["workspace.build.requested"]

    def __init__(self, service: WorkspaceBuildApplicationService) -> None:
        self._service = service

    async def handle(self, event: ButlerEvent, ctx: HandlerContext) -> HandlerResult:
        """Persist, run, and report one workspace build using its durable task state."""
        del ctx
        try:
            manifest, manifest_dir, workspace_id, idempotency_key, generation_id, expected_active = _request(event)
            submission = await asyncio.to_thread(
                self._service.submit, manifest, manifest_dir, idempotency_key, generation_id, expected_active
            )
            if submission.workspace_id != workspace_id:
                raise ValueError("workspace_id does not match the submitted manifest")
            if submission.scheduled:
                if submission.request is None:
                    raise ValueError("scheduled workspace build is missing its request")
                await asyncio.to_thread(self._service.run, submission.request)
            status = await asyncio.to_thread(self.get_task_status, workspace_id, submission.task_id)
            if status is None:
                raise ValueError("workspace build task status is missing")
            data = _status_data(status)
            return HandlerResult(
                success=status.state is WorkspaceGenerationState.ACTIVE,
                data=data,
                error=None
                if status.state is WorkspaceGenerationState.ACTIVE
                else f"workspace generation is {status.state.name}",
                events=[ButlerEvent(event_type=_status_event_type(status.state), payload=data, source=self.handler_id)],
            )
        except (RuntimeError, ValueError, TypeError) as error:
            return HandlerResult(success=False, error=str(error))

    def get_task_status(self, workspace_id: str, task_id: str) -> WorkspaceBuildTaskStatus | None:
        """Return the persisted workspace task status for Butler callers."""
        return self._service.get_task_status(workspace_id, task_id)

    def close(self) -> None:
        """Release the workspace service's dedicated Neo4j driver."""
        self._service.close()


def create_workspace_build_handler(config: OntoAgentConfig) -> WorkspaceBuildHandler:
    """Create the Butler workspace-build dependency graph from application configuration."""
    return WorkspaceBuildHandler(create_workspace_build_service(config))


def _request(event: ButlerEvent) -> tuple[Mapping[str, object], Path, str, str, str, str | None]:
    payload = event.payload
    manifest = payload.get("manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("workspace build manifest must be an object")
    manifest_dir = payload.get("manifest_dir")
    if not isinstance(manifest_dir, str) or not manifest_dir.strip():
        raise ValueError("workspace build manifest_dir must be a nonblank string")
    workspace_id = _nonblank(payload.get("workspace_id"), "workspace_id")
    if manifest.get("workspace_id") != workspace_id:
        raise ValueError("workspace_id does not match manifest workspace_id")
    idempotency_key = _nonblank(payload.get("idempotency_key"), "idempotency_key")
    generation_id = _nonblank(payload.get("generation_id"), "generation_id")
    expected_active = payload.get("expected_active_generation_id")
    if expected_active is not None:
        expected_active = _nonblank(expected_active, "expected_active_generation_id")
    return manifest, Path(manifest_dir), workspace_id, idempotency_key, generation_id, expected_active


def _nonblank(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a nonblank string")
    return value.strip()


def _status_data(status: WorkspaceBuildTaskStatus) -> dict[str, str]:
    return {
        "task_id": status.task_id,
        "workspace_id": status.workspace_id,
        "generation_id": status.generation_id,
        "status": status.state.name,
    }


def _status_event_type(state: WorkspaceGenerationState) -> str:
    return f"workspace.build.task.{state.name.lower()}"
