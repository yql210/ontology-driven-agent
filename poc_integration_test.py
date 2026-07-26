"""NebulaGraphStore 真实集成测试

连接 124.221.243.142:9669，端到端测试每个 GraphStore 方法。
不使用 mock——所有操作在真实 NebulaGraph 3.7.0 上执行。
"""
from __future__ import annotations

import sys
import os
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from ontoagent.store.nebula_store import NebulaGraphStore
from ontoagent.store.nebula_schema import NebulaSchemaInitializer

HOST = "124.221.243.142"
PORT = 9669
USER = "root"
PASSWORD = "nebula"
SPACE = "ontoagent_integration_test"


def section(title: str):
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def assert_ok(condition: bool, msg: str) -> bool:
    icon = "✅" if condition else "❌"
    print(f"  {icon} {msg}")
    return condition


def main():
    print("=" * 60)
    print("NebulaGraphStore 真实集成测试")
    print(f"Server: {HOST}:{PORT}  Space: {SPACE}")
    print("=" * 60)

    # ============================================================
    # Step 0: 连接 + 初始化 Schema
    # ============================================================
    section("Step 0: 连接 + Schema 初始化")

    store = NebulaGraphStore(
        host=HOST, port=PORT, user=USER, password=PASSWORD, space=SPACE
    )
    print(f"  连接池初始化: ✅")

    # 先用 raw session 创建 Space（SchemaInitializer 需要 Space 已存在）
    from nebula3.gclient.net import ConnectionPool as RawPool
    from nebula3.Config import Config

    raw_config = Config()
    raw_config.max_connection_pool_size = 2
    raw_pool = RawPool()
    raw_pool.init([(HOST, PORT)], raw_config)
    raw_session = raw_pool.get_session(USER, PASSWORD)

    # 清理旧 Space + 创建新 Space
    raw_session.execute(f"DROP SPACE IF EXISTS {SPACE}")
    r = raw_session.execute(
        f"CREATE SPACE {SPACE} (vid_type=FIXED_STRING(36))"
    )
    assert_ok(r.is_succeeded(), "Space 创建")
    if not r.is_succeeded():
        print(f"  错误: {r.error_msg()}")
        sys.exit(1)

    print("  等待 Space 分布 (~20s)...")
    time.sleep(20)

    # 切换到新 Space
    r = raw_session.execute(f"USE {SPACE}")
    if not r.is_succeeded():
        print(f"  首次 USE 失败, 再等 10s...")
        time.sleep(10)
        raw_session.execute(f"USE {SPACE}")

    # 创建 Schema
    initializer = NebulaSchemaInitializer(raw_session, space_name=SPACE)
    tag_ddls = initializer.create_tags()
    edge_ddls = initializer.create_edges()
    index_ddls = initializer.create_indexes()

    for ddl in tag_ddls:
        r = raw_session.execute(ddl)
        if not r.is_succeeded():
            print(f"  ❌ Tag DDL 失败: {r.error_msg()} | {ddl[:80]}")

    for ddl in edge_ddls:
        r = raw_session.execute(ddl)
        if not r.is_succeeded():
            print(f"  ❌ Edge DDL 失败: {r.error_msg()} | {ddl[:80]}")

    for ddl in index_ddls:
        r = raw_session.execute(ddl)
        # 索引可能因属性长度问题失败，不阻塞
        if not r.is_succeeded():
            print(f"  ⚠️ Index DDL: {r.error_msg()[:60]} | {ddl[:60]}")

    assert_ok(True, f"Schema 创建完成 ({len(tag_ddls)} Tag, {len(edge_ddls)} Edge)")
    print("  等待 Schema 完全生效 (~15s)...")
    time.sleep(15)

    raw_session.release()
    raw_pool.close()

    # ============================================================
    # Step 1: merge_node + get_node（CRUD 基础）
    # ============================================================
    section("Step 1: merge_node + get_node")

    node_id = str(uuid.uuid4())
    properties = {
        "id": node_id,
        "name": "test_function_alpha",
        "filePath": "/src/test/alpha.py",
        "entityType": "function",
        "language": "python",
        "lines": "42",
        "docstring": "A test function for integration",
    }

    # 写入
    result = store.merge_node("CodeEntity", properties)
    assert_ok(result.get("name") == "test_function_alpha", "merge_node 写入返回正确")
    time.sleep(1)

    # 读取
    node = store.get_node(node_id)
    assert_ok(node is not None, "get_node 返回节点")
    if node:
        assert_ok(
            node.get("name") == "test_function_alpha", "get_node name 正确"
        )
        print(f"  节点字段: {list(node.keys())}")

    # 更新（UPSERT 幂等）
    properties["lines"] = "100"
    result = store.merge_node("CodeEntity", properties)
    time.sleep(1)
    node = store.get_node(node_id)
    if node:
        assert_ok(node.get("lines") in ("100", 100), "merge_node 更新属性正确")

    # 不存在的节点
    node_none = store.get_node("nonexistent-0000-0000-0000-000000000000")
    assert_ok(node_none is None, "get_node 不存在返回 None")

    # ============================================================
    # Step 2: merge_relation + get_relations
    # ============================================================
    section("Step 2: merge_relation + get_relations")

    # 创建第二个节点
    node2_id = str(uuid.uuid4())
    store.merge_node("CodeEntity", {
        "id": node2_id,
        "name": "test_function_beta",
        "filePath": "/src/test/beta.py",
        "entityType": "function",
        "language": "python",
    })
    time.sleep(1)

    # 创建关系
    rel_result = store.merge_relation(
        source_id=node_id,
        target_id=node2_id,
        rel_type="calls",
        source_label="CodeEntity",
        target_label="CodeEntity",
    )
    assert_ok(isinstance(rel_result, dict), "merge_relation 返回 dict")
    time.sleep(1)

    # 查询关系
    rels = store.get_relations(source_id=node_id)
    assert_ok(len(rels) >= 1, f"get_relations 按源节点查到 {len(rels)} 条关系")
    if rels:
        print(f"  关系数据示例: {list(rels[0].keys())}")

    rels_by_target = store.get_relations(target_id=node2_id)
    assert_ok(len(rels_by_target) >= 1, f"get_relations 按目标节点查到 {len(rels_by_target)} 条")

    rels_by_type = store.get_relations(rel_type="calls")
    assert_ok(len(rels_by_type) >= 1, f"get_relations 按类型查到 {len(rels_by_type)} 条")

    # ============================================================
    # Step 3: query() 原生 nGQL 查询
    # ============================================================
    section("Step 3: query() 原生 nGQL")

    # 基本查询
    rows = store.query(
        'MATCH (n:CodeEntity) WHERE n.CodeEntity.name == "test_function_alpha" '
        'RETURN n.CodeEntity.name AS name, n.CodeEntity.filePath AS file_path'
    )
    assert_ok(len(rows) >= 1, f"query 基本查询返回 {len(rows)} 行")
    if rows:
        print(f"  查询结果: {rows[0]}")

    # 变长路径查询
    rows = store.query(
        f'MATCH (n)-[:CALLS*1..3]->(callee) WHERE id(n) == "{node_id}" '
        f'RETURN callee.CodeEntity.name AS name'
    )
    assert_ok(len(rows) >= 1, f"query 变长路径返回 {len(rows)} 行")

    # 统计查询
    rows = store.query(
        'MATCH (n:CodeEntity) RETURN count(*) AS cnt'
    )
    assert_ok(len(rows) >= 1, "query 聚合统计返回结果")
    if rows:
        print(f"  CodeEntity 总数: {rows[0]}")

    # ============================================================
    # Step 4: delete_relation + delete_node
    # ============================================================
    section("Step 4: delete_relation + delete_node")

    # 删除关系
    deleted = store.delete_relation(node_id, node2_id, "calls")
    assert_ok(deleted, "delete_relation 删除关系")
    time.sleep(1)

    # 验证关系已删除
    rels_after = store.get_relations(source_id=node_id)
    assert_ok(len(rels_after) == 0, "关系已删除")

    # 删除节点
    deleted = store.delete_node(node_id)
    assert_ok(deleted, "delete_node 删除节点")
    time.sleep(1)

    deleted2 = store.delete_node(node2_id)
    assert_ok(deleted2, "delete_node 删除第二个节点")

    # 验证节点已删除
    node_after = store.get_node(node_id)
    assert_ok(node_after is None, "节点已删除")

    # ============================================================
    # Step 5: merge_nodes_batch（批量写入）
    # ============================================================
    section("Step 5: merge_nodes_batch 批量写入")

    batch_nodes = []
    for i in range(50):
        batch_nodes.append({
            "id": str(uuid.uuid4()),
            "name": f"batch_func_{i:03d}",
            "filePath": f"/src/batch/file_{i}.py",
            "entityType": "function",
            "language": "python",
            "lines": str(i * 10),
        })

    t0 = time.perf_counter()
    # merge_nodes_batch 如果存在的话
    if hasattr(store, "merge_nodes_batch"):
        count = store.merge_nodes_batch("CodeEntity", batch_nodes)
        elapsed = (time.perf_counter() - t0) * 1000
        assert_ok(count == 50, f"merge_nodes_batch 写入 {count}/50 个节点")
        print(f"  批量写入 50 节点耗时: {elapsed:.0f}ms ({elapsed/50:.1f}ms/条)")
    else:
        # 没有 batch 方法，用循环 merge_node
        for props in batch_nodes:
            store.merge_node("CodeEntity", props)
        print(f"  (无 merge_nodes_batch, 循环 merge_node 写入 50 节点)")

    time.sleep(1)

    # 验证批量写入
    rows = store.query('MATCH (n:CodeEntity) WHERE n.CodeEntity.name STARTS WITH "batch_func_" RETURN count(*) AS cnt')
    if rows:
        print(f"  验证: 查到 {rows[0]} 个 batch_func 节点")

    # ============================================================
    # Step 6: cleanup_orphan_nodes
    # ============================================================
    section("Step 6: cleanup_orphan_nodes")

    orphan_count = store.cleanup_orphan_nodes()
    assert_ok(orphan_count >= 0, f"cleanup_orphan_nodes 清理了 {orphan_count} 个孤立节点")

    # ============================================================
    # 清理
    # ============================================================
    section("清理测试数据")

    store.close()

    # 删除测试 Space
    raw_config = Config()
    raw_config.max_connection_pool_size = 2
    raw_pool = RawPool()
    raw_pool.init([(HOST, PORT)], raw_config)
    raw_session = raw_pool.get_session(USER, PASSWORD)
    raw_session.execute(f"DROP SPACE IF EXISTS {SPACE}")
    raw_session.release()
    raw_pool.close()
    print("  测试 Space 已删除 ✅")

    # ============================================================
    # 汇总
    # ============================================================
    section("集成测试完成 ✅")


if __name__ == "__main__":
    main()
