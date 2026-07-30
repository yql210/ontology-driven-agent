#!/usr/bin/env python3
"""对比 GO FROM vs MATCH 在 NebulaGraph 大图上的边加载耗时。

不修改源码，仅通过 GraphStore API 对比两种查询策略的实测性能。

用法（需先 build 写入数据）::

    uv run ontoagent build <repo> --skip-semantic --skip-clustering
    uv run python scripts/verify_go_from.py

结果输出 GO FROM 耗时、MATCH 耗时、加速比、返回边数对比。
"""

from __future__ import annotations

import os
import time

from ontoagent.store.nebula_store import NebulaGraphStore

REL_TYPES = ["CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS"]
NODE_LABEL = "CodeEntity"


def _build_store() -> NebulaGraphStore:
    """从 .env / 环境变量读取 NebulaGraph 连接配置。"""
    host = os.getenv("ONTOAGENT_NEBULA_HOST", "127.0.0.1")
    port = int(os.getenv("ONTOAGENT_NEBULA_PORT", "9669"))
    user = os.getenv("ONTOAGENT_NEBULA_USER", "root")
    password = os.getenv("ONTOAGENT_NEBULA_PASSWORD", "nebula")
    space = os.getenv("ONTOAGENT_NEBULA_SPACE", "ontoagent")
    return NebulaGraphStore(host=host, port=port, user=user, password=password, space=space)


def _benchmark_go_from(store: NebulaGraphStore) -> tuple[float, int]:
    """GO FROM 路径（当前 get_edges_by_types 实现）。"""
    start = time.perf_counter()
    edges = store.get_edges_by_types(REL_TYPES, NODE_LABEL)
    elapsed = time.perf_counter() - start
    return elapsed, len(edges)


def _benchmark_match(store: NebulaGraphStore) -> tuple[float, int]:
    """MATCH 路径（旧实现，经 CypherToNgqlAdapter）。"""
    type_filter = "|".join(REL_TYPES)
    cypher = f"MATCH (a)-[r:{type_filter}]->(b) RETURN id(a) AS source_id, id(b) AS target_id"
    start = time.perf_counter()
    edges = store.query(cypher)
    elapsed = time.perf_counter() - start
    return elapsed, len(edges)


def main() -> None:
    print("=" * 60)
    print("GO FROM vs MATCH — NebulaGraph edge loading benchmark")
    print("=" * 60)

    store = _build_store()
    print(f"Connected to space: {store._space}")
    health = store.health_check()
    print(f"Health: tags={health.get('tag_count')}, edges={health.get('edge_count')}")
    print()

    # 1. GO FROM（新实现）
    print("[1/2] GO FROM (LOOKUP ON + batched GO FROM vid OVER edges)...")
    go_time, go_count = _benchmark_go_from(store)
    print(f"  → {go_count} edges in {go_time:.2f}s")
    print()

    # 2. MATCH（旧实现）
    print("[2/2] MATCH (full scan via CypherToNgqlAdapter)...")
    match_time, match_count = _benchmark_match(store)
    print(f"  → {match_count} edges in {match_time:.2f}s")
    print()

    # 对比
    print("-" * 60)
    speedup = match_time / go_time if go_time > 0 else float("inf")
    print(f"GO FROM:  {go_time:7.2f}s  ({go_count} edges)")
    print(f"MATCH:    {match_time:7.2f}s  ({match_count} edges)")
    print(f"Speedup:  {speedup:.1f}x")
    if go_count != match_count:
        print(f"⚠ Edge count mismatch: GO={go_count}, MATCH={match_count}")
    else:
        print("✓ Edge counts match")
    print("-" * 60)

    store.close()


if __name__ == "__main__":
    main()
