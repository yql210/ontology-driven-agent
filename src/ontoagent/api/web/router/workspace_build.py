"""Durable workspace build endpoints backed by ``WorkspaceBuildApplicationService``."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ontoagent.config import OntoAgentConfig
from ontoagent.parsing.service_graph.workspace.build_application_service import WorkspaceBuildApplicationService
from ontoagent.parsing.service_graph.workspace.models import WorkspaceGenerationState
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import WorkspaceServiceGraphPublishInput

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workspace-build"])
_background_tasks: set[asyncio.Task[None]] = set()


class WorkspaceRepositoryRequest(BaseModel):
    """One repository in a workspace build manifest."""

    model_config = ConfigDict(extra="forbid")

    repo_id: str = Field(min_length=1, pattern=r".*\S.*")
    path: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    git_url: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    branch: str = Field(min_length=1, pattern=r".*\S.*")
    source_revision: str = Field(min_length=1, pattern=r".*\S.*")
    languages: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source(self) -> WorkspaceRepositoryRequest:
        if (self.path is None) == (self.git_url is None):
            raise ValueError("repository must declare exactly one of path or git_url")
        return self


class WorkspaceManifestRequest(BaseModel):
    """The local-only manifest accepted by the workspace application service."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1, pattern=r".*\S.*")
    name: str = Field(min_length=1, pattern=r".*\S.*")
    repositories: list[WorkspaceRepositoryRequest] = Field(min_length=3)


class WorkspaceBuildRequest(BaseModel):
    """Durable workspace build submission payload."""

    model_config = ConfigDict(extra="forbid")

    manifest: WorkspaceManifestRequest
    generation_id: str = Field(min_length=1, pattern=r".*\S.*")
    idempotency_key: str = Field(min_length=1, pattern=r".*\S.*")
    expected_active_generation_id: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")


class WorkspaceBuildServiceFactory:
    """Create a request-scoped workspace build application service."""

    def create(self) -> WorkspaceBuildApplicationService:
        return WorkspaceBuildApplicationService.from_config(OntoAgentConfig.from_env())


workspace_build_service_factory = WorkspaceBuildServiceFactory()


def _state_reason(state: WorkspaceGenerationState) -> str:
    if state is WorkspaceGenerationState.ACTIVE:
        return "generation is active"
    return f"generation {state.value}"


async def _run_submission(
    service: WorkspaceBuildApplicationService, submission_request: WorkspaceServiceGraphPublishInput | None
) -> None:
    """Run blocking generation publication without retaining a database driver."""
    try:
        if submission_request is None:
            logger.error("scheduled workspace build has no submission request")
            return
        await asyncio.to_thread(service.run, submission_request)
    except Exception:
        logger.exception("workspace build execution failed")
    finally:
        service.close()


@router.post("/workspaces/{workspace_id}/build", status_code=202)
async def submit_workspace_build(workspace_id: str, body: WorkspaceBuildRequest) -> JSONResponse:
    """Durably accept a workspace generation and schedule its publication."""
    if workspace_id != body.manifest.workspace_id:
        raise HTTPException(status_code=422, detail="workspace_id must match manifest.workspace_id")

    service = workspace_build_service_factory.create()
    try:
        submission = service.submit(
            body.manifest.model_dump(),
            Path.cwd(),
            body.idempotency_key,
            body.generation_id,
            body.expected_active_generation_id,
        )
    except ValueError as error:
        service.close()
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        service.close()
        raise

    if submission.scheduled:
        task = asyncio.create_task(_run_submission(service, submission.request))
        # Keep the task strongly referenced while it owns the service connection.
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    else:
        service.close()

    return JSONResponse(
        status_code=202,
        content={
            "task_id": submission.task_id,
            "workspace_id": submission.workspace_id,
            "generation_id": submission.generation_id,
            "status": WorkspaceGenerationState.PENDING.name,
        },
    )


@router.get("/workspaces/{workspace_id}/tasks/{task_id}")
def get_workspace_build_task(workspace_id: str, task_id: str) -> JSONResponse:
    """Read the persisted state of one workspace generation task."""
    service = workspace_build_service_factory.create()
    try:
        status = service.get_task_status(workspace_id, task_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        service.close()

    if status is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return JSONResponse(
        content={
            "task_id": status.task_id,
            "workspace_id": status.workspace_id,
            "generation_id": status.generation_id,
            "status": status.state.name,
            "reason": _state_reason(status.state),
        }
    )
