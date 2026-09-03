import json
import math
from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.models import (
    DetectorFacts,
    Evidence,
    HttpEndpoint,
    RepositorySnapshot,
    ServiceDefinition,
    UnresolvedFact,
)


def test_models_validate_and_serialize_deterministically():
    snapshot = RepositorySnapshot("repo", "rev", Path("."), frozenset({"Java", "YAML"}))
    assert snapshot.languages == frozenset({"java", "yaml"})
    evidence = Evidence(
        "repo", "rev", "x.java", 1, 2, "spring-http", "1", "provider_mapping", "provider|orders:Order|GET|/orders", 0.9
    )
    service = ServiceDefinition("repo", "orders:Order", "spring", evidence.id)
    endpoint = HttpEndpoint(
        "repo", "orders:Order", "provider", "provider_mapping", "GET", "/orders", "x.java", evidence.id
    )
    facts = DetectorFacts("spring-http", "1", "repo", "rev", (service,), (endpoint,), (evidence,), ())
    assert endpoint.canonical_key == "HTTP|GET|/orders|orders:Order"
    assert json.dumps(facts.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False) == json.dumps(
        facts.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def test_models_reject_invalid_values():
    with pytest.raises(ValueError):
        RepositorySnapshot("", "r", Path("."), frozenset({"java"}))
    with pytest.raises(ValueError):
        Evidence("r", "v", "f", 2, 1, "d", "v", "t", "s", 0.5)
    with pytest.raises(ValueError):
        Evidence("r", "v", "f", 1, 1, "d", "v", "t", "s", math.nan)


def test_facts_require_matching_evidence():
    ep = HttpEndpoint("r", "s", "provider", "provider_mapping", "GET", "/x", "f", "missing")
    with pytest.raises(ValueError):
        DetectorFacts("d", "v", "r", "rev", (), (ep,), (), ())


def test_unresolved_reason_codes_are_frozen():
    with pytest.raises(ValueError):
        UnresolvedFact("r", "f", "e", "NOPE", "x")
