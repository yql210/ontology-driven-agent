import { test, expect } from '@playwright/test'

const workspace = process.env.P6_E_WORKSPACE_ID ?? ''
const generation = process.env.P6_E_GENERATION_ID ?? ''
const fromGeneration = process.env.P6_E_FROM_GENERATION_ID ?? ''
const fullPrincipal = process.env.P6_E_FULL_PRINCIPAL ?? 'e2e-full'
const filteredPrincipal = process.env.P6_E_FILTERED_PRINCIPAL ?? 'e2e-filtered'
const query = (principal: string) => `principal=${encodeURIComponent(principal)}&generation_id=${encodeURIComponent(generation)}`

test.beforeAll(() => {
  expect(workspace).toBeTruthy()
  expect(generation).toBeTruthy()
  expect(fromGeneration).toBeTruthy()
})

test('full principal can inspect the published workspace graph', async ({ page }) => {
  await page.goto(`/workspaces/${workspace}?${query(fullPrincipal)}`)
  await expect(page.getByRole('heading', { name: workspace })).toBeVisible()
  const generationMetadata = page
    .locator('section.metadata div')
    .filter({ has: page.getByText('Generation', { exact: true }) })
    .locator('strong')
  await expect(generationMetadata).toHaveText(generation, { exact: true })
  await expect(page.locator('header').getByText('full', { exact: true })).toBeVisible()

  await page.goto(`/workspaces/${workspace}/services?${query(fullPrincipal)}`)
  await expect(page.getByRole('heading', { name: 'Operation directory' })).toBeVisible()
  await expect(page.getByText('repo: provider-orders', { exact: true }).first()).toBeVisible()
  const drilldown = page.locator('button:not([disabled])', { hasText: 'Providers / consumers' }).first()
  await expect(drilldown).toBeVisible()
  await drilldown.click()
  await expect(page.getByText('Providers', { exact: true })).toBeVisible()
  await expect(page.getByText('Consumers', { exact: true })).toBeVisible()

  await page.goto(`/workspaces/${workspace}/topology?${query(fullPrincipal)}`)
  await expect(page.getByRole('heading', { name: 'Dependency topology', exact: true })).toBeVisible()
  await expect(page.getByText('Visible dependencies', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Evidence' }).first().click()
  await expect(page.getByText('Evidence', { exact: true }).last()).toBeVisible()

  await page.goto(`/workspaces/${workspace}/quality?${query(fullPrincipal)}&from_generation=${encodeURIComponent(fromGeneration)}`)
  await expect(page.getByRole('heading', { name: 'Change and quality' })).toBeVisible()
  await expect(page.getByText(fromGeneration, { exact: true })).toBeVisible()
  await expect(page.getByText('Current generation', { exact: true })).toBeVisible()
  const impact = page.getByRole('button', { name: 'Impact' }).first()
  await expect(impact).toBeVisible()
  await impact.click()
  await expect(page.getByText(/Impact:/)).toBeVisible()
})

test('filtered principal only sees its granted repository', async ({ page }) => {
  await page.goto(`/workspaces/${workspace}/services?${query(filteredPrincipal)}`)
  await expect(page.getByText('Visibility: filtered', { exact: true })).toBeVisible()
  await expect(page.getByText('repo: provider-orders', { exact: true })).toHaveCount(0)
  await expect(page.getByText('repo: consumer-checkout', { exact: true }).first()).toBeVisible()
  await expect(page.locator('.operation-list article').filter({ hasText: 'isolated-catalog' })).toHaveCount(0)
})
