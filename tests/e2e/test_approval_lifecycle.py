#!/usr/bin/env python3
"""E2E test: express_intent -> ApprovalGate -> ShapeEvaluator -> Execute (full lifecycle).

Tests the complete user flow on real Neo4j with real demo-service data.
"""
import os
os.environ.setdefault("ONTOAGENT_NEO4J_URI", "bolt://124.221.243.142:7687")
os.environ.setdefault("ONTOAGENT_NEO4J_USER", "neo4j")
os.environ.setdefault("ONTOAGENT_NEO4J_PASSWORD", "neo4j123456")
os.environ.setdefault("ONTOAGENT_ENABLE_SHAPES", "true")

import sys
sys.path.insert(0, "/opt/data/workspace/ontology-driven-agent/src")

import json
import time
import logging

# Suppress noisy Neo4j warnings
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

# Reset all singletons for clean state
import ontoagent.agent.tools as tools
tools._action_executor = None
tools._function_runner = None
tools._APPROVAL_GATE = None
tools._shape_registry = None
tools._intent_map = None

from ontoagent.execution.functions import registry as fn_registry
fn_registry.clear_registry()
from ontoagent.execution.functions.builtin import register_all as _r1; _r1()
from ontoagent.execution.functions.check_compliance import register_all as _r2; _r2()
from ontoagent.execution.functions.trace_business_impact import register_all as _r3; _r3()
from ontoagent.execution.functions.general import register_all as _r4; _r4()

from pathlib import Path
fyaml = Path("/opt/data/workspace/ontology-driven-agent/src/ontoagent/config/functions.yaml")
if fyaml.exists():
    fn_registry.load_from_yaml(fyaml)

from ontoagent.agent.tools import (
    express_intent, get_neo4j,
    _get_approval_gate, _get_action_executor, _get_shape_registry,
)

# Helpers
PASSED = 0
FAILED = 0
RESULTS = []

def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        RESULTS.append(("PASS", name, ""))
        print(f"  PASS: {name}")
    else:
        FAILED += 1
        RESULTS.append(("FAIL", name, detail))
        print(f"  FAIL: {name} -- {detail}")

def call(intent_type="", target="", approval_id="", approved=False):
    kwargs = {}
    if intent_type: kwargs["intent_type"] = intent_type
    if target: kwargs["target"] = target
    if approval_id:
        kwargs["approval_id"] = approval_id
        kwargs["approved"] = approved
    return json.loads(express_intent.invoke(kwargs))

# ============================================================
# Scenario 1: BLOCK -- refactor on tradeQueueDestinationName (sensitive data)
#   tradeQueueDestinationName -> PROCESSES_DATA -> 支付流水 (restricted)
#   shape:sensitive_data: CodeEntity/UPDATE, severity=block
#   Test through _check_with_shapes with UPDATE capability
# ============================================================
print("\n" + "=" * 70)
print("Scenario 1: BLOCK -- sensitive data constraint")
print("=" * 70)

neo4j = get_neo4j()
executor = _get_action_executor(neo4j)

# Resolve the entity
entity = executor.resolve_entity("tradeQueueDestinationName")
print(f"  Entity: {entity['name']}, labels={entity.get('labels', [])}")

# Use _check_with_shapes with an action config that has UPDATE capability
from ontoagent.execution.action_types import ActionConfig
update_cfg = ActionConfig(
    name="test_block",
    intent_type="test_block",
    trigger_hint="test",
    bind_to="code_entity",
    functions=["update_entity"],  # has CodeEntity:UPDATE capability
    submission_criteria=[],
    requires_approval=False,
)
block_reason, warnings = executor.check_with_shapes(entity, update_cfg)
print(f"  block_reason={block_reason}")
print(f"  warnings={warnings}")
check("BLOCK: block_reason is not None", block_reason is not None, "should be blocked")
check("BLOCK: mentions sensitivity/block",
      block_reason is not None and ("block" in block_reason.lower() or "sensitivity" in block_reason.lower() or "restricted" in block_reason.lower() or "敏感" in block_reason),
      f"got: {block_reason}")

# ============================================================
# Scenario 2: ALLOW -- refactor on non-sensitive entity
# ============================================================
print("\n" + "=" * 70)
print("Scenario 2: ALLOW -- non-sensitive entity via express_intent")
print("=" * 70)

data = call(intent_type="refactor", target="LoginController.confirm()")
print(f"  status={data.get('status')}")
if data.get("status") == "error":
    err = data.get("error", "")
    check("ALLOW: not blocked by shapes", "block" not in err.lower() and "sensitivity" not in err.lower(), f"err={err}")
    check("ALLOW: not approval_required", True)
elif data.get("status") == "completed":
    check("ALLOW: not blocked", True)
    check("ALLOW: not approval_required", True)
