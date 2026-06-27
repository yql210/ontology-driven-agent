"""Phase 3 integration tests — end-to-end cross-service & MQ bridge + ontology pipeline.

测试覆盖:
1. Python 源码解析 → HTTP 调用提取 → calls_service 关系验证
2. Python 源码解析 → MQ producer 提取 → publishes_to 关系验证
3. YAML 业务本体加载 → DataAsset/ComplianceItem 构造 → linker 串联
"""

from __future__ import annotations

import json
from pathlib import Path

from ontoagent.domain.schema import CodeEntity, ComplianceItem, DataAsset, Relation, ServiceEntity
from ontoagent.parsing.extractor.external_calls import extract_external_calls_python
from ontoagent.parsing.parser.base import ExtractedRelation
from ontoagent.parsing.parser.python_parser import PythonParser
from ontoagent.pipeline.business_loader import load_business_ontology
from ontoagent.pipeline.data_mapper import map_code_to_data_assets
from ontoagent.pipeline.service_linker import build_service_relations
from ontoagent.pipeline.topic_linker import build_topic_relations

# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: PythonParser → HTTP calls → calls_service 端到端
# ═══════════════════════════════════════════════════════════════════════════════

PYTHON_HTTP_SERVICE_CODE = b"""\
import requests

def charge_payment(user_id: str, amount: float) -> dict:
    response = requests.post("http://payment-api/charge", json={
        "user_id": user_id,
        "amount": amount,
    })
    return response.json()

def check_order_status() -> dict:
    resp = requests.get("http://order-service/orders/12345")
    return resp.json()
"""


def test_python_calls_service_integration() -> None:
    """端到端: PythonParser 解析 requests.post/get → extract_external_calls_python → 验证 calls_service 关系。

    场景: 模块包含对 payment-api 和 order-service 的 HTTP 调用。
    预期: 提取 2 条 calls_service 关系，target_name 为 service hostname。
    """
    # ── Arrange ──
    parser = PythonParser()
    module_name = "payment_module"
    file_path = "payment_module.py"

    # ── Act: parse + extract external calls ──
    tree = parser._parser.parse(PYTHON_HTTP_SERVICE_CODE)
    root_node = tree.root_node
    relations = extract_external_calls_python(root_node, PYTHON_HTTP_SERVICE_CODE, module_name, file_path)

    # ── Assert: relations contain calls_service ──
    assert len(relations) == 2, f"expected 2 external calls, got {len(relations)}"

    call_targets = {r.target_name for r in relations}
    assert call_targets == {"payment-api", "order-service"}, f"unexpected targets: {call_targets}"

    for r in relations:
        assert r.source_name == module_name
        assert r.source_type == "module"
        assert r.target_type == "ServiceEntity"
        assert r.relation_type == "calls_service", f"expected calls_service, got {r.relation_type}"
        assert r.file_path == file_path

    # ── Act: pass through service_linker to build ServiceEntity + Relation ──
    services, svc_relations = build_service_relations(relations)

    # ── Assert: services deduplicated, Relations created ──
    assert len(services) == 2
    service_names = {s.name for s in services}
    assert service_names == {"payment-api", "order-service"}
    for s in services:
        assert isinstance(s, ServiceEntity)
        assert s.status == "running"
        assert s.version == "unknown"

    assert len(svc_relations) == 2
    for r in svc_relations:
        assert isinstance(r, Relation)
        assert r.relation_type == "calls_service"
        assert r.source_id == module_name


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: PythonParser → MQ producer → publishes_to + consumed_by 桥接
# ═══════════════════════════════════════════════════════════════════════════════

PYTHON_MQ_BRIDGE_CODE = b"""\
import json
from kafka import KafkaProducer

producer = KafkaProducer(bootstrap_servers="localhost:9092")

def publish_order_event(order: dict) -> None:
    producer.send("order-events", value=json.dumps(order).encode())

def publish_payment_event(payment: dict) -> None:
    producer.send("payment-events", value=json.dumps(payment).encode())
"""


