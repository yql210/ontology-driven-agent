# Phase 4 Day 4 Hotfix 计划

> Hermes 审核 → Claude Code 执行

## 修复项 1: test_trace_failed_status RuntimeWarning

### 问题
`tests/unit/agent/test_graph_trace.py` 的 `test_trace_failed_status` 中，`mock_astream_events` 是 async generator 但直接 `raise ValueError`，导致 `graph.py` 的 `async for event in agent.astream_events(...)` 把异常协程当作非异步调用，产生 `RuntimeWarning: coroutine was never awaited`。

### 根因
`graph.py` 第 143 行 `async for event in agent.astream_events(...)` 中，`agent.astream_events` 是 mock 对象的方法。在 `test_trace_failed_status` 中，`mock_agent = MagicMock()` 且 `mock_agent.astream_events = mock_astream_events`。由于 `mock_astream_events` 是 async generator function，Python 在 MagicMock 赋值后不会自动识别为 async iterable。当它 raise 异常时，async for 拿到的是 coroutine 对象（未被 await），触发 RuntimeWarning。

### 修复方案
在 `test_trace_failed_status` 中，mock_agent 需要显式设置 `astream_events` 为 mock_async_generator：

```python
@pytest.mark.unit
async def test_trace_failed_status(trace_collector: TraceCollector):
    """Test that failed status is set on exception."""

    async def mock_astream_events(*args, **kwargs):
        raise ValueError("Test error")
        yield  # 使其成为 async generator

    with patch("layerkg.agent.graph.create_agent") as mock_create_agent:
        mock_agent = MagicMock()
        mock_agent.astream_events = mock_astream_events
        mock_create_agent.return_value = mock_agent

        events = []
        async for event in run_query_stream(
            "test query",
            thread_id="test-thread",
            trace_collector=trace_collector,
        ):
            events.append(event)

        trace = await trace_collector.get_trace("test-thread")
        assert trace is not None
        assert trace.status == "failed"
```

**关键修改**：在 `raise ValueError("Test error")` 后面加一个 `yield`（永远不可达但使函数成为 async generator）。

### 验证
```bash
uv run pytest tests/unit/agent/test_graph_trace.py::test_trace_failed_status -v -W error::RuntimeWarning
```

---

## 修复项 2: 前端 Mermaid Code-Splitting

### 问题
`frontend/src/views/TraceDetailView.vue` 中 `import('mermaid')` 已经用了动态 import，但 mermaid 仍然被打进主 bundle（1514KB），因为顶层可能有静态引用。

### 分析
查看 TraceDetailView.vue 的 `renderMermaid()` 函数：
```typescript
function renderMermaid() {
  import('mermaid').then((m) => {
    m.default.initialize({ startOnLoad: false, theme: 'dark' })
    m.default.contentLoaded()
  })
}
```
这已经是动态 import，但 Vite 的 rollup 可能因为其他原因没做 code-splitting。需要检查是否还有其他静态 import。

### 修复方案
1. 确认没有其他文件静态 import mermaid
2. 在 `vite.config.ts` 中手动配置 manualChunks 把 mermaid 分离：

```typescript
// frontend/vite.config.ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          mermaid: ['mermaid'],
        },
      },
    },
  },
})
```

### 验证
```bash
cd frontend && npm run build
# 主 bundle 应 < 500KB，mermaid 单独 chunk
```

---

## 执行步骤
1. 修复 `tests/unit/agent/test_graph_trace.py` — 加 yield
2. 创建/修改 `frontend/vite.config.ts` — manualChunks
3. 运行全部测试 `uv run pytest tests/ -q`
4. 运行前端 build `cd frontend && npm run build`
5. 确认无 warning、主 bundle < 500KB
