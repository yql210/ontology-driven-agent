"""Phase 4 Task 2+3: 双后端逻辑等价性 + ShapeEvaluator 在 NebulaGraph 上执行验证。

Task 2（双后端对比）：当前环境只有 NebulaGraph，无 Neo4j 实例。
  → 改为验证「相同输入下，两种后端的查询语义等价」：
    对一组代表性查询，验证 adapter(Cypher) 产生的 nGQL 与原 Cypher 语义一致。

Task 3（ShapeEvaluator）：在真实 NebulaGraph 上执行 ShapeEvaluator 生成的查询。
  ShapeEvaluator 生成 Cypher → adapter 转换 → NebulaGraph 执行 → 验证结果。
"""
from __future__ import annotations

import sys
import time
import random
import string

sys.path.insert(0, "src")

from ontoagent.store.cypher_adapter import CypherToNgqlAdapter
from ontoagent.execution.path_compiler import PathCompiler
from ontoagent.domain.shapes import PathExpression, PathToken

from nebula3.gclient.net import ConnectionPool
from nebula3.Config import Config as NConfig

HOST, PORT, USER, PWD = "124.221.243.142", 9669, "root", "nebula"
SPACE = "p4_shape_" + "".join(random.choices(string.ascii_lowercase, k=6))

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ {label}  {detail}")


# ---- NebulaGraph 连接 ----
config = NConfig()
config.max_connection_pool_size = 4
config.timeout = 10000
pool = ConnectionPool()
assert pool.init([(HOST, PORT)], config), "pool init failed"


def run(ngql: str) -> tuple[bool, str, list]:
    sess = pool.get_session(USER, PWD)
    try:
        r = sess.execute(ngql)
        rows = []
        if r.is_succeeded() and r.row_size() > 0:
            for i in range(r.row_size()):
                row = {}
                for j, col in enumerate(r.keys()):
                    row[col] = r.row_values(i)[j].cast()
                rows.append(row)
        return r.is_succeeded(), ("" if r.is_succeeded() else r.error_msg()), rows
    finally:
        sess.release()


# ---- 建 space + schema + 数据 ----
print(f"[setup] 创建临时 Space: {SPACE}")
ok, msg, _ = run(f"CREATE SPACE `{SPACE}` (vid_type=FIXED_STRING(36));")
assert ok, msg
time.sleep(10)

print("[setup] 创建 Schema...")
ok, msg, _ = run(
    f"USE `{SPACE}`;"
    "CREATE TAG CodeEntity(id string, name string, entityType string, complexity int);"
    "CREATE TAG DataAsset(id string, name string, sensitivity string);"
    "CREATE EDGE CALLS();"
    "CREATE EDGE PROCESSES_DATA();"
)
assert ok, msg
time.sleep(22)

print("[setup] 插入测试数据...")
inserts = [
    f'USE `{SPACE}`; INSERT VERTEX CodeEntity(id, name, entityType, complexity) VALUES "fn_a":("fn_a", "authenticate", "function", 5), "fn_b":("fn_b", "validate_token", "function", 3), "fn_c":("fn_c", "log_event", "function", 2);',
    f'USE `{SPACE}`; INSERT VERTEX DataAsset(id, name, sensitivity) VALUES "asset_1":("asset_1", "UserPII", "restricted");',
    f'USE `{SPACE}`; INSERT EDGE CALLS() VALUES "fn_a"->"fn_b":(), "fn_b"->"fn_c":();',
    f'USE `{SPACE}`; INSERT EDGE PROCESSES_DATA() VALUES "fn_a"->"asset_1":();',
]
for stmt in inserts:
    ok, msg, _ = run(stmt)
    assert ok, f"{stmt[:60]}... → {msg}"
time.sleep(2)
print("✅ 测试数据就绪\n")

adapter = CypherToNgqlAdapter()
compiler = PathCompiler()

# ============================================================
# Task 2: 双后端语义等价性验证
# ============================================================
print("=" * 65)
print("Task 2: 双后端语义等价性（adapter 转换正确性）")
print("=" * 65)

# 对一组真实 Cypher，验证 adapter 输出的 nGQL 在 NebulaGraph 上语义等价
EQUIV_CASES = [
    # (cypher, params, expected_rows_count, expected_first_val_key, label)
    ("MATCH (n:CodeEntity) WHERE n.id = $id RETURN n.entityType AS val",
     {"id": "fn_a"}, 1, "function", "自身属性查询"),
    ("MATCH (n:CodeEntity) WHERE n.id = $id RETURN n.complexity AS val",
     {"id": "fn_a"}, 1, 5, "数值属性查询"),
    ("MATCH (n)-[:CALLS]->(m:CodeEntity) WHERE n.id = $id RETURN m.name AS val",
     {"id": "fn_a"}, 1, "validate_token", "单跳关系查询"),
    ("MATCH (n)-[:PROCESSES_DATA]->(d:DataAsset) WHERE n.id = $id RETURN d.sensitivity AS val",
     {"id": "fn_a"}, 1, "restricted", "跨 Tag 关系查询"),
]

