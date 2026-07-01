"""RED phase — CapabilityReasoner tests.
All should fail until CapabilityReasoner is implemented.
"""

from __future__ import annotations


class TestCapabilityReasoner:
    """CapabilityReasoner — transitive inference on PRODUCES/CONSUMES relations."""

    def test_direct_dataflow(self):
        """A PRODUCES X, B CONSUMES X → dataflow edge A→B."""
        from ontoagent.execution.reasoner.capability_reasoner import CapabilityReasoner

        capabilities = [
            {"id": "cap-A", "name": "订单创建", "business_domain": "order"},
            {"id": "cap-B", "name": "订单通知", "business_domain": "order"},
        ]
        relations = [
            ("cap-A", "produces", "OrderCreated"),
            ("cap-B", "consumes", "OrderCreated"),
        ]

        reasoner = CapabilityReasoner()
        deps = reasoner.infer_dataflow(capabilities, relations)

        assert len(deps) == 1
        assert deps[0].source == "cap-A"
        assert deps[0].target == "cap-B"
        assert deps[0].via == "OrderCreated"

    def test_transitive_dataflow_chain(self):
        """A→X→B, B→Y→C → A→B→C inferred transitively."""
        from ontoagent.execution.reasoner.capability_reasoner import CapabilityReasoner

        capabilities = [
            {"id": "cap-A", "name": "数据采集"},
            {"id": "cap-B", "name": "数据处理"},
            {"id": "cap-C", "name": "数据报告"},
        ]
        relations = [
            ("cap-A", "produces", "RawData"),
            ("cap-B", "consumes", "RawData"),
            ("cap-B", "produces", "ProcessedData"),
            ("cap-C", "consumes", "ProcessedData"),
        ]

        reasoner = CapabilityReasoner()
        deps = reasoner.infer_dataflow(capabilities, relations)

        assert len(deps) == 2
        # Verify both edges exist
        edges = {(d.source, d.target, d.via) for d in deps}
        assert ("cap-A", "cap-B", "RawData") in edges
        assert ("cap-B", "cap-C", "ProcessedData") in edges

    def test_no_relation_produces_empty_deps(self):
        """Capabilities with no PRODUCES/CONSUMES → empty deps."""
        from ontoagent.execution.reasoner.capability_reasoner import CapabilityReasoner

        capabilities = [
            {"id": "cap-A", "name": "独立能力A"},
            {"id": "cap-B", "name": "独立能力B"},
        ]
        relations = []  # no relations

        reasoner = CapabilityReasoner()
        deps = reasoner.infer_dataflow(capabilities, relations)

        assert deps == []

    def test_unmatched_consume_no_dataflow(self):
        """B CONSUMES X but no one PRODUCES X → no edge."""
        from ontoagent.execution.reasoner.capability_reasoner import CapabilityReasoner

        capabilities = [
            {"id": "cap-A", "name": "订单创建", "business_domain": "order"},
            {"id": "cap-B", "name": "物流发货", "business_domain": "logistics"},
        ]
        relations = [
            ("cap-A", "produces", "OrderCreated"),
            ("cap-B", "consumes", "UnknownData"),  # no one produces this
        ]

        reasoner = CapabilityReasoner()
        deps = reasoner.infer_dataflow(capabilities, relations)

        assert deps == []

    def test_multiple_consumers_of_same_produce(self):
        """A produces X, B and C both consume X → two dataflow edges."""
        from ontoagent.execution.reasoner.capability_reasoner import CapabilityReasoner

        capabilities = [
            {"id": "cap-A", "name": "用户注册"},
            {"id": "cap-B", "name": "发送欢迎邮件"},
            {"id": "cap-C", "name": "创建用户目录"},
        ]
        relations = [
            ("cap-A", "produces", "UserCreated"),
            ("cap-B", "consumes", "UserCreated"),
            ("cap-C", "consumes", "UserCreated"),
        ]

        reasoner = CapabilityReasoner()
        deps = reasoner.infer_dataflow(capabilities, relations)

        assert len(deps) == 2
        edges = {(d.source, d.target, d.via) for d in deps}
        assert ("cap-A", "cap-B", "UserCreated") in edges
        assert ("cap-A", "cap-C", "UserCreated") in edges

    def test_circular_dependency_handled(self):
        """A produces X consumed by B, B produces Y consumed by A → two edges, no infinite loop."""
        from ontoagent.execution.reasoner.capability_reasoner import CapabilityReasoner

        capabilities = [
            {"id": "cap-A", "name": "服务A"},
            {"id": "cap-B", "name": "服务B"},
        ]
        relations = [
            ("cap-A", "produces", "DataA"),
            ("cap-B", "consumes", "DataA"),
            ("cap-B", "produces", "DataB"),
            ("cap-A", "consumes", "DataB"),
        ]

        reasoner = CapabilityReasoner()
        deps = reasoner.infer_dataflow(capabilities, relations)

        assert len(deps) == 2
        edges = {(d.source, d.target, d.via) for d in deps}
        assert ("cap-A", "cap-B", "DataA") in edges
        assert ("cap-B", "cap-A", "DataB") in edges

    def test_dataflow_dependency_has_expected_fields(self):
        """DataflowDependency exposes source, target, via, source_name, target_name."""
        from ontoagent.execution.reasoner.capability_reasoner import CapabilityReasoner

        capabilities = [
            {"id": "cap-1", "name": "支付处理", "business_domain": "payment"},
            {"id": "cap-2", "name": "发票生成", "business_domain": "billing"},
        ]
        relations = [
            ("cap-1", "produces", "PaymentResult"),
            ("cap-2", "consumes", "PaymentResult"),
        ]

        reasoner = CapabilityReasoner()
        deps = reasoner.infer_dataflow(capabilities, relations)

        dep = deps[0]
        assert dep.source == "cap-1"
        assert dep.target == "cap-2"
        assert dep.via == "PaymentResult"
        assert dep.source_name == "支付处理"
        assert dep.target_name == "发票生成"

    def test_reasoner_accepts_real_entities(self):
        """Reasoner accepts actual CapabilityEntity objects, not just dicts."""
        from ontoagent.domain.schema import CapabilityEntity
        from ontoagent.execution.reasoner.capability_reasoner import CapabilityReasoner

        cap_a = CapabilityEntity(
            name="用户认证", business_domain="auth", description="认证用户身份"
        )
        cap_b = CapabilityEntity(
            name="权限检查", business_domain="auth", description="检查用户权限"
        )
        relations = [
            ("cap-a", "produces", "AuthToken"),
            ("cap-b", "consumes", "AuthToken"),
        ]

        reasoner = CapabilityReasoner()
        deps = reasoner.infer_dataflow([cap_a, cap_b], relations)

        assert len(deps) == 1
        assert deps[0].source == "cap-a"
        assert deps[0].target == "cap-b"
