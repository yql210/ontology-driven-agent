# Phase 1：接口入口识别 — 实施计划

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.
> **Design doc:** 思源笔记 `LayerKG 代码→业务追溯方案 V2.0（接口驱动）` (ID: `20260627161926-0brm9rb`)

**Goal:** Parser 能识别 HTTP/RPC/定时任务/MQ/事件等业务入口，CodeEntity 加上业务入口字段。

**Architecture:** 不改 entity_type，不改新增实体。给 CodeEntity 加 6 个可选字段，parser 在遍历 AST 时提取装饰器/注解，通过规则表分类为 entry_category，路由路径/cron 等元数据存 entry_metadata JSON。

**Tech Stack:** Python 3.13+ · tree-sitter-python 0.25 · tree-sitter-java 0.23 · pytest · ruff

---

## 前置：确认现有状态

```bash
# 当前 1222 tests pass，ruff clean
uv run pytest tests/unit/ -x -q
uv run ruff check src/ tests/

# 现有测试文件分布（tests/unit/ 根目录，无 parsing/ 子目录）:
#   test_python_parser.py, test_java_parser.py, test_doc_parser.py,
#   test_relation_extractor.py, test_parser_base.py, test_semantic_extractor.py
# 现有 pipeline 测试（tests/unit/pipeline/，无 test_builder_utils.py）:
#   test_builder.py, test_aligner.py, test_change_detector.py 等
```

### 前置验证：Java AST 注解节点类型确认
```bash
# 已通过 tree-sitter 0.23 验证的 AST 结构：
#   marker_annotation → children: [@, identifier]（无 field_name）
#   annotation → children: [@, identifier, annotation_argument_list]
#   annotation_argument_list → children: [element_value_pair, ...] 或 [element_value]
#
# child_by_field_name("name") 对这两种节点返回 None！
# 必须遍历 children 找 identifier 类型节点。见 Task 5 修正后代码。
```

---

### Task 1：CodeEntity 加 6 个业务字段

**Objective:** Schema 层扩展，零逻辑改动。

**Files:**
- Modify: `src/ontoagent/domain/schema.py` — CodeEntity dataclass

**Step 1：添加字段**

在 CodeEntity 现有字段 `created_at` 之后、`VALID_ENTITY_TYPES` 常量之前追加：

```python
# 业务入口标记（Phase 1）
entry_category: str | None = None
# 取值: http_api / rpc_service / scheduled / mq_consumer / event_handler

entry_metadata: str | None = None
# JSON 字符串: {"route": "/api/payment/charge", "method": "POST", "cron": "0 0 * * *", ...}

business_process: str | None = None
# 业务流程分组: "支付扣款" / "用户认证"

business_priority: str | None = None
# P0 / P1 / P2

business_lifecycle: str | None = None
# active / deprecated / sunset

business_owner: str | None = None
# 团队归属: "支付团队"
```

并在 `VALID_ENTITY_TYPES` 常量附近追加类级常量（非 dataclass 字段，与 `VALID_ENTITY_TYPES` 并列）：

```python
VALID_ENTRY_CATEGORIES = frozenset({"http_api", "rpc_service", "scheduled", "mq_consumer", "event_handler"})
```

然后在 `__post_init__` 末尾追加校验（注意 `self.` 前缀）：

```python
if self.entry_category is not None and self.entry_category not in self.VALID_ENTRY_CATEGORIES:
    raise SchemaValidationError(
        f"Invalid entry_category '{self.entry_category}'. "
        f"Must be one of {self.VALID_ENTRY_CATEGORIES} or None"
    )
```

**Step 2：运行 schema 测试**

```bash
uv run pytest tests/unit/test_schema.py tests/unit/test_schema_extra.py -v
# Expected: ~200 tests pass（新增字段有默认值 None，不影响现有构造）
```

**Step 3：Commit**

```bash
git add src/ontoagent/domain/schema.py
git commit -m "feat(schema): add 6 business entry fields to CodeEntity"
```

---

### Task 2：entity_to_dict 序列化新字段

