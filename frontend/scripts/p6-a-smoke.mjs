import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const api = await readFile(new URL('../src/api/workspace.ts', import.meta.url), 'utf8')
const view = await readFile(new URL('../src/views/WorkspaceOverviewView.vue', import.meta.url), 'utf8')

assert.match(api, /service-graph\/directory/)
assert.match(api, /encodeURIComponent\(workspaceId\)/)
assert.match(api, /X-Workspace-Principal': principal/)
for (const field of ['generation_id', 'repo_id', 'page_size', 'depth', 'node_limit', 'cursor']) {
  assert.match(api, new RegExp(`Object\.entries\(params\).*${field}|${field}`))
}
assert.match(api, /value !== undefined && value !== ''/)
assert.match(view, /fetchWorkspaceServiceGraphDirectory\(workspaceId\.value, principal\.value, requestParams\.value\)/)
assert.match(view, /watch\(\[workspaceId, principal, requestParams\], load, \{ immediate: true \}\)/)
assert.match(view, /to="\/graph"/)
assert.match(view, /to="\/repos"/)
for (const field of ['source_revision', 'sourceRevision', 'revision']) {
  assert.match(view, new RegExp(`['"]${field}['"]`))
}
assert.doesNotMatch(view, /source_revisions|commit|git_sha|latest_revision/)

console.log('P6-A frontend source smoke passed')