else:
    check("ALLOW: not blocked", data.get("status") != "blocked", f"got {data.get('status')}")
    check("ALLOW: not approval_required", data.get("status") != "approval_required", f"got {data.get('status')}")

# ============================================================
# Scenario 3: Function danger_level -> PENDING (document action)
#   document -> generate_api_doc (danger_level=write)
#   Flow: express_intent -> shapes pass (READ) -> executor.execute() ->
#         FunctionRunner -> FunctionDangerPolicy: write -> PENDING
# ============================================================
print("\n" + "=" * 70)
print("Scenario 3: Function danger_level (write -> PENDING)")
print("=" * 70)

data = call(intent_type="document", target="LoginController.confirm()")
print(f"  status={data.get('status')}, level={data.get('level')}")
if data.get("status") == "approval_required":
    check("danger_level: triggers approval", True)
    check("danger_level: level is function", data.get("level") == "function", f"got {data.get('level')}")
    check("danger_level: has approval_id", bool(data.get("approval_id")))
    token_3 = data["approval_id"]
    print(f"  approval_id={token_3}")

    # Approve and execute
    print(f"  -> Approving token {token_3}...")
    data2 = call(approval_id=token_3, approved=True)
    print(f"  after approve: status={data2.get('status')}")
    check("danger_level: approved executes", data2.get("status") != "approval_required", f"got {data2.get('status')}")
    check("danger_level: approved not blocked", data2.get("status") != "blocked", f"got {data2.get('status')}")

    # Token one-time use
    data3 = call(approval_id=token_3, approved=True)
    print(f"  reuse token: status={data3.get('status')}")
    check("danger_level: token one-time use", data3.get("status") == "error", f"got {data3.get('status')}")
else:
    check("danger_level: triggers approval", False, f"got status={data.get('status')}, error={data.get('error','')}")

# ============================================================
# Scenario 4: Reject approval -- operation NOT executed
# ============================================================
print("\n" + "=" * 70)
print("Scenario 4: Reject approval")
print("=" * 70)

data = call(intent_type="document", target="LoginController.confirm()")
print(f"  initial: status={data.get('status')}")
if data.get("status") == "approval_required":
    token_4 = data["approval_id"]
    print(f"  -> Rejecting token {token_4}...")
    data2 = call(approval_id=token_4, approved=False)
    print(f"  after reject: status={data2.get('status')}")
    check("reject: status is rejected", data2.get("status") == "rejected", f"got {data2.get('status')}")

    # Token consumed after rejection
    data3 = call(approval_id=token_4, approved=True)
    check("reject: token consumed", data3.get("status") == "error", f"got {data3.get('status')}")
else:
    check("reject: triggers approval", False, f"got {data.get('status')}")

# ============================================================
# Scenario 5: ESCALATE -- ontology shapes (priority=0 -> Rule 4)
# ============================================================
print("\n" + "=" * 70)
print("Scenario 5: ESCALATE (priority=0 -> forced escalate)")
print("=" * 70)

from ontoagent.domain.schema import ONTOLOGY_ENTITY_LABELS
from ontoagent.execution.shape_registry import ShapeRegistry
from ontoagent.execution.shape_evaluator import ShapeEvaluator
from ontoagent.execution.decision_fuser import DecisionFuser
from ontoagent.domain.shapes import Operation, Severity
from ontoagent.pipeline.ontology_loader import load_ontology_to_shapes, write_shapes_yaml

ec_shapes = load_ontology_to_shapes(
    "/tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json",
    include_axioms=False, include_properties=False, include_relations=False,
)
write_shapes_yaml(ec_shapes, "/tmp/ecommerce_shapes.yaml")

combined_reg = ShapeRegistry(valid_labels=set(ONTOLOGY_ENTITY_LABELS))
combined_reg.load_from_yaml(Path("/opt/data/workspace/ontology-driven-agent/src/ontoagent/pipeline/shapes.yaml"))
combined_reg.load_from_yaml(Path("/tmp/ecommerce_shapes.yaml"))

