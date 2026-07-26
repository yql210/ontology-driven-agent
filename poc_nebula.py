"""POC: NebulaGraph Schema 初始化 + 关键查询验证

从 OntoAgent schema.py 自动生成 NebulaGraph DDL，写入测试数据，验证关键查询。
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from nebula3.gclient.net import ConnectionPool
from nebula3.Config import Config

from ontoagent.domain.schema import VALID_ENTITY_LABELS, RELATION_TYPE_TO_NEO4J

HOST = "124.221.243.142"
PORT = 9669
USER = "root"
PASSWORD = "nebula"
SPACE = "ontoagent_poc"

# ============================================================
# Step 1: 连接
# ============================================================
def connect():
    config = Config()
    config.max_connection_pool_size = 5
    pool = ConnectionPool()
    assert pool.init([(HOST, PORT)], config), "连接失败"
    session = pool.get_session(USER, PASSWORD)
    session.execute(f"USE {SPACE}")  # 先切换到默认 space
    return pool, session


# ============================================================
# Step 2: 创建 Schema
# ============================================================
def create_schema(session):
    """创建 Space + Tag + Edge + 索引"""

    # 2.1 创建/重建 Space（VID 用 FIXED_STRING(36) 匹配 UUID）
    session.execute(f"DROP SPACE IF EXISTS {SPACE}")
    r = session.execute(
        f"CREATE SPACE {SPACE} (vid_type=FIXED_STRING(36)) "
        f"COMMENT='OntoAgent POC test space'"
    )
    print(f"[Space] {'✅' if r.is_succeeded() else '❌ ' + r.error_msg()}")
    if not r.is_succeeded():
        return False

    # NebulaGraph DDL 是异步的，Space 创建后需要等待其分布到所有节点
    import time
    print("[Space] 等待 Space 分布完成...")
    time.sleep(20)
    session.execute("SUBMIT JOB")

    # 切换到新 Space（新建 session 确保 meta 已更新）
    time.sleep(3)
    r = session.execute(f"USE {SPACE}")
    if not r.is_succeeded():
        print(f"[Space] 首次 USE 失败，再等 10s...")
        time.sleep(10)
        r = session.execute(f"USE {SPACE}")
    assert r.is_succeeded(), f"USE SPACE failed: {r.error_msg()}"

    # 2.2 创建 13 个 Tag
    # POC 阶段：所有属性用 string，验证基本兼容性
    # 完整实现时会从 dataclass 反射属性名和类型
    tag_fields = {
        "CodeEntity": "name string, filePath string, entityType string, language string, lines string, docstring string",
        "ConceptEntity": "name string, description string, category string",
        "DocEntity": "name string, filePath string, docType string",
        "ResourceEntity": "name string, resourceType string, description string",
        "ModuleEntity": "name string, description string, moduleType string",
        "ChangeSetEntity": "name string, commitHash string, author string, `timestamp` string",
        "LogEntity": "name string, level string, message string, `timestamp` string",
        "AlertEntity": "name string, severity string, message string, `timestamp` string",
        "ServiceEntity": "name string, description string, endpoint string",
        "DataAsset": "name string, description string, classification string",
        "ComplianceItem": "name string, regulation string, requirement string",
        "CapabilityEntity": "name string, description string",
        "ProcessEntity": "name string, description string",
    }

    for label in VALID_ENTITY_LABELS:
        fields = tag_fields.get(label, "name string")
        r = session.execute(f"CREATE TAG IF NOT EXISTS {label} ({fields})")
        status = "✅" if r.is_succeeded() else "❌ " + r.error_msg()
        print(f"[Tag] {label}: {status}")
        if not r.is_succeeded():
            return False

    # 2.3 创建 26 个 Edge type
    for rel_type_upper in RELATION_TYPE_TO_NEO4J.values():
        r = session.execute(f"CREATE EDGE IF NOT EXISTS {rel_type_upper}()")
        status = "✅" if r.is_succeeded() else "❌ " + r.error_msg()
        print(f"[Edge] {rel_type_upper}: {status}")
        if not r.is_succeeded():
            return False

    # 2.4 创建索引（NebulaGraph 需要先建索引才能 MATCH 查询属性）
    # 对每个 Tag 的 id 属性建索引（OntoAgent 用 UUID 作为 id）
    for label in VALID_ENTITY_LABELS:
        r = session.execute(
            f'CREATE TAG INDEX IF NOT EXISTS idx_{label}_id ON {label}(name(64))'
        )
        if not r.is_succeeded():
            print(f"[Index] {label}: ❌ " + r.error_msg())

    print("[Index] 索引创建完成")

    # 2.5 提交 Schema 变更（NebulaGraph DDL 是异步的，需要等待）
    print("[Schema] 等待 DDL 生效...")
    import time
    time.sleep(10)
    r = session.execute("SUBMIT JOB")
    print(f"[Schema] SUBMIT JOB: {'✅' if r.is_succeeded() else '⚠️ ' + r.error_msg()}")
    time.sleep(3)

    return True


# ============================================================
# Step 3: 写入测试数据
# ============================================================
def insert_test_data(session):
    """写入 CodeEntity 节点 + CALLS 关系，模拟函数调用链"""

    # 3.1 插入节点（模拟一个 3 层调用链: a -> b -> c -> d）
    # VID 用标准 36 字符 UUID
    nodes = [
        # CodeEntity: function 类型
        ("a0000001-0000-0000-0000-000000000001", "CodeEntity", {
            "name": "process_order",
            "filePath": "/src/orders/service.py",
            "entityType": "function",
            "language": "python",
            "lines": "45",
            "docstring": "Process a customer order"
        }),
        ("a0000001-0000-0000-0000-000000000002", "CodeEntity", {
            "name": "validate_input",
            "filePath": "/src/utils/validators.py",
            "entityType": "function",
            "language": "python",
            "lines": "20",
            "docstring": "Validate user input"
        }),
        ("a0000001-0000-0000-0000-000000000003", "CodeEntity", {
            "name": "save_to_db",
            "filePath": "/src/db/repository.py",
            "entityType": "function",
            "language": "python",
            "lines": "15",
            "docstring": "Persist data to database"
        }),
        ("a0000001-0000-0000-0000-000000000004", "CodeEntity", {
            "name": "send_notification",
            "filePath": "/src/notifications/sender.py",
            "entityType": "function",
            "language": "python",
            "lines": "30",
            "docstring": "Send email notification"
        }),
        # ConceptEntity
        ("c0000001-0000-0000-0000-000000000001", "ConceptEntity", {
            "name": "订单处理",
            "description": "Order processing business concept",
            "category": "business_concept"
        }),
        # DataAsset
        ("d0000001-0000-0000-0000-000000000001", "DataAsset", {
            "name": "客户订单数据",
            "description": "Customer order records",
            "classification": "confidential"
        }),
        # ComplianceItem
        ("e0000001-0000-0000-0000-000000000001", "ComplianceItem", {
            "name": "GDPR-第17条",
            "regulation": "GDPR",
            "requirement": "Right to erasure"
        }),
    ]

    for vid, tag, props in nodes:
        prop_str = ', '.join(f'{k}: "{v}"' for k, v in props.items())
        ngql = f'INSERT VERTEX {tag}({", ".join(props.keys())}) VALUES "{vid}":({", ".join(f'"{v}"' for v in props.values())})'
        r = session.execute(ngql)
        if not r.is_succeeded():
            print(f"[Insert] {tag}/{props['name']}: ❌ " + r.error_msg())
            print(f"  nGQL: {ngql}")
            return False
        print(f"[Insert] {tag}/{props['name']}: ✅")

    # 3.2 插入关系（调用链: process_order -> validate_input -> save_to_db, process_order -> send_notification）
    edges = [
        ("a0000001-0000-0000-0000-000000000001", "a0000001-0000-0000-0000-000000000002", "CALLS"),
        ("a0000001-0000-0000-0000-000000000002", "a0000001-0000-0000-0000-000000000003", "CALLS"),
        ("a0000001-0000-0000-0000-000000000003", "a0000001-0000-0000-0000-000000000004", "CALLS"),
        # 业务关系
        ("a0000001-0000-0000-0000-000000000001", "c0000001-0000-0000-0000-000000000001", "DESCRIBES"),
        ("a0000001-0000-0000-0000-000000000001", "d0000001-0000-0000-0000-000000000001", "PROCESSES_DATA"),
        ("d0000001-0000-0000-0000-000000000001", "e0000001-0000-0000-0000-000000000001", "GOVERNED_BY"),
    ]

    for src, dst, edge_type in edges:
        ngql = f'INSERT EDGE {edge_type}() VALUES "{src}"->"{dst}":()'
        r = session.execute(ngql)
        if not r.is_succeeded():
            print(f"[Edge] {edge_type}: ❌ " + r.error_msg())
            return False
        print(f"[Edge] {edge_type} {src[:8]}...→{dst[:8]}...: ✅")

    import time
    time.sleep(2)  # 等待数据可见
    return True


# ============================================================
# Step 4: 验证关键查询
# ============================================================
def verify_queries(session):
    """验证 5 类关键 nGQL 查询"""

    print("\n" + "=" * 60)
    print("关键查询验证")
    print("=" * 60)

    # ---- 查询 1: 基本节点查找（属性访问需 tag 前缀）----
    print("\n--- Q1: 基本节点查找（属性访问 tag 前缀）---")
    ngql = (
        'MATCH (n:CodeEntity) '
        'WHERE n.CodeEntity.name == "process_order" '
        'RETURN n.CodeEntity.name AS name, n.CodeEntity.filePath AS path'
    )
    r = session.execute(ngql)
    print(f"  nGQL: {ngql}")
    print(f"  结果: {'✅' if r.is_succeeded() else '❌ ' + r.error_msg()}")
    if r.is_succeeded() and r.row_size() > 0:
        for col in r.keys():
            print(f"    {col}: {r.row_values(0)[r.keys().index(col)]}")

    # ---- 查询 2: 变长路径遍历（ShapeEvaluator 核心模式）----
    print("\n--- Q2: 变长路径遍历 *1..3（ShapeEvaluator 模式）---")
    ngql = (
        'MATCH (n)-[:CALLS*1..3]->(callee) '
        'WHERE id(n) == "a0000001-0000-0000-0000-000000000001" '
        'RETURN callee.CodeEntity.name AS name'
    )
    r = session.execute(ngql)
    print(f"  nGQL: {ngql}")
    print(f"  结果: {'✅' if r.is_succeeded() else '❌ ' + r.error_msg()}")
    if r.is_succeeded():
        print(f"  命中 {r.row_size()} 个被调用函数:")
        for i in range(r.row_size()):
            row = r.row_values(i)
            print(f"    - {row}")

    # ---- 查询 3: startNode/endNode 重写（graph.py 模式）----
    print("\n--- Q3: 边查询（startNode/endNode 重写）---")
    # Neo4j: MATCH (a)-[r]->(b) RETURN startNode(r).id, endNode(r).id
    # NebulaGraph: 直接用 pattern 中的节点变量
    ngql = (
        'MATCH (a:CodeEntity)-[r:CALLS]->(b:CodeEntity) '
        'RETURN a.CodeEntity.name AS caller, b.CodeEntity.name AS callee, type(r) AS rel'
    )
    r = session.execute(ngql)
    print(f"  nGQL: {ngql}")
    print(f"  结果: {'✅' if r.is_succeeded() else '❌ ' + r.error_msg()}")
    if r.is_succeeded():
        print(f"  命中 {r.row_size()} 条边:")
        for i in range(r.row_size()):
            row = r.row_values(i)
            print(f"    - {row}")

    # ---- 查询 4: 多跳业务追溯（trace_business_impact 模式）----
    print("\n--- Q4: 多跳业务追溯（Code→Concept→DataAsset→Compliance）---")
    ngql = (
        'MATCH (c:CodeEntity)-[:DESCRIBES]->(concept:ConceptEntity) '
        'MATCH (c)-[:PROCESSES_DATA]->(data:DataAsset)-[:GOVERNED_BY]->(ci:ComplianceItem) '
        'WHERE c.CodeEntity.name == "process_order" '
        'RETURN c.CodeEntity.name AS code, concept.ConceptEntity.name AS concept, '
        'data.DataAsset.name AS data_asset, ci.ComplianceItem.name AS compliance'
    )
    r = session.execute(ngql)
    print(f"  nGQL: {ngql}")
    print(f"  结果: {'✅' if r.is_succeeded() else '❌ ' + r.error_msg()}")
    if r.is_succeeded():
        print(f"  命中 {r.row_size()} 条追溯:")
        for i in range(r.row_size()):
            row = r.row_values(i)
            print(f"    - {row}")

    # ---- 查询 5: tags() 替代 labels()（统计聚合）----
    print("\n--- Q5: 统计聚合（tags() 替代 labels()）---")
    ngql = (
        'MATCH (n) '
        'RETURN tags(n) AS labels, count(*) AS cnt'
    )
    r = session.execute(ngql)
    print(f"  nGQL: {ngql}")
    print(f"  结果: {'✅' if r.is_succeeded() else '❌ ' + r.error_msg()}")
    if r.is_succeeded():
        print(f"  节点统计:")
        for i in range(r.row_size()):
            row = r.row_values(i)
            print(f"    - {row}")

    # ---- 查询 6: UPSERT（替代 MERGE）----
    print("\n--- Q6: UPSERT VERTEX（替代 MERGE）---")
    ngql = (
        'UPSERT VERTEX ON CodeEntity "a0000001-0000-0000-0000-000000000001" '
        'SET lines = "50" '
        'WHEN lines == "45"'
    )
    r = session.execute(ngql)
    print(f"  nGQL: {ngql}")
    print(f"  结果: {'✅' if r.is_succeeded() else '❌ ' + r.error_msg()}")

    # 验证更新
    ngql_check = (
        'MATCH (n:CodeEntity) '
        'WHERE n.CodeEntity.name == "process_order" '
        'RETURN n.CodeEntity.lines AS lines'
    )
    r2 = session.execute(ngql_check)
    if r2.is_succeeded() and r2.row_size() > 0:
        print(f"  验证: lines 已更新为 {r2.row_values(0)}")

    # ---- 查询 7: 变长路径 + path 变量（graph.py 可视化模式）----
    print("\n--- Q7: 变长路径 + path 变量（可视化邻居展开）---")
    ngql = (
        'MATCH p = (n)-[*1..2]-(neighbor) '
        'WHERE id(n) == "a0000001-0000-0000-0000-000000000001" '
        'RETURN nodes(p) AS path_nodes, length(p) AS depth '
        'LIMIT 5'
    )
    r = session.execute(ngql)
    print(f"  nGQL: {ngql}")
    print(f"  结果: {'✅' if r.is_succeeded() else '❌ ' + r.error_msg()}")
    if r.is_succeeded():
        print(f"  命中 {r.row_size()} 条路径:")
        for i in range(min(r.row_size(), 3)):
            row = r.row_values(i)
            print(f"    - {row}")


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("NebulaGraph POC: Schema + 查询验证")
    print(f"Server: {HOST}:{PORT}")
    print("=" * 60)

    pool, session = connect()

    try:
        # Step 1: 创建 Schema
        print("\n[Step 1] 创建 Schema...")
        if not create_schema(session):
            print("❌ Schema 创建失败")
            sys.exit(1)

        # Step 2: 写入测试数据
        print("\n[Step 2] 写入测试数据...")
        if not insert_test_data(session):
            print("❌ 数据写入失败")
            sys.exit(1)

        # Step 3: 验证查询
        print("\n[Step 3] 验证关键查询...")
        verify_queries(session)

        print("\n" + "=" * 60)
        print("✅ POC 验证完成")
        print("=" * 60)

    finally:
        session.release()
        pool.close()
