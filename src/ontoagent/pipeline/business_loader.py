from __future__ import annotations

from pathlib import Path

import yaml

from ontoagent.domain.schema import ComplianceItem, DataAsset


def load_business_ontology(yaml_path: str | Path) -> tuple[list[DataAsset], list[ComplianceItem]]:
    """Load business ontology definitions from a YAML configuration file.

    Args:
        yaml_path: Path to the business_ontology.yaml file.

    Returns:
        A tuple of (data_assets, compliance_items) parsed from the YAML.
    """
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}
    assets = [DataAsset(**item) for item in data.get("data_assets", [])]
    items = [ComplianceItem(**item) for item in data.get("compliance_items", [])]
    return assets, items