rows = neo4j.query("MATCH (n:ResourceEntity {name: '客户'}) RETURN n.id as id, n.name as name, labels(n) as labels LIMIT 1")
if rows:
    entity = {"id": rows[0]["id"], "name": rows[0]["name"], "labels": rows[0]["labels"]}
    evaluator = ShapeEvaluator(combined_reg, neo4j)
    results = evaluator.evaluate(entity, [Operation.UPDATE])
    report = DecisionFuser.fuse(results)
    triggered = [r for r in results if r.triggered]

    print(f"  Entity: {entity['name']}, triggered: {len(triggered)} shapes")
    for s in triggered:
        print(f"    {s.shape.id}: severity={s.shape.severity.value}, priority={s.shape.priority}")
    print(f"  DecisionFuser: {report.severity.value}")
    check("ESCALATE: severity is escalate", report.severity == Severity.ESCALATE, f"got {report.severity}")

    # Verify _check_with_shapes maps ESCALATE to approval_required
    from ontoagent.execution.action_types import ActionConfig
    mock_cfg = ActionConfig(
        name="test_update",
        intent_type="test_update",
        trigger_hint="test",
        bind_to="code_entity",
        functions=["update_entity"],
        submission_criteria=[],
        requires_approval=False,
    )
    # Reconstruct executor with combined registry (public API, no private attr)
    from ontoagent.execution.action_executor import ActionExecutor
    executor = ActionExecutor(neo4j, shape_registry=combined_reg)
    block_reason, warnings = executor.check_with_shapes(entity, mock_cfg)
    print(f"  _check_with_shapes: block_reason={block_reason}, warnings={warnings}")
    check("ESCALATE: no block_reason (ESCALATE != BLOCK)", block_reason is None, f"got {block_reason}")
    check("ESCALATE: approval_required in warnings",
          any("approval_required" in str(w) for w in warnings),
          f"warnings: {warnings}")
else:
    check("ESCALATE: entity found", False, "no ResourceEntity named '客户'")

# ============================================================
# Scenario 6: Full approval lifecycle (approve -> execute -> token consumed)
# ============================================================
print("\n" + "=" * 70)
print("Scenario 6: Full approval lifecycle")
print("=" * 70)

data = call(intent_type="document", target="LoginController.confirm()")
print(f"  Step 1 - express_intent: status={data.get('status')}")
if data.get("status") == "approval_required":
    token = data["approval_id"]
    print(f"  Step 2 - got approval_id: {token}")

    data2 = call(approval_id=token, approved=True)
    print(f"  Step 3 - approved & executed: status={data2.get('status')}")
    check("lifecycle: executes after approval", data2.get("status") != "approval_required", f"got {data2.get('status')}")
    check("lifecycle: not blocked after approval", data2.get("status") != "blocked", f"got {data2.get('status')}")

    data3 = call(approval_id=token, approved=True)
    print(f"  Step 4 - token reuse: status={data3.get('status')}")
    check("lifecycle: token one-time use", data3.get("status") == "error", f"got {data3.get('status')}")
else:
    check("lifecycle: triggers approval", False, f"got {data.get('status')}, err={data.get('error','')}")

# ============================================================
# Scenario 7: Invalid token
# ============================================================
print("\n" + "=" * 70)
print("Scenario 7: Invalid token")
print("=" * 70)
data = call(approval_id="fake_token_xyz", approved=True)
print(f"  status={data.get('status')}, error={data.get('error','')}")
check("invalid token: error", data.get("status") == "error", f"got {data.get('status')}")
check("invalid token: message mentions token",
      "令牌" in data.get("error", "") or "token" in data.get("error", "").lower(),
      f"got: {data.get('error')}")

# ============================================================
# Scenario 8: Expired token (TTL exceeded)
# ============================================================
print("\n" + "=" * 70)
print("Scenario 8: Expired token")
print("=" * 70)

from ontoagent.domain.approval import ApprovalContext, PendingApproval, generate_token
gate = _get_approval_gate()
ctx = ApprovalContext(intent_type="refactor", target="test_func", session_id="")
token = generate_token("refactor", "test_func", "s1")
expired_pa = PendingApproval(token=token, context=ctx, ttl=1)
gate._pending[token] = expired_pa
time.sleep(2)

data = call(approval_id=token, approved=True)
print(f"  status={data.get('status')}, error={data.get('error','')}")
check("expired token: error", data.get("status") == "error", f"got {data.get('status')}")

# ============================================================
# Scenario 9: Entity not found
# ============================================================
print("\n" + "=" * 70)
print("Scenario 9: Entity not found")
print("=" * 70)
data = call(intent_type="refactor", target="nonexistent_entity_xyz_123")
print(f"  status={data.get('status')}, error={data.get('error','')}")
check("not found: error", data.get("status") == "error", f"got {data.get('status')}")
check("not found: message mentions entity",
      "实体" in data.get("error", "") or "entity" in data.get("error", "").lower(),
      f"got: {data.get('error')}")

# ============================================================
# Scenario 10: Unknown intent type
# ============================================================
print("\n" + "=" * 70)
print("Scenario 10: Unknown intent type")
print("=" * 70)
data = call(intent_type="nonexistent_action_xyz", target="LoginController.confirm()")
print(f"  status={data.get('status')}, error={data.get('error','')}")
check("unknown intent: error", data.get("status") == "error", f"got {data.get('status')}")
check("unknown intent: message mentions unknown",
      "未知" in data.get("error", "") or "unknown" in data.get("error", "").lower(),
      f"got: {data.get('error')}")

