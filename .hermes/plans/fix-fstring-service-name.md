# Fix: f-string ServiceEntity name extraction

**问题：** `external_calls.py` 只处理 `arg.type == "string"`，遇到 `f"http://..."` 格式的请求 URL 时 tree-sitter 节点类型是 `formatted_string`，被跳过 → ServiceEntity 名称显示 f-string 原文。

**根因位置：**
- `src/ontoagent/parsing/extractor/external_calls.py` 第 36 行和第 79 行

**修复方案：**
1. 添加辅助函数 `_extract_string_from_node(arg_node)` — 处理三种节点类型：
   - `"string"` → 现有逻辑：`arg.text.decode().strip("\"'")`
   - `"formatted_string"` → 提取 `string_content` 子节点的文本拼接。tree-sitter Python 中 `formatted_string` 的子节点：前缀 `f"` 在父节点 text 中，`string_content` 是纯文本段，`interpolation` 是 `{...}` 部分。只需收集 `string_content` 子节点文本。
   - 其他 → 返回 `None`（跳过）
2. 将第 36 行和第 79 行的 `if arg.type == "string":` 替换为使用辅助函数。
3. 类似地处理 MQ topic 的 f-string（第 56 行）和 standalone 调用（第 79 行）。

**验证：**
1. 写单元测试：构造含 f-string URL 的 AST 片段，验证提取结果
2. 跑 demo build：`uv run ontoagent build /opt/data/workspace/demo-service/ --skip-semantic --skip-clustering --clear --verbose-build`
3. 查 Neo4j 确认 ServiceEntity 名称不再含 f-string 语法
4. 全量测试：`uv run pytest tests/ -q`
