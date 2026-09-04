"""Tests for durable ACTIVE Neo4j service graph Web endpoints."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ontoagent.api.web.app import create_app
from ontoagent.api.web.router import service_graph
from ontoagent.parsing.service_graph.query import (
    ServiceGraphQueryBlockReason,
    ServiceGraphQueryResult,
    ServiceGraphQueryStatus,
)


@pytest.fixture
def client() -> TestClient:
    """Create an app without entering its external-service lifespan."""
    return TestClient(create_app())


@pytest.fixture
def adapter() -> MagicMock:
    """Provide a durable adapter double with a JSON-safe READY envelope."""
    result = ServiceGraphQueryResult(ServiceGraphQueryStatus.READY, "repo-1", "generation-1", (), (), ())
    mock = MagicMock()
    mock.service_directory.return_value = result
    mock.find_endpoint_providers.return_value = result
    mock.find_endpoint_consumers.return_value = result
    mock.find_service_dependencies.return_value = result
    mock.get_evidence.return_value = result
    return mock


@pytest.fixture
def factory(adapter: MagicMock) -> MagicMock:
    """Provide an injectable factory double without opening Neo4j."""
    mock = MagicMock()

    @contextmanager
    def create(namespace: str):
        assert namespace == "test-namespace"
        yield adapter

    mock.create.side_effect = create
    return mock


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "params", "method_name", "method_args"),
    [
        ("/api/service-graph/directory", {}, "service_directory", ("repo-1", "generation-1")),
        (
            "/api/service-graph/providers",
            {"endpoint_key": "HTTP:GET:/orders"},
            "find_endpoint_providers",
            ("repo-1", "generation-1", "HTTP:GET:/orders"),
        ),
        (
            "/api/service-graph/consumers",
            {"endpoint_key": "HTTP:GET:/orders"},
            "find_endpoint_consumers",
            ("repo-1", "generation-1", "HTTP:GET:/orders"),
        ),
        (
            "/api/service-graph/dependencies",
            {"service_id": "service-1"},
            "find_service_dependencies",
            ("repo-1", "generation-1", "service-1"),
        ),
        (
            "/api/service-graph/evidence",
            {"entity_or_relation_id": "relation-1"},
            "get_evidence",
            ("repo-1", "generation-1", "relation-1"),
        ),
    ],
)
def test_service_graph_routes_delegate_to_durable_adapter(
    client: TestClient,
    factory: MagicMock,
    adapter: MagicMock,
    path: str,
    params: dict[str, str],
    method_name: str,
    method_args: tuple[str, ...],
) -> None:
    """Every public route returns the durable adapter's existing query envelope."""
    with patch.object(service_graph, "service_graph_query_adapter_factory", factory):
        response = client.get(
            path, params={"repo_id": "repo-1", "generation_id": "generation-1", "namespace": "test-namespace", **params}
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "repo_id": "repo-1",
        "generation_id": "generation-1",
        "reasons": [],
        "nodes": [],
        "relations": [],
    }
    getattr(adapter, method_name).assert_called_once_with(*method_args)
    factory.create.assert_called_once_with("test-namespace")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/service-graph/directory", {"generation_id": "generation-1", "namespace": "test-namespace"}),
        (
            "/api/service-graph/providers",
            {"repo_id": "repo-1", "generation_id": "generation-1", "namespace": "test-namespace"},
        ),
        (
            "/api/service-graph/consumers",
            {"repo_id": "repo-1", "generation_id": "generation-1", "namespace": "test-namespace"},
        ),
        (
            "/api/service-graph/dependencies",
            {"repo_id": "repo-1", "generation_id": "generation-1", "namespace": "test-namespace"},
        ),
        (
            "/api/service-graph/evidence",
            {"repo_id": "repo-1", "generation_id": "generation-1", "namespace": "test-namespace"},
        ),
    ],
)
def test_service_graph_routes_require_all_query_identifiers(
    client: TestClient, path: str, params: dict[str, str]
) -> None:
    """Identity and route-specific query identifiers are mandatory."""
    response = client.get(path, params=params)

    assert response.status_code == 422


@pytest.mark.unit
def test_service_graph_blocked_result_maps_to_conflict(
    client: TestClient, factory: MagicMock, adapter: MagicMock
) -> None:
    """Manifest, provenance, and identity blocks are conflict outcomes, never not-found responses."""
    adapter.service_directory.return_value = ServiceGraphQueryResult(
        ServiceGraphQueryStatus.BLOCKED,
        "repo-1",
        None,
        (ServiceGraphQueryBlockReason.GENERATION_MISMATCH,),
        (),
        (),
    )
    with patch.object(service_graph, "service_graph_query_adapter_factory", factory):
        response = client.get(
            "/api/service-graph/directory",
            params={"repo_id": "repo-1", "generation_id": "generation-1", "namespace": "test-namespace"},
        )

    assert response.status_code == 409
    assert response.json()["reasons"] == ["generation_mismatch"]


@pytest.mark.unit
def test_service_graph_malformed_result_maps_to_validation_error(
    client: TestClient, factory: MagicMock, adapter: MagicMock
) -> None:
    """Adapter-declared malformed requests retain FastAPI validation semantics."""
    adapter.service_directory.return_value = ServiceGraphQueryResult(
        ServiceGraphQueryStatus.BLOCKED,
        "unknown",
        None,
        (ServiceGraphQueryBlockReason.MALFORMED_REQUEST,),
        (),
        (),
    )
    with patch.object(service_graph, "service_graph_query_adapter_factory", factory):
        response = client.get(
            "/api/service-graph/directory",
            params={"repo_id": " ", "generation_id": "generation-1", "namespace": "test-namespace"},
        )

    assert response.status_code == 422


@pytest.mark.unit
def test_service_graph_routes_do_not_use_generic_graph_store(factory: MagicMock) -> None:
    """The durable service graph API does not construct the generic GraphStore abstraction."""
    with (
        patch("ontoagent.api.web.app.create_graph_store") as create_graph_store,
        patch.object(service_graph, "service_graph_query_adapter_factory", factory),
    ):
        response = TestClient(create_app()).get(
            "/api/service-graph/directory",
            params={"repo_id": "repo-1", "generation_id": "generation-1", "namespace": "test-namespace"},
        )

    assert response.status_code == 200
    create_graph_store.assert_not_called()