**Objective:** 构建流水线写入 Neo4j 时新字段不丢失。

**Files:**
- Modify: `src/ontoagent/pipeline/builder_utils.py` — `entity_to_dict` 函数

**Step 1：更新 entity_to_dict（条件写入，与现有风格一致）**

在 `entity_to_dict` 返回的 dict 中添加（与现有 `file_path` 一样的条件写入风格）：

```python
if entity.entry_category:
    d["entry_category"] = entity.entry_category
if entity.entry_metadata:
    d["entry_metadata"] = entity.entry_metadata
if entity.business_process:
    d["business_process"] = entity.business_process
if entity.business_priority:
    d["business_priority"] = entity.business_priority
if entity.business_lifecycle:
    d["business_lifecycle"] = entity.business_lifecycle
if entity.business_owner:
    d["business_owner"] = entity.business_owner
```

> 注意：条件写入避免了 Neo4j 节点被 `null` 属性污染，保持 `WHERE entry_category IS NOT NULL` 查询语义正确。

**Step 2：运行 pipeline 测试**

```bash
uv run pytest tests/unit/pipeline/ -v -x
# Expected: 全部通过（无 test_builder_utils.py，但 builder 测试会间接触发序列化路径）
```

**Step 3：Commit**

```bash
git add src/ontoagent/pipeline/builder_utils.py
git commit -m "feat(builder): serialize new business entry fields in entity_to_dict"
```

---

### Task 3：新增入口规则表

**Objective:** 集中管理装饰器/注解名 → entry_category 的映射。

**Files:**
- Create: `src/ontoagent/parsing/extractor/entry_point_rules.py`

**Step 1：写规则表**

```python
"""业务入口点分类规则 — 集中管理装饰器/注解名 → entry_category 映射。"""

# ── Python 装饰器名 → entry_category ─────────────────────────

PY_FRAMEWORK_HTTP_PATTERNS = {
    # FastAPI / Starlette
    ("app", "get"), ("app", "post"), ("app", "put"),
    ("app", "delete"), ("app", "patch"), ("app", "route"),
    ("router", "get"), ("router", "post"), ("router", "put"),
    ("router", "delete"), ("router", "patch"),
    # Flask
    ("app", "route"),
}

PY_STANDALONE_HTTP = set()  # Phase 1 不使用独立动词（误报率太高）

PY_SCHEDULE_NAMES = {
    "scheduled_task", "task", "celery.task",
    "scheduled_job", "app.scheduled",
}

PY_MQ_NAMES = {
    "kafka_handler", "consumer", "rabbitmq.consumer",
    "redis.pubsub", "subscriber",
}

PY_EVENT_NAMES = {
    "event_handler",  # 只有明确的 event_handler 进入此类别
    # 注意: "subscriber" 已移入 PY_MQ_NAMES，避免重复分类死代码
}

# ── 注意：不使用独立 HTTP 动词名（get/post/put/delete/route），
# 必须带框架前缀（app.xxx / router.xxx）才分类为 http_api。
# 裸 @get 和 @post 在 Python 生态中可能来自 ORM/缓存等非 HTTP 框架。

# ── Java 注解名 → entry_category ─────────────────────────────

JAVA_HTTP_ANNOTATIONS = {
    "GetMapping", "PostMapping", "PutMapping", "DeleteMapping",
    "PatchMapping", "RequestMapping", "Path",
}

JAVA_SCHEDULE_ANNOTATIONS = {"Scheduled"}

JAVA_MQ_ANNOTATIONS = {
    "KafkaListener", "RabbitListener", "JmsListener",
    "StreamListener",
}

JAVA_EVENT_ANNOTATIONS = {"EventListener", "TransactionalEventListener"}

JAVA_RPC_ANNOTATIONS = {"WebService", "RpcService"}


# ── 分类函数 ─────────────────────────────────────────────────

def classify_python_decorator(
    attr_path: tuple[str, ...], standalone_name: str
) -> str | None:
    """根据 Python 装饰器属性路径或独立名返回 entry_category。"""
    if any(attr_path[:len(pat)] == pat for pat in PY_FRAMEWORK_HTTP_PATTERNS):
        return "http_api"
    if standalone_name in PY_STANDALONE_HTTP:
        return "http_api"
    if standalone_name in PY_SCHEDULE_NAMES:
        return "scheduled"
    if standalone_name in PY_MQ_NAMES:
        return "mq_consumer"
    if standalone_name in PY_EVENT_NAMES:
        return "event_handler"
    return None


def classify_java_annotation(annotation_name: str) -> str | None:
    """根据 Java 注解名返回 entry_category。"""
    if annotation_name in JAVA_HTTP_ANNOTATIONS:
        return "http_api"
    if annotation_name in JAVA_SCHEDULE_ANNOTATIONS:
        return "scheduled"
    if annotation_name in JAVA_MQ_ANNOTATIONS:
        return "mq_consumer"
    if annotation_name in JAVA_EVENT_ANNOTATIONS:
        return "event_handler"
    if annotation_name in JAVA_RPC_ANNOTATIONS:
        return "rpc_service"
    return None
```