def test_mq_pub_sub_bridge() -> None:
    """端到端: PythonParser 解析 kafka producer.send → extract → publishes_to 关系。

    场景: 模块发布消息到 "order-events" 和 "payment-events" 两个 topic。
    同时验证 mq_consumer 一侧的 consumed_by 桥接。
    """
    # ── Arrange ──
    parser = PythonParser()
    module_name = "event_producer"
    file_path = "event_producer.py"

    # ── Act: parse + extract external calls ──
    tree = parser._parser.parse(PYTHON_MQ_BRIDGE_CODE)
    root_node = tree.root_node
    relations = extract_external_calls_python(root_node, PYTHON_MQ_BRIDGE_CODE, module_name, file_path)

    # ── Assert: relations contain publishes_to ──
    assert len(relations) == 2, f"expected 2 publish calls, got {len(relations)}"

    topic_names = {r.target_name for r in relations}
    assert topic_names == {"order-events", "payment-events"}, f"unexpected topics: {topic_names}"

    for r in relations:
        assert r.source_name == module_name
        assert r.source_type == "module"
        assert r.target_type == "ConceptEntity"
        assert r.relation_type == "publishes_to", f"expected publishes_to, got {r.relation_type}"
        assert r.file_path == file_path

    # ── Act: pass through topic_linker to build ConceptEntity + Relation ──
    code_entities: list[CodeEntity] = []
    topics, topic_relations = build_topic_relations(code_entities, relations)

    # ── Assert: ConceptEntities for each unique topic ──
    assert len(topics) == 2
    topic_name_set = {t.name for t in topics}
    assert topic_name_set == {"order-events", "payment-events"}
    for t in topics:
        assert t.entity_type == "message_topic"
        assert t.description.startswith("消息主题:")

    assert len(topic_relations) == 2
    for r in topic_relations:
        assert r.relation_type == "publishes_to"
        assert r.source_id == module_name

    # ── Arrange: mq_consumer 侧 ──
    consumer_code = [
        CodeEntity(
            name="handle_order_event",
            entity_type="function",
            entry_category="mq_consumer",
            entry_metadata=json.dumps({"topic": "order-events", "queue": "order-queue"}),
        ),
        CodeEntity(
            name="handle_payment_event",
            entity_type="function",
            entry_category="mq_consumer",
            entry_metadata=json.dumps({"topic": "payment-events", "queue": "payment-queue"}),
        ),
    ]

    # ── Act: 桥接 consumer 到 topic ──
    consumer_topics, consumer_relations = build_topic_relations(consumer_code, [])

    # ── Assert: consumed_by 关系建立 ──
    assert len(consumer_topics) == 2
    assert len(consumer_relations) == 2
    for r in consumer_relations:
        assert r.relation_type == "consumed_by"
        # source_id 是 CodeEntity 的 UUID
        source_ids = {ce.id for ce in consumer_code}
        assert r.source_id in source_ids


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: YAML 本体加载 → DataAsset/ComplianceItem → map + linker 串联
# ═══════════════════════════════════════════════════════════════════════════════

YAML_ONTOLOGY_FULL = """\
data_assets:
  - name: "用户个人信息"
    description: "PII数据"
    sensitivity: "confidential"
    data_type: "pii"
    aliases: ["PII", "user_profile"]
  - name: "支付交易记录"
    description: "金融交易数据"
    sensitivity: "restricted"
    data_type: "financial"
    aliases: ["payment", "transaction"]
  - name: "审计日志"
    description: "合规审计数据"
    sensitivity: "internal"
    data_type: "operational"
    aliases: ["audit", "log_entry"]
compliance_items:
  - name: "GDPR-删除权"
    description: "用户数据删除权"
    regulation: "GDPR"
    severity: "critical"
    requirement: "30天内完成删除"
  - name: "PCI-加密标准"
    description: "支付卡数据加密"
    regulation: "PCI-DSS"
    severity: "critical"
    requirement: "AES-256加密存储"
  - name: "SOX-审计追踪"
    description: "财务审计追踪"
    regulation: "SOX"
    severity: "high"
    requirement: "保留审计日志7年"
"""


