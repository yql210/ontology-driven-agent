"""Phase 4 Task 4: 性能 benchmark — 批量写入 + 变长路径延迟。

参照 Phase 0 POC 的 benchmark 标准：
- 变长路径 P50 < 100ms 为达标
- 批量写入吞吐量记录（无硬性标准，用于评估）

测试场景：
1. 批量写入：500 节点 + 500 边
2. 变长路径查询：*1..3 延迟（P50/P95/P99）
3. 单跳查询延迟（baseline 对比）
"""
from __future__ import annotations

import sys
import time
import uuid
import random
import string
import statistics

sys.path.insert(0, "src")

from nebula3.gclient.net import ConnectionPool
from nebula3.Config import Config as NConfig

HOST, PORT, USER, PWD = "124.221.243.142", 9669, "root", "nebula"
SPACE = "p4_bench_" + "".join(random.choices(string.ascii_lowercase, k=6))
N_NODES = 500

config = NConfig()
config.max_connection_pool_size = 8
config.timeout = 30000
pool = ConnectionPool()
assert pool.init([(HOST, PORT)], config), "pool init failed"


def run(ngql: str) -> tuple[bool, str]:
    sess = pool.get_session(USER, PWD)
    try:
        r = sess.execute(ngql)
        return r.is_succeeded(), ("" if r.is_succeeded() else r.error_msg())
    finally:
        sess.release()


# ---- Setup ----
print(f"[setup] Space: {SPACE}")
ok, msg = run(f"CREATE SPACE `{SPACE}` (vid_type=FIXED_STRING(36));")
assert ok, msg
time.sleep(10)

print("[setup] Schema...")
ok, msg = run(
    f"USE `{SPACE}`;"
    "CREATE TAG CodeEntity(id string, name string, entityType string);"
    "CREATE EDGE CALLS();"
)
assert ok, msg
time.sleep(22)

# ============================================================
# Benchmark 1: 批量写入
# ============================================================
print(f"\n{'='*65}")
print(f"Benchmark 1: 批量写入 ({N_NODES} 节点 + {N_NODES-1} 边)")
print(f"{'='*65}")

# 生成数据
vids = [str(uuid.uuid4()) for _ in range(N_NODES)]
nodes = [(vid, f"fn_{i}", "function") for i, vid in enumerate(vids)]
edges = [(vids[i], vids[i + 1]) for i in range(N_NODES - 1)]

# 写节点（分批，每批 100）
print(f"\n[1a] 批量写 {N_NODES} 节点...")
BATCH = 100
t0 = time.perf_counter()
for start in range(0, N_NODES, BATCH):
    chunk = nodes[start : start + BATCH]
    values = ", ".join(f'"{vid}":("{vid}", "{name}", "{et}")' for vid, name, et in chunk)
    ok, msg = run(f'USE `{SPACE}`; INSERT VERTEX CodeEntity(id, name, entityType) VALUES {values};')
    if not ok:
        print(f"  ❌ batch {start} failed: {msg}")
        sys.exit(1)
t_nodes = time.perf_counter() - t0
print(f"  ✅ {N_NODES} 节点写入: {t_nodes:.2f}s ({N_NODES/t_nodes:.0f} nodes/s)")

# 写边（分批）
print(f"\n[1b] 批量写 {len(edges)} 边...")
t0 = time.perf_counter()
for start in range(0, len(edges), BATCH):
    chunk = edges[start : start + BATCH]
    values = ", ".join(f'"{s}"->"{t}":()' for s, t in chunk)
    ok, msg = run(f'USE `{SPACE}`; INSERT EDGE CALLS() VALUES {values};')
    if not ok:
        print(f"  ❌ edge batch {start} failed: {msg}")
        sys.exit(1)
t_edges = time.perf_counter() - t0
print(f"  ✅ {len(edges)} 边写入: {t_edges:.2f}s ({len(edges)/t_edges:.0f} edges/s)")

