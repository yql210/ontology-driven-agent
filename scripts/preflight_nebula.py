"""NebulaGraph Phase 5.0 前置检查 v2 — 加长 DDL 等待。"""
from __future__ import annotations

import time
import sys

from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool


def wait_ddl(session, label: str, max_wait: int = 40) -> bool:
    """等待 DDL 生效，用 SHOW TAGS/EDGES 探针重试。"""
    for i in range(max_wait):
        r = session.execute("SHOW TAGS;")
        if r.is_succeeded() and not r.is_empty():
            return True
        time.sleep(1)
    print(f"  [WARN] DDL wait timeout for {label} after {max_wait}s")
    return False


def wait_edge(session, edge_name: str, max_wait: int = 40) -> bool:
    """等待指定 Edge type 出现在 SHOW EDGES 里。"""
    for i in range(max_wait):
        r = session.execute("SHOW EDGES;")
        if r.is_succeeded():
            try:
                rows = r.rows()
            except TypeError:
                rows = r.rows if r.rows else []
            for row in rows:
                try:
                    if row.values[0].get_s().decode() == edge_name:
                        return True
                except Exception:
                    pass
        time.sleep(1)
    return False


def get_err(r):
    if r.is_succeeded():
        return "OK"
    try:
        return r.error_msg()
    except Exception:
        try:
            return str(r.error_msg)
        except Exception:
            return "unknown"


config = Config()
config.max_connection_pool_size = 5
pool = ConnectionPool()
ok = pool.init([("124.221.243.142", 9669)], config)
if not ok:
    print("FAIL: cannot init pool")
    sys.exit(1)

session = pool.get_session("root", "nebula123")
try:
    print("1. Checking ontoagent space...")
    r = session.execute("SHOW SPACES;")
    spaces = []
    try:
        rows = r.rows()
    except TypeError:
        rows = r.rows if r.rows else []
    for row in rows:
        try:
            spaces.append(row.values[0].get_s().decode())
        except Exception:
            pass
    print(f"   Spaces: {spaces}")

    print("2. Creating test space...")
    session.execute(
        "CREATE SPACE IF NOT EXISTS test_preflight "
        "(vid_type=FIXED_STRING(36), partition_num=5, replica_factor=1);"
    )
    print("   Waiting for space to be ready...")
    time.sleep(15)  # space 创建需要较长等待
    session.execute("USE test_preflight;")

    print("3. Creating Tag + Edge with properties...")
    session.execute("CREATE TAG IF NOT EXISTS test_v(name string);")
    session.execute("CREATE EDGE IF NOT EXISTS test_edge(weight double, confidence double);")
    print("   Waiting for schema...")
    time.sleep(20)  # Tag/Edge 创建需要等待

    # 探针确认
    r = session.execute("SHOW TAGS;")
    print(f"   SHOW TAGS: succeeded={r.is_succeeded()}, empty={r.is_empty()}")
    r = session.execute("SHOW EDGES;")
    print(f"   SHOW EDGES: succeeded={r.is_succeeded()}, empty={r.is_empty()}")

    print("4. Testing UPSERT EDGE...")
    r = session.execute('INSERT VERTEX test_v(name string) VALUES "v1":("a"), "v2":("b");')
    print(f"   INSERT VERTEX: succeeded={r.is_succeeded()} err={get_err(r)}")
    time.sleep(2)
    r = session.execute('UPSERT EDGE ON test_edge "v1"->"v2"@0 SET weight=1.0, confidence=0.9;')
    print(f"   UPSERT EDGE: succeeded={r.is_succeeded()} err={get_err(r)}")

    if r.is_succeeded():
        time.sleep(1)
        r = session.execute('FETCH PROP ON test_edge "v1"->"v2" YIELD weight AS w, confidence AS c;')
        print(f"   FETCH EDGE back: succeeded={r.is_succeeded()}, empty={r.is_empty()}")

    print("5. Testing CLEAR SPACE...")
    session.execute('INSERT VERTEX test_v(name string) VALUES "v3":("c");')
    time.sleep(1)
    r = session.execute("CLEAR SPACE test_preflight;")
    print(f"   CLEAR SPACE: succeeded={r.is_succeeded()} err={get_err(r)}")
    time.sleep(5)
    r = session.execute("SHOW TAGS;")
    print(f"   SHOW TAGS after CLEAR (should have schema): succeeded={r.is_succeeded()}, empty={r.is_empty()}")

    print("6. Testing batch INSERT VERTEX (100)...")
    vals = ", ".join([f'"batch_{i}":("name_{i}")' for i in range(100)])
    r = session.execute(f'INSERT VERTEX test_v(name string) VALUES {vals};')
    print(f"   Batch INSERT 100: succeeded={r.is_succeeded()} err={get_err(r)}")

    print("7. Testing tags(vertex) function...")
    session.execute('INSERT VERTEX test_v(name string) VALUES "tagtest":("hello");')
    time.sleep(2)
    r = session.execute('FETCH PROP ON * "tagtest" YIELD tags(vertex) AS tags, properties(vertex) AS props;')
    print(f"   tags(vertex): succeeded={r.is_succeeded()} err={get_err(r)}")

    print("8. Testing GO ... STEPS (traversal)...")
    session.execute('CREATE EDGE IF NOT EXISTS rel();')
    time.sleep(15)
    session.execute(
        'INSERT VERTEX test_v(name string) VALUES "root":("root"), '
        '"child1":("c1"), "child2":("c2");'
    )
    session.execute(
        'INSERT EDGE rel VALUES "root"->"child1":(), "root"->"child2":(), "child1"->"child2":();'
    )
    time.sleep(2)
    r = session.execute('GO 1 TO 3 STEPS FROM "root" OVER rel YIELD id($$) AS dst;')
    print(f"   GO 1 TO 3 STEPS: succeeded={r.is_succeeded()} err={get_err(r)}")
    if r.is_succeeded() and not r.is_empty():
        print(f"   Traversal rows: {len(r.rows())}")

    # 清理
    session.execute("DROP SPACE test_preflight;")
    print("\n=== Preflight complete ===")
finally:
    session.release()
    pool.close()
