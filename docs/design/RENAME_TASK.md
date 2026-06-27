# 项目改名任务：layerkg → ontoagent

## 改名规则

| 旧名 | 新名 |
|------|------|
| `layerkg` (包名/模块名) | `ontoagent` |
| `layerkg` (CLI 命令) | `ontoagent` |
| `LAYERKG_` (环境变量前缀) | `ONTOAGENT_` |
| `LayerKG` (类名/项目名/显示名) | `OntoAgent` |

## P0：必须改

### 1. 包目录
```bash
mv src/layerkg → src/ontoagent
```

### 2. 全局 import 替换
所有 Python 文件中：
- `import layerkg` → `import ontoagent`
- `from layerkg` → `from ontoagent`
- `"layerkg"` → `"ontoagent"` (在 setup/pyproject 中)

### 3. pyproject.toml
- `name = "layerkg"` → `name = "ontoagent"`
- `[project.scripts] layerkg =` → `ontoagent =`
- 所有 `\b` 边界匹配

### 4. 测试目录
- `tests/` 下所有 import
- conftest.py 中的引用
- pytest 配置中的 --cov=layerkg → --cov=ontoagent

## P1：应该改

### 5. 配置文件
- `.env.example`: 所有 `LAYERKG_*` → `ONTOAGENT_*`
- `config.py` / `settings.py`: 环境变量读取
- `docker-compose.yml`: 服务名 + 环境变量

### 6. 文档文件
- `CLAUDE.md`: 命令示例、项目名、引用
- `README.md`: 项目描述
- `.claude/rules/architecture.md`: 路径引用

## P2：建议改

### 7. 前端
- `frontend/` 中 package.json 项目名（如果有）
- 前端代码中的 API 路径引用

### 8. 设计文档
- `docs/design/` 中引用 `layerkg` 的地方

## 执行要求

1. 先用 `grep -r "layerkg\|LAYERKG\|LayerKG" src/ tests/ .env.example CLAUDE.md README.md pyproject.toml docker-compose.yml frontend/ 2>/dev/null | head -50` 确认范围
2. 分步执行：先 mv 目录 → 全局替换 import → 改 pyproject.toml → 改配置 → 改文档
3. 每步完成后跑 `uv run ruff check src/ tests/` 确认没有语法错误
4. 跑 `uv run pytest tests/unit/ -x -q` 确认最少 10 个测试通过（类型检查会有预存警告）
5. 最终commit

## 不改的

- Neo4j 数据库（标签是 dataclass 类名，不需要改名）
- ChromaDB 数据（$LAYERKG_CHROMA_DIR 改为 $ONTOAGENT_CHROMA_DIR 但数据目录路径可以不变）
- 思源笔记内容（单独处理）
- Gitee 仓库名（用户手动操作）

## ⚠️ 重要

- 这是纯文本替换，不改变任何逻辑
- 用 `rg` 做全局搜索确保没有遗漏
- 改完后 `ruff check` + `pytest unit` 必须通过