time.sleep(2)

# ============================================================
# Benchmark 2: 变长路径查询延迟
# ============================================================
print(f"\n{'='*65}")
print(f"Benchmark 2: 查询延迟（采样 50 次）")
print(f"{'='*65}")

# 预热
for vid in vids[:5]:
    run(f'USE `{SPACE}`; MATCH (n)-[:CALLS*1..3]->(m) WHERE id(n) == "{vid}" RETURN m LIMIT 10;')

SAMPLE_SIZE = 50
sample_vids = random.sample(vids, SAMPLE_SIZE)

# 用复用 session 测试真实查询延迟（NebulaGraphStore 生产模式）
bench_sess = pool.get_session(USER, PWD)
bench_sess.execute(f'USE `{SPACE}`;')

# [2a] 变长路径 *1..3
print(f"\n[2a] 变长路径 *1..3 ({SAMPLE_SIZE} samples, 复用 session)...")
latencies_v = []
for vid in sample_vids:
    t0 = time.perf_counter()
    bench_sess.execute(f'MATCH (n)-[:CALLS*1..3]->(m) WHERE id(n) == "{vid}" RETURN m LIMIT 10;')
    latencies_v.append((time.perf_counter() - t0) * 1000)

latencies_v.sort()
p50 = latencies_v[len(latencies_v) // 2]
p95 = latencies_v[int(len(latencies_v) * 0.95)]
p99 = latencies_v[int(len(latencies_v) * 0.99)]
mean = statistics.mean(latencies_v)
status = "✅ 达标" if p50 < 100 else "⚠️ 超标"
print(f"  变长路径 *1..3: P50={p50:.1f}ms P95={p95:.1f}ms P99={p99:.1f}ms mean={mean:.1f}ms")
print(f"  标准: P50 < 100ms → {status}")

# [2b] 单跳查询（baseline）
print(f"\n[2b] 单跳 CALLS (baseline, {SAMPLE_SIZE} samples)...")
latencies_s = []
for vid in sample_vids:
    t0 = time.perf_counter()
    bench_sess.execute(f'MATCH (n)-[:CALLS]->(m) WHERE id(n) == "{vid}" RETURN m LIMIT 10;')
    latencies_s.append((time.perf_counter() - t0) * 1000)

latencies_s.sort()
p50s = latencies_s[len(latencies_s) // 2]
p95s = latencies_s[int(len(latencies_s) * 0.95)]
print(f"  单跳 CALLS:      P50={p50s:.1f}ms P95={p95s:.1f}ms")

# [2c] 按 ID 取节点
print(f"\n[2c] ID 查节点 ({SAMPLE_SIZE} samples)...")
latencies_id = []
for vid in sample_vids:
    t0 = time.perf_counter()
    bench_sess.execute(f'MATCH (n:CodeEntity) WHERE id(n) == "{vid}" RETURN n;')
    latencies_id.append((time.perf_counter() - t0) * 1000)

bench_sess.release()

latencies_id.sort()
p50id = latencies_id[len(latencies_id) // 2]
print(f"  ID 查节点:       P50={p50id:.1f}ms")

# ---- 汇总 ----
print(f"\n{'='*65}")
print("Benchmark 汇总")
print(f"{'='*65}")
print(f"  批量写入: {N_NODES} 节点 {N_NODES/t_nodes:.0f}/s | {len(edges)} 边 {len(edges)/t_edges:.0f}/s")
print(f"  变长路径: P50={p50:.1f}ms ({'达标' if p50 < 100 else '超标'}，标准<100ms)")
print(f"  单跳路径: P50={p50s:.1f}ms")
print(f"  ID 查询:  P50={p50id:.1f}ms")

# 清理
print(f"\n[cleanup] DROP SPACE {SPACE}")
run(f"DROP SPACE IF EXISTS `{SPACE}`")
pool.close()

sys.exit(0 if p50 < 100 else 1)
