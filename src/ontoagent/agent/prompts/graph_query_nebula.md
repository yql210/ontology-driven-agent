## graph_query 查询语言：nGQL（NebulaGraph）

当前后端是 NebulaGraph，graph_query 工具接收 nGQL 查询语句。

### 关键语法规则（与 Cypher 的差异）

1. **属性访问必须带 Tag 前缀**：`v.TagName.fieldName`，不是 `v.fieldName`
   - ✅ 正确：`RETURN n.CodeEntity.name`
   - ❌ 错误：`RETURN n.name`

2. **相等比较用 `==`**：`WHERE n.CodeEntity.name == "value"`，不是 `=`

3. **获取节点的 Tag 列表**：用 `tags(n)`，不是 `labels(n)`

4. **边的起点和终点**：直接在 MATCH pattern 中绑定变量
   - ✅ 正确：`MATCH (a)-[r]->(b) RETURN id(a), id(b)`
   - ❌ 错误：`RETURN startNode(r), endNode(r)`

5. **变长路径语法与 Cypher 完全一致**：`-[:EDGE*1..3]->`

### Schema（Tag 和 Edge 定义）

Tag（属性访问必须带 Tag 前缀）：
- CodeEntity(name, filePath, entityType, language, lines, docstring)
- ConceptEntity(name, description, category)
- DocEntity(name, filePath, docType)
- ResourceEntity(name, resourceType, description)
- ModuleEntity(name, description, moduleType)
- ChangeSetEntity(name, commitHash, author)
- LogEntity(name, level, message)
- AlertEntity(name, severity, message)
- ServiceEntity(name, description, endpoint)
- DataAsset(name, description, classification)
- ComplianceItem(name, regulation, requirement)
- CapabilityEntity(name, description)
- ProcessEntity(name, description)

Edge（关系类型，大写）：
CALLS, EXTENDS, IMPLEMENTS, IMPORTS, CONTAINS, SEMANTIC_IMPACT, DESCRIBES, ILLUSTRATES, DERIVED_FROM, CHANGED_IN, AFFECTS, TRIGGERED_BY, LOGS_FROM, RUNS_AS, SERVICE_DEPENDS_ON, PROCESSES_DATA, SUBJECT_TO, GOVERNED_BY, CALLS_SERVICE, PUBLISHES_TO, CONSUMED_BY, PRODUCES, CONSUMES, COMPOSES_INTO, REALIZED_BY, PRECEDES, EQUIVALENT_TO

### 查询示例

查找名为 xxx 的函数：

```ngql
MATCH (n:CodeEntity) WHERE n.CodeEntity.name == "xxx"
RETURN n.CodeEntity.name AS name, n.CodeEntity.filePath AS file_path
```

查找函数调用关系（3 跳内）：

```ngql
MATCH (n)-[:CALLS*1..3]->(callee) WHERE n.CodeEntity.name == "xxx"
RETURN callee.CodeEntity.name AS callee_name
```

查找谁调用了 xxx：

```ngql
MATCH (caller:CodeEntity)-[:CALLS]->(callee:CodeEntity)
WHERE callee.CodeEntity.name == "xxx"
RETURN caller.CodeEntity.name AS caller_name
```

查找所有 CALLS 边：

```ngql
MATCH (a:CodeEntity)-[r:CALLS]->(b:CodeEntity)
RETURN a.CodeEntity.name AS caller, b.CodeEntity.name AS callee
```

查找模块包含的实体：

```ngql
MATCH (m:ModuleEntity)-[:CONTAINS]->(c)
RETURN m.ModuleEntity.name AS module, c.CodeEntity.name AS entity
```

统计每种实体类型数量：

```ngql
MATCH (n) RETURN tags(n) AS label, count(*) AS cnt
```

查找处理了数据资产的代码及合规约束：

```ngql
MATCH (c:CodeEntity)-[:PROCESSES_DATA]->(d:DataAsset)-[:GOVERNED_BY]->(ci:ComplianceItem)
WHERE d.DataAsset.name == "xxx"
RETURN c.CodeEntity.name AS code, ci.ComplianceItem.name AS compliance
```
