"""Phase 3 E2E: 用临时 space 验证 adapter 转换出的 nGQL 语法正确性。"""
import sys
sys.path.insert(0, "src")
from ontoagent.store.cypher_adapter import CypherToNgqlAdapter
from nebula3.gclient.net import ConnectionPool
from nebula3.Config import Config as NConfig
import time, random, string

HOST, PORT, USER, PWD = "124.221.243.142", 9669, "root", "nebula"
SPACE = "phase3_t_" + "".join(random.choices(string.ascii_lowercase, k=6))

config = NConfig()
config.max_connection_pool_size = 4
config.timeout = 10000
pool = ConnectionPool()
assert pool.init([(HOST, PORT)], config), "pool init failed"


def run(ngql: str) -> tuple[bool, str]:
    sess = pool.get_session(USER, PWD)
    try:
        r = sess.execute(ngql)
        return r.is_succeeded(), ("" if r.is_succeeded() else r.error_msg())
    finally:
        sess.release()


# 1. 创建临时 space + schema
print(f"[1/4] 创建临时 Space: {SPACE}")
ok, msg = run(f"CREATE SPACE `{SPACE}` (vid_type=FIXED_STRING(36));")
assert ok, f"create space failed: {msg}"
print("等待 space 异步生效 (~10s)...")
time.sleep(10)

print("[2/4] 创建 Schema...")
ok, msg = run(
    f"USE `{SPACE}`;"
    "CREATE TAG CodeEntity(id string, name string, entityType string);"
    "CREATE TAG ConceptEntity(id string, name string);"
    "CREATE TAG ComplianceItem(id string);"
    "CREATE EDGE CALLS();"
    "CREATE EDGE SUBJECT_TO();"
)
assert ok, f"schema failed: {msg}"
print("等待 schema 异步生效 (~22s)...")
time.sleep(22)

# 2. 插入测试数据
print("[3/4] 插入测试数据...")
inserts = [
    f'USE `{SPACE}`; INSERT VERTEX CodeEntity(id, name, entityType) VALUES "test-code-1":("test-code-1", "fn_a", "function");',
    f'USE `{SPACE}`; INSERT VERTEX CodeEntity(id, name, entityType) VALUES "test-code-2":("test-code-2", "fn_b", "function");',
    f'USE `{SPACE}`; INSERT VERTEX ConceptEntity(id, name) VALUES "test-concept-1":("test-concept-1", "Auth");',
    f'USE `{SPACE}`; INSERT VERTEX ComplianceItem(id) VALUES "test-comp-1":("test-comp-1");',
    f'USE `{SPACE}`; INSERT EDGE CALLS() VALUES "test-code-1"->"test-code-2":();',
    f'USE `{SPACE}`; INSERT EDGE SUBJECT_TO() VALUES "test-comp-1"->"test-code-1":();',
]
for stmt in inserts:
    ok, msg = run(stmt)
    assert ok, f"insert failed: {stmt[:60]} -> {msg}"
time.sleep(2)
print("✅ 测试数据就绪\n")

# 3. 测试 adapter
adapter = CypherToNgqlAdapter()
REAL_CYPHERS = [
    ("MATCH (n:CodeEntity) WHERE n.id = $id RETURN n",
     {"id": "test-code-1"}, "节点详情查询"),
    ("MATCH (caller:CodeEntity)-[r:CALLS]->(callee:CodeEntity) "
     "WHERE caller.id = $entity_id RETURN callee.id, callee.name",
     {"entity_id": "test-code-1"}, "CALLS 关系查询"),
    ("MATCH (n:CodeEntity) WHERE n.id = $id RETURN n.entityType AS val",
     {"id": "test-code-1"}, "属性查询"),
    ("MATCH (c:ComplianceItem)-[r:SUBJECT_TO]->(t) "
     "WHERE c.id = $id RETURN type(r), t.id",
     {"id": "test-comp-1"}, "SUBJECT_TO 关系"),
    ("MATCH (n:CodeEntity) WHERE NOT (n)--() RETURN count(n) AS cnt",
     {}, "孤立节点统计"),
    ("MATCH (n)-[r]-(m) WHERE id(n) = $id RETURN type(r), m",
     {"id": "test-code-1"}, "邻居查询（labels 转换）"),
]

print("=" * 65)
all_ok = True
for i, (cypher, params, label) in enumerate(REAL_CYPHERS, 1):
    try:
        ngql = adapter.adapt(cypher)
        convert_ok = True
        convert_err = ""
    except Exception as e:
        ngql = ""
        convert_ok = False
        convert_err = str(e)[:80]

    if convert_ok:
        ngql_exec = ngql
        for k, v in params.items():
            ngql_exec = ngql_exec.replace(f"${k}", f'"{v}"')
        full = f"USE `{SPACE}`; {ngql_exec}"
        try:
            ok, msg = run(full)
        except Exception as e:
            ok, msg = False, str(e)[:100]
    else:
        ok, msg = False, convert_err

    status = "✅" if ok else "❌"
    print(f"{status} [{i}/{len(REAL_CYPHERS)}] {label}")
    print(f"   Cypher: {cypher[:65]}")
    print(f"   nGQL:   {ngql[:65]}")
    if not ok:
        print(f"   ⚠️  {msg[:100]}")
        all_ok = False
    print()

print("=" * 65)
if all_ok:
    print("🎉 Phase 3 真实 NebulaGraph E2E 全通过！")
else:
    print("⚠️  存在失败")

print(f"\n[4/4] 清理 Space: {SPACE}")
run(f"DROP SPACE IF EXISTS `{SPACE}`")
pool.close()
sys.exit(0 if all_ok else 1)
