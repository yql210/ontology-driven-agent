"""Phase 4 Task 1: 配置切换验证 — GRAPH_BACKEND 路由正确性。

验证：
1. graph_backend=neo4j → create_graph_store 返回 Neo4jGraphStore 实例
2. graph_backend=nebula → create_graph_store 返回 NebulaGraphStore 实例
3. graph_backend=<其他> → raise ValueError
4. 环境变量 ONTOAGENT_GRAPH_BACKEND 能被 OntoAgentConfig.from_env 正确读取
5. prompt.py 的 _resolve_graph_backend 与配置一致
6. 默认值是 neo4j（向后兼容）
"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, "src")

from ontoagent.config import OntoAgentConfig
from ontoagent.store.factory import create_graph_store
from ontoagent.store.graph_store import GraphStore
from ontoagent.store.nebula_store import NebulaGraphStore
from ontoagent.store.neo4j_store import Neo4jGraphStore

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ {label}  {detail}")


print("=" * 65)
print("Phase 4 Task 1: 配置切换验证")
print("=" * 65)

# --- Test 1: factory 路由 ---
print("\n[1] factory.create_graph_store 路由")
with patch("ontoagent.store.factory.Neo4jGraphStore") as mock_neo4j, \
     patch("ontoagent.store.factory.NebulaGraphStore") as mock_nebula:
    mock_neo4j.return_value = MagicMock_Neo4j = type("M", (), {})()
    mock_nebula.return_value = MagicMock_Nebula = type("M", (), {})()

    # neo4j
    cfg = OntoAgentConfig(graph_backend="neo4j")
    store = create_graph_store(cfg)
    check("neo4j → Neo4jGraphStore", mock_neo4j.called)
    mock_neo4j.reset_mock()

    # nebula
    cfg = OntoAgentConfig(graph_backend="nebula")
    store = create_graph_store(cfg)
    check("nebula → NebulaGraphStore", mock_nebula.called)
    mock_nebula.reset_mock()

    # 无效
    cfg = OntoAgentConfig(graph_backend="redis")
    try:
        create_graph_store(cfg)
        check("无效 backend → ValueError", False, "未抛异常")
    except ValueError as e:
        check("无效 backend → ValueError", "Unsupported graph_backend" in str(e))

# --- Test 2: OntoAgentConfig.from_env 读取环境变量 ---
print("\n[2] OntoAgentConfig.from_env 读取 ONTOAGENT_GRAPH_BACKEND")
# 保存原始值
orig = os.environ.get("ONTOAGENT_GRAPH_BACKEND")

try:
    # 默认值
    os.environ.pop("ONTOAGENT_GRAPH_BACKEND", None)
    c = OntoAgentConfig.from_env()
    check("默认值 = neo4j", c.graph_backend == "neo4j", f"got {c.graph_backend!r}")

    # 设为 nebula
    os.environ["ONTOAGENT_GRAPH_BACKEND"] = "nebula"
    c = OntoAgentConfig.from_env()
    check("env=nebula → graph_backend=nebula", c.graph_backend == "nebula")

    # 设为 neo4j 显式
    os.environ["ONTOAGENT_GRAPH_BACKEND"] = "neo4j"
    c = OntoAgentConfig.from_env()
    check("env=neo4j → graph_backend=neo4j", c.graph_backend == "neo4j")

    # 大小写（config 原样保留，prompt 做了 .lower()）
    os.environ["ONTOAGENT_GRAPH_BACKEND"] = "NEBULA"
    c = OntoAgentConfig.from_env()
    check("env=NEBULA → config 原样保留", c.graph_backend == "NEBULA")
finally:
    if orig is not None:
        os.environ["ONTOAGENT_GRAPH_BACKEND"] = orig
    else:
        os.environ.pop("ONTOAGENT_GRAPH_BACKEND", None)

# --- Test 3: prompt.py _resolve_graph_backend ---
print("\n[3] prompt.py 后端感知")
from ontoagent.agent.prompt import _resolve_graph_backend, _get_query_lang_name

orig = os.environ.get("ONTOAGENT_GRAPH_BACKEND")
try:
    os.environ["ONTOAGENT_GRAPH_BACKEND"] = "neo4j"
    check("neo4j → Cypher", _get_query_lang_name() == "Cypher")

    os.environ["ONTOAGENT_GRAPH_BACKEND"] = "nebula"
    check("nebula → nGQL", _get_query_lang_name() == "nGQL")

    os.environ["ONTOAGENT_GRAPH_BACKEND"] = "NEBULA"
    check("NEBULA → nGQL (大小写不敏感)", _get_query_lang_name() == "nGQL")

    os.environ["ONTOAGENT_GRAPH_BACKEND"] = "unknown"
    check("unknown → fallback Cypher", _get_query_lang_name() == "Cypher")
finally:
    if orig is not None:
        os.environ["ONTOAGENT_GRAPH_BACKEND"] = orig
    else:
        os.environ.pop("ONTOAGENT_GRAPH_BACKEND", None)

# --- Test 4: GraphStore 子类类型正确 ---
print("\n[4] GraphStore 子类继承关系")
check("Neo4jGraphStore is GraphStore", issubclass(Neo4jGraphStore, GraphStore))
check("NebulaGraphStore is GraphStore", issubclass(NebulaGraphStore, GraphStore))

# --- 汇总 ---
print("\n" + "=" * 65)
print(f"结果: {PASS} passed, {FAIL} failed")
print("=" * 65)
sys.exit(0 if FAIL == 0 else 1)
