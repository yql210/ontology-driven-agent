# Ontology-Driven Agent + LayerKG

> **For Hermes:** 使用 claude-code skill 的 print mode (`-p`) 执行任务。每个任务 2-5 分钟粒度。强制 TDD。详细规范见 `.claude/rules/` 目录。

## 项目简介
基于本体驱动的智能开发运维平台。底层 LayerKG 知识图谱引擎（Neo4j + ChromaDB），上层 LangGraph 多Agent编排。

## 技术栈
| 类别 | 技术 | 版本 |
|------|------|------|
| 语言 | Python | 3.13+ |
| 包管理 | uv | 0.11+ |
| AST解析 | tree-sitter | latest |
| 图数据库 | Neo4j | 5.x (bolt://REDACTED_IP:7687) |
| 向量库 | ChromaDB | latest |
| 代码嵌入 | Qwen2.5-0.5B-Coder (Ollama) | http://REDACTED_IP:11434 |
| Agent框架 | LangGraph | Phase 1 |
| CLI | Click | latest |
| 格式化/检查 | ruff | latest |
| 类型检查 | pyright | latest |

## 精确命令（Claude 直接执行）
```bash
# 包管理
uv add <package>              # 添加依赖
uv add --dev <package>        # 添加开发依赖
uv sync                       # 同步环境

# 测试
uv run pytest tests/ -v                           # 全量测试
uv run pytest tests/unit/ -v                      # 只跑单元测试
uv run pytest tests/ -m "not integration" -v      # 跳过集成测试
uv run pytest tests/unit/test_schema.py::test_x -v # 单个测试
uv run pytest tests/ --cov=layerkg --cov-report=term-missing  # 覆盖率

# 格式化与检查
uv run ruff check src/ tests/    # 静态检查
uv run ruff format src/ tests/   # 自动格式化
uv run ruff check --fix src/     # 自动修复

# 类型检查
uv run pyright src/              # 类型检查

# CLI (Phase 0 D5+)
uv run layerkg build ./repo      # 全量构建
uv run layerkg query "foo"       # 查询
uv run layerkg update --since HEAD~1  # 增量更新

# Git
git add -A && git commit -m "type: description"
```

## 项目结构（Phase 0 目标）
```
src/layerkg/
├── __init__.py
├── schema.py           # 6个实体 + 11个关系的 dataclass
├── graph_store.py      # GraphStore 抽象接口
├── neo4j_store.py      # Neo4j 实现
├── chroma_store.py     # ChromaDB 向量存储
├── parser/
│   ├── __init__.py
│   ├── base.py         # 解析器抽象
│   └── python_parser.py # Tree-sitter Python 解析
├── extractor/
│   ├── __init__.py
│   └── relation.py     # 结构关系提取
├── cli.py              # Click CLI 入口
└── config.py           # 配置管理

tests/
├── conftest.py
├── unit/
│   ├── test_schema.py
│   ├── test_graph_store.py
│   └── test_parser.py
└── integration/
    ├── test_neo4j_store.py
    └── test_chroma_store.py
```

## LayerKG Schema 速查
### 6 实体
1. **CodeEntity** — function/class/interface/module/file
2. **ConceptEntity** — business_concept/design_pattern/api_contract/data_model/process
3. **DocEntity** — readme/module_doc/api_doc/comment/wiki/architecture_doc
4. **ResourceEntity** — image/diagram/pdf/config/schema_file/log
5. **ModuleEntity** — 功能模块（聚类结果）
6. **ChangeSetEntity** — 变更集

### 11 关系
- 结构（AST）: calls, extends, implements, imports, contains
- 语义（LLM）: semantic_impact, describes, illustrates, derived_from
- 变更: changed_in, affects

## 详细规范
- `.claude/rules/python.md` — Python 编码规范
- `.claude/rules/testing.md` — 测试 TDD 规范
- `.claude/rules/neo4j.md` — Neo4j + LayerKG Schema 规范

## 外部服务
- Neo4j: `bolt://REDACTED_IP:7687` (neo4j/REDACTED_PASSWORD)
- Ollama: `http://REDACTED_IP:11434` (qwen2.5-coder:0.5b, qwen3.5:9b)
- 思源笔记: `http://REDACTED_IP:REDACTED_PORT`
