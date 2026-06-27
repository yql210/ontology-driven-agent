# Phase 3：跨服务桥接 + 异步消息 — 实施计划 V3

> **For Hermes:** 使用 delegate_task 逐任务执行。Phase 1+2 已完成 1271 tests。

**Goal:** 跨服务调用链 + 异步消息桥接。ServiceEntity 加业务字段。

**Architecture:** 3 条新关系 + ServiceEntity 加 2 字段 + 新建 `parsing/extractor/external_calls.py`（parser 已超 800 行不得加方法）+ 2 个 pipeline linker + builder 集成 + migration。

**YAGNI 覆盖清单：**

| 模式 | Python | Java | 提取内容 |
|------|--------|------|---------|
| HTTP 客户端 | `requests.*`, `httpx.AsyncClient.*` | `restTemplate.*`, `webClient.*` | URL → 服务名 |
| MQ Producer | `kafka.send`, `rabbitmq.publish` | `kafkaTemplate.send`, `rabbitTemplate.convertAndSend` | topic 名 |
| MQ Consumer | Phase 1 已有 `@KafkaListener` 等 | Phase 1 已有 | entry_metadata.topic |

**不覆盖:** FeignClient/Dubbo/gRPC/aiohttp/OkHttp（Phase 3.5）。

**当前基线:**
- schema.py 656 行，python_parser.py 864 行（⚠️超红线），java_parser.py 1210 行（⚠️超红线）
- 18 关系，6 Action，9 实体
- ConceptEntity.VALID_ENTITY_TYPES = {business_concept, design_pattern, api_contract, data_model, process}
- ServiceEntity 无 capability_label/team 字段
- builder.py 无 ServiceEntity 处理逻辑

**验收标准：**
```bash
uv run pytest tests/unit/test_schema.py tests/unit/test_schema_extra.py -v  # 全部通过
uv run pytest tests/unit/test_python_parser.py tests/unit/test_java_parser.py -v  # 全部通过
uv run pytest tests/unit/pipeline/test_service_linker.py tests/unit/pipeline/test_topic_linker.py -v  # 全部通过
uv run pytest tests/unit/ -q  # ~1290+ tests
```

---

## Task 1: Schema 层全部改动

**Files:** `src/ontoagent/domain/schema.py`

**1a. ServiceEntity 追加字段**（created_at 之后）:
```python
capability_label: str | None = None  # "支付处理" / "用户认证"
team: str | None = None              # "支付团队"
```

**1b. ConceptEntity.VALID_ENTITY_TYPES 追加** `"message_topic"`

**1c. 注册表更新** — VALID_RELATION_TYPES 追加 3 个，RELATION_TYPE_TO_NEO4J 追加 3 条映射，RELATION_CONSTRAINTS 追加 3 条约束（domain/range）。

**验证:**
```bash
uv run pytest tests/unit/test_schema.py tests/unit/test_schema_extra.py -v -x
```

**Commit:**
```bash
git add src/ontoagent/domain/schema.py
git commit -m "feat(schema): add ServiceEntity business fields, message_topic type, 3 cross-service relations"
```

---

## Task 2: 同步 semantic_linker CONCEPT_ENTITY_TYPES

**Files:** `src/ontoagent/pipeline/semantic_linker.py`

查找 `CONCEPT_ENTITY_TYPES` frozenset，追加 `"message_topic"`。否则 topic_linker 创建的 ConceptEntity 会被 semantic_linker 当作 unknown 跳过。

**验证:**
```bash
uv run pytest tests/unit/pipeline/test_semantic_linker.py -v -x -q
```

**Commit:**
```bash
git add src/ontoagent/pipeline/semantic_linker.py
git commit -m "fix(semantic): add message_topic to CONCEPT_ENTITY_TYPES"
```

---

## Task 3: 新建 external_calls.py（外部调用提取）

**Files:**
- Create: `src/ontoagent/parsing/extractor/external_calls.py`
- Create: `tests/unit/test_external_calls.py`

核心签名：

```python
def extract_external_calls_python(root_node, source: bytes, module_name: str, file_path: str) -> list[ExtractedRelation]:
    """扫描 Python AST，提取 HTTP 客户端调用和 MQ producer 调用。
    返回 calls_service / publishes_to 关系的 ExtractedRelation 列表。"""

def extract_external_calls_java(root_node, source: bytes, file_path: str) -> list[ExtractedRelation]:
    """扫描 Java AST，提取 HTTP 客户端调用和 MQ producer 调用。"""
```

解析策略（YAGNI）:
- Python: 扫描 `call` 节点，attribute 路径匹配 `requests.post/get/put/delete/patch` 和 `httpx.AsyncClient.get/post` → 从第一个 string 参数提取 URL → 域名作为 target_name → calls_service
- Python: `kafka_producer.send("topic")` → target_name=topic → publishes_to
- Java: 扫描 `method_invocation` 节点，匹配 `restTemplate.postForObject/getForObject/exchange` 和 `webClient.post/get/put/delete` → uri()/首个 string 参数 → 域名 → calls_service
- Java: `kafkaTemplate.send("topic")` / `rabbitTemplate.convertAndSend("exchange","routing",msg)` → topic → publishes_to

**验证:**
```bash
uv run pytest tests/unit/test_external_calls.py -v  # ~6 tests (3 Python + 3 Java)
```

**Commit:**
```bash
git add src/ontoagent/parsing/extractor/external_calls.py tests/unit/test_external_calls.py
git commit -m "feat(parser): add external call extraction for cross-service tracing"
```

---

## Task 4: Python parser + Java parser 调用 new extractor

**Files:**
- `src/ontoagent/parsing/parser/python_parser.py` (~5 行改)
- `src/ontoagent/parsing/parser/java_parser.py` (~5 行改)

