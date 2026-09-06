"""ACL-gated, workspace-scoped facade for persisted service graph reads."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Protocol

from ontoagent.domain.workspace_authorization import (
    PrincipalIdentity,
    WorkspaceAuthorizationFailure,
    WorkspaceQueryAuthorization,
)
from ontoagent.domain.workspace_graph_query import (
    WorkspaceGraphPage,
    WorkspaceGraphQueryRequest,
    WorkspaceGraphQueryValidationError,
    WorkspaceGraphVisibility,
)
from ontoagent.execution.workspace_query_authorization import (
    WorkspaceAuthorizationError,
    WorkspaceQueryAuthorizationService,
)


class WorkspaceServiceGraphReadRepository(Protocol):
    """A trusted generation gate followed by an immutable graph read."""

    def read(
        self, authorization: WorkspaceQueryAuthorization
    ) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]: ...


class WorkspaceServiceGraphReadError(RuntimeError):
    """A receipt or namespace trust failure that must not leak through a query response."""


class WorkspaceServiceGraphQueryService:
    """Authorize first, then return only physically visible graph records."""

    def __init__(
        self,
        authorization: WorkspaceQueryAuthorizationService,
        repository: WorkspaceServiceGraphReadRepository,
        cursor_secret: bytes,
    ) -> None:
        if not isinstance(cursor_secret, bytes) or len(cursor_secret) < 16:
            raise ValueError("cursor_secret must contain at least 16 bytes")
        self._authorization = authorization
        self._repository = repository
        self._cursor_secret = cursor_secret

    def service_directory(
        self, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest
    ) -> WorkspaceGraphPage:
        return self._query(principal, request, "service_directory")

    def operation_directory(
        self, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest
    ) -> WorkspaceGraphPage:
        return self._query(principal, request, "operation_directory", {"ServiceOperation"})

    def providers(
        self, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest, endpoint_key: str
    ) -> WorkspaceGraphPage:
        return self._query(principal, request, "providers", endpoint_key=endpoint_key, role="provider")

    def consumers(
        self, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest, endpoint_key: str
    ) -> WorkspaceGraphPage:
        return self._query(principal, request, "consumers", endpoint_key=endpoint_key, role="consumer")

    def dependencies(
        self, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest, node_id: str
    ) -> WorkspaceGraphPage:
        return self._query(principal, request, "dependencies", related_to=node_id)

    def evidence(
        self, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest, node_id: str
    ) -> WorkspaceGraphPage:
        return self._query(
            principal,
            request,
            "evidence",
            related_to=node_id,
            relation_types={"SUPPORTED_BY_EVIDENCE", "METHOD_EVIDENCE"},
        )

    def unresolved(self, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest) -> WorkspaceGraphPage:
        return self._query(principal, request, "unresolved", {"MethodUnresolved"})

    def build_task(
        self, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest, task_id: str
    ) -> WorkspaceGraphPage:
        return self._query(principal, request, "build_task", related_to=task_id, node_types={"BuildTask"})

    def changes(
        self, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest, from_generation_id: str
    ) -> WorkspaceGraphPage:
        return self._query(principal, request, "changes", from_generation_id=from_generation_id)

    def impact(
        self, principal: PrincipalIdentity, request: WorkspaceGraphQueryRequest, node_id: str
    ) -> WorkspaceGraphPage:
        return self._query(principal, request, "impact", related_to=node_id)

    def _query(
        self,
        principal: PrincipalIdentity,
        request: WorkspaceGraphQueryRequest,
        operation: str,
        node_types: set[str] | None = None,
        *,
        endpoint_key: str | None = None,
        role: str | None = None,
        related_to: str | None = None,
        relation_types: set[str] | None = None,
        from_generation_id: str | None = None,
    ) -> WorkspaceGraphPage:
        if type(principal) is not PrincipalIdentity or type(request) is not WorkspaceGraphQueryRequest:
            raise WorkspaceGraphQueryValidationError("principal and request have invalid types")
        for name, value in (
            ("endpoint_key", endpoint_key),
            ("node_id", related_to),
            ("from_generation_id", from_generation_id),
        ):
            if value is not None and (type(value) is not str or not value.strip()):
                raise WorkspaceGraphQueryValidationError(f"{name} must be nonblank")
        authorization = self._authorization.authorize(principal, request.workspace_id, request.generation_id)
        context = self._context(
            principal, authorization, request, operation, endpoint_key, role, related_to, from_generation_id
        )
        offset = self._cursor_offset(request.cursor, context)
        try:
            nodes, edges = self._repository.read(authorization)
        except WorkspaceServiceGraphReadError as error:
            raise WorkspaceAuthorizationError(WorkspaceAuthorizationFailure.CONFLICT) from error
        visible_nodes, visible_edges = _filter_graph(nodes, edges, authorization, request.repo_id)
        if node_types:
            visible_nodes = tuple(node for node in visible_nodes if node.get("node_type") in node_types)
        if endpoint_key is not None:
            visible_nodes = tuple(
                node for node in visible_nodes if node.get("canonical_key") == endpoint_key and node.get("role") == role
            )
        if related_to is not None:
            visible_edges = tuple(
                edge
                for edge in visible_edges
                if related_to in (edge.get("source_id"), edge.get("target_id"), edge.get("id"))
            )
            ids = {related_to} | {str(edge[key]) for edge in visible_edges for key in ("source_id", "target_id")}
            visible_nodes = tuple(node for node in visible_nodes if node.get("id") in ids)
        if relation_types:
            visible_edges = tuple(edge for edge in visible_edges if edge.get("relation_type") in relation_types)
            ids = {str(edge[key]) for edge in visible_edges for key in ("source_id", "target_id")}
            visible_nodes = tuple(node for node in visible_nodes if node.get("id") in ids)
        ordered = tuple(sorted(visible_nodes, key=lambda node: str(node["id"])))[: request.node_limit]
        page = ordered[offset : offset + request.page_size]
        page_ids = {str(node["id"]) for node in page}
        page_edges = tuple(
            edge
            for edge in sorted(visible_edges, key=lambda edge: str(edge["id"]))
            if edge.get("source_id") in page_ids
        )
        next_cursor = (
            self._cursor(offset + request.page_size, context) if offset + request.page_size < len(ordered) else None
        )
        visibility = (
            WorkspaceGraphVisibility.FULL if authorization.repositories.is_full else WorkspaceGraphVisibility.FILTERED
        )
        return WorkspaceGraphPage(
            authorization.workspace_id, authorization.generation_id, visibility, page, page_edges, next_cursor
        )

    def _context(
        self,
        principal: PrincipalIdentity,
        authorization: WorkspaceQueryAuthorization,
        request: WorkspaceGraphQueryRequest,
        operation: str,
        endpoint_key: str | None,
        role: str | None,
        related_to: str | None,
        from_generation_id: str | None,
    ) -> dict[str, object]:
        repos = ",".join(sorted(authorization.repositories.repo_ids))
        return {
            "p": principal.principal_id,
            "w": authorization.workspace_id,
            "g": authorization.generation_id,
            "a": hashlib.sha256(repos.encode()).hexdigest(),
            "o": operation,
            "r": request.repo_id,
            "s": request.page_size,
            "d": request.depth,
            "n": request.node_limit,
            "k": endpoint_key,
            "role": role,
            "i": related_to,
            "f": from_generation_id,
        }

    def _cursor(self, offset: int, context: Mapping[str, object]) -> str:
        payload = json.dumps({"offset": offset, "context": context}, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def _cursor_offset(self, cursor: str | None, context: Mapping[str, object]) -> int:
        if cursor is None:
            return 0
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()):
                raise ValueError
            decoded = json.loads(payload)
            offset = decoded["offset"]
            if type(offset) is not int or offset < 0 or decoded["context"] != context:
                raise ValueError
            return offset
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise WorkspaceGraphQueryValidationError("cursor is invalid for this query context") from error


def _filter_graph(
    nodes: tuple[dict[str, object], ...],
    edges: tuple[dict[str, object], ...],
    authorization: WorkspaceQueryAuthorization,
    repo_filter: str | None,
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    allowed = authorization.repositories.repo_ids
    visible = tuple(node for node in nodes if _repo(node) in allowed)
    by_id = {str(node.get("id")): node for node in visible}
    edges = tuple(edge for edge in edges if set(_edge_repos(edge, by_id)).issubset(allowed))
    if repo_filter is None:
        return visible, edges
    if repo_filter not in allowed:
        return (), ()
    matching_edges = tuple(edge for edge in edges if repo_filter in _edge_repos(edge, by_id))
    ids = {str(edge[key]) for edge in matching_edges for key in ("source_id", "target_id")}
    ids |= {str(node["id"]) for node in visible if _repo(node) == repo_filter}
    return tuple(node for node in visible if str(node["id"]) in ids), matching_edges


def _repo(node: Mapping[str, object]) -> str | None:
    value = node.get("repo_id", node.get("repoId"))
    return value if type(value) is str else None


def _edge_repos(edge: Mapping[str, object], nodes: Mapping[str, Mapping[str, object]]) -> tuple[str, ...]:
    value = edge.get("repo_ids")
    if isinstance(value, tuple) and all(type(item) is str for item in value):
        return value
    return tuple(
        repo
        for item in (edge.get("source_id"), edge.get("target_id"))
        if (repo := _repo(nodes.get(str(item), {}))) is not None
    )
