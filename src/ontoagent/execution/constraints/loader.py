from __future__ import annotations

from pathlib import Path

from ontoagent.domain.constraints import GuardLevel, TraversalConstraint
from ontoagent.domain.ontology_constraints import ConstraintFieldDescriptor
from ontoagent.execution.constraints.propagator import PropagationRule


class OntologyConstraintLoader:
    """三层约束加载器：本体注册表 + YAML 遍历路径 + 覆盖合并。"""

    def __init__(self, registry: dict[str, ConstraintFieldDescriptor]) -> None:
        self._registry = registry

    def load_all(
        self,
        constraints_yaml: str | Path | None = None,
        overrides_yaml: str | Path | None = None,
    ) -> tuple[list[TraversalConstraint], dict[str, PropagationRule], list[str]]:
        """一站式加载：返回 (traversals, propagation_rules, warnings)。

        自动从注册表填充 value_mapping，检测缺失，应用覆盖。
        """
        traversals: list[TraversalConstraint] = []
        rules: dict[str, PropagationRule] = {}
        warnings: list[str] = []
        yaml_data = self._read_yaml(constraints_yaml)
        overrides_data = self._read_yaml(overrides_yaml) if overrides_yaml else {}

        # Traversal constraints
        for name, cfg in yaml_data.get("traversal_constraints", {}).items():
            key = f"{cfg['target_label']}.{cfg['collect_property']}"
            descriptor = self._registry.get(key)
            if descriptor is None:
                warnings.append(
                    f"WARN: {key} 未在 ONTOLOGY_CONSTRAINT_REGISTRY 注册"
                    f" — 约束 '{name}' 将使用空 value_mapping"
                )
                value_mapping: dict[str, GuardLevel] = {}
            else:
                value_mapping = descriptor.value_mapping
            traversals.append(
                TraversalConstraint(
                    name=name,
                    source_label=cfg["source_label"],
                    relation_chain=cfg["relation_chain"],
                    target_label=cfg["target_label"],
                    collect_property=cfg["collect_property"],
                    value_mapping=value_mapping,
                    aggregation=cfg.get("aggregation", "max"),
                    ontology_source=key if descriptor else "",
                )
            )

        # Propagation rules — also auto-fill from registry
        for name, cfg in yaml_data.get("propagation_rules", {}).items():
            raw_mapping: dict[str, str] = cfg.get("value_mapping", {})
            # If collect_property corresponds to a registered field, prefer registry
            # (propagation rules don't have target_label, so we search by field_name or neo4j_property)
            for reg_key, desc in self._registry.items():
                if (reg_key.endswith(f".{cfg['collect_property']}") or
                    (desc.neo4j_property and desc.neo4j_property == cfg['collect_property'])):
                    raw_mapping = {k: v.value for k, v in desc.value_mapping.items()}
                    break
            rules[name] = PropagationRule(
                name=name,
                along=cfg.get("along", []),
                direction=cfg.get("direction", "forward"),
                max_depth=cfg.get("max_depth", 5),
                collect_property=cfg.get("collect_property", ""),
                value_mapping=raw_mapping,
                aggregation=cfg.get("aggregation", "max"),
            )

        # Apply overrides
        for override in (overrides_data.get("overrides") or []):
            ov_type = override.get("type")
            if ov_type == "patch":
                self._apply_patch(traversals, override)
            elif ov_type == "allow_all":
                self._apply_allow_all(traversals, override, warnings)
            elif ov_type == "add_constraint":
                self._apply_add_constraint(traversals, override)

        # Missing registry check (post-overrides, so added constraints are also checked)
        referenced = {(c.target_label, c.collect_property) for c in traversals}
        for label, prop in referenced:
            key = f"{label}.{prop}"
            if key not in self._registry:
                warnings.append(
                    f"WARN: '{label}.{prop}' referenced in constraints.yaml"
                    f" but missing from ONTOLOGY_CONSTRAINT_REGISTRY"
                    f" — constraints for this path may be incomplete"
                )

        return traversals, rules, warnings

    def _apply_patch(self, traversals: list[TraversalConstraint], override: dict) -> None:
        target_name = override["target"]
        for c in traversals:
            if c.name == target_name:
                # modify
                for val, level_str in override.get("modify", {}).items():
                    c.value_mapping[val] = GuardLevel(level_str)
                # remove_values
                for val in override.get("remove_values", []):
                    c.value_mapping.pop(val, None)
                # add_values
                for val, level_str in override.get("add_values", {}).items():
                    c.value_mapping[val] = GuardLevel(level_str)
                break

    def _apply_allow_all(
        self, traversals: list[TraversalConstraint], override: dict, warnings: list[str]
    ) -> None:
        target_entity = override["target_entity"]
        warnings.append(
            f"INFO: allow_all for {target_entity}: {override.get('reason', 'no reason')}"
        )

    def _apply_add_constraint(
        self, traversals: list[TraversalConstraint], override: dict
    ) -> None:
        cfg = override["constraint"]
        key = f"{cfg['target_label']}.{cfg['collect_property']}"
        descriptor = self._registry.get(key)
        if descriptor is not None:
            value_mapping: dict[str, GuardLevel] = descriptor.value_mapping
        else:
            value_mapping = {
                k: GuardLevel(v) for k, v in cfg.get("value_mapping", {}).items()
            }
        traversals.append(
            TraversalConstraint(
                name=cfg["name"],
                source_label=cfg["source_label"],
                relation_chain=cfg["relation_chain"],
                target_label=cfg["target_label"],
                collect_property=cfg["collect_property"],
                value_mapping=value_mapping,
                aggregation=cfg.get("aggregation", "max"),
                ontology_source=key if descriptor else "",
            )
        )

    def _read_yaml(self, path: str | Path | None) -> dict:
        if path is None:
            return {}
        import yaml

        p = Path(path) if isinstance(path, str) else path
        if p.exists():
            with open(p) as f:
                return yaml.safe_load(f) or {}
        return {}

    @property
    def registry(self) -> dict[str, ConstraintFieldDescriptor]:
        return self._registry
