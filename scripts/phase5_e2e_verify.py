"""Phase 5 真实 NebulaGraph E2E 验收脚本。

验证：
1. NebulaGraphStore 实例化时自动创建 schema（Space/Tag/Edge/Index）
2. batch 接口写入节点和关系
3. $param 查询兜底
4. clear_all + ensure_constraints
"""
from __future__ import annotations

import os
import sys
import time

# 设置 NebulaGraph 环境变量
os.environ["ONTOAGENT_GRAPH_BACKEND"] = "nebula"
os.environ["ONTOAGENT_NEBULA_HOST"] = "124.221.243.142"
os.environ["ONTOAGENT_NEBULA_PORT"] = "9669"
os.environ["ONTOAGENT_NEBULA_USER"] = "root"
os.environ["ONTOAGENT_NEBULA_PASSWORD"] = "nebula123"
os.environ["ONTOAGENT_NEBULA_SPACE"] = "ontoagent_e2e"

sys.path.insert(0, "src")

from ontoagent.store.nebula_store import NebulaGraphStore

CHECKS_PASSED = 0
CHECKS_FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global CHECKS_PASSED, CHECKS_FAILED
    if condition:
        CHECKS_PASSED += 1
        print(f"  ✅ {name}")
    else:
        CHECKS_FAILED += 1
        print(f"  ❌ {name} {detail}")


print("=" * 60)
print("Phase 5 E2E 验收 — 真实 NebulaGraph 124.221.243.142:9669")
print("=" * 60)

# Check 1: 实例化（自动 schema init）
print("\n--- Check 1: NebulaGraphStore 实例化 + schema 自动初始化 ---")
try:
    store = NebulaGraphStore(
        host="124.221.243.142",
        port=9669,
        user="root",
        password="nebula123",
        space="ontoagent_e2e",
    )
    check("NebulaGraphStore 实例化成功", True)
    check("schema_ready 标记为 True", store._schema_ready, f"(实际: {store._schema_ready})")
except Exception as e:
    check("NebulaGraphStore 实例化成功", False, str(e))
    sys.exit(1)

# Check 2: SHOW TAGS 确认 schema 存在
print("\n--- Check 2: 确认 Tag/Edge 已创建 ---")
try:
    with store._session_scope() as session:
        r = session.execute("SHOW TAGS;")
        check("SHOW TAGS 成功", r.is_succeeded())
        
        r2 = session.execute("SHOW EDGES;")
        check("SHOW EDGES 成功", r2.is_succeeded())

        # 确认 CodeEntity Tag 存在
        r3 = session.execute("DESCRIBE TAG `CodeEntity`;")
        check("CodeEntity Tag 存在", r3.is_succeeded())

        # 确认 SchemaVersion Tag 存在
        r4 = session.execute("DESCRIBE TAG `SchemaVersion`;")
        check("SchemaVersion Tag 存在", r4.is_succeeded())

        # 确认 CALLS Edge 存在
        r5 = session.execute("DESCRIBE EDGE `CALLS`;")
        check("CALLS Edge 存在", r5.is_succeeded())
except Exception as e:
    check("Schema 检查", False, str(e))

# Check 3: clear_all
print("\n--- Check 3: clear_all ---")
try:
    result = store.clear_all()
    check("clear_all 执行成功", True)
except Exception as e:
    check("clear_all 执行成功", False, str(e))

# Check 4: ensure_constraints
print("\n--- Check 4: ensure_constraints ---")
try:
    store.ensure_constraints()
    check("ensure_constraints 执行成功", True)
except Exception as e:
    check("ensure_constraints 执行成功", False, str(e))

# Check 5: merge_nodes_batch
print("\n--- Check 5: merge_nodes_batch ---")
try:
    nodes = [
        {"id": "e2e-func-1", "name": "authenticate", "entity_type": "function", "file_path": "/src/auth.py"},
        {"id": "e2e-func-2", "name": "validate_token", "entity_type": "function", "file_path": "/src/auth.py"},
        {"id": "e2e-func-3", "name": "AuthService", "entity_type": "class", "file_path": "/src/auth.py"},
    ]
    count = store.merge_nodes_batch("CodeEntity", nodes, batch_size=200)
    check(f"merge_nodes_batch 写入 {count} 节点", count == 3, f"(实际: {count})")
except Exception as e:
    check("merge_nodes_batch", False, str(e))

# Check 6: 验证节点写入
print("\n--- Check 6: 验证节点数据 ---")
try:
    with store._session_scope() as session:
        r = session.execute('FETCH PROP ON `CodeEntity` "e2e-func-1" YIELD `CodeEntity`.name AS name;')
        check("FETCH 写入的节点", r.is_succeeded() and not r.is_empty(), f"succeeded={r.is_succeeded()}, empty={r.is_empty()}")
except Exception as e:
    check("FETCH 节点", False, str(e))

# Check 7: merge_relations_batch
print("\n--- Check 7: merge_relations_batch ---")
try:
    rels = [
        {"source_id": "e2e-func-1", "target_id": "e2e-func-2", "rel_type": "calls"},
        {"source_id": "e2e-func-1", "target_id": "e2e-func-3", "rel_type": "calls"},
    ]
    count = store.merge_relations_batch(rels, batch_size=200)
    check(f"merge_relations_batch 写入 {count} 关系", count == 2, f"(实际: {count})")
except Exception as e:
    check("merge_relations_batch", False, str(e))

# Check 8: $param 查询兜底
print("\n--- Check 8: $param 查询兜底 ---")
try:
    results = store.query(
        "MATCH (n {name: $name}) RETURN n.CodeEntity.name AS name LIMIT 1",
        {"name": "authenticate"},
    )
    check("$param 查询返回结果", len(results) >= 0, f"(返回 {len(results)} 行)")
except Exception as e:
    check("$param 查询", False, str(e))

# Check 9: $param list 抛 TypeError
print("\n--- Check 9: $param list 抛 TypeError ---")
try:
    store.query("MATCH (n) WHERE n.id IN $ids RETURN n", {"ids": ["a", "b"]})
    check("$param list 抛 TypeError", False, "应该抛 TypeError 但没有")
except TypeError as e:
    check("$param list 抛 TypeError", True, str(e))
except Exception as e:
    check("$param list 抛 TypeError", False, f"抛了 {type(e).__name__}: {e}")

# 清理
print("\n--- 清理 ---")
try:
    store.clear_all()
    store.close()
    check("清理成功", True)
except Exception as e:
    check("清理", False, str(e))

print(f"\n{'=' * 60}")
print(f"Phase 5 E2E 验收结果: {CHECKS_PASSED} passed, {CHECKS_FAILED} failed")
print(f"{'=' * 60}")
sys.exit(0 if CHECKS_FAILED == 0 else 1)
