"""ontology_loader 输出 entry_type + ontology_ref 测试。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ontoagent.pipeline.ontology_loader import (
    _resolve_entry_type,
    load_ontology_to_shapes,
)


def test_resolve_entry_type_known_source() -> None:
    assert _resolve_entry_type("rdb") == "ResourceEntity"
    assert _resolve_entry_type("code") == "CodeEntity"
    assert _resolve_entry_type("doc") == "DocEntity"


def test_resolve_entry_type_unknown_source() -> None:
    assert _resolve_entry_type("garbage") == "ResourceEntity"


def test_load_minimal_ontology_emits_entry_type_and_ontology_ref() -> None:
    ontology = {
        "version": "1.0",
        "domain": "ecommerce",
        "entity_types": [
            {
                "id": "concept_001",
                "name": "订单",
                "source": "rdb",
                "source_ref": "table:order",
                "is_entity_type": True,
                "confidence": 0.85,
            }
        ],
        "axioms": [],
        "properties": [],
        "relations": [],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(ontology, f)
        tmp_path = f.name

    shapes = load_ontology_to_shapes(
        tmp_path,
        include_axioms=False,
        include_properties=False,
        include_relations=False,
    )
    Path(tmp_path).unlink()

    assert len(shapes) == 1
    target = shapes[0]["target"]
    assert target["entry_type"] == "ResourceEntity"
    assert target["ontology_ref"] == "订单"