**Step 2：写测试**

Create `tests/unit/test_entry_point_rules.py`（与现有 parsing 测试一致，放在 tests/unit/ 根目录）：

```python
from ontoagent.parsing.extractor.entry_point_rules import (
    classify_python_decorator,
    classify_java_annotation,
)

class TestPythonClassification:
    def test_fastapi_app_post_returns_http_api(self):
        assert classify_python_decorator(("app", "post"), "post") == "http_api"

    def test_flask_app_route_returns_http_api(self):
        assert classify_python_decorator(("app", "route"), "route") == "http_api"

    def test_scheduled_task_returns_scheduled(self):
        assert classify_python_decorator((), "scheduled_task") == "scheduled"

    def test_kafka_handler_returns_mq_consumer(self):
        assert classify_python_decorator((), "kafka_handler") == "mq_consumer"

    def test_event_handler_returns_event_handler(self):
        assert classify_python_decorator((), "event_handler") == "event_handler"

    def test_unknown_returns_none(self):
        assert classify_python_decorator((), "unknown_decorator") is None


class TestJavaClassification:
    def test_get_mapping_returns_http_api(self):
        assert classify_java_annotation("GetMapping") == "http_api"

    def test_post_mapping_returns_http_api(self):
        assert classify_java_annotation("PostMapping") == "http_api"

    def test_scheduled_returns_scheduled(self):
        assert classify_java_annotation("Scheduled") == "scheduled"

    def test_kafka_listener_returns_mq_consumer(self):
        assert classify_java_annotation("KafkaListener") == "mq_consumer"

    def test_event_listener_returns_event_handler(self):
        assert classify_java_annotation("EventListener") == "event_handler"

    def test_unknown_returns_none(self):
        assert classify_java_annotation("Override") is None
```

**Step 3：运行测试**

```bash
uv run pytest tests/unit/test_entry_point_rules.py -v
# Expected: 12 passed
```

**Step 4：Commit**

```bash
git add src/ontoagent/parsing/extractor/entry_point_rules.py tests/unit/test_entry_point_rules.py
git commit -m "feat(parser): add entry point classification rules"
```

---

### Task 4：Python parser 提取装饰器

**Objective:** `python_parser.py` 在解析函数时提取装饰器信息并分类。

**Files:**
- Modify: `src/ontoagent/parsing/parser/python_parser.py`

**Step 1：在 `_walk` 方法中识别 `decorated_definition` 节点**

在 `_walk` 的 `node_type` 分支中，**在** `function_definition` 判断之前插入：

```python
# 装饰过的定义（在 _walk 中 function_definition 判断之前插入）
elif node_type == "decorated_definition":
    decorators_info = self._extract_decorators_from_node(node, source)
    # 找到内层的 function_definition 或 class_definition
    for child in node.children:
        if child.type == "function_definition":
            self._extract_function(
                child, source, file_path, entities, relations,
                module_name, parent_class_name,
                decorators=decorators_info  # 新参数
            )
            return
        elif child.type == "class_definition":
            class_name = self._extract_class(
                child, source, file_path, entities, relations,
                module_name, parent_class_name,
                decorators=decorators_info
            )
            for sub in child.children:
                self._walk(sub, source, file_path, entities, relations,
                          module_name, parent_class_name=class_name)
            return
```

