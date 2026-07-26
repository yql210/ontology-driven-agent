## graph_query 查询语言：Cypher（Neo4j）

当前后端是 Neo4j，graph_query 工具接收 Cypher 查询语句。

### Schema

实体标签：CodeEntity, ConceptEntity, DocEntity, ResourceEntity, ModuleEntity, ChangeSetEntity, LogEntity, AlertEntity, ServiceEntity, DataAsset, ComplianceItem, CapabilityEntity, ProcessEntity

关系类型：CALLS, EXTENDS, IMPLEMENTS, IMPORTS, CONTAINS, SEMANTIC_IMPACT, DESCRIBES, ILLUSTRATES, DERIVED_FROM, CHANGED_IN, AFFECTS, TRIGGERED_BY, LOGS_FROM, RUNS_AS, SERVICE_DEPENDS_ON, PROCESSES_DATA, SUBJECT_TO, GOVERNED_BY, CALLS_SERVICE, PUBLISHES_TO, CONSUMED_BY, PRODUCES, CONSUMES, COMPOSES_INTO, REALIZED_BY, PRECEDES, EQUIVALENT_TO

### 查询示例

查找名为 xxx 的函数：

```cypher
MATCH (n:CodeEntity) WHERE n.name CONTAINS 'xxx' RETURN n.name, n.filePath LIMIT 10
```

查找函数调用关系：

```cypher
MATCH (a)-[:CALLS]->(b) WHERE a.name CONTAINS 'xxx' RETURN a.name, b.name LIMIT 10
```

统计每种实体类型数量：

```cypher
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS cnt
```
