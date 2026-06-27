from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ontoagent.config import OntoAgentConfig
from ontoagent.pipeline.builder import OntoAgentBuilder
from ontoagent.store.schema_version import SchemaStatus


@pytest.fixture
def mock_config() -> OntoAgentConfig:
    """创建测试配置。"""
    return OntoAgentConfig(
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="test",
        chroma_persist_dir=None,
        ollama_base_url="http://localhost:11434",
        embedding_model="test-model",
    )


@pytest.fixture
def builder(mock_config: OntoAgentConfig) -> OntoAgentBuilder:
    """创建 Builder 实例。"""
    return OntoAgentBuilder(mock_config)


@pytest.fixture
def temp_repo_with_business_yaml(tmp_path: Path) -> Path:
    """创建带 ontoagent.yaml 的临时测试仓库。"""
    (tmp_path / "module1.py").write_text("def customer():\n    pass\n\nclass Bar:\n    pass\n")
    (tmp_path / "module2.py").write_text("def payment():\n    pass\n")

    yaml_content = """data_assets:
  - name: CustomerData
    description: Customer PII data including names and emails
    sensitivity: confidential
    data_type: pii
    aliases:
      - customer
      - user_data
      - pii
  - name: PaymentRecords
    description: Financial transaction records
    sensitivity: restricted
    data_type: financial
    aliases:
      - payment
      - transaction

compliance_items:
  - name: GDPR-Article5
    description: Personal data shall be processed lawfully, fairly and transparently
    regulation: GDPR
    severity: critical
    requirement: Data processing must have a lawful basis and be transparent to data subjects
  - name: PCI-DSS-Requirement3
    description: Protect stored cardholder data
    regulation: PCI-DSS
    severity: high
    requirement: Cardholder data must be encrypted at rest using strong cryptography
"""
    (tmp_path / "ontoagent.yaml").write_text(yaml_content)
    return tmp_path


class TestBusinessOntologyLoading:
    """测试 business ontology YAML 加载。"""

    def test_yaml_exists_and_loaded_during_build(
        self, builder: OntoAgentBuilder, temp_repo_with_business_yaml: Path
    ) -> None:
        """验证 ontoagent.yaml 存在时，DataAsset 和 ComplianceItem 被写入 Neo4j。"""
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
            patch.object(builder, "_check_llm_available", return_value=False),
        ):
            # Act
            result = builder.build(temp_repo_with_business_yaml)

            # Assert - 构建成功
            assert result.aborted is False
            assert result.files_scanned == 2

            # Assert - DataAsset 和 ComplianceItem 被写入 Neo4j
            # 收集所有 merge_nodes_batch 调用中的 label 参数
            merge_nodes_labels = [
                call.kwargs.get("label") or call.args[0]
                for call in mock_graph.merge_nodes_batch.call_args_list
            ]

            assert "DataAsset" in merge_nodes_labels, (
                f"Expected 'DataAsset' in merge_nodes_batch labels, got {merge_nodes_labels}"
            )
            assert "ComplianceItem" in merge_nodes_labels, (
                f"Expected 'ComplianceItem' in merge_nodes_batch labels, got {merge_nodes_labels}"
            )

            # Assert - 验证写入的 DataAsset 数量
            for call in mock_graph.merge_nodes_batch.call_args_list:
                label = call.kwargs.get("label") or call.args[0]
                if label == "DataAsset":
                    data_dicts = call.kwargs.get("dicts") or call.args[1]
                    assert len(data_dicts) == 2  # CustomerData + PaymentRecords
                    names = [d["name"] for d in data_dicts]
                    assert "CustomerData" in names
                    assert "PaymentRecords" in names
                elif label == "ComplianceItem":
                    data_dicts = call.kwargs.get("dicts") or call.args[1]
                    assert len(data_dicts) == 2  # GDPR-Article5 + PCI-DSS-Requirement3
                    names = [d["name"] for d in data_dicts]
                    assert "GDPR-Article5" in names
                    assert "PCI-DSS-Requirement3" in names

            # Assert - processes_data 关系被写入
            rel_types = []
            for call in mock_graph.merge_relations_batch.call_args_list:
                rel_data = call.kwargs.get("dicts") or call.args[0]
                for rel in rel_data:
                    rel_types.append(rel.get("rel_type", ""))

            assert "processes_data" in rel_types, (
                f"Expected 'processes_data' in relation types, got {rel_types}"
            )

    def test_yaml_missing_skips_gracefully(
        self, builder: OntoAgentBuilder, tmp_path: Path
    ) -> None:
        """验证 ontoagent.yaml 不存在时，正常跳过不报错。"""
        # Arrange
        (tmp_path / "module1.py").write_text("def foo():\n    pass\n")
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
            patch.object(builder, "_check_llm_available", return_value=False),
        ):
            # Act
            result = builder.build(tmp_path)

            # Assert - 不报错，正常完成
            assert result.aborted is False
            assert result.files_scanned == 1

            # Assert - DataAsset/ComplianceItem 标签未被使用
            merge_nodes_labels = [
                call.kwargs.get("label") or call.args[0]
                for call in mock_graph.merge_nodes_batch.call_args_list
            ]
            assert "DataAsset" not in merge_nodes_labels
            assert "ComplianceItem" not in merge_nodes_labels
