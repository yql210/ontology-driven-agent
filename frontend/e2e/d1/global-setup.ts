import { execFileSync } from 'node:child_process'
import { unlinkSync, writeFileSync } from 'node:fs'
import type { FullConfig } from '@playwright/test'

const fixtureFile = '.d42-fixture.json'

export default function globalSetup(_config: FullConfig): () => void {
  const output = execFileSync('uv', ['run', 'python', 'scripts/d42_e_setup.py'], { encoding: 'utf8', cwd: '..' })
  const line = output.trim().split('\n').find((entry) => entry.startsWith('D42_FIXTURE='))
  if (!line) throw new Error(`fixture setup did not emit D42_FIXTURE: ${output}`)
  const fixture = JSON.parse(line.slice('D42_FIXTURE='.length)) as Record<string, string>
  for (const [key, value] of Object.entries(fixture)) process.env[`D42_${key}`] = value
  writeFileSync(fixtureFile, JSON.stringify(fixture))

  return (): void => {
    try {
      execFileSync('uv', ['run', 'python', 'scripts/d42_e_cleanup.py'], { encoding: 'utf8', cwd: '..' })
    } finally {
      unlinkSync(fixtureFile, { force: true })
    }
  }
}
