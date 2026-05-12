# Phase 4 P1 修复方案

## 问题清单

### P1-1：CORS allow_origins=["*"]
**现状**：`app.py:32` `allow_origins=["*"]`，任何来源都能调 API
**修复**：改为从环境变量 `CORS_ORIGINS` 读取（逗号分隔），默认 `["http://localhost:5173"]`
**文件**：`src/layerkg/web/app.py`
**影响**：无破坏性变更，只需在 `.env` 中配置

### P1-2：ToolCallBlock 不显示工具结果
**现状**：
- 后端 SSE `tool_end` 事件有 `result` 字段
- 前端 `types.ts:33-39` 的 `ToolCall` 接口**没有 result 字段**
- `chat.ts:61-71` 的 `tool_end` handler 只设 `status='completed'`，不存 result
- `ToolCallBlock.vue:17-19` 只展示 `toolCall.args`，不展示 result

**修复**：
1. `types.ts`：ToolCall 接口加 `result?: string`
2. `chat.ts`：`tool_end` handler 存 `event.result` 到 toolCall
3. `ToolCallBlock.vue`：展开时同时显示 args 和 result

### P1-3：Mermaid 渲染时机不可靠
**现状**：
- `TracesView.vue:38-43` 和 `TraceDetailView.vue:62-67`
- `import('mermaid').then(m => m.contentLoaded())` 
- `contentLoaded()` 扫描页面所有 `.mermaid` 元素，但此时 Vue 可能还没渲染 DOM
- 多次打开/关闭 modal 后可能重复渲染

**修复**：
1. 用 `mermaid.render()` 替代 `contentLoaded()`
2. `render(id, code)` 返回 SVG string，手动插入 DOM
3. 使用 `nextTick` 确保 DOM ready
4. 渲染结果缓存（同一 mermaid code 不重复渲染）

## 实施计划

### Task 1：P1-1 CORS 配置化（app.py）
修改 `create_app()` 中 CORS 配置：
```python
import os

def create_app() -> FastAPI:
    cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
```

### Task 2：P1-1 CORS 测试
在 `test_web.py` 追加：
- `test_cors_default_origins` — 默认允许 localhost:5173
- `test_cors_custom_origins` — 环境变量配置生效
- `test_cors_wildcard_blocked` — 默认配置不允许任意来源

### Task 3：P1-2 ToolCall 接口加 result 字段
修改 `frontend/src/api/types.ts`：
```typescript
export interface ToolCall {
  id: string
  tool: string
  args?: Record<string, unknown>
  result?: string  // ← 新增
  status: 'running' | 'completed' | 'failed'
  error?: string
}
```

### Task 4：P1-2 chat store 存 tool_end result
修改 `frontend/src/stores/chat.ts` 的 `tool_end` handler：
```typescript
case 'tool_end': {
  const calls = lastMsg.toolCalls
  if (calls) {
    for (let i = calls.length - 1; i >= 0; i--) {
      if (calls[i].tool === event.tool && calls[i].status === 'running') {
        calls[i].status = 'completed'
        if (event.result) calls[i].result = event.result  // ← 新增
        break
      }
    }
  }
  break
}
```

### Task 5：P1-2 ToolCallBlock 显示 result
修改 `frontend/src/components/ToolCallBlock.vue`：
- args 和 result 分两个折叠区域
- result 用不同底色区分

### Task 6：P1-3 Mermaid 渲染改为 render() API
修改 `TracesView.vue` 和 `TraceDetailView.vue`：
- 用 `ref<string>` 存渲染后的 SVG
- `nextTick` 后调 `mermaid.render(id, code)`
- 用 `v-html` 插入 SVG

### Task 7：前端构建验证 + 后端测试
1. `cd frontend && npm run build` 确认无编译错误
2. `uv run pytest tests/ -v` 确认后端测试全通过
3. `uv run ruff check src/ tests/`