**Step 2：新增 `_extract_decorators_from_node` 方法（~25 行）**

```python
def _extract_decorators_from_node(self, node, source: bytes) -> list[dict]:
    """从 decorated_definition 节点提取所有装饰器信息。"""
    decorators = []
    for child in node.children:
        if child.type != "decorator":
            continue
        info = {"raw": child.text.decode("utf-8", errors="replace")}

        # 提取装饰器名
        name_node = child.children[1] if len(child.children) > 1 else None
        if name_node:
            if name_node.type == "identifier":
                info["name"] = name_node.text.decode()
                info["attr_path"] = ()
            elif name_node.type == "attribute":
                info["attr_path"] = tuple(
                    c.text.decode()
                    for c in name_node.children
                    if c.type == "identifier"
                )
                info["name"] = info["attr_path"][-1] if info["attr_path"] else ""
            elif name_node.type == "call":
                # @app.get("/path") — call 内部第一个是 attribute/identifier
                call_name = name_node.children[0] if name_node.children else None
                if call_name and call_name.type == "attribute":
                    info["attr_path"] = tuple(
                        c.text.decode()
                        for c in call_name.children
                        if c.type == "identifier"
                    )
                    info["name"] = info["attr_path"][-1] if info["attr_path"] else ""
                elif call_name and call_name.type == "identifier":
                    info["name"] = call_name.text.decode()
                    info["attr_path"] = ()
                # 提取调用参数（路由路径）
                info["args"] = self._extract_decorator_args(name_node, source)

        decorators.append(info)
    return decorators
```

**Step 3：新增 `_extract_decorator_args` 方法（~15 行）**

```python
def _extract_decorator_args(self, node, source: bytes) -> list[str]:
    """从 decorator 的 call 节点提取字符串参数。"""
    args = []
    for child in node.children:
        if child.type == "argument_list":
            for arg in child.children:
                if arg.type == "string":
                    text = arg.text.decode("utf-8", errors="replace")
                    args.append(text.strip("\"'"))
    return args
```

**Step 4：修改 `_extract_function` 签名，追加 `decorators` 参数**

```python
def _extract_function(
    self, node, source, file_path, entities, relations,
    module_name, parent_class_name,
    decorators: list[dict] | None = None,  # 新增
) -> None:
```

在创建 CodeEntity 后、`entities.append(entity)` 之前，添加入口分类逻辑：

```python
        # 业务入口分类（Phase 1）
        if decorators:
            for deco in decorators:
                category = classify_python_decorator(
                    deco.get("attr_path", ()),
                    deco.get("name", ""),
                )
                if category:
                    entity.entry_category = category
                    meta = {}
                    if category == "http_api":
                        args = deco.get("args", [])
                        if args:
                            meta["route"] = args[0]
                    entity.entry_metadata = json.dumps(meta) if meta else None
                    break  # 一个函数只取第一个匹配的入口类型
```

**Step 5：同样修改 `_extract_class` 签名，追加 `decorators` 参数**

（类级装饰器如 `@RestController` 虽然不在 Phase 1 重点范围内，但为保持接口一致加参数）

**Step 6：运行 Python parser 测试**

```bash
uv run pytest tests/unit/test_python_parser.py -v
# Expected: 全部通过（现有测试不依赖装饰器）
```

**Step 7：Commit**

```bash
git add src/ontoagent/parsing/parser/python_parser.py
git commit -m "feat(parser): extract decorators in Python parser for entry point classification"
```

---

### Task 5：Java parser 提取注解

**Objective:** `java_parser.py` 在解析方法时提取注解信息并分类。

**Files:**
- Modify: `src/ontoagent/parsing/parser/java_parser.py`

