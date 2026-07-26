"""POC Step 3: 智谱 GLM 生成 nGQL 质量测试

验证 D1 方案的核心假设：给 LLM 一个包含语法差异+Schema+Few-shot 的 prompt，
它能否生成正确的 nGQL 查询。

测试方法：
1. 构造 D1 方案设计的 prompt（Schema-aware + Few-shot + 语法规则）
2. 让智谱 GLM 生成 20 条 nGQL 查询
3. 在真实 NebulaGraph 上执行，统计正确率
"""
from __future__ import annotations

import json
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import httpx
from nebula3.gclient.net import ConnectionPool
from nebula3.Config import Config

HOST = "124.221.243.142"
PORT = 9669
USER = "root"
PASSWORD = "nebula"
SPACE = "ontoagent_poc"

# 智谱 API
ZHIPU_API_KEY = os.environ.get("ONTOAGENT_AGENT_API_KEY", "")
ZHIPU_BASE_URL = os.environ.get("ONTOAGENT_AGENT_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
ZHIPU_MODEL = os.environ.get("ONTOAGENT_AGENT_LLM_MODEL", "glm-4-flash")

# D1 方案的 nGQL prompt 模板
SYSTEM_PROMPT = """你是一个图查询专家。你帮助用户将自然语言问题转换为 NebulaGraph 的 nGQL 查询语句。

## 数据库环境
你连接的是 NebulaGraph 3.7.0 图数据库。

## Schema（Tag 和 Edge 定义）

### Tag（实体类型，属性访问必须带 Tag 前缀）
- CodeEntity(name, filePath, entityType, language, lines, docstring)
- ConceptEntity(name, description, category)
- DocEntity(name, filePath, docType)
- DataAsset(name, description, classification)
- ComplianceItem(name, regulation, requirement)

### Edge（关系类型）
- CALLS：函数调用（CodeEntity → CodeEntity）
- CONTAINS：包含关系
- DESCRIBES：描述关系（CodeEntity → ConceptEntity）
- PROCESSES_DATA：处理数据（CodeEntity → DataAsset）
- GOVERNED_BY：受约束（DataAsset → ComplianceItem）
- EXTENDS, IMPLEMENTS, IMPORTS：继承/实现/导入

## nGQL 与 Cypher 的关键语法差异

1. **属性访问必须带 Tag 前缀**：`v.TagName.fieldName`，不是 `v.fieldName`
   - ✅ 正确：RETURN n.CodeEntity.name
   - ❌ 错误：RETURN n.name

2. **相等比较用 ==**：`WHERE n.CodeEntity.name == "value"`，不是 `=`

3. **获取节点的 Tag**：用 `tags(n)`，不是 `labels(n)`

4. **变长路径语法与 Cypher 完全一致**：`-[:EDGE*1..3]->`

5. **边的起点和终点**：直接在 MATCH pattern 中绑定变量，不用 startNode()/endNode()
   - ✅ 正确：MATCH (a)-[r]->(b) RETURN id(a), id(b)
   - ❌ 错误：RETURN startNode(r), endNode(r)

## 查询示例（Few-shot）

用户：查找名为 process_order 的函数
nGQL：MATCH (n:CodeEntity) WHERE n.CodeEntity.name == "process_order" RETURN n.CodeEntity.name AS name, n.CodeEntity.filePath AS file_path

用户：查找 process_order 调用了哪些函数（3跳以内）
nGQL：MATCH (n)-[:CALLS*1..3]->(callee) WHERE n.CodeEntity.name == "process_order" RETURN callee.CodeEntity.name AS callee_name

用户：查找所有 CodeEntity 节点的名字和类型
nGQL：MATCH (n:CodeEntity) RETURN n.CodeEntity.name AS name, n.CodeEntity.entityType AS type LIMIT 10

用户：统计每种实体类型有多少个
nGQL：MATCH (n) RETURN tags(n) AS label, count(*) AS cnt

用户：查找处理了"客户订单数据"这个数据资产的代码，以及这个数据资产受哪些合规条款约束
nGQL：MATCH (c:CodeEntity)-[:PROCESSES_DATA]->(d:DataAsset)-[:GOVERNED_BY]->(ci:ComplianceItem) WHERE d.DataAsset.name == "客户订单数据" RETURN c.CodeEntity.name AS code, d.DataAsset.name AS data, ci.ComplianceItem.name AS compliance
"""

# 20 个测试问题（覆盖主要查询模式）
TEST_QUESTIONS = [
    # 基本查找（5题）
    "查找名为 process_order 的函数",
    "查找名为 validate_input 的函数的文件路径",
    "查找所有 function 类型的 CodeEntity",
    "查找 docstring 包含 database 的函数",
    "查找名为 订单处理 的概念实体",
    # 变长路径（4题）
    "查找 process_order 调用了哪些函数（3跳内）",
    "查找谁调用了 save_to_db（反向1跳）",
    "查找 process_order 到 send_notification 的调用链",
    "查找 process_order 的所有下游函数（不限跳数，上限10）",
    # 边/关系查询（3题）
    "查找所有 CALLS 关系，返回调用者和被调用者的名字",
    "查找 DESCRIBES 关系连接的代码和概念",
    "查找所有处理了 DataAsset 的代码",
    # 多跳追溯（3题）
    "查找处理了客户订单数据的代码，以及该数据受哪些合规条款约束",
    "查找 process_order 描述了哪些概念",
    "查找所有 ComplianceItem 以及约束的数据资产",
    # 聚合统计（3题）
    "统计每种实体类型有多少个节点",
    "统计有多少条 CALLS 边",
    "查找每个 CodeEntity 调用了多少个其他函数",
    # 复杂查询（2题）
    "查找同时描述了概念且处理了数据的代码",
    "查找没有被任何其他函数调用的函数",
]


def call_zhipu(question: str) -> str:
    """调用智谱 GLM 生成 nGQL"""
    headers = {
        "Authorization": f"Bearer {ZHIPU_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": ZHIPU_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"请将以下问题转换为 nGQL 查询语句。只返回 nGQL 语句，不要解释。\n\n问题：{question}"},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{ZHIPU_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


def extract_ngql(response: str) -> str:
    """从 LLM 回复中提取 nGQL 语句"""
    # 去掉 markdown 代码块
    lines = response.strip().split("\n")
    ngql_lines = []
    in_code = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or stripped.upper().startswith(("MATCH", "LOOKUP", "GO", "FETCH", "RETURN")):
            ngql_lines.append(line)
        elif not stripped and ngql_lines:
            break

    if ngql_lines:
        return " ".join(ngql_lines).strip()
    return response.strip()


def execute_and_check(session, ngql: str) -> tuple[bool, str, int]:
    """在 NebulaGraph 上执行 nGQL，返回 (成功, 错误信息, 行数)"""
    if not ngql:
        return False, "空查询", 0

    r = session.execute(ngql)
    if r.is_succeeded():
        return True, "ok", r.row_size()
    else:
        return False, r.error_msg(), 0


def main():
    print("=" * 60)
    print("智谱 GLM 生成 nGQL 质量测试")
    print(f"Model: {ZHIPU_MODEL}")
    print(f"NebulaGraph: {HOST}:{PORT}")
    print("=" * 60)

    if not ZHIPU_API_KEY:
        print("❌ 缺少 ONTOAGENT_AGENT_API_KEY 环境变量")
        print("   请设置智谱 API key")
        sys.exit(1)

    # 连接 NebulaGraph
    config = Config()
    config.max_connection_pool_size = 3
    pool = ConnectionPool()
    pool.init([(HOST, PORT)], config)
    session = pool.get_session(USER, PASSWORD)
    session.execute(f"USE {SPACE}")

    results = []
    correct = 0
    total = len(TEST_QUESTIONS)

    try:
        for i, question in enumerate(TEST_QUESTIONS, 1):
            print(f"\n[{i}/{total}] {question}")

            # LLM 生成
            try:
                raw_response = call_zhipu(question)
                ngql = extract_ngql(raw_response)
                print(f"  LLM: {ngql[:120]}...")
            except Exception as e:
                print(f"  ❌ LLM 调用失败: {e}")
                results.append({"q": question, "status": "llm_error", "error": str(e)})
                continue

            # 执行验证
            ok, err, rows = execute_and_check(session, ngql)
            if ok:
                print(f"  ✅ 执行成功 (返回 {rows} 行)")
                correct += 1
                results.append({"q": question, "ngql": ngql, "status": "pass", "rows": rows})
            else:
                print(f"  ❌ 执行失败: {err}")
                # 检查是否是常见错误
                error_type = "unknown"
                if "SemanticError" in err or "only" in err.lower():
                    error_type = "property_access"  # 缺 tag 前缀
                elif "SyntaxError" in err:
                    error_type = "syntax"
                results.append({"q": question, "ngql": ngql, "status": "fail", "error": err, "error_type": error_type})

            time.sleep(0.5)  # 避免 API 限流

    finally:
        session.release()
        pool.close()

    # 汇总
    print("\n" + "=" * 60)
    print(f"测试结果: {correct}/{total} 正确 ({correct/total*100:.0f}%)")
    print("=" * 60)

    # 错误分析
    errors = [r for r in results if r["status"] == "fail"]
    if errors:
        print(f"\n错误分析 ({len(errors)} 条失败):")
        from collections import Counter
        error_types = Counter(r.get("error_type", "unknown") for r in errors)
        for et, cnt in error_types.most_common():
            print(f"  {et}: {cnt} 条")

    # Go/No-Go
    rate = correct / total
    print(f"\nGo 标准: 正确率 > 80%")
    print(f"实际: {rate*100:.0f}%")
    print(f"结论: {'✅ Go' if rate >= 0.8 else '⚠️ 需改进 prompt'}")

    # 保存详细结果
    with open("poc_llm_ngql_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n详细结果已保存到 poc_llm_ngql_results.json")


if __name__ == "__main__":
    main()
