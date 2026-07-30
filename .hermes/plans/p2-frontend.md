# Phase 2 — 前端仓库管理页面 + 跨仓库关系

## Task 1: API 层 (frontend/src/api/repo.ts)

新建 `frontend/src/api/repo.ts`:

```typescript
import { apiClient } from './graph';  // 复用已有 axios 实例

export interface Repository {
  id: string;
  name: string;
  url: string;
  branch: string;
  status: string;
  builtAt?: string;
}

export interface BuildRequest {
  repo_url: string;
  branch?: string;
  repo_id?: string;
  token?: string;
  skip_semantic?: boolean;
  skip_clustering?: boolean;
  clear?: boolean;
}

export interface BuildStatus {
  task_id: string;
  status: string;
  repo_id: string;
  message?: string;
}

export const repoApi = {
  listRepos: () => apiClient.get('/api/repos'),
  registerRepo: (data: { name: string; url: string; branch?: string }) => apiClient.post('/api/repos', data),
  triggerBuild: (data: BuildRequest) => apiClient.post('/api/build', data),
  getBuildStatus: (taskId: string) => apiClient.get(`/api/build/status/${taskId}`),
};
```

## Task 2: 仓库管理页面 (frontend/src/views/RepoView.vue)

新建 Vue 3 `<script setup lang="ts">` 组件：

- 仓库列表表格（name, url, branch, status badge, built_at）
- "添加仓库"按钮 → 弹出表单（repo_url, branch, repo_id, token）
- "构建"按钮 → 触发 POST /api/build → 轮询状态直到完成
- 构建进度显示（pending → cloning → building → success/failed）

布局参考现有 GraphView.vue 的风格（深色主题）。

## Task 3: 路由注册 (frontend/src/router/index.ts)

加 /repos 路由指向 RepoView。

## Task 4: 导航 (frontend/src/App.vue)

在导航栏加"仓库管理"入口。

## 约束

- 使用 Vue 3 Composition API (`<script setup lang="ts">`)
- 复用现有 apiClient（看 frontend/src/api/graph.ts 里的 axios 实例）
- 深色主题，与现有页面风格一致
- 构建状态轮询用 setInterval + clearTimeout（3 秒间隔，最多 100 次）
- npm run build 必须通过（TypeScript 编译无误）
