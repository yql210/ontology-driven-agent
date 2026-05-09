# Day 9 Hotfix: merge_relation Cypher MERGE 模式匹配 Bug

## 问题描述

构建在 Stage 2 约束验证失败：
```
Node(261) already exists with label `CodeEntity` and property `id` = 'e67e6f52-...'
```

## 根因分析

`neo4j_store.py` 的 `merge_relation` 方法（第 192 行）使用单条 MERGE 语句匹配整个模式：

```cypher
MERGE (source:CodeEntity {id: $source_id})-[r:CONTAINS]->(target:CodeEntity {id: $target_id})
```

**Neo4j MERGE 语义**：对整个模式要么全部匹配，要么全部创建。
- 当 source 和 target 节点已存在（由 merge_node 先创建），但关系不存在时
- MERGE 找不到完整模式（关系缺失）
- 于是尝试创建整个模式，包括重新创建 source/target 节点
- 触发 `(CodeEntity.id)` UNIQUE 约束冲突

## 修复方案

将 `merge_relation` 的单条 MERGE 拆成 MATCH + MERGE 两步：

```python
# 第一步：MATCH 已存在的节点
source_part = f"source:{source_label}" if source_label else "source"
target_part = f"target:{target_label}" if target_label else "target"

cypher = f"MATCH ({source_part} {{id: $source_id}})"
cypher += f" MATCH ({target_part} {{id: $target_id}})"
cypher += f" MERGE (source)-[r:{neo4j_rel_type}]->(target)"
```

## 修改文件

仅修改 `src/layerkg/neo4j_store.py` 的 `merge_relation` 方法（约第 189-206 行）。

### 当前代码（第 189-206 行）：
```python
        # 动态构建 MERGE 语句（带 label 优化）
        source_part = f"source:{source_label}" if source_label else "source"
        target_part = f"target:{target_label}" if target_label else "target"
        cypher = f"MERGE ({source_part} {{id: $source_id}})-[r:{neo4j_rel_type}]->({target_part} {{id: $target_id}})"

        # 准备参数
        params: dict[str, Any] = {"source_id": source_id, "target_id": target_id}

        # 如果有属性，添加 SET 子句
        if properties:
            set_clauses = []
            for key, value in properties.items():
                set_clauses.append(f"r.{key} = ${key}")
                params[key] = value
            cypher += " SET " + ", ".join(set_clauses)

        with self._driver.session() as session:
            session.run(cypher, **params)
```

### 修改后代码：
```python
        # 动态构建 MATCH + MERGE 语句
        # 注意：必须拆成 MATCH（找已有节点）+ MERGE（仅操作关系），
        # 避免 MERGE 全模式时重新创建已存在节点导致 UNIQUE 约束冲突。
        source_part = f"source:{source_label}" if source_label else "source"
        target_part = f"target:{target_label}" if target_label else "target"

        cypher = f"MATCH ({source_part} {{id: $source_id}})"
        cypher += f" MATCH ({target_part} {{id: $target_id}})"
        cypher += f" MERGE (source)-[r:{neo4j_rel_type}]->(target)"

        # 准备参数
        params: dict[str, Any] = {"source_id": source_id, "target_id": target_id}

        # 如果有属性，添加 SET 子句
        if properties:
            set_clauses = []
            for key, value in properties.items():
                set_clauses.append(f"r.{key} = ${key}")
                params[key] = value
            cypher += " SET " + ", ".join(set_clauses)

        with self._driver.session() as session:
            session.run(cypher, **params)
```

## 测试

修改后运行：
```bash
uv run pytest tests/ -q --tb=short
uv run ruff check src/ tests/
```

确认所有测试通过（679 tests），然后重新构建验证。

## 构建验证

```bash
# 清空数据
export LAYERKG_NEO4J_URI="bolt://REDACTED_IP:7687" LAYERKG_NEO4J_PASSWORD="REDACTED_PASSWORD" LAYERKG_OLLAMA_URL="http://REDACTED_IP:11434"
uv run python -c "
from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore
import shutil, os
config = LayerKGConfig.from_env()
store = Neo4jGraphStore(uri=config.neo4j_uri, user=config.neo4j_user, password=config.neo4j_password)
store._driver.execute_query('MATCH (n) DETACH DELETE n')
store.close()
chroma_dir = config.chroma_persist_dir
if os.path.exists(chroma_dir): shutil.rmtree(chroma_dir)
print('Cleared')
"

# 构建验证
uv run layerkg build . --verbose-build
```
