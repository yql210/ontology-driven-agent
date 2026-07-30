<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { repoApi } from '../api/repo'
import type { BuildRequest, Repository } from '../api/types'

const POLL_INTERVAL_MS = 3000
const MAX_POLLS = 100

interface ActiveBuild {
  taskId: string
  repoId: string
  status: string
  message: string
  attempts: number
  timer: ReturnType<typeof setInterval> | null
}

interface AddFormState {
  repo_url: string
  branch: string
  repo_id: string
  token: string
  skip_semantic: boolean
  skip_clustering: boolean
  clear: boolean
}

const repos = ref<Repository[]>([])
const loading = ref(true)
const pageError = ref('')

const showAddModal = ref(false)
const formError = ref('')
const submitting = ref(false)
const addForm = ref<AddFormState>({
  repo_url: '',
  branch: 'main',
  repo_id: '',
  token: '',
  skip_semantic: false,
  skip_clustering: false,
  clear: false,
})

const activeBuilds = ref<Record<string, ActiveBuild>>({})

const activeBuildByRepoId = computed(() => {
  const map: Record<string, ActiveBuild> = {}
  for (const b of Object.values(activeBuilds.value)) {
    map[b.repoId] = b
  }
  return map
})

async function loadRepos() {
  loading.value = true
  pageError.value = ''
  try {
    repos.value = await repoApi.listRepos()
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function openAddModal() {
  addForm.value = {
    repo_url: '',
    branch: 'main',
    repo_id: '',
    token: '',
    skip_semantic: false,
    skip_clustering: false,
    clear: false,
  }
  formError.value = ''
  showAddModal.value = true
}

function deriveName(url: string): string {
  const cleaned = url.replace(/\/+$/, '').replace(/\.git$/, '')
  const seg = cleaned.split('/').pop() ?? ''
  return seg.length > 0 ? seg : 'default'
}

async function submitAddForm() {
  if (!addForm.value.repo_url.trim()) {
    formError.value = 'repo_url 不能为空'
    return
  }
  formError.value = ''
  submitting.value = true
  const req: BuildRequest = {
    repo_url: addForm.value.repo_url.trim(),
    branch: addForm.value.branch.trim() || undefined,
    repo_id: addForm.value.repo_id.trim() || undefined,
    token: addForm.value.token.trim() || undefined,
    skip_semantic: addForm.value.skip_semantic,
    skip_clustering: addForm.value.skip_clustering,
    clear: addForm.value.clear,
  }
  const repoId = req.repo_id ?? deriveName(req.repo_url)
  try {
    const trigger = await repoApi.triggerBuild(req)
    showAddModal.value = false
    startPolling(trigger.task_id, repoId)
  } catch (e) {
    formError.value = e instanceof Error ? e.message : String(e)
  } finally {
    submitting.value = false
  }
}

async function buildExisting(repo: Repository) {
  if (activeBuildByRepoId.value[repo.id]) return
  const req: BuildRequest = {
    repo_url: repo.url,
    branch: repo.branch ?? 'main',
    repo_id: repo.id,
  }
  try {
    const trigger = await repoApi.triggerBuild(req)
    startPolling(trigger.task_id, repo.id)
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : String(e)
  }
}

function startPolling(taskId: string, repoId: string) {
  activeBuilds.value[taskId] = {
    taskId,
    repoId,
    status: 'pending',
    message: '',
    attempts: 0,
    timer: null,
  }
  setRepoStatus(repoId, 'pending')

  const poll = async () => {
    const build = activeBuilds.value[taskId]
    if (!build) return
    build.attempts += 1

    try {
      const s = await repoApi.getBuildStatus(taskId)
      build.status = s.status
      build.message = s.message ?? ''
      setRepoStatus(repoId, s.status)

      if (s.status === 'success' || s.status === 'failed') {
        stopPolling(taskId)
        if (s.status === 'success') {
          await loadRepos()
        }
        return
      }
    } catch (e) {
      build.message = e instanceof Error ? e.message : String(e)
    }

    if (build.attempts >= MAX_POLLS) {
      build.message = `轮询超时（${MAX_POLLS} 次）`
      build.status = 'failed'
      stopPolling(taskId)
    }
  }

  void poll()
  activeBuilds.value[taskId]!.timer = setInterval(poll, POLL_INTERVAL_MS)
}

function stopPolling(taskId: string) {
  const build = activeBuilds.value[taskId]
  if (!build) return
  if (build.timer !== null) {
    clearInterval(build.timer)
    build.timer = null
  }
  setTimeout(() => {
    delete activeBuilds.value[taskId]
  }, 5000)
}

function setRepoStatus(repoId: string, status: string) {
  const r = repos.value.find((x) => x.id === repoId)
  if (r) r.status = status
}

function statusBadge(status: string): { cls: string; label: string } {
  switch (status) {
    case 'success':
      return { cls: 'badge-success', label: '✅ 成功' }
    case 'building':
      return { cls: 'badge-running', label: '🔨 构建中' }
    case 'cloning':
      return { cls: 'badge-running', label: '⬇️ 克隆中' }
    case 'pending':
      return { cls: 'badge-pending', label: '⏳ 等待' }
    case 'failed':
      return { cls: 'badge-error', label: '❌ 失败' }
    default:
      return { cls: 'badge-default', label: status || '-' }
  }
}

onMounted(() => {
  loadRepos()
})

onUnmounted(() => {
  for (const taskId of Object.keys(activeBuilds.value)) {
    const b = activeBuilds.value[taskId]
    if (b && b.timer !== null) clearInterval(b.timer)
  }
})
</script>

<template>
  <div class="repo-view">
    <header class="repo-header">
      <h1>📦 仓库管理</h1>
      <div class="header-actions">
        <button class="btn-refresh" :disabled="loading" @click="loadRepos">刷新</button>
        <button class="btn-add" @click="openAddModal">+ 添加仓库</button>
      </div>
    </header>

    <div v-if="loading" class="loading">加载中...</div>

    <div v-else-if="pageError" class="error-banner">⚠️ {{ pageError }}</div>

    <div v-else-if="repos.length === 0" class="empty-state">
      <p>暂无已注册仓库</p>
      <button class="btn-add" @click="openAddModal">+ 添加仓库</button>
    </div>

    <table v-else class="repo-table">
      <thead>
        <tr>
          <th>名称</th>
          <th>URL</th>
          <th>分支</th>
          <th>状态</th>
          <th>构建时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="repo in repos" :key="repo.id" class="repo-row">
          <td class="name-cell">{{ repo.name }}</td>
          <td class="url-cell" :title="repo.url">{{ repo.url || '-' }}</td>
          <td>{{ repo.branch || '-' }}</td>
          <td>
            <span :class="['badge', statusBadge(repo.status).cls]">
              {{ statusBadge(repo.status).label }}
            </span>
            <div v-if="activeBuildByRepoId[repo.id]?.message" class="build-message">
              {{ activeBuildByRepoId[repo.id]?.message }}
            </div>
          </td>
          <td class="time-cell">{{ repo.builtAt ?? '-' }}</td>
          <td>
            <button
              class="btn-build"
              :disabled="!!activeBuildByRepoId[repo.id]"
              @click="buildExisting(repo)"
            >
              构建
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <Teleport to="body">
      <div v-if="showAddModal" class="modal-overlay" @click.self="showAddModal = false">
        <div class="modal-content">
          <header class="modal-header">
            <h2>添加仓库并构建</h2>
            <button class="close-btn" @click="showAddModal = false">&times;</button>
          </header>
          <div class="modal-body">
            <div class="form-group">
              <label>Repo URL <span class="required">*</span></label>
              <input
                v-model="addForm.repo_url"
                type="text"
                placeholder="https://github.com/org/repo.git 或 ./local/path"
              />
            </div>
            <div class="form-row">
              <div class="form-group">
                <label>分支</label>
                <input v-model="addForm.branch" type="text" placeholder="main" />
              </div>
              <div class="form-group">
                <label>Repo ID（可空）</label>
                <input v-model="addForm.repo_id" type="text" placeholder="自动从 URL 推导" />
              </div>
            </div>
            <div class="form-group">
              <label>Token（私有仓库）</label>
              <input v-model="addForm.token" type="password" placeholder="github_pat_..." />
            </div>
            <div class="form-group form-checkboxes">
              <label class="checkbox">
                <input v-model="addForm.skip_semantic" type="checkbox" /> 跳过语义
              </label>
              <label class="checkbox">
                <input v-model="addForm.skip_clustering" type="checkbox" /> 跳过聚类
              </label>
              <label class="checkbox">
                <input v-model="addForm.clear" type="checkbox" /> 清空图库
              </label>
            </div>
            <div v-if="formError" class="error-banner">⚠️ {{ formError }}</div>
          </div>
          <footer class="modal-footer">
            <button class="btn-cancel" :disabled="submitting" @click="showAddModal = false">取消</button>
            <button class="btn-submit" :disabled="submitting" @click="submitAddForm">
              {{ submitting ? '提交中...' : '触发构建' }}
            </button>
          </footer>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.repo-view {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
  animation: slide-up 0.3s ease-out;
}

.repo-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.repo-header h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  background: linear-gradient(135deg, #a78bfa, #60a5fa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.btn-refresh,
.btn-add,
.btn-build,
.btn-cancel,
.btn-submit {
  padding: 8px 16px;
  border-radius: var(--radius-pill);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  font-family: var(--font-sans);
  transition: box-shadow var(--transition-fast), transform var(--transition-fast),
    color var(--transition-fast), border-color var(--transition-fast),
    background var(--transition-fast);
}

.btn-refresh {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  border: 1px solid var(--border-dim);
}
.btn-refresh:hover:not(:disabled) {
  color: var(--text-primary);
  border-color: var(--border-default);
  background: rgba(51, 65, 85, 0.8);
}

.btn-add {
  background: linear-gradient(135deg, #8b5cf6, #3b82f6);
  color: #fff;
  border: none;
  position: relative;
  overflow: hidden;
}
.btn-add::after {
  content: '';
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.15), transparent);
  animation: shimmer 3s ease-in-out infinite;
}
.btn-add:hover {
  box-shadow: var(--glow-md);
  transform: translateY(-1px);
}

.btn-build {
  padding: 6px 14px;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--border-default);
  font-size: 13px;
}
.btn-build:hover:not(:disabled) {
  border-color: var(--primary);
  box-shadow: 0 0 8px rgba(139, 92, 246, 0.2);
}
.btn-build:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.loading,
.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--text-muted);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.error-banner {
  background: rgba(248, 113, 113, 0.1);
  border: 1px solid rgba(248, 113, 113, 0.3);
  color: #f87171;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  font-size: 13px;
  margin-bottom: 12px;
}

