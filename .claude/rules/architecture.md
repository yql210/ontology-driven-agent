# 架构规范

## 适用范围
所有 `src/layerkg/` 目录下的新增文件和模块改动。

## 分层结构（严格单向依赖）

```
api/          ← 入口层（cli, mcp_server, web）
  ↓
agent/        ← LangGraph 编排
butler/       ← 事件驱动自动化
  ↓
pipeline/     ← 构建管道（builder, incremental_updater, change_detector...）
execution/    ← 执行可靠性（action, saga, circuit_breaker, functions, connectors）
  ↓
parsing/      ← 解析与提取（parser, extractor）
store/        ← 存储适配器（graph_store, neo4j_store, chroma_store, migrations）
  ↓
domain/       ← 领域模型（schema, exceptions, provenance）— 最内层，零内部依赖
```

**依赖方向只能从上往下，禁止反向和跨层。** 例如 `domain/` 不得 import `store/`，`store/` 不得 import `pipeline/`。

## 文件放置规则

### 根目录文件上限：5 个
`src/layerkg/` 根目录只允许放置以下文件：
- `__init__.py`
- `config.py`
- 其他真正的横切基础设施

超过 5 个时，必须引入新的子包。**禁止在根目录直接新增业务模块。**

### 新文件归属判断

新增 `.py` 文件时，按以下顺序判断归属：

| 文件性质 | 放入目录 | 示例 |
|---------|---------|------|
| 实体定义、异常、值对象 | `domain/` | schema, exceptions, provenance |
| 数据库/向量库适配器 | `store/` | graph_store, neo4j_store |
| 解析器、提取器 | `parsing/` | python_parser, relation |
| 构建/更新/检测管道 | `pipeline/` | builder, incremental_updater |
| Action/Function/可靠性机制 | `execution/` | action_executor, saga |
| CLI/MCP/Web 入口 | `api/` | cli, mcp_server |
| LangGraph 编排 | `agent/` | graph, tools, prompt |
| 事件驱动/自动化 | `butler/` | engine, event_bus |

### 子目录内部文件上限：15 个
任何子包内 `.py` 文件超过 15 个时，考虑按子领域进一步拆分。

## 文件行数约束

| 行数 | 状态 | 要求 |
|------|------|------|
| < 300 | ✅ 健康 | 无特殊要求 |
| 300-500 | ⚠️ 关注 | 新增功能时考虑是否应提取到新文件 |
| 500-800 | 🔴 警告 | 下一次迭代必须拆分，提取独立职责到新模块 |
| > 800 | 🚨 紧急 | 立即拆分，禁止继续往里加方法 |

拆分方式：将内聚的私有方法组提取为模块级函数（参考 `builder_utils.py`、`semantic_linker.py`），原类保留方法签名改为委托调用。

## 避免重复代码

同一段逻辑（>10 行）出现在两个文件中时，必须提取为共享函数放到合适的工具模块中。例如 `entity_to_dict` 同时被 `builder.py` 和 `incremental_updater.py` 使用，应放在 `pipeline/builder_utils.py`。

## 测试镜像规则

`tests/unit/` 的子目录结构应与 `src/layerkg/` 对应：

```
tests/unit/
├── agent/       ← 测试 agent/
├── butler/      ← 测试 butler/
├── execution/   ← 测试 execution/
├── pipeline/    ← 测试 pipeline/
├── web/         ← 测试 api/web/
├── store/       ← 测试 store/（如有足够文件）
└── *.py         ← domain/parsing 等小模块测试可留在根目录
```

小型模块（< 3 个测试文件）的测试可以留在 `tests/unit/` 根目录。

## 何时引入新子包

当满足以下任一条件时，应创建新的子包：

1. 某类文件数量 ≥ 5 个，且它们有明确的共同领域
2. 新增模块与现有所有子包都不属于同一层
3. 多个文件之间有强内聚关系（频繁互相 import），形成了一个"子系统"

## 违规检查

以下情况视为架构违规：

- ❌ 在 `src/layerkg/` 根目录直接新增业务模块（非 config/__init__）
- ❌ `domain/` 中的文件 import 了 `store/` 或更上层的模块
- ❌ 单文件超过 800 行仍在继续添加方法
- ❌ 同一段 >10 行逻辑在多个文件中重复出现
- ❌ 新增文件没有放入任何子包，而是散落在不相关的目录中
