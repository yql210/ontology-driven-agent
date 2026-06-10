"""Tests for updated agent prompt with dynamic intent routing."""

from __future__ import annotations

from layerkg.agent.prompt import AGENT_SYSTEM_PROMPT, INTENT_SECTION


def test_prompt_contains_express_intent() -> None:
    """System prompt references express_intent tool."""
    assert "express_intent" in AGENT_SYSTEM_PROMPT


def test_prompt_contains_intent_section() -> None:
    """System prompt includes dynamically generated intent section."""
    assert INTENT_SECTION != ""
    assert "express_intent" in INTENT_SECTION
    assert "refactor" in INTENT_SECTION
    assert "document" in INTENT_SECTION
    assert "analyze_impact" in INTENT_SECTION
    # Intent section is embedded in the prompt
    assert INTENT_SECTION in AGENT_SYSTEM_PROMPT


def test_prompt_not_contains_ontology_action() -> None:
    """System prompt no longer references ontology_action."""
    assert "ontology_action" not in AGENT_SYSTEM_PROMPT


def test_prompt_not_contains_ontology_action_description() -> None:
    """Old ontology_action description blocks are removed."""
    assert "CodeEntity**: refactor" not in AGENT_SYSTEM_PROMPT
    assert "AlertEntity**: diagnose" not in AGENT_SYSTEM_PROMPT


def test_intent_section_has_all_four_intents() -> None:
    """INTENT_SECTION lists all 4 configured intents."""
    for intent in ["refactor", "document", "analyze_impact", "extract_interface"]:
        assert intent in INTENT_SECTION