def test_full_pipeline_with_linkers(tmp_path: Path) -> None:
    """端到端: YAML 加载 → DataAsset/ComplianceItem 构造 → data_mapper → linker 验证。

    场景: 完整的业务本体 YAML (3 DataAssets + 3 ComplianceItems)，
    通过 data_mapper 将 CodeEntity 映射到 DataAsset，结合 service_linker
    和 topic_linker 形成完整流水线。
    """
    # ── Arrange: write YAML ──
    yaml_path = tmp_path / "test_ontology.yaml"
    yaml_path.write_text(YAML_ONTOLOGY_FULL)

    # ── Act: load business ontology ──
    assets, items = load_business_ontology(str(yaml_path))

    # ── Assert: DataAsset 构造正确 ──
    assert len(assets) == 3, f"expected 3 DataAssets, got {len(assets)}"
    assert all(isinstance(a, DataAsset) for a in assets)
    asset_names = {a.name for a in assets}
    assert asset_names == {"用户个人信息", "支付交易记录", "审计日志"}

    for a in assets:
        assert a.id is not None
        assert a.name
        assert a.sensitivity in DataAsset.VALID_SENSITIVITIES
        assert a.data_type in DataAsset.VALID_DATA_TYPES
        assert len(a.aliases) > 0, f"DataAsset '{a.name}' has empty aliases"

    # ── Assert: ComplianceItem 构造正确 ──
    assert len(items) == 3, f"expected 3 ComplianceItems, got {len(items)}"
    assert all(isinstance(i, ComplianceItem) for i in items)
    item_names = {i.name for i in items}
    assert item_names == {"GDPR-删除权", "PCI-加密标准", "SOX-审计追踪"}

    for i in items:
        assert i.id is not None
        assert i.name
        assert i.severity in ComplianceItem.VALID_SEVERITIES
        assert i.regulation

    # ── Arrange: create mock CodeEntities ──
    code_entities = [
        CodeEntity(name="get_user_pii", entity_type="function"),  # matches PII alias
        CodeEntity(name="process_payment", entity_type="function"),  # matches payment alias
        CodeEntity(name="write_audit_log_entry", entity_type="function"),  # matches log_entry alias
    ]

    # ── Act: map code to data assets ──
    pairs = map_code_to_data_assets(code_entities, assets)

    # ── Assert: each CodeEntity matches expected DataAsset ──
    assert len(pairs) == 3, f"expected 3 mapping pairs, got {len(pairs)}"
    assert (code_entities[0].id, assets[0].id) in pairs  # get_user_pii → 用户个人信息
    assert (code_entities[1].id, assets[1].id) in pairs  # process_payment → 支付交易记录
    assert (code_entities[2].id, assets[2].id) in pairs  # write_audit_log_entry → 审计日志

    # ── Arrange: 构建 ExtractedRelations 模拟 HTTP 调用 ──
    http_relations = [
        ExtractedRelation(
            source_name="get_user_pii",
            source_type="function",
            target_name="user-service",
            target_type="ServiceEntity",
            relation_type="calls_service",
            file_path="user_api.py",
        ),
        ExtractedRelation(
            source_name="process_payment",
            source_type="function",
            target_name="payment-gateway",
            target_type="ServiceEntity",
            relation_type="calls_service",
            file_path="payment_api.py",
        ),
    ]

    # ── Act: service_linker 串联 ──
    services, svc_relations = build_service_relations(http_relations)

    # ── Assert: ServiceEntity + Relation 正确生成 ──
    assert len(services) == 2
    svc_names = {s.name for s in services}
    assert svc_names == {"user-service", "payment-gateway"}
    for s in services:
        assert isinstance(s, ServiceEntity)
        assert s.status == "running"

    assert len(svc_relations) == 2
    for r in svc_relations:
        assert r.relation_type == "calls_service"

    # ── Arrange: 构建 ExtractedRelations 模拟 MQ publish ──
    mq_relations = [
        ExtractedRelation(
            source_name="process_payment",
            source_type="function",
            target_name="payment-events",
            target_type="ConceptEntity",
            relation_type="publishes_to",
            file_path="payment_api.py",
        ),
    ]

    # ── Act: topic_linker 串联 ──
    topics, topic_relations = build_topic_relations(code_entities, mq_relations)

    # ── Assert: ConceptEntity(message_topic) + Relation 正确生成 ──
    assert len(topics) == 1
    assert topics[0].name == "payment-events"
    assert topics[0].entity_type == "message_topic"

    assert len(topic_relations) == 1
    assert topic_relations[0].relation_type == "publishes_to"
    assert topic_relations[0].source_id == "process_payment"