在每个 parser 的 `parse_source` 末尾（`_walk` 完成后），调用 `extract_external_calls_python/java(root_node, source, ...)` 并把结果追加到 `relations` 列表。

**注意0:** parser 已超 800 行红线，**不往 parser 里加新方法**。只在 parse_source 末尾加一行委托调用来规避架构约束。

**注意1:** incremental_updater(`pipeline/incremental_updater.py:249,339,421`) 内部通过 parser.parse_file() 调用链自动触发 parse_source，**增量更新已自动继承外部调用提取**，无需额外集成。

**验证:**
```bash
uv run pytest tests/unit/test_python_parser.py tests/unit/test_java_parser.py -v -x -q
```

**Commit:**
```bash
git add src/ontoagent/parsing/parser/python_parser.py src/ontoagent/parsing/parser/java_parser.py
git commit -m "feat(parser): delegate external call extraction from parsers"
```

---

## Task 5: Pipeline service_linker + topic_linker

**Files:**
- Create: `src/ontoagent/pipeline/service_linker.py` + `tests/unit/pipeline/test_service_linker.py`
- Create: `src/ontoagent/pipeline/topic_linker.py` + `tests/unit/pipeline/test_topic_linker.py`

**service_linker**: 输入 ExtractedRelation(relation_type=calls_service) → 聚合 ServiceEntity(stable) + Relation(calls_service)

**topic_linker**: 输入 ExtractedRelation(relation_type=publishes_to) + CodeEntity(entry_category=mq_consumer) → 聚合 ConceptEntity(type=message_topic) + Relation(publishes_to/consumed_by)。去重: name + root_id 唯一。

**验证:**
```bash
uv run pytest tests/unit/pipeline/test_service_linker.py tests/unit/pipeline/test_topic_linker.py -v
```

**Commit:**
```bash
git add src/ontoagent/pipeline/service_linker.py src/ontoagent/pipeline/topic_linker.py tests/unit/pipeline/test_service_linker.py tests/unit/pipeline/test_topic_linker.py
git commit -m "feat(pipeline): add service linker and topic linker"
```

---

## Task 6: Builder 集成 + service_entity_to_dict

**Files:**
- `src/ontoagent/pipeline/builder_utils.py` — 新增 `service_entity_to_dict()`
- `src/ontoagent/pipeline/builder.py` — import + 在 Stage 2/2.5 后插入 linker 调用

**builder_utils.py:** 仿 data_asset_to_dict 模式新增:

```python
def service_entity_to_dict(entity: ServiceEntity) -> dict[str, object]:
    d = {"id": entity.id, "name": entity.name, "version": entity.version,
         "status": entity.status, "created_at": entity.created_at}
    if entity.endpoint: d["endpoint"] = entity.endpoint
    if entity.code_entity_id: d["code_entity_id"] = entity.code_entity_id
    if entity.capability_label: d["capability_label"] = entity.capability_label
    if entity.team: d["team"] = entity.team
    return d
```

**builder.py:** 需先读代码确认 Stage 2 后的直接插入点——在 structural write 之前或之后调用 linker，把产出的 entities 和 relations 注入主流程。

**验证:**
```bash
uv run pytest tests/unit/pipeline/ -q  # 全量 pipeline 测试
```

**Commit:**
```bash
git add src/ontoagent/pipeline/builder_utils.py src/ontoagent/pipeline/builder.py
git commit -m "feat(builder): integrate service_linker and topic_linker into build pipeline"
```

---

## Task 7: Migration v1.2.0（有实际价值）

**Files:**
- Create: `src/ontoagent/store/migrations/v1_2_0_add_cross_service_relations.py`
- Modify: `src/ontoagent/store/migrations/registry.py`

**UP:** 为 CALLS_SERVICE/PUBLISHES_TO/CONSUMED_BY 创建关系存在性约束（参照 neo4j 约束规则），注册版本。

**DOWN:** 删除约束。

**验证:**
```bash
uv run pytest tests/unit/test_schema_version.py -v -x
```

**Commit:**
```bash
git add src/ontoagent/store/migrations/v1_2_0_add_cross_service_relations.py src/ontoagent/store/migrations/registry.py
git commit -m "feat(migration): add v1.2.0 migration for cross-service relations"
```

---

## Task 8: 全量回归 + 集成测试

**Files:** `tests/unit/test_phase3_integration.py`

3 个端到端场景:
1. Python HTTP 调用 → calls_service → ServiceEntity 创建
2. MQ consumer + producer → ConceptEntity(message_topic) → publishes_to + consumed_by
3. 多个 producer 同一 topic → 去重验证

**验证:**
```bash
uv run pytest tests/unit/ -x -q  # ~1290+ tests
```

**Commit + Push:**
```bash
git add tests/unit/test_phase3_integration.py
git commit -m "test: add Phase 3 integration tests"
git push
```

---

## 改动量

| 文件 | 新增行 | 类型 |
|------|--------|------|
| `domain/schema.py` | ~50 | 修改 |
| `pipeline/semantic_linker.py` | ~2 | 修改 |
| `parsing/extractor/external_calls.py` | ~120 | **新建** |
| `parsing/parser/python_parser.py` | ~5 | 修改 |
| `parsing/parser/java_parser.py` | ~5 | 修改 |
| `pipeline/service_linker.py` | ~80 | **新建** |
| `pipeline/topic_linker.py` | ~80 | **新建** |
| `pipeline/builder_utils.py` | ~20 | 修改 |
| `pipeline/builder.py` | ~15 | 修改 |
| `store/migrations/v1_2_0...` | ~30 | **新建** |
| `store/migrations/registry.py` | ~3 | 修改 |
| `tests/` (5 新文件) | ~350 | **新建** |
| **合计** | **~760** | |
