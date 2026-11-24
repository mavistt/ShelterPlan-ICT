import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  reporter: 'list',
  timeout: 45_000,
  expect: { timeout: 12_000 },
  use: {
    baseURL: process.env.FRONTEND_BASE_URL ?? 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: { channel: 'chrome' },
    ...devices['Desktop Chrome'],
  },
})
