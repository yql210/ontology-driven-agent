# Neo4j 与 LayerKG 规范

## 适用范围
涉及 Neo4j 图数据库操作和 LayerKG Schema 的所有代码。

## Neo4j 连接
```python
# 连接参数（通过环境变量或配置传入）
NEO4J_URI = "bolt://REDACTED_IP:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "REDACTED_PASSWORD"
```

## 连接管理
- 使用 context manager 管理连接生命周期
- Driver 是全局单例，不要每次操作创建新 Driver
- Session 是短生命周期，每次操作创建新 Session
- 使用 `neo4j` Python driver v6.2+

```python
from neo4j import GraphDatabase

class Neo4jGraphStore:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> Neo4jGraphStore:
        return self

    def __exit__(self, *args) -> None:
        self.close()
```

## Cypher 查询规范
- 查询使用**参数化**，不要字符串拼接
- 节点标签用 PascalCase：`CodeEntity`, `ConceptEntity`
- 关系类型用 UPPER_SNAKE：`CALLS`, `IMPORTS`, `CONTAINS`
- 属性名用 camelCase：`entityType`, `filePath`

```python
# ✅ 正确：参数化查询
session.run(
    "MERGE (n:CodeEntity {id: $id}) SET n.name = $name",
    id=entity.id, name=entity.name
)

# ❌ 错误：字符串拼接
session.run(f"MERGE (n:CodeEntity {{id: '{entity.id}'}})")
```

## LayerKG Schema（6实体+11关系）
### 实体及标签
| Dataclass | Neo4j Label | 类型字段值 |
|-----------|-------------|-----------|
| CodeEntity | CodeEntity | function, class, interface, module, file, enum, record, field |
| ConceptEntity | ConceptEntity | business_concept, design_pattern, api_contract, data_model, process |
| DocEntity | DocEntity | readme, module_doc, api_doc, comment, wiki, architecture_doc |
| ResourceEntity | ResourceEntity | image, diagram, pdf, config, schema_file, log |
| ModuleEntity | ModuleEntity | 功能模块（聚类结果） |
| ChangeSetEntity | ChangeSetEntity | 变更集 |

### 关系及类型
| 关系 | Neo4j Type | 来源 |
|------|-----------|------|
| calls | CALLS | AST 结构 |
| extends | EXTENDS | AST 结构 |
| implements | IMPLEMENTS | AST 结构 |
| imports | IMPORTS | AST 结构 |
| contains | CONTAINS | AST 结构 |
| semantic_impact | SEMANTIC_IMPACT | LLM 语义 |
| describes | DESCRIBES | LLM 语义 |
| illustrates | ILLUSTRATES | LLM 语义 |
| derived_from | DERIVED_FROM | LLM 语义 |
| changed_in | CHANGED_IN | 变更追踪 |
| affects | AFFECTS | 变更追踪 |

## 索引与约束
- 每个实体标签的 `id` 字段必须唯一约束
- `name` 字段建立索引加速查询
- Phase 0 创建以下约束：

```cypher
CREATE CONSTRAINT IF NOT EXISTS FOR (n:CodeEntity) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:ConceptEntity) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:DocEntity) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:ResourceEntity) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:ModuleEntity) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:ChangeSetEntity) REQUIRE n.id IS UNIQUE;
```

## 测试策略
- 集成测试使用真实 Neo4j 实例
- 测试后清理：`MATCH (n) DETACH DELETE n`
- 使用 `@pytest.mark.integration` 标记
- CI 环境用 Neo4j Docker 容器
