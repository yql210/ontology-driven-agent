"""Phase 4 Task 5: 端到端 build 验证。

用 NebulaGraphStore 真实执行 OntoAgentBuilder 的核心写入流程：
1. 创建 mini Python repo（2 个 .py 文件）
2. 初始化 NebulaGraph Space + Schema
3. 用 PythonParser 解析 → NebulaGraphStore 写入
4. 验证：节点数、关系数、查询结果
"""
from __future__ import annotations

import sys
import os
import time
import uuid
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

from ontoagent.store.nebula_store import NebulaGraphStore
from ontoagent.store.nebula_schema import NebulaSchemaInitializer
from ontoagent.parsing.parser.python_parser import PythonParser
from nebula3.gclient.net import ConnectionPool
from nebula3.Config import Config as NConfig

HOST, PORT, USER, PWD = "124.221.243.142", 9669, "root", "nebula"
SPACE = "p4_e2e_" + uuid.uuid4().hex[:8]

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ {label}  {detail}")


# ============================================================
# 1. 创建 mini repo
# ============================================================
print("=" * 65)
print("Phase 4 Task 5: 端到端 build 验证")
print("=" * 65)

MINI_REPO = '''
def authenticate(user, password):
    """验证用户登录"""
    token = validate_token(user)
    log_event("login", user)
    return token

def validate_token(user):
    """校验 token 有效性"""
    return f"token_{user}"

def log_event(event, user):
    """记录事件日志"""
    pass

class AuthService:
    """认证服务"""
    def login(self, user, password):
        return authenticate(user, password)
'''

