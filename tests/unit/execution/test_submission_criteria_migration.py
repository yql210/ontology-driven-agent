"""Phase 3b: migration of submission_criteria to Shape (kind=structural).

Asserts:
  - refactor action's submission_criteria is now ["entity exists"] only
    (entity.lines > 100 migrated to a structural Shape).
  - shapes.yaml contains a structural Shape encoding the migrated constraint.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ontoagent.domain.shapes import ConstraintShape, Operation, ShapeKind

_PIPELINE_DIR = Path(__file__).resolve().parents[3] / "src" / "ontoagent" / "pipeline"
_ONTOLOGY_ACTIONS_YAML = _PIPELINE_DIR / "ontology_actions.yaml"
_SHAPES_YAML = _PIPELINE_DIR / "shapes.yaml"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@pytest.mark.unit
def test_refactor_submission_criteria_only_contains_entity_exists() -> None:
    """refactor action should no longer carry entity.lines > 100.

    The property-expression criterion has been migrated to a structural Shape;
    only the EntityExistsGuard-handled 'entity exists' precondition remains.
    """
    data = _load_yaml(_ONTOLOGY_ACTIONS_YAML)
    refactor = data["actions"]["refactor"]
    assert refactor["submission_criteria"] == ["entity exists"]


@pytest.mark.unit
def test_other_actions_keep_entity_exists_criterion() -> None:
    """All non-refactor actions still carry only 'entity exists'."""
    data = _load_yaml(_ONTOLOGY_ACTIONS_YAML)
    for name, action in data["actions"].items():
        if name == "refactor":
            continue
        assert action["submission_criteria"] == ["entity exists"], (
            f"action {name!r} unexpectedly changed: {action['submission_criteria']}"
        )


@pytest.mark.unit
def test_shapes_yaml_contains_structural_refactor_lines_shape() -> None:
    """A structural Shape exists encoding the migrated lines>100 constraint."""
    data = _load_yaml(_SHAPES_YAML)
    matches = [
        s
        for s in data["shapes"]
        if s.get("kind") == "structural"
        and s.get("target", {}).get("entry_type") == "CodeEntity"
        and s.get("constraint", {}).get("field") == "lines"
        and s.get("constraint", {}).get("operator") == ">"
        and s.get("constraint", {}).get("value") == 100
    ]
    assert matches, "no structural Shape with field=lines, operator='>', value=100 in shapes.yaml"

    shape_dict = matches[0]
    assert shape_dict["target"]["operation"] in {"UPDATE"}
    assert shape_dict["path"] == "SELF"


@pytest.mark.unit
def test_migrated_shape_loads_via_from_yaml_dict() -> None:
    """The migrated Shape parses cleanly through ConstraintShape.from_yaml_dict."""
    data = _load_yaml(_SHAPES_YAML)
    shape_dict = next(
        s for s in data["shapes"] if s.get("kind") == "structural" and s.get("constraint", {}).get("field") == "lines"
    )
    shape = ConstraintShape.from_yaml_dict(shape_dict)

    assert shape.kind is ShapeKind.STRUCTURAL
    assert shape.target.entry_type == "CodeEntity"
    assert shape.target.operation is Operation.UPDATE
    assert shape.path.is_self() is True
    assert shape.constraint.field == "lines"
    assert shape.constraint.operator == ">"
    assert shape.constraint.value == 100
