from __future__ import annotations

from pathlib import Path

from ontoagent.domain.schema import ComplianceItem, DataAsset
from ontoagent.pipeline.business_loader import load_business_ontology


class TestLoadBusinessOntology:
    """Tests for business_loader.load_business_ontology."""

    def test_loads_data_assets_and_compliance_items(self, tmp_path: Path) -> None:
        """Should parse 3 DataAssets and 3 ComplianceItems from the real YAML config."""
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
  - name: "系统运行日志"
    description: "运维数据"
    sensitivity: "internal"
    data_type: "operational"
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

        assets, items = load_business_ontology(str(yaml_path))

        # Verify 3 DataAssets
        assert len(assets) == 3
        assert all(isinstance(a, DataAsset) for a in assets)
        assert assets[0].name == "用户个人信息"
        assert assets[0].sensitivity == "confidential"
        assert assets[0].data_type == "pii"
        assert assets[0].aliases == ["PII"]
        assert assets[1].name == "支付交易记录"
        assert assets[1].sensitivity == "restricted"
        assert assets[1].data_type == "financial"
        assert assets[2].name == "系统运行日志"
        assert assets[2].sensitivity == "internal"
        assert assets[2].data_type == "operational"

        # Verify 3 ComplianceItems
        assert len(items) == 3
        assert all(isinstance(i, ComplianceItem) for i in items)
        assert items[0].name == "GDPR-删除"
        assert items[0].regulation == "GDPR"
        assert items[0].severity == "critical"
        assert items[1].name == "SOX-审计"
        assert items[1].regulation == "SOX"
        assert items[1].severity == "high"
        assert items[2].name == "PCI-加密"
        assert items[2].regulation == "PCI-DSS"
        assert items[2].severity == "critical"

    def test_loads_empty_yaml(self, tmp_path: Path) -> None:
        """Should return empty lists when YAML has no entries."""
        yaml_path = tmp_path / "empty.yaml"
        yaml_path.write_text("data_assets: []\ncompliance_items: []\n")

        assets, items = load_business_ontology(str(yaml_path))

        assert assets == []
        assert items == []

    def test_loads_missing_sections(self, tmp_path: Path) -> None:
        """Should return empty lists when sections are missing."""
        yaml_path = tmp_path / "minimal.yaml"
        yaml_path.write_text("")

        assets, items = load_business_ontology(str(yaml_path))

        assert assets == []
        assert items == []

    def test_loads_real_config_file(self) -> None:
        """Should successfully load the real business_ontology.yaml shipped with the project."""
        yaml_path = (
            Path(__file__).parent.parent.parent.parent / "src" / "ontoagent" / "pipeline" / "business_ontology.yaml"
        )

        assets, items = load_business_ontology(yaml_path)

        # Verify the real config has 3 of each
        assert len(assets) == 3
        assert len(items) == 3

        # Spot-check first DataAsset
        assert assets[0].name == "用户个人信息"
        assert assets[0].sensitivity == "confidential"
        assert assets[0].data_type == "pii"

        # Spot-check first ComplianceItem
        assert items[0].name == "GDPR-17-删除权"
        assert items[0].regulation == "GDPR"
        assert items[0].severity == "critical"