.repo-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0 8px;
}

.repo-table th {
  padding: 12px 16px;
  text-align: left;
  color: var(--text-muted);
  font-weight: 500;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.repo-table td {
  padding: 16px;
  background: var(--bg-card);
  border-top: 1px solid var(--border-dim);
  border-bottom: 1px solid var(--border-dim);
  position: relative;
  font-size: 14px;
}
.repo-table td:first-child {
  border-left: 1px solid var(--border-dim);
  border-radius: var(--radius-md) 0 0 var(--radius-md);
}
.repo-table td:last-child {
  border-right: 1px solid var(--border-dim);
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
}

.repo-row {
  transition: all var(--transition-normal);
}
.repo-row td {
  transition: border-color var(--transition-normal), background var(--transition-normal);
}
.repo-row:hover td {
  border-color: var(--border-default);
  background: rgba(30, 41, 59, 0.95);
}
.repo-row td:first-child::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: linear-gradient(180deg, #8b5cf6, #3b82f6);
  opacity: 0;
  border-radius: var(--radius-md) 0 0 var(--radius-md);
  transition: opacity var(--transition-normal);
  box-shadow: 0 0 8px rgba(139, 92, 246, 0.3);
}
.repo-row:hover td:first-child::before {
  opacity: 1;
}

.name-cell {
  color: var(--text-primary);
  font-weight: 500;
}

.url-cell {
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 13px;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.time-cell {
  color: var(--text-muted);
  font-size: 13px;
}

.badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  font-weight: 500;
}

.badge-success {
  background: rgba(52, 211, 153, 0.15);
  color: #34d399;
  box-shadow: 0 0 6px rgba(52, 211, 153, 0.15);
}

.badge-running {
  background: rgba(251, 191, 36, 0.15);
  color: #fbbf24;
  box-shadow: 0 0 6px rgba(251, 191, 36, 0.15);
  animation: pulse-glow 2s ease-in-out infinite;
}

.badge-pending {
  background: rgba(148, 163, 184, 0.12);
  color: var(--text-secondary);
}

.badge-error {
  background: rgba(248, 113, 113, 0.15);
  color: #f87171;
  box-shadow: 0 0 6px rgba(248, 113, 113, 0.15);
}

.badge-default {
  background: rgba(148, 163, 184, 0.15);
  color: var(--text-secondary);
}

.build-message {
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-muted);
  font-family: var(--font-mono);
}