**Step 1：新增 `_extract_annotations` 方法（~30 行）**

```python
def _extract_annotations(self, method_node) -> list[dict]:
    """从 method_declaration 节点提取注解信息。

    注意：tree-sitter Java 0.23 中 marker_annotation/annotation
    的 name 子节点无 field_name 标记，child_by_field_name("name")
    返回 None。必须遍历 children 找 identifier 类型节点。
    """
    annotations = []
    for child in method_node.children:
        if child.type != "modifiers":
            continue
        for mod in child.children:
            if mod.type == "marker_annotation":
                # @Override — 无参数，遍历 children 找 identifier
                for c in mod.children:
                    if c.type == "identifier":
                        annotations.append({
                            "name": c.text.decode("utf-8", errors="replace"),
                            "args": {},
                        })
                        break
            elif mod.type == "annotation":
                # @PostMapping("/path") 或 @Scheduled(cron="0 0 * * *")
                name = None
                for c in mod.children:
                    if c.type == "identifier":
                        name = c.text.decode("utf-8", errors="replace")
                        break
                if name:
                    args = self._extract_annotation_args(mod)
                    annotations.append({"name": name, "args": args})
    return annotations
```

**Step 2：新增 `_extract_annotation_args` 方法（~25 行）**

```python
def _extract_annotation_args(self, annotation_node) -> dict:
    """从 annotation 节点提取参数。

    实际 AST 节点类型（tree-sitter Java 0.23）：
    - annotation_argument_list（不是 "arguments"）
    - element_value_pair（单数，不是 "element_value_pairs"）
    - expression_statement 包裹 string_literal/decimal_integer_literal
    """
    args = {}
    for child in annotation_node.children:
        if child.type == "annotation_argument_list":
            # 区分有参数和无参数形式
            for arg in child.children:
                if arg.type == "element_value_pair":
                    # @Scheduled(cron="...", zone="...")
                    key_node = arg.child_by_field_name("key")
                    val_node = arg.child_by_field_name("value")
                    if key_node and val_node:
                        key = key_node.text.decode()
                        val = self._extract_annotation_value(val_node)
                        args[key] = val
                elif arg.type == "expression_statement":
                    # @PostMapping("/path") — 单值被 expression_statement 包裹
                    inner = arg.children[0] if arg.children else arg
                    if inner.type == "string_literal":
                        val = inner.text.decode("utf-8", errors="replace")
                        args["_value"] = val.strip("\"'")
                elif arg.type == "string_literal":
                    # 直接 string_literal（无 expression_statement 包裹的情况）
                    val = arg.text.decode("utf-8", errors="replace")
                    args["_value"] = val.strip("\"'")
    return args


def _extract_annotation_value(self, node) -> str | None:
    """提取注解参数中的字面值（字符串/数字/null）。"""
    inner = node
    if inner.type == "expression_statement":
        inner = inner.children[0] if inner.children else inner
    if inner.type in ("string_literal", "character_literal"):
        text = inner.text.decode("utf-8", errors="replace")
        return text.strip("\"'")
    if inner.type in ("decimal_integer_literal", "hex_integer_literal"):
        return inner.text.decode()
    if inner.type == "null_literal":
        return None
    return inner.text.decode("utf-8", errors="replace")  # 兜底
```

**Step 3：在 `_extract_method` 末尾加入口分类**

在 `entities.append(entity)` 之前：

```python
        # 业务入口分类（Phase 1）
        annotations = self._extract_annotations(node)
        for anno in annotations:
            category = classify_java_annotation(anno["name"])
            if category:
                entity.entry_category = category
                meta = {}
                if category == "http_api":
                    val = anno["args"].get("_value") or anno["args"].get("value")
                    if val:
                        meta["route"] = val
                    meta["method"] = anno["name"].replace("Mapping", "").upper()
                elif category == "scheduled":
                    cron = anno["args"].get("cron")
                    if cron:
                        meta["cron"] = cron
                elif category == "mq_consumer":
                    topics = anno["args"].get("topics") or anno["args"].get("_value")
                    if topics:
                        meta["topic"] = topics
                entity.entry_metadata = json.dumps(meta) if meta else None
                break
```

