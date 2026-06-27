from __future__ import annotations

import pytest

from ontoagent.domain.exceptions import SchemaValidationError
from ontoagent.domain.schema import ComplianceItem


class TestComplianceItem:
    def test_construct_minimal(self):
        ci = ComplianceItem(
            name="GDPR-17",
            description="删除权",
            regulation="GDPR",
            severity="critical",
            requirement="30天内删除",
        )
        assert ci.name == "GDPR-17"
        assert ci.regulation == "GDPR"
        assert ci.severity == "critical"
        assert ci.id is not None

    def test_invalid_severity_raises(self):
        with pytest.raises(SchemaValidationError):
            ComplianceItem(
                name="x",
                description="x",
                regulation="x",
                severity="INVALID",
                requirement="x",
            )

    def test_empty_name_raises(self):
        with pytest.raises(SchemaValidationError):
            ComplianceItem(
                name="",
                description="x",
                regulation="x",
                severity="low",
                requirement="x",
            )
