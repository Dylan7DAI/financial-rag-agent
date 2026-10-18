import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e', fullyParallel: true, workers: 2,
  use: { baseURL: 'http://127.0.0.1:5174', browserName: 'chromium', channel: process.env.PLAYWRIGHT_CHANNEL || 'chromium', trace: 'retain-on-failure' },
  webServer: [
    { command: 'node tests/fixture-server.js', url: 'http://127.0.0.1:8014/health', reuseExistingServer: false },
    { command: 'npm run dev -- --host 127.0.0.1 --port 5174 --strictPort', url: 'http://127.0.0.1:5174', env: { API_PROXY_TARGET: 'http://127.0.0.1:8014' }, reuseExistingServer: false },
  ],
})
