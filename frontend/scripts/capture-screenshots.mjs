import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { chromium } from 'playwright'

const baseURL = process.env.FRONTEND_BASE_URL ?? 'http://127.0.0.1:5173'
const outputDir = path.resolve('screenshots')
await mkdir(outputDir, { recursive: true })
const browser = await chromium.launch({ headless: true, channel: 'chrome' })
const report = []

try {
  const page = await browser.newPage()
  const errors = []
  const failedRequests = []
  const assetResponses = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('requestfailed', (request) => failedRequests.push({ url: request.url(), error: request.failure()?.errorText }))
  page.on('response', (response) => { if (response.url().includes('/__a/v2/layers/')) assetResponses.push({ status: response.status(), url: response.url() }) })
  await page.goto(baseURL, { waitUntil: 'domcontentloaded', timeout: 45_000 })
  await page.getByRole('heading', { name: '规划总览' }).waitFor({ state: 'visible' })
  await page.getByText('C 服务在线').waitFor({ state: 'visible', timeout: 20_000 })
  await page.locator('.map-canvas canvas').waitFor({ state: 'visible', timeout: 20_000 })
  await page.waitForFunction(() => document.querySelector('.map-canvas')?.dataset.didFit === 'yes', null, { timeout: 20_000 }).catch(() => {})
  await page.waitForTimeout(1200)

  for (const [width, height] of [[1920, 1080], [1440, 900], [1366, 768]]) {
    await page.setViewportSize({ width, height })
    await page.waitForTimeout(450)
    const layout = await page.evaluate(() => ({
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      viewportHeight: window.innerHeight,
      documentHeight: document.documentElement.scrollHeight,
      mapWidth: Math.round(document.querySelector('.map-shell')?.getBoundingClientRect().width ?? 0),
      mapHeight: Math.round(document.querySelector('.map-shell')?.getBoundingClientRect().height ?? 0),
      agentVisible: Boolean(document.querySelector('.assistant-panel')?.getBoundingClientRect().width),
    }))
    const file = `workspace_${width}x${height}.png`
    await page.screenshot({ path: path.join(outputDir, file), fullPage: false, animations: 'disabled' })
    report.push({ file, layout, noHorizontalOverflow: layout.documentWidth <= width, pageErrors: [...errors], failedRequests: [...failedRequests], assetResponses: [...assetResponses] })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('link', { name: /空间数据/ }).click()
  await page.getByRole('heading', { name: '空间数据' }).waitFor({ state: 'visible' })
  await page.locator('.map-layer-trigger').click()
  const heatResponse = page.waitForResponse((response) => response.url().includes('/__a/v2/layers/shiqi/population_heatmap') && response.status() === 200)
  await page.locator('.layer-option').filter({ hasText: '人口估算热度' }).locator('input').check()
  await heatResponse
  await page.waitForTimeout(900)
  const heatLayout = await page.evaluate(() => ({ viewportWidth: innerWidth, documentWidth: document.documentElement.scrollWidth, viewportHeight: innerHeight, mapWidth: Math.round(document.querySelector('.map-shell')?.getBoundingClientRect().width ?? 0), mapHeight: Math.round(document.querySelector('.map-shell')?.getBoundingClientRect().height ?? 0) }))
  await page.screenshot({ path: path.join(outputDir, 'spatial_heatmap_1440x900.png'), fullPage: false, animations: 'disabled' })
  report.push({ file: 'spatial_heatmap_1440x900.png', layout: heatLayout, noHorizontalOverflow: heatLayout.documentWidth <= heatLayout.viewportWidth, pageErrors: [...errors], failedRequests: [...failedRequests], assetResponses: [...assetResponses] })
  await page.locator('.map-layer-trigger').click()
  for (const regionId of ['east_district', 'xiaolan']) {
    const boundaryResponse = page.waitForResponse((response) => response.url().includes(`/__a/v2/layers/${regionId}/boundary`) && response.status() === 200)
    await page.locator('.region-picker').click()
    await page.locator('.region-menu button').filter({ hasText: regionId }).click()
    await boundaryResponse
    await page.waitForFunction(() => document.querySelector('.map-canvas')?.dataset.didFit === 'yes', null, { timeout: 30_000 })
    await page.waitForFunction(() => {
      const composer = document.querySelector('.chat-composer textarea')
      return composer instanceof HTMLTextAreaElement && !composer.disabled
    }, null, { timeout: 30_000 })
    await page.waitForTimeout(500)
    const layout = await page.evaluate(() => ({ viewportWidth: innerWidth, documentWidth: document.documentElement.scrollWidth, viewportHeight: innerHeight, mapWidth: Math.round(document.querySelector('.map-shell')?.getBoundingClientRect().width ?? 0), mapHeight: Math.round(document.querySelector('.map-shell')?.getBoundingClientRect().height ?? 0) }))
    const file = `spatial_${regionId}_1440x900.png`
    await page.screenshot({ path: path.join(outputDir, file), fullPage: false, animations: 'disabled' })
    report.push({ file, layout, noHorizontalOverflow: layout.documentWidth <= layout.viewportWidth, pageErrors: [...errors], failedRequests: [...failedRequests], assetResponses: [...assetResponses] })
  }
  await writeFile(path.join(outputDir, 'layout-review.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8')
  console.log(JSON.stringify(report, null, 2))
} finally {
  await browser.close()
}
