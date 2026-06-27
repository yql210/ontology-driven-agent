"""Intent router — build intent_type → ActionConfig mapping from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from ontoagent.execution.action_types import ActionConfig


def build_intent_map(yaml_path: Path) -> dict[str, ActionConfig]:
    """Parse YAML and return {intent_type: ActionConfig}."""
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    intent_map: dict[str, ActionConfig] = {}
    for action_name, action_data in data.get("actions", {}).items():
        config = ActionConfig(
            name=action_name,
            intent_type=action_data.get("intent_type", action_name),
            trigger_hint=action_data.get("trigger_hint", ""),
            bind_to=action_data.get("bind_to", ""),
            submission_criteria=action_data.get("submission_criteria", []),
            functions=action_data.get("functions", []),
            requires_approval=action_data.get("requires_approval", False),
        )
        if config.intent_type in intent_map:
            raise ValueError(f"Duplicate intent_type: {config.intent_type}")
        intent_map[config.intent_type] = config

    return intent_map


def build_intent_prompt(intent_map: dict[str, ActionConfig]) -> str:
    """Generate Agent prompt text describing available intents."""
    if not intent_map:
        return ""

    lines = ["当用户有操作意图时，使用 express_intent 工具，可用操作："]
    for intent_type, config in intent_map.items():
        lines.append(f"- {intent_type}: {config.trigger_hint}")
    lines.append("参数：intent_type（操作类型）, target（目标实体名称）, params（可选参数 dict）")
    return "\n".join(lines)
