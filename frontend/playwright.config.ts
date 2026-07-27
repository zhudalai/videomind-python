import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright E2E 配置 —— 分三层：
 *   L1 smoke         5 个新页加载 + Health/Settings 真实 fetch（必跑，快）
 *   L2 api-contract  9 个新端点契约固化（必跑，快，纯 request）
 *   L3 full-chain    上传→SSE 进度→详情→问答（守门：VM_E2E_FULL=1 且就绪视频存在，默认 skip）
 *
 * 前端 dev server（vite，:4000）与后端（uvicorn，:8002）需在跑。
 * webServer reuseExistingServer 复用已运行的 vite：若 4000 已可达则不重启，
 * 否则本地 `npm run dev` 拉起（vite port 4000）。
 * 后端不托管 —— L3 守门若后端不在则跳过。
 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['list']],
  timeout: 60_000,
  expect: { timeout: 10_000 },

  use: {
    baseURL: 'http://localhost:4000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:4000',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
