from pathlib import Path

import pytest

from ontoagent.parsing.service_graph import DetectorFacts, Evidence, MessageEndpoint, RpcEndpoint
from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.models import RepositorySnapshot

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"


def test_i2_models_have_canonical_keys_and_evidence_validation():
    evidence = Evidence("r", "v", "A.java", 1, 1, "d", "1", "x", "subject", 1.0)
    rpc = RpcEndpoint("r", "-:api.Order", "provider", "rpc", "api.Order", "get", "-", "1", "A.java", evidence.id, "get")
    msg = MessageEndpoint("r", "kafka", "producer", "orders", "ignored", "A.java", evidence.id, '"orders"')
    facts = DetectorFacts("d", "1", "r", "v", (), (), (evidence,), (), (), (), (rpc,), (msg,))
    assert rpc.canonical_key == "DUBBO|-|api.Order|get|1"
    assert msg.canonical_key == "MQ|kafka|orders|-"
    assert facts.to_dict()["rpc_endpoints"][0]["canonical_key"] == rpc.canonical_key
    with pytest.raises(ValueError):
        DetectorFacts("d", "1", "r", "v", (), (), (), (), (), (), (rpc,), ())


def test_dubbo_java_annotation_extracts_provider_and_consumer(tmp_path: Path):
    (tmp_path / "Orders.java").write_text(
        """package example.orders;
interface OrderApi { String get(); }
@DubboService(group = \"\", version = \"1\")
public class Orders implements OrderApi { public String get() { return \"ok\"; } }
class Checkout { @DubboReference OrderApi api; void run() { api.get(); } }
""",
        encoding="utf-8",
    )
    facts = DubboDetector().detect(RepositorySnapshot("repo", "rev", tmp_path, frozenset({"java"})))
    assert any(e.role == "provider" and e.interface_name == "example.orders.OrderApi" for e in facts.rpc_endpoints)
    assert any(e.role == "consumer" and e.method == "get" for e in facts.rpc_endpoints)


def test_dubbo_xml_extracts_provider_consumer_and_missing_interface(tmp_path: Path):
    (tmp_path / "dubbo.xml").write_text(
        """<beans>
<dubbo:service interface=\"example.orders.OrderApi\" group=\"g\" version=\"1\"/>
<dubbo:reference interface=\"example.orders.OrderApi\"/>
<dubbo:service ref=\"dynamic\"/>
</beans>
""",
        encoding="utf-8",
    )
    facts = DubboDetector().detect(RepositorySnapshot("repo", "rev", tmp_path, frozenset({"xml"})))
    assert {e.role for e in facts.rpc_endpoints} == {"provider", "consumer"}
    assert facts.rpc_endpoints[0].file_path == "dubbo.xml"
    assert any(u.reason_code == "UNSUPPORTED_CALL_SHAPE" for u in facts.unresolved)