print("\n[1/5] 创建 mini Python repo...")
with tempfile.TemporaryDirectory() as tmp:
    repo_path = Path(tmp)
    (repo_path / "auth.py").write_text(MINI_REPO)
    print(f"  repo: {repo_path}")
    print(f"  文件: auth.py (3 函数 + 1 类)")

    # ============================================================
    # 2. 初始化 NebulaGraph Space + Schema
    # ============================================================
    print(f"\n[2/5] 初始化 NebulaGraph Space: {SPACE}")
    config = NConfig()
    config.max_connection_pool_size = 10
    config.timeout = 30000
    pool = ConnectionPool()
    assert pool.init([(HOST, PORT)], config), "pool init failed"

    def raw_run(ngql: str) -> tuple[bool, str]:
        sess = pool.get_session(USER, PWD)
        try:
            r = sess.execute(ngql)
            return r.is_succeeded(), ("" if r.is_succeeded() else r.error_msg())
        finally:
            sess.release()

    ok, msg = raw_run(f'DROP SPACE IF EXISTS `{SPACE}`;')
    ok, msg = raw_run(f"CREATE SPACE `{SPACE}` (vid_type=FIXED_STRING(36));")
    assert ok, f"create space: {msg}"
    time.sleep(10)

    print("  初始化 Schema...")
    store = NebulaGraphStore(host=HOST, port=PORT, user=USER, password=PWD, space=SPACE)
    # 直接用 NebulaSchemaInitializer（需要 raw session）
    raw_sess = pool.get_session(USER, PWD)
    try:
        from ontoagent.store.nebula_schema import NebulaSchemaInitializer
        init = NebulaSchemaInitializer(raw_sess, space_name=SPACE)
        init.ensure_space()  # 确保 space 存在（已建则跳过）
        # 在 space 内建 tags + edges
        raw_sess.execute(f"USE `{SPACE}`;")
        for ddl in init.create_tags():
            raw_sess.execute(ddl)
        for ddl in init.create_edges():
            raw_sess.execute(ddl)
        for ddl in init.create_indexes():
            try:
                raw_sess.execute(ddl)
            except Exception:
                pass  # 索引可能已存在
        print("  ✅ Schema 创建完成")
    except Exception as e:
        print(f"  ⚠️ Schema initializer fallback: {e}")
        core_schema = (
            f"USE `{SPACE}`;"
            "CREATE TAG IF NOT EXISTS CodeEntity(id string, name string, entityType string, "
            "qualifiedName string, filePath string, signature string, docstring string, "
            "complexity int, extracted_at string);"
            "CREATE EDGE IF NOT EXISTS CALLS(extracted_at string);"
            "CREATE EDGE IF NOT EXISTS CONTAINS(extracted_at string);"
            "CREATE EDGE IF NOT EXISTS IMPORTS(extracted_at string);"
        )
        ok, msg = raw_run(core_schema)
        assert ok, f"core schema: {msg}"
    finally:
        raw_sess.release()

    print("  等待 schema 异步生效 (~25s)...")
    time.sleep(25)

    # ============================================================
    # 3. 解析 + 写入
    # ============================================================
    print("\n[3/5] 解析 mini repo + 写入 NebulaGraph...")
    parser = PythonParser()
    result = parser.parse_file(repo_path / "auth.py")
    entities = result.entities
    raw_relations = result.relations
    print(f"  解析结果: {len(entities)} 实体, {len(raw_relations)} 原始关系")
    for e in entities:
        print(f"    - {e.entity_type}: {e.name} ({e.id})")

    # 名称 → UUID 映射
    name_to_id = {e.name: e.id for e in entities}

    # 构造 schema.Relation（UUID 引用）
    from ontoagent.domain.schema import Relation
    relations = []
    for raw_r in raw_relations:
        src_id = name_to_id.get(raw_r.source_name)
        tgt_id = name_to_id.get(raw_r.target_name)
        if src_id and tgt_id:
            relations.append(Relation(
                source_id=src_id, target_id=tgt_id, relation_type=raw_r.relation_type,
            ))
    print(f"  有效关系（UUID 解析后）: {len(relations)}")

    # 写节点
    from ontoagent.pipeline.builder_utils import entity_to_dict
    from ontoagent.domain.provenance import add_provenance
    batch_time = "2026-07-26T00:00:00Z"

    node_count = 0
    for entity in entities:
        props = add_provenance(entity_to_dict(entity), extracted_at=batch_time)
        try:
            store.merge_node("CodeEntity", props)
            node_count += 1
        except Exception as e:
            print(f"  ⚠️ merge_node {entity.name} failed: {e}")
    print(f"  写入节点: {node_count}/{len(entities)}")

    # 写关系
    rel_count = 0
    for rel in relations:
        props = add_provenance({}, extracted_at=batch_time)
        try:
            store.merge_relation(
                rel.source_id, rel.target_id, rel.relation_type.upper(), props,
                source_label="CodeEntity", target_label="CodeEntity",
            )
            rel_count += 1
        except Exception as e:
            print(f"  ⚠️ merge_relation {rel.relation_type} failed: {e}")
    print(f"  写入关系: {rel_count}/{len(relations)}")

    time.sleep(2)

    # ============================================================
    # 4. 验证查询结果
    # ============================================================
    print("\n[4/5] 验证 NebulaGraph 中的数据...")

    # 节点总数
    sess = pool.get_session(USER, PWD)
    sess.execute(f"USE `{SPACE}`;")

    def query(ngql: str) -> list:
        r = sess.execute(ngql)
        if not r.is_succeeded():
            print(f"  query error: {r.error_msg()}")
            return []
        rows = []
        for i in range(r.row_size()):
            row = {}
            for j, col in enumerate(r.keys()):
                row[col] = r.row_values(i)[j].cast() if r.row_values(i)[j] else None
            rows.append(row)
        return rows

    # 节点数
    rows = query("MATCH (n:CodeEntity) RETURN count(n) AS cnt;")
    check("节点数 > 0", bool(rows) and int(rows[0].get("cnt", 0)) > 0, str(rows))

    # 查 authenticate 函数
    rows = query(
        'MATCH (n:CodeEntity) WHERE n.CodeEntity.name == "authenticate" '
        'RETURN n.CodeEntity.entityType AS type, n.CodeEntity.name AS name;'
    )
    check("authenticate 函数存在", bool(rows), str(rows))
    if rows:
        check("authenticate entityType=function",
              str(rows[0].get("type", "")) == "function", str(rows))

    # CALLS 关系
    rows = query(
        'MATCH (a:CodeEntity)-[:CALLS]->(b:CodeEntity) '
        'WHERE a.CodeEntity.name == "authenticate" '
        'RETURN b.CodeEntity.name AS callee;'
    )
    callee_names = [str(r.get("callee", "")) for r in rows]
    check("authenticate CALLS validate_token", "validate_token" in callee_names, str(callee_names))
    check("authenticate CALLS log_event", "log_event" in callee_names, str(callee_names))

    sess.release()

    # ============================================================
    # 5. 清理
    # ============================================================
    print(f"\n[5/5] 清理 Space: {SPACE}")
    store.close()

raw_run(f"DROP SPACE IF EXISTS `{SPACE}`;")
pool.close()

print(f"\n{'='*65}")
print(f"结果: {PASS} passed, {FAIL} failed")
print(f"{'='*65}")
sys.exit(0 if FAIL == 0 else 1)
