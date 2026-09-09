import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e/d1',
  globalSetup: './e2e/d1/global-setup.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 60_000 },
  use: { baseURL: 'http://127.0.0.1:5174', trace: 'retain-on-failure' },
  webServer: [
    {
      command: 'uv run uvicorn ontoagent.api.web.app:create_app --factory --host 127.0.0.1 --port 8012',
      url: 'http://127.0.0.1:8012/openapi.json',
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'ONTOAGENT_WEB_API_URL=http://127.0.0.1:8012 npm run dev -- --host 127.0.0.1 --port 5174',
      url: 'http://127.0.0.1:5174',
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
