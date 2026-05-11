# Phase 4 Day 3 修复计划

> 修复反思中发现的 5 个偏差（全部在 `frontend/src/views/GraphView.vue`）

## 修复项

### Fix 1: 着色策略 — data(color) → CSS selector
**文件**: `frontend/src/views/GraphView.vue`

**问题**: L65 使用 `'background-color': 'data(color)'`，设计方案明确要求用 CSS selector 按类型着色。

**修改**: 
1. 删除 node style 中的 `'background-color': 'data(color)'`，改为默认灰色 `'#999'`
2. 新增 6 个 selector 规则，按 `neo4jLabel` 着色
3. 删除 `updateGraph()` 中 `color: typeColors[...]` 的赋值（不再需要 data color）

颜色映射（严格按设计方案）：
```
CodeEntity:    #4A90D9 (蓝)
ConceptEntity: #27AE60 (绿)
ModuleEntity:  #F39C12 (橙)
DocEntity:     #8E44AD (紫)
ResourceEntity:#E91E8C (粉)
ChangeSetEntity:#E74C3C (红)
```

### Fix 2: 搜索高亮（替代过滤）
**文件**: `frontend/src/views/GraphView.vue`

**问题**: 当前搜索是前端过滤（每次输入重渲染整个图），设计方案要求搜索**高亮首个匹配节点**。

**修改**:
1. 删除 `filteredNodes` computed 中的 `searchQuery` 过滤逻辑
2. 新增 `watch(searchQuery)` — 输入时在 cy 实例中搜索匹配节点，选中并高亮首个结果
3. 如果无匹配，用 `cy.elements().unselect()` 取消选择
4. 不再每次搜索都重绘整个图

```typescript
watch(searchQuery, (query) => {
  if (!cy) return
  cy.elements().unselect()
  if (!query) return
  const match = cy.nodes().filter((n) => 
    n.data('name')?.toLowerCase().includes(query.toLowerCase())
  )
  if (match.length > 0) {
    const first = match[0]
    first.select()
    cy.animate({
      center: { eles: first },
      duration: 300,
    })
  }
})
```

### Fix 3: 类型筛选 — 单选 → 多选
**文件**: `frontend/src/views/GraphView.vue`

**问题**: 当前用 `<select>` 单选，设计方案要求多选 checkbox。

**修改**:
1. 将 `<select>` 替换为自定义下拉多选组件
2. 新增 `selectedTypes: ref<string[]>([])` （数组代替字符串）
3. 下拉面板中每种类型一个 checkbox
4. 点击外部关闭下拉
5. `filteredNodes` 只根据 `selectedTypes` 过滤（不再管 searchQuery）

### Fix 4: 边标签显示 + 选中高亮颜色
**文件**: `frontend/src/views/GraphView.vue`

**问题**: 
- 边的 style 没有 `'label': 'data(label)'`，边上看不到关系类型名
- 选中高亮用黑色 `#000`，设计要求金色 `#FFD700`

**修改**:
1. edge style 中添加 `'label': 'data(label)'`
2. `node:selected` 的 `border-color` 改为 `'#FFD700'`

### Fix 5: 双击展开 depth + 统计栏
**文件**: `frontend/src/views/GraphView.vue`, `frontend/src/stores/graph.ts`

**问题**: 
- `expandNode` 用了 depth=1，设计要求 2
- 底部统计栏缺少"当前显示"数量

**修改**:
1. `stores/graph.ts` L61: `depth: 1` → `depth: 2`
2. `GraphView.vue` 统计栏添加 `当前显示: {{ filteredNodes.length }}`

## 不改的文件
- 后端 `graph.py` — 无问题
- `app.py` — 无问题
- `api/graph.ts` — 无问题
- `api/types.ts` — 无问题
- `NodeDetail.vue` — 无问题
- 测试文件 — 无问题（纯前端修改）

## 验证
```bash
cd frontend && npm run build
```
