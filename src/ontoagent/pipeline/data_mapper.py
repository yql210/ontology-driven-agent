from __future__ import annotations

from ontoagent.domain.schema import CodeEntity, DataAsset


def map_code_to_data_assets(
    code_entities: list[CodeEntity], data_assets: list[DataAsset]
) -> list[tuple[str, str]]:
    """返回 (code_entity_id, data_asset_id) 匹配对。

    策略: DataAsset.aliases 每个词在 CodeEntity.name 中不区分大小写子串匹配。
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for asset in data_assets:
        for alias in asset.aliases:
            alias_lower = alias.lower()
            for ce in code_entities:
                key = (ce.id, asset.id)
                if key not in seen and alias_lower in ce.name.lower():
                    pairs.append(key)
                    seen.add(key)
                    break
    return pairs
