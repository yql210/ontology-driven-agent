"""POC Step 2: NebulaGraph 性能 benchmark

测试关键操作的延迟，与 D1-Final 的 Go/No-Go 标准对比。
Go 标准：ShapeEvaluator 单次 evaluate < 100ms
"""
from __future__ import annotations

import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from nebula3.gclient.net import ConnectionPool
from nebula3.Config import Config

HOST = "124.221.243.142"
PORT = 9669
USER = "root"
PASSWORD = "nebula"
SPACE = "ontoagent_poc"


def connect():
    config = Config()
    config.max_connection_pool_size = 5
    pool = ConnectionPool()
    pool.init([(HOST, PORT)], config)
    session = pool.get_session(USER, PASSWORD)
    session.execute(f"USE {SPACE}")
    return pool, session


def benchmark(session, label, ngql, iterations=20):
    """执行 N 次查询，统计延迟"""
    # warmup
    for _ in range(3):
        session.execute(ngql)

    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        r = session.execute(ngql)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)  # ms
        if not r.is_succeeded():
            print(f"  ❌ {r.error_msg()}")
            return None

    times.sort()
    p50 = times[len(times) // 2]
    p99 = times[int(len(times) * 0.99)]
    avg = sum(times) / len(times)
    mn = min(times)
    mx = max(times)

    print(f"  {label}")
    print(f"    iterations: {iterations}")
    print(f"    avg: {avg:.1f}ms  P50: {p50:.1f}ms  P99: {p99:.1f}ms  min: {mn:.1f}ms  max: {mx:.1f}ms")
    return {"avg": avg, "p50": p50, "p99": p99, "min": mn, "max": mx}


def main():
    print("=" * 60)
    print("NebulaGraph 性能 Benchmark")
    print(f"Server: {HOST}:{PORT}  Space: {SPACE}")
    print("=" * 60)

    pool, session = connect()
    results = {}

    try:
        # BM1: 基本节点查找（按 name 属性索引）
        print("\n--- BM1: 基本节点查找（name 属性索引）---")
        results["BM1"] = benchmark(session, "节点查找", (
            'MATCH (n:CodeEntity) '
            'WHERE n.CodeEntity.name == "process_order" '
            'RETURN n.CodeEntity.name AS name'
        ))

        # BM2: 变长路径 *1..3（ShapeEvaluator 核心模式）
        print("\n--- BM2: 变长路径 *1..3（ShapeEvaluator 模式）---")
        results["BM2"] = benchmark(session, "变长路径 1..3", (
            'MATCH (n)-[:CALLS*1..3]->(callee) '
            'WHERE id(n) == "a0000001-0000-0000-0000-000000000001" '
            'RETURN callee.CodeEntity.name AS name'
        ))

        # BM3: 变长路径 *1..1（单跳，最高频）
        print("\n--- BM3: 变长路径 *1..1（单跳）---")
        results["BM3"] = benchmark(session, "单跳路径", (
            'MATCH (n)-[:CALLS*1..1]->(callee) '
            'WHERE id(n) == "a0000001-0000-0000-0000-000000000001" '
            'RETURN callee.CodeEntity.name AS name'
        ))

        # BM4: 多跳业务追溯（4 跳跨实体）
        print("\n--- BM4: 多跳业务追溯（Code→Concept+DataAsset→Compliance）---")
        results["BM4"] = benchmark(session, "4跳业务追溯", (
            'MATCH (c:CodeEntity)-[:DESCRIBES]->(concept:ConceptEntity) '
            'MATCH (c)-[:PROCESSES_DATA]->(data:DataAsset)-[:GOVERNED_BY]->(ci:ComplianceItem) '
            'WHERE c.CodeEntity.name == "process_order" '
            'RETURN c.CodeEntity.name, concept.ConceptEntity.name, '
            'data.DataAsset.name, ci.ComplianceItem.name'
        ))

        # BM5: UPSERT（写入性能）
        print("\n--- BM5: UPSERT VERTEX（写入）---")
        results["BM5"] = benchmark(session, "UPSERT 写入", (
            'UPSERT VERTEX ON CodeEntity "a0000001-0000-0000-0000-000000000001" '
            'SET lines = "55"'
        ))

        # BM6: 统计聚合（全表扫描）
        print("\n--- BM6: 统计聚合（全节点 tags）---")
        results["BM6"] = benchmark(session, "统计聚合", (
            'MATCH (n) RETURN tags(n) AS labels, count(*) AS cnt'
        ))

        # BM7: 批量写入性能（循环 UPSERT 100 节点）
        print("\n--- BM7: 批量写入（100 节点循环 UPSERT）---")
        t0 = time.perf_counter()
        for i in range(100):
            vid = f"b00{i:04d}-0000-0000-0000-000000000000"
            ngql = (
                f'INSERT VERTEX CodeEntity(name, filePath, entityType, language, lines, docstring) '
                f'VALUES "{vid}":("test_func_{i}", "/src/test.py", "function", "python", "{i}", "test")'
            )
            r = session.execute(ngql)
            if not r.is_succeeded():
                print(f"  ❌ 第{i}条: {r.error_msg()}")
                break
        t1 = time.perf_counter()
        batch_time = (t1 - t0) * 1000
        print(f"    100 节点循环写入: {batch_time:.0f}ms  平均: {batch_time/100:.1f}ms/条")

        # 汇总
        print("\n" + "=" * 60)
        print("Go/No-Go 评估")
        print("=" * 60)
        print(f"\nGo 标准: ShapeEvaluator 单次 evaluate < 100ms")
        print(f"  BM2 (变长路径 *1..3) P50: {results['BM2']['p50']:.1f}ms")
        print(f"  BM3 (单跳路径 *1..1) P50: {results['BM3']['p50']:.1f}ms")
        print(f"  BM4 (4跳业务追溯)   P50: {results['BM4']['p50']:.1f}ms")
        print(f"\n  结论: {'✅ Go' if results['BM2']['p50'] < 100 else '⚠️ 超标'}")

        print(f"\n写入性能:")
        print(f"  BM5 (单次 UPSERT) avg: {results['BM5']['avg']:.1f}ms")
        print(f"  BM7 (批量100条) avg: {batch_time/100:.1f}ms/条")

    finally:
        session.release()
        pool.close()


if __name__ == "__main__":
    main()
