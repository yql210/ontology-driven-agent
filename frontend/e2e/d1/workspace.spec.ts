import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

const fixture = JSON.parse(readFileSync('.d42-fixture.json', 'utf8')) as Record<string, string>
const workspace = fixture.WORKSPACE_ID ?? ''
const generation = fixture.GENERATION_ID ?? ''
const fullPrincipal = fixture.FULL_PRINCIPAL ?? ''
const filteredPrincipal = fixture.FILTERED_PRINCIPAL ?? ''
const signature = 'example.orders.api.OrderService#getOrder(java.lang.String):example.orders.api.OrderSummary'
const query = (principal: string) =>
  `principal=${encodeURIComponent(principal)}&generation_id=${encodeURIComponent(generation)}&page_size=100&node_limit=1000`

test.beforeAll(() => {
  expect(workspace).toBeTruthy()
  expect(generation).toBeTruthy()
  expect(fullPrincipal).toBeTruthy()
  expect(filteredPrincipal).toBeTruthy()
})

test('full principal sees source-pinned Java provider operation and evidence', async ({ page }) => {
  await page.goto(`/workspaces/${workspace}/services?${query(fullPrincipal)}`)
  await expect(page.getByRole('heading', { name: 'Operation directory' })).toBeVisible()

  const providerOperation = page
    .locator('.operation-list article')
    .filter({ hasText: 'sample-order-provider' })
    .filter({ hasText: signature })
  await expect(providerOperation).toHaveCount(1)
  await expect(providerOperation.getByText('repo: sample-order-provider', { exact: true })).toBeVisible()
  await expect(providerOperation.getByText('revision: 7777777777777777777777777777777777777777', { exact: true })).toBeVisible()
  await providerOperation.getByRole('button', { name: 'Providers / consumers' }).click()
  await expect(page.getByText('Providers', { exact: true })).toBeVisible()

  await page.goto(`/workspaces/${workspace}/topology?${query(fullPrincipal)}`)
  await expect(page.getByRole('heading', { name: 'Dependency topology', exact: true })).toBeVisible()
  const providerNode = page.locator('[data-testid="topology-node"]').filter({ hasText: signature })
  await expect(providerNode).toHaveCount(1)
  const evidenceResponse = page.waitForResponse(
    (response) => response.url().includes('/service-graph/evidence?') && response.status() === 200,
  )
  await providerNode.getByRole('button', { name: 'Evidence' }).click()
  const evidence = await evidenceResponse
  await expect(page.locator('.drawer')).toContainText('Evidence')
  await expect(page.locator('.drawer')).toContainText('src/main/java/example/orders/api/OrderService.java')
  await expect(page.locator('.drawer')).toContainText('6666666666666666666666666666666666666666')
  await expect(page.locator('.drawer li')).not.toHaveCount(0)
  const evidencePayload = (await evidence.json()) as { nodes: Array<Record<string, unknown>> }
  expect(
    evidencePayload.nodes.some(
      (node) => node.repo_id === 'sample-order-contract' && node.file_path === 'src/main/java/example/orders/api/OrderService.java',
    ),
  ).toBe(true)
})

test('filtered principal is physically redacted from provider and API details', async ({ page }) => {
  await page.goto(`/workspaces/${workspace}/topology?${query(filteredPrincipal)}`)
  await expect(page.getByText('Visibility: filtered', { exact: false })).toBeVisible()
  const body = page.locator('body')
  await expect(body).not.toContainText('sample-order-provider')
  await expect(body).not.toContainText('sample-order-contract')
  await expect(body).not.toContainText('example.orders.api.OrderService')
  await expect(body).not.toContainText('dubbo-operation:')
  await expect(body).not.toContainText('target_reference')
  await expect(body).not.toContainText('provider_operation_id')
  await expect(body).not.toContainText('provider_endpoint_reference')
})
