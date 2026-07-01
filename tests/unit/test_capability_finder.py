"""CapabilityFinder tests — semantic search for business capabilities.

Uses mock to avoid Ollama embedding dependency in unit tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ontoagent.parsing.extractor.capability_finder import CapabilityFinder, CapabilityMatch


def _make_search_result(item_id: str, name: str, domain: str, text: str, distance: float = 0.3):
    """Helper to create ChromaDB search result dict."""
    return {
        "id": item_id,
        "text": text,
        "metadata": {"entity_type": "CapabilityEntity", "name": name, "business_domain": domain},
        "distance": distance,
    }


class TestCapabilityFinder:
    """CapabilityFinder — mock-based tests to avoid Ollama dep."""

    def test_find_returns_top_k_capabilities(self):
        """Given indexed capabilities, find() returns top-k semantic matches."""
        mock_store = MagicMock()
        mock_store.search.return_value = [
            _make_search_result("cap-pay", "process_payment", "payment", "支付处理 API", 0.1),
            _make_search_result("cap-inv", "check_inventory", "inventory", "库存校验 API", 0.5),
        ]

        finder = CapabilityFinder(mock_store)
        results = finder.find("处理用户支付", top_k=2)

        assert len(results) == 2
        assert results[0].id == "cap-pay"

    def test_find_returns_empty_for_no_matches(self):
        """find() returns empty list when ChromaDB returns no results."""
        mock_store = MagicMock()
        mock_store.search.return_value = []

        finder = CapabilityFinder(mock_store)
        results = finder.find("nonexistent", top_k=5)

        assert results == []

    def test_find_respects_top_k(self):
        """find() respects top_k parameter."""
        mock_store = MagicMock()
        mock_store.search.return_value = [
            _make_search_result(f"cap-{i}", f"cap_{i}", "test", f"能力 {i}", 0.1 * i)
            for i in range(3)
        ]

        finder = CapabilityFinder(mock_store)
        results = finder.find("测试业务能力", top_k=3)

        assert len(results) == 3

    def test_match_contains_expected_fields(self):
        """Each CapabilityMatch has id, name, domain, description, distance."""
        mock_store = MagicMock()
        mock_store.search.return_value = [
            _make_search_result("cap-1", "process_refund", "payment", "退款处理 API", 0.25),
        ]

        finder = CapabilityFinder(mock_store)
        results = finder.find("退款", top_k=1)

        assert len(results) == 1
        match = results[0]
        assert isinstance(match, CapabilityMatch)
        assert match.id == "cap-1"
        assert match.name == "process_refund"
        assert match.domain == "payment"
        assert match.description == "退款处理 API"
        assert 0.0 <= match.distance <= 2.0

    def test_find_with_domain_filter(self):
        """find() passes domain filter to ChromaStore.search()."""
        mock_store = MagicMock()
        mock_store.search.return_value = [
            _make_search_result("cap-inv", "inv", "inventory", "库存查询"),
        ]

        finder = CapabilityFinder(mock_store)
        results = finder.find("查询", top_k=5, domain="inventory")

        assert len(results) == 1
        assert results[0].domain == "inventory"
        # Verify domain filter was passed correctly
        mock_store.search.assert_called_once_with(
            "查询", n_results=5, where={"business_domain": "inventory"}
        )

    def test_find_passes_n_results_to_store(self):
        """find() passes top_k as n_results to ChromaDB."""
        mock_store = MagicMock()
        mock_store.search.return_value = []

        finder = CapabilityFinder(mock_store)
        finder.find("query", top_k=7)

        mock_store.search.assert_called_once_with("query", n_results=7, where=None)
