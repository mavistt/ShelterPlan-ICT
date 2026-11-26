import { test, expect, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

const proofDir = path.resolve('..', 'reports', 'integration', 'eval_run_v1', 'screenshots')

async function openWorkbench(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await expect(page.locator('.intro-backdrop')).toBeVisible({ timeout: 20_000 })
  await page.locator('.intro-skip-btn').click()
  await expect(page.locator('.intro-backdrop')).toHaveCount(0)
  await expect(page.locator('.map-canvas canvas')).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.region-drop-btn strong')).toHaveText('石岐街道')
  await expect(page.getByText('避难场所名录 (25)')).toBeVisible()
  await expect(page.locator('.copilot-composer textarea')).toBeEnabled({ timeout: 30_000 })
}

async function waitForIntroScenarios(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await expect(page.locator('.intro-scenario-btn').first()).toContainText('立即在地图上运行', { timeout: 30_000 })
}

test('loads manifest-verified GIS layers and the frozen evaluation summary', async ({ page }) => {
  const layerFailures: string[] = []
  page.on('response', (response) => {
    if (response.url().includes('/__a/v2/layers/') && response.status() >= 400) {
      layerFailures.push(`${response.status()} ${response.url()}`)
    }
  })
  await openWorkbench(page)
  await page.locator('.eval-launch-btn').click()
  const panel = page.getByRole('dialog', { name: 'ShelterPlan 集成评测概览' })
  await expect(panel).toBeVisible()
  await expect(panel.locator('.eval-kpi-grid strong').first()).toHaveText('6/8')
  await expect(panel.locator('.eval-arm-card')).toHaveCount(4)
  await expect(panel.getByText('30 待审核', { exact: true })).toBeVisible()
  await expect(panel.getByText('VERIFIED 0', { exact: true })).toBeVisible()
  await expect(panel.locator('.eval-note').first()).toContainText('16/24 frozen tasks NOT_RUN')
  expect(layerFailures).toEqual([])
  mkdirSync(proofDir, { recursive: true })
  await page.screenshot({ path: path.join(proofDir, 'integrated-evaluation-panel.png'), animations: 'disabled' })
})

test('runs a real C-to-B assessment from the map-first scenario entry', async ({ page }) => {
  test.setTimeout(150_000)
  await waitForIntroScenarios(page)
  await page.locator('.intro-scenario-btn').first().click()
  await expect(page.locator('.ma-status.status-succeeded').last()).toBeVisible({ timeout: 120_000 })
  await expect(page.locator('.ma-kpi-grid').last()).toBeVisible()
  await expect(page.locator('.ma-verify-row').last()).toContainText('Verifier 通过')
  mkdirSync(proofDir, { recursive: true })
  await page.screenshot({ path: path.join(proofDir, 'shiqi-assessment-result.png'), animations: 'disabled' })
})

test('routes the budget preset to MIP and displays the Greedy baseline', async ({ page }) => {
  test.setTimeout(150_000)
  await waitForIntroScenarios(page)
  await page.locator('.intro-scenario-btn').nth(2).click()
  await expect(page.locator('.ma-status.status-succeeded').last()).toBeVisible({ timeout: 120_000 })
  await expect(page.locator('.ma-task-tag').last()).toContainText('预算优化 (MIP)')
  await expect(page.locator('.greedy-card').last()).toBeVisible()
  await expect(page.locator('.greedy-card').last()).toContainText('Greedy')
  mkdirSync(proofDir, { recursive: true })
  await page.screenshot({ path: path.join(proofDir, 'shiqi-mip-greedy-result.png'), animations: 'disabled' })
})

test('runs East District through C and B, and keeps Xiaolan planning disabled', async ({ page }) => {
  test.setTimeout(180_000)
  await openWorkbench(page)
  await page.locator('.region-drop-btn').click()
  await page.locator('.region-opt-row').filter({ hasText: '东区街道' }).click()
  await expect(page.locator('.region-drop-btn strong')).toHaveText('东区街道')
  await expect(page.getByText('避难场所名录 (31)')).toBeVisible()
  const composer = page.locator('.copilot-composer textarea')
  await expect(composer).toBeEnabled({ timeout: 30_000 })
  await composer.fill('请评估当前区域的避难服务能力，步行阈值为15分钟，人口需求比例为10%。')
  await page.locator('.composer-send-btn').click()
  await expect(page.locator('.ma-status.status-succeeded').last()).toBeVisible({ timeout: 120_000 })
  await expect(page.locator('.ma-kpi-grid').last()).toBeVisible()
  await expect(page.locator('.ma-verify-row').last()).toContainText('Verifier 通过')
  mkdirSync(proofDir, { recursive: true })
  await page.screenshot({ path: path.join(proofDir, 'east-district-assessment-result.png'), animations: 'disabled' })

  await page.locator('.region-drop-btn').click()
  await page.locator('.region-opt-row').filter({ hasText: '小榄镇' }).click()
  await expect(page.locator('.region-drop-btn strong')).toHaveText('小榄镇')
  await expect(page.locator('.region-access-badge')).toContainText('规划已禁用')
  await expect(composer).toBeDisabled()
  await expect(page.locator('.session-pill-banner')).toContainText('规划已禁用')
})