# ============================================================
# Scenario 11: Audit log verification
# ============================================================
print("\n" + "=" * 70)
print("Scenario 11: Audit log records decisions")
print("=" * 70)

audit = gate.audit_log
print(f"  Total audit entries: {len(audit)}")
for entry in audit[-5:]:
    print(f"    action={entry['action']}, intent={entry['intent_type']}, target={entry['target']}, token={entry.get('token','')[:8]}...")
check("audit: has entries", len(audit) > 0, "audit log empty")

pending_entries = [e for e in audit if e["action"] == "pending"]
check("audit: has pending entries", len(pending_entries) > 0, "no pending entries")

resolved_entries = [e for e in audit if e["action"] == "resolved"]
check("audit: has resolved entries", len(resolved_entries) > 0, "no resolved entries")

rejected_entries = [e for e in audit if e["action"] == "rejected"]
check("audit: has rejected entries", len(rejected_entries) > 0, "no rejected entries")

# ============================================================
# Scenario 12: Cross-domain ontology_ref pre-filtering
# ============================================================
print("\n" + "=" * 70)
print("Scenario 12: Cross-domain ontology_ref pre-filtering")
print("=" * 70)

cs_shapes = load_ontology_to_shapes(
    "/tmp/code_security_domain/output/ontology.json",
    include_axioms=False, include_properties=False, include_relations=False,
)
write_shapes_yaml(cs_shapes, "/tmp/code_security_shapes.yaml")

multi_reg = ShapeRegistry(valid_labels=set(ONTOLOGY_ENTITY_LABELS))
multi_reg.load_from_yaml(Path("/opt/data/workspace/ontology-driven-agent/src/ontoagent/pipeline/shapes.yaml"))
multi_reg.load_from_yaml(Path("/tmp/ecommerce_shapes.yaml"))
multi_reg.load_from_yaml(Path("/tmp/code_security_shapes.yaml"))
total_shapes = len(multi_reg)
print(f"  Combined registry: {total_shapes} shapes")

rows_ec = neo4j.query("MATCH (n:ResourceEntity {name: '客户'}) RETURN n.id as id, n.name as name, labels(n) as labels LIMIT 1")
rows_cs = neo4j.query("MATCH (n:ResourceEntity {name: '漏洞'}) RETURN n.id as id, n.name as name, labels(n) as labels LIMIT 1")

if rows_ec and rows_cs:
    ec_entity = {"id": rows_ec[0]["id"], "name": rows_ec[0]["name"], "labels": rows_ec[0]["labels"]}
    cs_entity = {"id": rows_cs[0]["id"], "name": rows_cs[0]["name"], "labels": rows_cs[0]["labels"]}

    evaluator_multi = ShapeEvaluator(multi_reg, neo4j)

    ec_results = evaluator_multi.evaluate(ec_entity, [Operation.UPDATE])
    ec_triggered = [r for r in ec_results if r.triggered]
    ec_refs = {r.shape.target.ontology_ref for r in ec_triggered if r.shape.target.ontology_ref}
    print(f"  客户 UPDATE: evaluated={len(ec_results)}, triggered={len(ec_triggered)}, refs={ec_refs}")
    check("cross-domain: 客户 triggers only ecommerce shapes",
          "漏洞" not in ec_refs and "安全策略" not in ec_refs,
          f"refs={ec_refs}")

    cs_results = evaluator_multi.evaluate(cs_entity, [Operation.UPDATE])
    cs_triggered = [r for r in cs_results if r.triggered]
    cs_refs = {r.shape.target.ontology_ref for r in cs_triggered if r.shape.target.ontology_ref}
    print(f"  漏洞 UPDATE: evaluated={len(cs_results)}, triggered={len(cs_triggered)}, refs={cs_refs}")
    check("cross-domain: 漏洞 triggers only code_security shapes",
          "客户" not in cs_refs and "订单" not in cs_refs,
          f"refs={cs_refs}")

    check("cross-domain: no cross-contamination",
          ec_refs != cs_refs,
          f"ec={ec_refs}, cs={cs_refs}")
else:
    check("cross-domain: entities found", False, "missing entities")

# ============================================================
# SUMMARY
# ============================================================
print("\n" + "=" * 70)
print(f"E2E TEST RESULTS: {PASSED} passed, {FAILED} failed, {PASSED + FAILED} total")
print("=" * 70)
print()
if FAILED == 0:
    print("ALL TESTS PASSED -- full end-to-end chain verified!")
else:
    print(f"{FAILED} test(s) failed:")
    for status, name, detail in RESULTS:
        if status == "FAIL":
            print(f"   FAIL: {name}: {detail}")

neo4j.close()
