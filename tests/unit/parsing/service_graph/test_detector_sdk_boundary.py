from __future__ import annotations

from pathlib import Path


def test_method_core_has_no_protocol_specific_imports_or_constants():
    core = Path("src/ontoagent/parsing/service_graph/methods.py").read_text(encoding="utf-8")
    sdk = Path("src/ontoagent/parsing/service_graph/detector_sdk.py").read_text(encoding="utf-8")

    assert "Dubbo" not in core + sdk
    assert "JSF" not in core + sdk
    assert ".detectors." not in core + sdk
