from __future__ import annotations

import pytest

from ontoagent.domain.exceptions import SchemaValidationError
from ontoagent.domain.schema import DataAsset


class TestDataAsset:
    def test_construct_minimal(self):
        da = DataAsset(name="手机号", description="用户手机号", sensitivity="confidential", data_type="pii")
        assert da.name == "手机号"
        assert da.description == "用户手机号"
        assert da.sensitivity == "confidential"
        assert da.data_type == "pii"
        assert da.aliases == []
        assert da.id is not None

    def test_construct_with_aliases(self):
        da = DataAsset(
            name="手机号",
            description="...",
            sensitivity="confidential",
            data_type="pii",
            aliases=["phone", "mobile"],
        )
        assert "phone" in da.aliases
        assert "mobile" in da.aliases

    def test_invalid_sensitivity_raises(self):
        with pytest.raises(SchemaValidationError):
            DataAsset(name="x", description="x", sensitivity="INVALID", data_type="pii")

    def test_invalid_data_type_raises(self):
        with pytest.raises(SchemaValidationError):
            DataAsset(name="x", description="x", sensitivity="internal", data_type="INVALID")

    def test_empty_name_raises(self):
        with pytest.raises(SchemaValidationError):
            DataAsset(name="", description="x", sensitivity="internal", data_type="pii")
