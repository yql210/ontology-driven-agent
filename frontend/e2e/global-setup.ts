import { execFileSync } from 'node:child_process'
import { unlinkSync, writeFileSync } from 'node:fs'
import type { FullConfig } from '@playwright/test'

export default function globalSetup(_config: FullConfig): () => void {
  const output = execFileSync('uv', ['run', 'python', 'scripts/p6_e_setup.py'], { encoding: 'utf8', cwd: '..' })
  const line = output.trim().split('\n').find((entry) => entry.startsWith('P6_E_FIXTURE='))
  if (!line) throw new Error(`fixture setup did not emit P6_E_FIXTURE: ${output}`)
  const fixture = JSON.parse(line.slice('P6_E_FIXTURE='.length)) as Record<string, string>
  for (const [key, value] of Object.entries(fixture)) process.env[`P6_E_${key}`] = value
  writeFileSync('.p6-e-fixture.json', JSON.stringify(fixture))

  return (): void => {
    try {
      execFileSync('uv', ['run', 'python', 'scripts/p6_e_cleanup.py'], { encoding: 'utf8', cwd: '..' })
    } finally {
      unlinkSync('.p6-e-fixture.json', { force: true })
    }
  }
}
