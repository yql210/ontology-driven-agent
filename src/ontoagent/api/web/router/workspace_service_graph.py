"""Workspace-scoped, ACL-gated service graph read endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.responses import JSONResponse

from ontoagent.api.workspace_service_graph import (
    workspace_graph_page_envelope,
    workspace_service_graph_query_service_factory,
)
from ontoagent.domain.workspace_authorization import PrincipalIdentity, WorkspaceAuthorizationFailure
from ontoagent.domain.workspace_graph_query import WorkspaceGraphQueryRequest, WorkspaceGraphQueryValidationError
from ontoagent.execution.workspace_query_authorization import WorkspaceAuthorizationError
from ontoagent.execution.workspace_service_graph_query import WorkspaceServiceGraphQueryService

router = APIRouter(tags=["workspace-service-graph"])
Nonblank = Annotated[str, Query(min_length=1, pattern=r".*\S.*")]
EndpointPath = Annotated[str, Path(min_length=1, pattern=r".*\S.*")]


def workspace_principal(x_workspace_principal: Annotated[str | None, Header()] = None) -> PrincipalIdentity:
    """Resolve the authenticated workspace principal; override this dependency in deployments/tests."""
    try:
        return PrincipalIdentity(x_workspace_principal or "")
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid workspace principal") from error


def _request(
    workspace_id: str,
    generation_id: str | None,
    repo_id: str | None,
    page_size: int,
    cursor: str | None,
    depth: int,
    node_limit: int,
) -> WorkspaceGraphQueryRequest:
    try:
        return WorkspaceGraphQueryRequest(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit)
    except WorkspaceGraphQueryValidationError as error:
        raise HTTPException(status_code=422, detail="invalid workspace graph request") from error


def _run(
    principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest, operation: Callable[..., object], *args: str
) -> JSONResponse:
    try:
        with workspace_service_graph_query_service_factory.create() as service:
            page = operation(service, principal, request, *args)
        return JSONResponse(content=workspace_graph_page_envelope(page))  # type: ignore[arg-type]
    except WorkspaceAuthorizationError as error:
        status = {
            WorkspaceAuthorizationFailure.FORBIDDEN: 403,
            WorkspaceAuthorizationFailure.NOT_FOUND: 404,
            WorkspaceAuthorizationFailure.CONFLICT: 409,
        }[error.failure]
        raise HTTPException(status_code=status, detail=error.failure.value) from None
    except WorkspaceGraphQueryValidationError:
        raise HTTPException(status_code=422, detail="invalid workspace graph request") from None


def _directory(
    service: WorkspaceServiceGraphQueryService, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest
):
    return service.service_directory(principal, request)


def _operations(
    service: WorkspaceServiceGraphQueryService, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest
):
    return service.operation_directory(principal, request)


def _endpoint_methods(
    service: WorkspaceServiceGraphQueryService,
    principal: PrincipalIdentity,
    request: WorkspaceGraphQueryRequest,
    endpoint_id: str,
):
    return service.endpoint_methods(principal, request, endpoint_id)


def _providers(
    service: WorkspaceServiceGraphQueryService,
    principal: PrincipalIdentity,
    request: WorkspaceGraphQueryRequest,
    key: str,
):
    return service.providers(principal, request, key)


def _consumers(
    service: WorkspaceServiceGraphQueryService,
    principal: PrincipalIdentity,
    request: WorkspaceGraphQueryRequest,
    key: str,
):
    return service.consumers(principal, request, key)


def _dependencies(
    service: WorkspaceServiceGraphQueryService,
    principal: PrincipalIdentity,
    request: WorkspaceGraphQueryRequest,
    node_id: str,
):
    return service.dependencies(principal, request, node_id)


def _evidence(
    service: WorkspaceServiceGraphQueryService,
    principal: PrincipalIdentity,
    request: WorkspaceGraphQueryRequest,
    node_id: str,
):
    return service.evidence(principal, request, node_id)


def _unresolved(
    service: WorkspaceServiceGraphQueryService, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest
):
    return service.unresolved(principal, request)


def _build_task(
    service: WorkspaceServiceGraphQueryService,
    principal: PrincipalIdentity,
    request: WorkspaceGraphQueryRequest,
    task_id: str,
):
    return service.build_task(principal, request, task_id)


def _changes(
    service: WorkspaceServiceGraphQueryService,
    principal: PrincipalIdentity,
    request: WorkspaceGraphQueryRequest,
    generation_id: str,
):
    return service.changes(principal, request, generation_id)


def _impact(
    service: WorkspaceServiceGraphQueryService,
    principal: PrincipalIdentity,
    request: WorkspaceGraphQueryRequest,
    node_id: str,
):
    return service.impact(principal, request, node_id)


def _common(
    workspace_id: str,
    generation_id: str | None,
    repo_id: str | None,
    page_size: int,
    cursor: str | None,
    depth: int,
    node_limit: int,
) -> WorkspaceGraphQueryRequest:
    return _request(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit)


Common = Annotated[str | None, Query(min_length=1, pattern=r".*\S.*")]
IntegerPage = Annotated[int, Query(ge=1, le=100)]
IntegerDepth = Annotated[int, Query(ge=1, le=8)]
IntegerLimit = Annotated[int, Query(ge=1, le=1000)]


@router.get("/workspaces/{workspace_id}/service-graph/directory")
def directory(
    workspace_id: str,
    principal: Annotated[PrincipalIdentity, Depends(workspace_principal)],
    generation_id: Common = None,
    repo_id: Common = None,
    page_size: IntegerPage = 50,
    cursor: Common = None,
    depth: IntegerDepth = 1,
    node_limit: IntegerLimit = 200,
) -> JSONResponse:
    return _run(
        principal, _common(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit), _directory
    )


@router.get("/workspaces/{workspace_id}/service-graph/operations")
def operations(
    workspace_id: str,
    principal: Annotated[PrincipalIdentity, Depends(workspace_principal)],
    generation_id: Common = None,
    repo_id: Common = None,
    page_size: IntegerPage = 50,
    cursor: Common = None,
    depth: IntegerDepth = 1,
    node_limit: IntegerLimit = 200,
) -> JSONResponse:
    return _run(
        principal, _common(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit), _operations
    )


@router.get("/workspaces/{workspace_id}/service-graph/endpoints/{endpoint_id}/methods")
def endpoint_methods(
    workspace_id: str,
    endpoint_id: EndpointPath,
    principal: Annotated[PrincipalIdentity, Depends(workspace_principal)],
    generation_id: Common = None,
    repo_id: Common = None,
    page_size: IntegerPage = 50,
    cursor: Common = None,
    depth: IntegerDepth = 1,
    node_limit: IntegerLimit = 200,
) -> JSONResponse:
    return _run(
        principal,
        _common(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit),
        _endpoint_methods,
        endpoint_id,
    )


@router.get("/workspaces/{workspace_id}/service-graph/providers")
def providers(
    workspace_id: str,
    endpoint_key: Nonblank,
    principal: Annotated[PrincipalIdentity, Depends(workspace_principal)],
    generation_id: Common = None,
    repo_id: Common = None,
    page_size: IntegerPage = 50,
    cursor: Common = None,
    depth: IntegerDepth = 1,
    node_limit: IntegerLimit = 200,
) -> JSONResponse:
    return _run(
        principal,
        _common(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit),
        _providers,
        endpoint_key,
    )


@router.get("/workspaces/{workspace_id}/service-graph/consumers")
def consumers(
    workspace_id: str,
    endpoint_key: Nonblank,
    principal: Annotated[PrincipalIdentity, Depends(workspace_principal)],
    generation_id: Common = None,
    repo_id: Common = None,
    page_size: IntegerPage = 50,
    cursor: Common = None,
    depth: IntegerDepth = 1,
    node_limit: IntegerLimit = 200,
) -> JSONResponse:
    return _run(
        principal,
        _common(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit),
        _consumers,
        endpoint_key,
    )


@router.get("/workspaces/{workspace_id}/service-graph/dependencies")
def dependencies(
    workspace_id: str,
    node_id: Nonblank,
    principal: Annotated[PrincipalIdentity, Depends(workspace_principal)],
    generation_id: Common = None,
    repo_id: Common = None,
    page_size: IntegerPage = 50,
    cursor: Common = None,
    depth: IntegerDepth = 1,
    node_limit: IntegerLimit = 200,
) -> JSONResponse:
    return _run(
        principal,
        _common(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit),
        _dependencies,
        node_id,
    )


@router.get("/workspaces/{workspace_id}/service-graph/evidence")
def evidence(
    workspace_id: str,
    node_id: Nonblank,
    principal: Annotated[PrincipalIdentity, Depends(workspace_principal)],
    generation_id: Common = None,
    repo_id: Common = None,
    page_size: IntegerPage = 50,
    cursor: Common = None,
    depth: IntegerDepth = 1,
    node_limit: IntegerLimit = 200,
) -> JSONResponse:
    return _run(
        principal,
        _common(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit),
        _evidence,
        node_id,
    )


@router.get("/workspaces/{workspace_id}/service-graph/unresolved")
def unresolved(
    workspace_id: str,
    principal: Annotated[PrincipalIdentity, Depends(workspace_principal)],
    generation_id: Common = None,
    repo_id: Common = None,
    page_size: IntegerPage = 50,
    cursor: Common = None,
    depth: IntegerDepth = 1,
    node_limit: IntegerLimit = 200,
) -> JSONResponse:
    return _run(
        principal, _common(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit), _unresolved
    )


@router.get("/workspaces/{workspace_id}/service-graph/build-tasks/{task_id}")
def build_task(
    workspace_id: str,
    task_id: str,
    principal: Annotated[PrincipalIdentity, Depends(workspace_principal)],
    generation_id: Common = None,
    repo_id: Common = None,
    page_size: IntegerPage = 50,
    cursor: Common = None,
    depth: IntegerDepth = 1,
    node_limit: IntegerLimit = 200,
) -> JSONResponse:
    return _run(
        principal,
        _common(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit),
        _build_task,
        task_id,
    )


@router.get("/workspaces/{workspace_id}/service-graph/changes")
def changes(
    workspace_id: str,
    from_generation_id: Nonblank,
    principal: Annotated[PrincipalIdentity, Depends(workspace_principal)],
    generation_id: Common = None,
    repo_id: Common = None,
    page_size: IntegerPage = 50,
    cursor: Common = None,
    depth: IntegerDepth = 1,
    node_limit: IntegerLimit = 200,
) -> JSONResponse:
    return _run(
        principal,
        _common(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit),
        _changes,
        from_generation_id,
    )


@router.get("/workspaces/{workspace_id}/service-graph/impact")
def impact(
    workspace_id: str,
    node_id: Nonblank,
    principal: Annotated[PrincipalIdentity, Depends(workspace_principal)],
    generation_id: Common = None,
    repo_id: Common = None,
    page_size: IntegerPage = 50,
    cursor: Common = None,
    depth: IntegerDepth = 1,
    node_limit: IntegerLimit = 200,
) -> JSONResponse:
    return _run(
        principal, _common(workspace_id, generation_id, repo_id, page_size, cursor, depth, node_limit), _impact, node_id
    )
