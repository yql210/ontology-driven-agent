"""LangChain Tool 封装 — Agent 可调用的工具"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from layerkg.agent._helpers import get_chroma, get_neo4j


@tool
def semantic_search(query: str, top_k: int = 5) -> str:
    """语义搜索：在代码库中搜索与 query 相关的代码片段。

    Args:
        query: 搜索关键词或自然语言描述
        top_k: 返回结果数量，建议 5-10

    Returns:
        匹配的代码片段列表（JSON），包含文件路径、函数名、相似度分数
    """
    chroma = get_chroma()
    results = chroma.search(query_text=query, n_results=top_k)
    return json.dumps(results, ensure_ascii=False, indent=2)


@tool
def graph_query(cypher: str) -> str:
    """执行 Cypher 图查询，查询代码实体之间的关系。

    常用查询模式：
    - 函数调用关系：MATCH (a)-[:CALLS]->(b) WHERE a.name CONTAINS 'xxx' RETURN a.name, b.name
    - 模块依赖：MATCH (m:ModuleEntity)-[:CONTAINS]->(c) RETURN m.name, c.name
    - 概念关联：MATCH (c:ConceptEntity)<-[:DESCRIBES]-(e) RETURN c.name, e.name

    Args:
        cypher: Neo4j Cypher 查询语句

    Returns:
        查询结果的 JSON 格式字符串
    """
    neo4j = get_neo4j()
    try:
        results = neo4j.query(cypher)
        return json.dumps(results, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Cypher 查询错误: {e!s}\n请检查语法是否正确。"


# Day 1 先暴露这两个
ALL_TOOLS = [semantic_search, graph_query]