/* Modal */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: var(--bg-secondary);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  width: 90%;
  max-width: 560px;
  max-height: 85vh;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  animation: slide-up 0.3s var(--ease-spring);
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-dim);
}

.modal-header h2 {
  margin: 0;
  color: var(--text-primary);
  font-size: 16px;
  font-weight: 600;
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 24px;
  cursor: pointer;
  padding: 0;
  line-height: 1;
  transition: color var(--transition-fast);
}
.close-btn:hover {
  color: var(--text-primary);
}

.modal-body {
  padding: 20px;
  overflow: auto;
  max-height: calc(85vh - 130px);
  background: var(--bg-primary);
}

.form-group {
  margin-bottom: 16px;
}

.form-group label {
  display: block;
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 6px;
  font-weight: 500;
}

.required {
  color: #f87171;
}

.form-group input[type='text'],
.form-group input[type='password'] {
  width: 100%;
  padding: 8px 12px;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--border-dim);
  border-radius: var(--radius-sm);
  font-size: 14px;
  font-family: var(--font-sans);
  outline: none;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}
.form-group input:focus {
  border-color: transparent;
  background-image: linear-gradient(var(--bg-tertiary), var(--bg-tertiary)),
    linear-gradient(135deg, #8b5cf6, #3b82f6);
  background-origin: border-box;
  background-clip: padding-box, border-box;
  box-shadow: 0 0 12px rgba(139, 92, 246, 0.15);
}

.form-row {
  display: flex;
  gap: 12px;
}
.form-row .form-group {
  flex: 1;
}

.form-checkboxes {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.checkbox {
  display: flex !important;
  align-items: center;
  font-size: 13px !important;
  cursor: pointer;
}
.checkbox input {
  margin-right: 6px;
  accent-color: var(--primary);
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid var(--border-dim);
  background: var(--bg-primary);
}

.btn-cancel {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  border: 1px solid var(--border-dim);
}
.btn-cancel:hover:not(:disabled) {
  color: var(--text-primary);
  border-color: var(--border-default);
}

.btn-submit {
  background: linear-gradient(135deg, #8b5cf6, #3b82f6);
  color: #fff;
  border: none;
}
.btn-submit:hover:not(:disabled) {
  box-shadow: var(--glow-md);
  transform: translateY(-1px);
}
.btn-submit:disabled,
.btn-cancel:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
