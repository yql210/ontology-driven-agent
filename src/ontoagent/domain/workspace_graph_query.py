"""Typed, transport-neutral contracts for workspace service graph reads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkspaceGraphVisibility(StrEnum):
    FULL = "full"
    FILTERED = "filtered"


class WorkspaceGraphQueryValidationError(ValueError):
    """A request validation outcome intended to map to HTTP 422."""

    status_code = 422


@dataclass(frozen=True)
class WorkspaceGraphQueryRequest:
    workspace_id: str
    generation_id: str | None = None
    repo_id: str | None = None
    page_size: int = 50
    cursor: str | None = None
    depth: int = 1
    node_limit: int = 200

    def __post_init__(self) -> None:
        _text(self.workspace_id, "workspace_id")
        for name in ("generation_id", "repo_id", "cursor"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        for name, value, minimum, maximum in (
            ("page_size", self.page_size, 1, 100),
            ("depth", self.depth, 1, 8),
            ("node_limit", self.node_limit, 1, 1000),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise WorkspaceGraphQueryValidationError(f"{name} must be between {minimum} and {maximum}")


@dataclass(frozen=True)
class WorkspaceGraphPage:
    workspace_id: str
    generation_id: str
    visibility: WorkspaceGraphVisibility
    nodes: tuple[dict[str, object], ...]
    edges: tuple[dict[str, object], ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        for name in ("workspace_id", "generation_id"):
            _text(getattr(self, name), name)
        if type(self.visibility) is not WorkspaceGraphVisibility:
            raise ValueError("visibility must be a WorkspaceGraphVisibility")
        if type(self.nodes) is not tuple or type(self.edges) is not tuple:
            raise ValueError("nodes and edges must be tuples")
        if self.next_cursor is not None:
            _text(self.next_cursor, "next_cursor")


def _text(value: object, name: str) -> None:
    if type(value) is not str or not value.strip():
        raise WorkspaceGraphQueryValidationError(f"{name} must be nonblank")