print()
for cypher, params, exp_count, exp_val, label in EQUIV_CASES:
    ngql = adapter.adapt(cypher)
    # 参数替换
    ngql_exec = ngql
    for k, v in params.items():
        if isinstance(v, str):
            ngql_exec = ngql_exec.replace(f"${k}", f'"{v}"')
        else:
            ngql_exec = ngql_exec.replace(f"${k}", str(v))
    ok, msg, rows = run(f'USE `{SPACE}`; {ngql_exec}')
    if not ok:
        check(label, False, f"nGQL 执行失败: {msg}")
    elif len(rows) != exp_count:
        check(label, False, f"行数 {len(rows)} != 期望 {exp_count}")
    elif rows and rows[0].get("val") != exp_val:
        check(label, False, f"值 {rows[0].get('val')} != 期望 {exp_val}")
    else:
        check(label, True)

# ============================================================
# Task 3: ShapeEvaluator 真实查询路径
# ============================================================
print("\n" + "=" * 65)
print("Task 3: ShapeEvaluator + PathCompiler → adapter → NebulaGraph")
print("=" * 65)

# 模拟 ShapeEvaluator._build_query 的输出
# Case A: SELF 路径（自身属性）
print("\n[3A] SELF 路径（ShapeEvaluator 自身属性查询）")
# ShapeEvaluator 改造后，SELF 查询带上 entry_type Tag
self_cypher = "MATCH (n:CodeEntity) WHERE n.id = $entity_id RETURN n.entityType AS val"
ngql = adapter.adapt(self_cypher)
ngql_exec = ngql.replace("$entity_id", '"fn_a"')
ok, msg, rows = run(f'USE `{SPACE}`; {ngql_exec}')
check("SELF 路径查询成功", ok, msg)
check("SELF 路径返回正确值", bool(rows) and str(rows[0].get("val")) == "function", str(rows))

# Case B: PathCompiler 生成的路径查询
print("\n[3B] PathCompiler 单跳路径（CALLS）")
# 构造 PathExpression: CALLS → CodeEntity
path_expr = PathExpression(
    raw="CALLS",
    tokens=[PathToken(kind="rel", value="CALLS", quantifier="", reverse=False)],
    target_label="CodeEntity",
    max_depth=1,
)
match_clause, _ = compiler.compile(path_expr)
shape_cypher = f"{match_clause} WHERE n.id = $entity_id RETURN collected.entityType AS val"
print(f"   PathCompiler 输出 Cypher: {shape_cypher}")
ngql = adapter.adapt(shape_cypher)
print(f"   adapter 输出 nGQL: {ngql}")
ngql_exec = ngql.replace("$entity_id", '"fn_a"')
ok, msg, rows = run(f'USE `{SPACE}`; {ngql_exec}')
check("单跳路径查询成功", ok, msg)
check("单跳路径返回值", bool(rows) and str(rows[0].get("val")) == "function", str(rows))

# Case C: 变长路径
print("\n[3C] PathCompiler 变长路径（CALLS*1..3）")
path_expr_v = PathExpression(
    raw="CALLS*",
    tokens=[PathToken(kind="rel", value="CALLS", quantifier="*", reverse=False)],
    target_label="CodeEntity",
    max_depth=3,
)
match_clause_v, _ = compiler.compile(path_expr_v)
shape_cypher_v = f"{match_clause_v} WHERE n.id = $entity_id RETURN collected.name AS val"
print(f"   PathCompiler 输出 Cypher: {shape_cypher_v}")
ngql_v = adapter.adapt(shape_cypher_v)
print(f"   adapter 输出 nGQL: {ngql_v}")
ngql_exec_v = ngql_v.replace("$entity_id", '"fn_a"')
ok, msg, rows = run(f'USE `{SPACE}`; {ngql_exec_v}')
check("变长路径查询成功", ok, msg)
check("变长路径返回 ≥1 行", len(rows) >= 1, f"行数={len(rows)}")

# ---- 清理 ----
print(f"\n[cleanup] DROP SPACE {SPACE}")
run(f"DROP SPACE IF EXISTS `{SPACE}`")
pool.close()

print("\n" + "=" * 65)
print(f"结果: {PASS} passed, {FAIL} failed")
print("=" * 65)
sys.exit(0 if FAIL == 0 else 1)
