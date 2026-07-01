"""RED phase — Builder CapabilityEntity ChromaDB indexing tests.
Tests that Stage 5 writes CapabilityEntities to ChromaDB.
All should fail until integration is implemented.
"""

from __future__ import annotations


class TestBuilderCapabilityVectorIndexing:
    """Verify that CapabilityEntities are indexed into ChromaDB during build."""

    def test_capability_dict_has_searchable_text(self):
        """capability_to_searchable_text() produces description + keywords text."""
        from ontoagent.pipeline.builder_utils import capability_to_searchable_text

        # This should produce a searchable text string
        text = capability_to_searchable_text(
            name="process_payment",
            description="处理用户支付请求",
            keywords=["payment", "credit_card", "refund"],
        )

        assert "process_payment" in text or "支付" in text or "payment" in text
        assert len(text) > 0
