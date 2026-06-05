# Phase 4 Day 5: 端到端联调 + 体验打磨

> 目标：让整个系统 前端→后端→Agent→Neo4j/ChromaDB 真正跑通，修 bug + 优化体验

## 现状分析

### 各模块状态
| 模块 | 代码 | 测试 | 真实联调 |
|------|------|------|----------|
| 后端 FastAPI (app.py) | ✅ | ✅ | ❓ 未联调 |
| Chat SSE (chat.py) | ✅ | ✅ | ❓ |
| Agent (graph.py) | ✅ | ✅ | ❓ 用 DeepSeek API |
| Tools (8个) | ✅ | ✅ | ❓ 连远程 Neo4j |
| Graph API (graph.py router) | ✅ | ✅ | ❓ |
| Trace API (trace.py router) | ✅ | ✅ | ❓ |
| 前端 ChatView | ✅ | build ✅ | ❓ |
| 前端 GraphView | ✅ | build ✅ | ❓ |
| 前端 TracesView | ✅ | build ✅ | ❓ |

### 已知风险点
1. **Agent LLM 用 DeepSeek API** — `agent_base_url=https://api.deepseek.com`，需确认连通性和延迟
2. **Neo4j 远程连接** — `bolt://<YOUR_SERVER_IP>:7687`，网络延迟+断连风险
3. **ChromaDB 本地** — `.chroma/` 目录，需确认数据是否还在
4. **SSE 流式传输** — 前端 EventSource 连接后端，跨域/断连/超时
5. **前端无 threadId 持久化** — 刷新页面丢失对话历史

---

## Task 1: 后端健康检查 + 服务连通性验证

### 目标
启动后端，验证所有外部服务可达

### 步骤
1. 启动后端 `uv run layerkg web --port 8000`
2. `curl /health` 验证启动
3. `curl /api/graph/stats` 验证 Neo4j 连通
4. `curl /api/trace/list` 验证 Trace API
5. 若 Neo4j 断连 → 修复连接逻辑（超时、重试）
6. 若 Agent LLM 不通 → 检查 .env 配置

### 修复内容（预判）
- `app.py` lifespan 中 Neo4j 连接失败应该优雅降级，不能让启动挂掉
- 添加 `/api/health/detail` 返回各服务状态（Neo4j/ChromaDB/Agent LLM）

---

## Task 2: Chat 端到端联调

### 目标
从前端发消息 → 后端 → Agent → 工具 → 回答，全链路跑通

### 步骤
1. 用 curl 测试 `/api/chat/stream` SSE：
   ```bash
   curl -N -X POST http://localhost:8000/api/chat/stream \
     -H "Content-Type: application/json" \
     -d '{"message": "列出所有模块"}'
   ```
2. 观察事件流：token、tool_start、tool_end、done
3. 记录首个完整对话的延迟
4. 验证 threadId 返回 + Trace 记录

### 修复内容（预判）
- Agent 可能超时（DeepSeek API 延迟高）→ 调整 timeout
- 工具调用失败（Neo4j 查询语法错误）→ 修 Cypher
- SSE 格式问题（前端解析失败）→ 对齐事件格式

---

## Task 3: Graph View 联调

### 目标
图谱可视化页面正确显示 Neo4j 中的节点和边

### 步骤
1. 浏览器访问 `/` → Graph 页面
2. 验证节点按类型着色
3. 测试搜索功能
4. 测试节点点击 → 详情面板
5. 测试类型筛选
6. 测试 center 模式（输入实体名展开）

### 修复内容（预判）
- 大量节点时性能问题 → 限制默认加载数量
- 边标签显示
- 节点详情面板数据格式

---

## Task 4: Traces 页面联调

### 目标
Trace 列表和详情正确展示

### 步骤
1. 先在 Chat 页面发起一次对话（产生 Trace）
2. 访问 /traces 页面 → 验证列表显示
3. 点击某个 Trace → 验证详情页（时间线 + Mermaid）
4. 验证 Mermaid 图正确渲染
5. 验证 ChatView 中 "📊 查看 Trace →" 链接可用

### 修复内容（预判）
- Mermaid 渲染失败（异步加载时序）
- Trace 列表为空（collector 是内存的，重启丢失）

---

## Task 5: 体验打磨

### 目标
修复联调中发现的所有问题 + 优化体验

### 预判优化项
1. **错误提示优化** — 工具调用失败时展示友好提示，不暴露堆栈
2. **加载状态** — Agent 思考时显示 "🤔 思考中..." 动画
3. **空状态** — Graph 页面无数据时的友好提示
4. **响应速度** — Agent 首次响应延迟高，考虑加 LLM streaming 优化
5. **导航一致性** — 各页面间跳转流畅

---

## 执行策略

采用 **Hermes 手动联调 + Claude Code 修 bug** 模式：
1. Hermes 启动服务，手动 curl/浏览器测试，记录问题清单
2. 汇总问题清单后，交给 Claude Code 批量修复
3. 修复后重新验证
4. 重复直到全链路通畅

### 验证标准
- [ ] `/health` 返回 ok
- [ ] `/api/graph/stats` 返回节点/边统计
- [ ] Chat 流式对话成功（至少 1 个完整问答）
- [ ] Agent 正确调用工具（graph_query / semantic_search 等）
- [ ] Trace 列表有数据
- [ ] Trace 详情页展示完整
- [ ] Mermaid 图渲染成功
- [ ] Graph 页面显示节点/边
- [ ] 810+ tests 全部通过
- [ ] npm build 通过
