from __future__ import annotations

from ontoagent.domain.schema import CodeEntity, DataAsset
from ontoagent.pipeline.data_mapper import map_code_to_data_assets


def test_exact_match() -> None:
    """CodeEntity name contains alias → 1 pair."""
    ce = CodeEntity(name="validate_phone", entity_type="function")
    asset = DataAsset(
        name="PhoneNumber",
        description="User phone number",
        sensitivity="internal",
        data_type="pii",
        aliases=["phone"],
    )
    result = map_code_to_data_assets([ce], [asset])
    assert len(result) == 1
    assert result[0] == (ce.id, asset.id)


def test_no_match() -> None:
    """No alias substring in CodeEntity name → 0 pairs."""
    ce = CodeEntity(name="hello_world", entity_type="function")
    asset = DataAsset(
        name="PhoneNumber",
        description="User phone number",
        sensitivity="internal",
        data_type="pii",
        aliases=["phone"],
    )
    result = map_code_to_data_assets([ce], [asset])
    assert len(result) == 0


def test_multiple_assets() -> None:
    """Single CodeEntity matches multiple DataAssets → 2 pairs."""
    ce = CodeEntity(name="process_payment_amount", entity_type="function")
    asset1 = DataAsset(
        name="PaymentInfo",
        description="Payment details",
        sensitivity="confidential",
        data_type="financial",
        aliases=["payment"],
    )
    asset2 = DataAsset(
        name="Amount",
        description="Transaction amount",
        sensitivity="internal",
        data_type="financial",
        aliases=["amount"],
    )
    result = map_code_to_data_assets([ce], [asset1, asset2])
    assert len(result) == 2
    assert (ce.id, asset1.id) in result
    assert (ce.id, asset2.id) in result
