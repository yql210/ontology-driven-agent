"""Read-only Web endpoints for durable ACTIVE service graph queries."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from neo4j import GraphDatabase

from ontoagent.config import OntoAgentConfig
from ontoagent.parsing.service_graph.change_analysis import (
    ServiceGraphChangeAnalysisBlockReason,
    ServiceGraphChangeAnalysisResult,
    ServiceGraphChangeAnalysisStatus,
)
from ontoagent.parsing.service_graph.generation_manifest import Neo4jNamespace
from ontoagent.parsing.service_graph.neo4j_change_impact_adapter import Neo4jServiceGraphChangeImpactAdapter
from ontoagent.parsing.service_graph.neo4j_manifest_repository import Neo4jServiceGraphManifestRepository
from ontoagent.parsing.service_graph.neo4j_query_adapter import Neo4jServiceGraphQueryAdapter
from ontoagent.parsing.service_graph.query import (
    ServiceGraphQueryBlockReason,
    ServiceGraphQueryResult,
    ServiceGraphQueryStatus,
)

router = APIRouter(tags=["service-graph"])

NonblankQuery = Annotated[str, Query(min_length=1, pattern=r".*\S.*")]


class ServiceGraphQueryAdapterFactory:
    """Create request-scoped durable service graph adapters."""

    @contextmanager
    def create(self, namespace: str) -> Generator[Neo4jServiceGraphQueryAdapter]:
        """Open a Neo4j adapter for one namespace and always close its driver."""
        config = OntoAgentConfig.from_env()
        driver = GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password))
        try:
            manifest_repository = Neo4jServiceGraphManifestRepository(driver)
            yield Neo4jServiceGraphQueryAdapter(driver, manifest_repository, Neo4jNamespace(namespace))
        finally:
            driver.close()


service_graph_query_adapter_factory = ServiceGraphQueryAdapterFactory()


class ServiceGraphChangeImpactAdapterFactory:
    """Create request-scoped durable service graph historical analysis adapters."""

    @contextmanager
    def create(self, namespace: str) -> Generator[Neo4jServiceGraphChangeImpactAdapter]:
        """Open a Neo4j historical adapter for one namespace and always close its driver."""
        config = OntoAgentConfig.from_env()
        driver = GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password))
        try:
            manifest_repository = Neo4jServiceGraphManifestRepository(driver)
            yield Neo4jServiceGraphChangeImpactAdapter(driver, manifest_repository, Neo4jNamespace(namespace))
        finally:
            driver.close()


service_graph_change_impact_adapter_factory = ServiceGraphChangeImpactAdapterFactory()


def _response(result: ServiceGraphQueryResult) -> JSONResponse:
    """Serialize a query result with its contract-specific HTTP outcome."""
    if result.status is ServiceGraphQueryStatus.BLOCKED:
        status_code = 422 if ServiceGraphQueryBlockReason.MALFORMED_REQUEST in result.reasons else 409
        return JSONResponse(status_code=status_code, content=result.to_dict())
    return JSONResponse(content=result.to_dict())


def _change_response(result: ServiceGraphChangeAnalysisResult) -> JSONResponse:
    """Serialize a historical change analysis with its contract-specific HTTP outcome."""
    status_code = 200 if result.status is ServiceGraphChangeAnalysisStatus.READY else 409
    if ServiceGraphChangeAnalysisBlockReason.MALFORMED_REQUEST in result.reasons:
        status_code = 422
    return JSONResponse(status_code=status_code, content=result.to_dict())


@router.get("/service-graph/directory")
def service_directory(repo_id: NonblankQuery, generation_id: NonblankQuery, namespace: NonblankQuery) -> JSONResponse:
    """List services for one exact ACTIVE repository generation."""
    with service_graph_query_adapter_factory.create(namespace) as adapter:
        return _response(adapter.service_directory(repo_id, generation_id))


@router.get("/service-graph/providers")
def endpoint_providers(
    repo_id: NonblankQuery,
    generation_id: NonblankQuery,
    namespace: NonblankQuery,
    endpoint_key: NonblankQuery,
) -> JSONResponse:
    """List providers of an endpoint in one exact ACTIVE repository generation."""
    with service_graph_query_adapter_factory.create(namespace) as adapter:
        return _response(adapter.find_endpoint_providers(repo_id, generation_id, endpoint_key))


@router.get("/service-graph/consumers")
def endpoint_consumers(
    repo_id: NonblankQuery,
    generation_id: NonblankQuery,
    namespace: NonblankQuery,
    endpoint_key: NonblankQuery,
) -> JSONResponse:
    """List consumers of an endpoint in one exact ACTIVE repository generation."""
    with service_graph_query_adapter_factory.create(namespace) as adapter:
        return _response(adapter.find_endpoint_consumers(repo_id, generation_id, endpoint_key))


@router.get("/service-graph/dependencies")
def service_dependencies(
    repo_id: NonblankQuery,
    generation_id: NonblankQuery,
    namespace: NonblankQuery,
    service_id: NonblankQuery,
) -> JSONResponse:
    """List dependencies for a service in one exact ACTIVE repository generation."""
    with service_graph_query_adapter_factory.create(namespace) as adapter:
        return _response(adapter.find_service_dependencies(repo_id, generation_id, service_id))


@router.get("/service-graph/evidence")
def evidence(
    repo_id: NonblankQuery,
    generation_id: NonblankQuery,
    namespace: NonblankQuery,
    entity_or_relation_id: NonblankQuery,
) -> JSONResponse:
    """List evidence for a graph entity or relation in an ACTIVE generation."""
    with service_graph_query_adapter_factory.create(namespace) as adapter:
        return _response(adapter.get_evidence(repo_id, generation_id, entity_or_relation_id))


@router.get("/service-graph/changes")
def service_graph_changes(
    repo_id: NonblankQuery,
    from_generation: NonblankQuery,
    to_generation: NonblankQuery,
    namespace: NonblankQuery,
) -> JSONResponse:
    """Analyze durable historical service graph changes and direct cross-repository impacts."""
    with service_graph_change_impact_adapter_factory.create(namespace) as adapter:
        return _change_response(adapter.analyze(repo_id, from_generation, to_generation))
