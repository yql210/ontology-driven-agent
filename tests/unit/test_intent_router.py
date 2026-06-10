"""Tests for intent_router — YAML parsing, conflict detection, prompt generation."""

from __future__ import annotations

import textwrap

import pytest

from layerkg.action_types import ActionConfig
from layerkg.intent_router import build_intent_map, build_intent_prompt


class TestBuildIntentMap:
    """build_intent_map: parse YAML into {intent_type: ActionConfig}."""

    def test_build_intent_map_from_yaml(self, tmp_path):
        """Valid YAML produces correct intent_map."""
        yaml_content = textwrap.dedent("""\
            actions:
              refactor:
                intent_type: refactor
                trigger_hint: "用户要求重构"
                bind_to: code_entity
                submission_criteria:
                  - "entity exists"
                  - "entity.lines > 100"
                functions:
                  - check_refactor_eligibility
                requires_approval: false

              document:
                intent_type: document
                trigger_hint: "用户要求写文档"
                bind_to: code_entity
                submission_criteria:
                  - "entity exists"
                functions:
                  - generate_api_doc
                requires_approval: false
        """)
        yaml_file = tmp_path / "actions.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        result = build_intent_map(yaml_file)

        assert len(result) == 2
        assert "refactor" in result
        assert "document" in result

        refactor = result["refactor"]
        assert isinstance(refactor, ActionConfig)
        assert refactor.name == "refactor"
        assert refactor.intent_type == "refactor"
        assert refactor.trigger_hint == "用户要求重构"
        assert refactor.bind_to == "code_entity"
        assert refactor.submission_criteria == ["entity exists", "entity.lines > 100"]
        assert refactor.functions == ["check_refactor_eligibility"]
        assert refactor.requires_approval is False

        doc = result["document"]
        assert doc.functions == ["generate_api_doc"]

    def test_build_intent_map_duplicate_raises(self, tmp_path):
        """Duplicate intent_type raises ValueError."""
        yaml_content = textwrap.dedent("""\
            actions:
              refactor:
                intent_type: refactor
                trigger_hint: "重构"
                bind_to: code_entity
                functions:
                  - check_refactor_eligibility

              refactor_v2:
                intent_type: refactor
                trigger_hint: "重构v2"
                bind_to: code_entity
                functions:
                  - check_refactor_eligibility
        """)
        yaml_file = tmp_path / "dup.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        with pytest.raises(ValueError, match="Duplicate intent_type: refactor"):
            build_intent_map(yaml_file)

    def test_build_intent_map_defaults(self, tmp_path):
        """Missing optional fields get sensible defaults."""
        yaml_content = textwrap.dedent("""\
            actions:
              minimal:
                intent_type: minimal
                trigger_hint: "minimal action"
                bind_to: code_entity
        """)
        yaml_file = tmp_path / "minimal.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        result = build_intent_map(yaml_file)

        config = result["minimal"]
        assert config.submission_criteria == []
        assert config.functions == []
        assert config.requires_approval is False

    def test_build_intent_map_empty_actions(self, tmp_path):
        """Empty actions section produces empty map."""
        yaml_content = "actions: {}"
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        result = build_intent_map(yaml_file)

        assert result == {}


class TestBuildIntentPrompt:
    """build_intent_prompt: generate Agent prompt text from intent_map."""

    def test_build_intent_prompt_format(self):
        """Prompt contains all intent_types and their trigger_hints."""
        intent_map = {
            "refactor": ActionConfig(
                name="refactor",
                intent_type="refactor",
                trigger_hint="重构代码",
                bind_to="code_entity",
            ),
            "document": ActionConfig(
                name="document",
                intent_type="document",
                trigger_hint="写文档",
                bind_to="code_entity",
            ),
        }

        prompt = build_intent_prompt(intent_map)

        assert "express_intent" in prompt
        assert "refactor" in prompt
        assert "document" in prompt
        assert "重构代码" in prompt
        assert "写文档" in prompt
        assert "intent_type" in prompt

    def test_build_intent_prompt_empty(self):
        """Empty intent_map returns empty string."""
        prompt = build_intent_prompt({})
        assert prompt == ""