**Step 4：类级注解处理（降级为 debug 日志）**

Phase 1 **不**对类级注解（`@RestController`、Dubbo `@Service`）做入口分类。只提取注解信息到 debug 日志供 Phase 2 接续：

```python
# 在 _extract_class 末尾
class_annotations = self._extract_annotations(node)
if class_annotations:
    _logger.debug("Class %s has annotations: %s", class_name, [a["name"] for a in class_annotations])
    # Phase 2: 根据 @RestController → 此类所有 @GetMapping 方法的 route 以类级 @RequestMapping prefix 为基础拼接
```

**Step 5：构造器注解处理（Phase 1 不覆盖）**

`_extract_constructor` 不在此 Phase 添加注解提取。Spring 的 `@Autowired` 构造器本身不是业务入口，`@KafkaListener` 在构造器上的情况极少。Phase 2 再处理。

**Step 6：运行 Java parser 测试**

```bash
uv run pytest tests/unit/test_java_parser.py -v
# Expected: 全部通过
```

**Step 7：Commit**

```bash
git add src/ontoagent/parsing/parser/java_parser.py
git commit -m "feat(parser): extract annotations in Java parser for entry point classification"
```

---

### Task 6：端到端集成测试

**Objective:** 用真实代码片段验证入口识别全链路。

**Files:**
- Create: `tests/unit/test_entry_point_integration.py`（与现有 parsing 测试一致）

**Step 1：写 Python 端到端测试**

```python
import pytest
from pathlib import Path
from ontoagent.parsing.parser.python_parser import PythonParser


@pytest.fixture
def parser():
    return PythonParser()


class TestPythonEntryPointDetection:
    def test_fastapi_post_detected_as_http_api(self, parser):
        source = b'''
from fastapi import FastAPI
app = FastAPI()

@app.post("/api/payment/charge")
def charge(amount: float):
    pass
'''
        result = parser.parse_source(source, "test.py")
        entities = [e for e in result.entities if e.entity_type == "function"]
        assert len(entities) == 1
        func = entities[0]
        assert func.entry_category == "http_api"
        assert "/api/payment/charge" in func.entry_metadata

    def test_flask_route_detected_as_http_api(self, parser):
        source = b'''
from flask import Flask
app = Flask(__name__)

@app.route("/api/login", methods=["POST"])
def login():
    pass
'''
        result = parser.parse_source(source, "test.py")
        funcs = [e for e in result.entities if e.entity_type == "function"]
        assert len(funcs) == 1
        assert funcs[0].entry_category == "http_api"

    def test_scheduled_task_detected(self, parser):
        source = b'''
@scheduled_task
def daily_report():
    pass
'''
        result = parser.parse_source(source, "test.py")
        funcs = [e for e in result.entities if e.entity_type == "function"]
        assert len(funcs) == 1
        assert funcs[0].entry_category == "scheduled"

    def test_plain_function_not_classified(self, parser):
        source = b'''
def helper():
    pass
'''
        result = parser.parse_source(source, "test.py")
        funcs = [e for e in result.entities if e.entity_type == "function"]
        assert len(funcs) == 1
        assert funcs[0].entry_category is None
```

**Step 2：写 Java 端到端测试**

