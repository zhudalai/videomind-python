/**
 * L1 冒烟测试 —— 必跑，快。
 *
 * 验证 5 个新页（分析/健康/设置 + 上传/库 壳）能加载渲染，且其中 Health 与 Settings
 * 真实打到后端 :8002（前端 axios baseURL = VITE_API_BASE=http://localhost:8002/api）。
 *
 * 失败处置：dev server 未起 → 全红；后端不可达 → Health 卡片显 — / Settings「Dev 用户 ID」
 * 不显（fetch 失败），真实 fetch 测试红；其余静态串测试只依赖 dev server 自身渲染。
 */
import { test, expect, type APIRequestContext } from '@playwright/test'
import { probeBackend } from './lib'

const SHELL_ROUTES = ['/upload', '/videos'] as const

test.describe('L1 smoke — 5 页加载 + 真实 fetch', () => {
  test('分析/健康/设置 3 新页壳渲染不崩', async ({ page }) => {
    for (const path of ['/analysis', '/health', '/settings']) {
      await test.step(`访问 ${path}`, async () => {
        await page.goto(path)
        // Layout 侧边栏 banner 始终在（不受所选路由影响）
        await expect(page.getByRole('banner')).toBeVisible({ timeout: 15_000 })
      })
    }
  })

  test('上传/库 页壳渲染不崩', async ({ page }) => {
    for (const path of SHELL_ROUTES) {
      await page.goto(path)
      await expect(page.getByRole('banner')).toBeVisible({ timeout: 15_000 })
    }
  })

  test('Agent 分析页渲染核心区', async ({ page }) => {
    await page.goto('/analysis')
    await expect(page.getByRole('heading', { name: 'Agent 深度分析' })).toBeVisible()
    // CardTitle h3 锚定「分析目标」；label 形式「目标视频」用精确文本（避开 CardDescription 同串）
    await expect(page.getByRole('heading', { name: '分析目标' })).toBeVisible()
    await expect(page.getByText('目标视频', { exact: true })).toBeVisible()
  })

  test('健康监控页渲染四个组件卡 + 时间线区块', async ({ page }) => {
    await page.goto('/health')
    await expect(page.getByRole('heading', { name: '系统健康监控' })).toBeVisible()
    // 四个组件标签 —— 来自静态 COMPONENT_META，不依赖后端
    for (const label of ['PostgreSQL', 'Redis', 'Qdrant', 'MinIO']) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible()
    }
    await expect(page.getByText('可用性时间线')).toBeVisible()
  })

  test('设置页渲染主题三选项 + 偏好区', async ({ page }) => {
    await page.goto('/settings')
    await expect(page.getByRole('heading', { name: '设置' })).toBeVisible()
    // 副标题含「偏好」，用 heading 角色精确锚 CardTitle h3
    await expect(page.getByRole('heading', { name: '偏好' })).toBeVisible()
    for (const opt of ['浅色', '深色', '跟随系统']) {
      // exact 锁定设置页本地的三选一卡；Layout 顶栏全局主题切换 aria-label 含「深色」会子串命中
      await expect(page.getByRole('button', { name: opt, exact: true })).toBeVisible()
    }
  })

  test('健康监控真实 fetch /api/health/ready', async ({ page, request }: { page: import('@playwright/test').Page; request: APIRequestContext }) => {
    test.skip(!(await probeBackend(request)), '后端 :8002 不可达，跳过真实 fetch')
    const respPromise = page.waitForResponse(
      (r) => r.url().includes('/api/health/ready') && r.request().method() === 'GET',
      { timeout: 15_000 },
    )
    await page.goto('/health')
    const resp = await respPromise
    expect(resp.status()).toBe(200)
    const body = await resp.json()
    expect(['UP', 'DOWN']).toContain(body.status)
    expect(body.components).toBeDefined()
    // 不强断全 UP —— 容器可能部分停。只断言四组件字段形状对齐
    for (const k of ['postgres', 'redis', 'qdrant', 'minio']) {
      expect(['UP', 'DOWN']).toContain(body.components[k])
    }
    // UP 时显「连接正常」/ DOWN 时显「无法连接」—— 二选一可见即说明轮询真发生了
    await expect(page.getByText(/连接正常|无法连接/).first()).toBeVisible({ timeout: 15_000 })
  })

  test('设置页真实 fetch /api/user/config 并显示 dev 用户 ID', async ({ page, request }: { page: import('@playwright/test').Page; request: APIRequestContext }) => {
    test.skip(!(await probeBackend(request)), '后端 :8002 不可达，跳过真实 fetch')
    const respPromise = page.waitForResponse(
      (r) => r.url().includes('/api/user/config') && r.request().method() === 'GET',
      { timeout: 15_000 },
    )
    await page.goto('/settings')
    const resp = await respPromise
    expect(resp.status()).toBe(200)
    const body = await resp.json()
    expect(body.user_id).toMatch(/^[0-9a-f-]{36}$/i)
    expect(body.theme).toBeTruthy()
    expect(body.language).toBeTruthy()
    // dev 用户 ID 显示为 code 块
    await expect(page.getByText('Dev 用户 ID')).toBeVisible()
    await expect(page.getByText(body.user_id, { exact: true })).toBeVisible({ timeout: 10_000 })
  })
})
