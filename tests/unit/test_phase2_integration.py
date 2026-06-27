"""Phase 2 integration tests — YAML → DataAsset/ComplianceItem → CodeEntity mapping.

Validates the end-to-end flow: load business ontology from YAML, construct simulated
CodeEntity objects, and map them to DataAssets via alias-based matching.
"""

from __future__ import annotations

from pathlib import Path

from ontoagent.domain.schema import CodeEntity, ComplianceItem, DataAsset
from ontoagent.pipeline.business_loader import load_business_ontology
from ontoagent.pipeline.data_mapper import map_code_to_data_assets


def test_yaml_to_mapper_integration(tmp_path: Path) -> None:
    """Load sample YAML → 3 DataAssets + 3 ComplianceItems → map 3 CodeEntities.

    Constructs 3 CodeEntity objects whose names contain aliases from the YAML,
    then verifies map_code_to_data_assets returns the expected (ce_id, asset_id) pairs.
    """
    # ── Arrange: write sample YAML ──
    yaml_path = tmp_path / "test_ontology.yaml"
    yaml_path.write_text("""\
data_assets:
  - name: "用户个人信息"
    description: "PII数据"
    sensitivity: "confidential"
    data_type: "pii"
    aliases: ["PII"]
  - name: "支付交易记录"
    description: "金融数据"
    sensitivity: "restricted"
    data_type: "financial"
    aliases: ["payment"]
  - name: "系统运行日志"
    description: "运维数据"
    sensitivity: "internal"
    data_type: "operational"
    aliases: ["log"]
compliance_items:
  - name: "GDPR-删除"
    description: "删除权"
    regulation: "GDPR"
    severity: "critical"
    requirement: "30天删除"
  - name: "SOX-审计"
    description: "审计追踪"
    regulation: "SOX"
    severity: "high"
    requirement: "保留7年"
  - name: "PCI-加密"
    description: "加密要求"
    regulation: "PCI-DSS"
    severity: "critical"
    requirement: "AES-256"
""")

    # ── Act: load YAML ──
    assets, items = load_business_ontology(str(yaml_path))

    # ── Assert: YAML parsed correctly ──
    assert len(assets) == 3
    assert all(isinstance(a, DataAsset) for a in assets)
    assert len(items) == 3
    assert all(isinstance(i, ComplianceItem) for i in items)

    # ── Arrange: 3 mock CodeEntities ──
    ce1 = CodeEntity(name="get_pii_data", entity_type="function")  # matches "PII"
    ce2 = CodeEntity(name="process_payment", entity_type="function")  # matches "payment"
    ce3 = CodeEntity(name="write_log_entry", entity_type="function")  # matches "log"
    code_entities = [ce1, ce2, ce3]

    # ── Act: map code to data assets ──
    pairs = map_code_to_data_assets(code_entities, assets)

    # ── Assert: each CodeEntity matches its expected DataAsset ──
    # ce1 "get_pii_data" → asset[0] "用户个人信息" (alias "PII")
    # ce2 "process_payment" → asset[1] "支付交易记录" (alias "payment")
    # ce3 "write_log_entry" → asset[2] "系统运行日志" (alias "log")
    assert len(pairs) == 3
    assert (ce1.id, assets[0].id) in pairs
    assert (ce2.id, assets[1].id) in pairs
    assert (ce3.id, assets[2].id) in pairs


def test_data_asset_validation_chain(tmp_path: Path) -> None:
    """Verify DataAsset & ComplianceItem construction passes validation; aliases non-empty.

    Loads the real business_ontology.yaml, then confirms every DataAsset and
    ComplianceItem passes post-init validation, and the 3 DataAssets all have
    non-empty aliases.
    """
    # ── Arrange: use the real config file ──
    yaml_path = Path(__file__).parent.parent.parent / "src" / "ontoagent" / "pipeline" / "business_ontology.yaml"
    assets, items = load_business_ontology(yaml_path)

    # ── Assert: DataAsset validation ──
    assert len(assets) == 3
    for i, asset in enumerate(assets, start=1):
        # construction must not raise (sensitivity / data_type / name validation)
        assert isinstance(asset, DataAsset), f"asset[{i}] is not DataAsset"
        assert asset.name, f"asset[{i}] name is empty"
        assert asset.sensitivity in DataAsset.VALID_SENSITIVITIES, (
            f"asset[{i}] sensitivity '{asset.sensitivity}' invalid"
        )
        assert asset.data_type in DataAsset.VALID_DATA_TYPES, f"asset[{i}] data_type '{asset.data_type}' invalid"
        assert asset.id is not None, f"asset[{i}] id is None"

    # ── Assert: ComplianceItem validation ──
    assert len(items) == 3
    for i, item in enumerate(items, start=1):
        assert isinstance(item, ComplianceItem), f"item[{i}] is not ComplianceItem"
        assert item.name, f"item[{i}] name is empty"
        assert item.severity in ComplianceItem.VALID_SEVERITIES, f"item[{i}] severity '{item.severity}' invalid"
        assert item.id is not None, f"item[{i}] id is None"

    # ── Assert: all 3 DataAssets have non-empty aliases ──
    for i, asset in enumerate(assets, start=1):
        assert len(asset.aliases) > 0, f"asset[{i}] '{asset.name}' aliases is empty"