```python
from ontoagent.parsing.parser.java_parser import JavaParser


@pytest.fixture
def java_parser():
    return JavaParser()


class TestJavaEntryPointDetection:
    def test_spring_get_mapping_detected_as_http_api(self, java_parser):
        source = b'''
import org.springframework.web.bind.annotation.*;

@RestController
public class PaymentController {
    @GetMapping("/api/payment/status")
    public String status() {
        return "ok";
    }
}
'''
        result = java_parser.parse_source(source, "Demo.java")
        funcs = [e for e in result.entities if e.entity_type == "function"]
        assert len(funcs) == 1
        assert funcs[0].entry_category == "http_api"
        assert "/api/payment/status" in funcs[0].entry_metadata

    def test_scheduled_detected_with_cron(self, java_parser):
        source = b'''
import org.springframework.scheduling.annotation.Scheduled;

public class Tasks {
    @Scheduled(cron = "0 0 * * *")
    public void dailyJob() {
    }
}
'''
        result = java_parser.parse_source(source, "Demo.java")
        funcs = [e for e in result.entities if e.entity_type == "function"]
        assert len(funcs) == 1
        assert funcs[0].entry_category == "scheduled"
        assert "0 0 * * *" in funcs[0].entry_metadata

    def test_kafka_listener_detected(self, java_parser):
        source = b'''
import org.springframework.kafka.annotation.KafkaListener;

public class Consumers {
    @KafkaListener(topics = "payment-events")
    public void processPayment(String msg) {
    }
}
'''
        result = java_parser.parse_source(source, "Demo.java")
        funcs = [e for e in result.entities if e.entity_type == "function"]
        assert len(funcs) == 1
        assert funcs[0].entry_category == "mq_consumer"

    def test_plain_method_not_classified(self, java_parser):
        source = b'''
public class Util {
    public void helper() {
    }
}
'''
        result = java_parser.parse_source(source, "Demo.java")
        funcs = [e for e in result.entities if e.entity_type == "function"]
        assert len(funcs) == 1
        assert funcs[0].entry_category is None
```

**Step 3：运行集成测试**

```bash
uv run pytest tests/unit/test_entry_point_integration.py -v
# Expected: 8 passed
```

**Step 4：运行全量测试确认无回归**

```bash
uv run pytest tests/unit/ -x -q
# Expected: 1242+ passed (原有 1222 + 12 rules 测试 + 8 集成测试)
```

**Step 5：Commit**

```bash
git add tests/unit/test_entry_point_integration.py
git commit -m "test: add entry point detection integration tests for Python and Java"
```

---

## 最终质量门

```bash
# 全量单元测试
uv run pytest tests/unit/ -v

# 静态检查
uv run ruff check src/ tests/

# 格式化
uv run ruff format src/ tests/ --check
```

---

## 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| `decorated_definition` 嵌套导致重复提取 | 一个函数被提取两次 | `_walk` 中 return 阻止递归子节点 |
| Java annotation 的 arguments 嵌套层级复杂 | args 提取不完整 | `_raw` 兜底字段保留原始文本 |
| Python `@app.get` 因为 `app` 不是标准库导致解析失败 | 装饰器误判 | 装饰器名匹配是纯字符串，不依赖 import 解析 |
| `json` 模块未导入 | java_parser.py / python_parser.py 新增 `import json` | 确认文件头部已有 from __future__ import annotations，追加 `import json` |

---

## 改动量预估

| 文件 | 新增行 | 修改行 | 类型 |
|------|--------|--------|------|
| `domain/schema.py` | ~12 | 0 | 修改 |
| `pipeline/builder_utils.py` | ~10 | 0 | 修改 |
| `parsing/extractor/entry_point_rules.py` | ~80 | 0 | 新建 |
| `parsing/parser/python_parser.py` | ~70 | ~10 | 修改 |
| `parsing/parser/java_parser.py` | ~75 | ~5 | 修改 |
| `tests/unit/test_entry_point_rules.py` | ~60 | 0 | 新建 |
| `tests/unit/test_entry_point_integration.py` | ~110 | 0 | 新建 |
| **合计** | **~417** | **~15** | |

---

## 不在此 Phase 的范围

- ❌ 跨服务调用链桥接（Phase 3）
- ❌ 异步消息 Topic 桥接（Phase 3）
- ❌ DataAsset / ComplianceItem 业务实体（Phase 2）
- ❌ 接口入口的人工标签（business_process 等）— 字段已加但标注工具/UI 在 Phase 2
- ❌ @Scheduled 的 fixedDelay/fixedRate 变体（当前只提取 cron）
- ❌ Dubbo `@Service` 类级注解的 RPC 入口（类级装饰器分类在 Phase 2）
