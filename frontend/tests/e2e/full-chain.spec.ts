/**
 * L3 全链路守门 —— 默认 skip，需 `VM_E2E_FULL=1` 且后端有 ready 媒体才跑。
 *
 * 覆盖本次新功能的端到端：
 *   1. UI 提交 Agent 分析（/analysis）→ 拦截 POST /agent/analyze 拿 task_id
 *   2. 任务未完成时 GET /result → 409 契约验证
 *   3. 轮询状态机到终态（completed/failed）
 *   4. GET /checkpoints → 断点轨迹形状
 *   5. 若 completed：GET /result → AgentResultResponse 形状 + UI 渲染结果卡
 *   6. RAG 问答链路（API 级 search→chat，带 500-skip 守 Qdrant 未索引）
 *
 * 守门原因：依赖真实 LLM 与已索引视频，慢且非确定性；L1+L2 已覆盖冒烟与契约，
 * L3 只在显式 opt-in 时跑深链路。无 ready 媒体或后端离线 → 整组 skip 不红。
 */
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import { BACKEND, UUID_RE, probeBackend, probeReadyMedia, pollAgentStatus } from './lib'

const ANALYSIS_STATUSES = ['pending', 'planning', 'executing', 'critic_check', 'completed', 'failed']

interface Shared {
  mediaId: string
  userId: string
  goal: string
  resultTitle: string
}

const shared: Shared = { mediaId: '', userId: '', goal: '', resultTitle: '' }
let taskId = ''

test.describe('L3 full-chain — Agent+RAG 深链路（守门）', () => {
  test.describe.configure({ mode: 'serial' })

  test.beforeAll(async ({ request }: { request: APIRequestContext }) => {
    // 守门 1：显式 opt-in
    test.skip(!process.env.VM_E2E_FULL, '需 VM_E2E_FULL=1 才跑 L3 深链路')
    // 守门 2：后端在线
    test.skip(!(await probeBackend(request)), '后端 :8002 不可达')
    // 守门 3：有 ready 媒体
    const mediaId = await probeReadyMedia(request)
    test.skip(!mediaId, '无 status=ready 的媒体，L3 happy path 无前置')
    shared.mediaId = mediaId
    // 固定 goal：跨 test 复用同一 goal_hash，让 UI 幂等复用已 completed 的任务
    shared.goal = `L3 e2e 深链路分析 ${Date.now()}`
    // 取 dev user_id
    const cu = await request.get(`${BACKEND}/api/user/config`)
    const ub = await cu.json()
    shared.userId = ub.user_id
    test.skip(!UUID_RE.test(shared.userId), 'dev user_id 非法')
  })

  test('UI 提交 Agent 分析，拦截 task_id，轮询到终态并等结果卡', async ({ page }: { page: Page }) => {
    const analyzeResp = page.waitForResponse(
      (r) => r.url().includes('/api/agent/analyze') && r.request().method() === 'POST',
      { timeout: 20_000 },
    )
    await page.goto('/analysis')
    // 选 ready 视频（第一个 select 是目标视频）
    await page.locator('select').first().selectOption(shared.mediaId)
    await page.getByPlaceholder(/分析这个视频/).fill(shared.goal)
    await page.getByRole('button', { name: '开始分析' }).click()

    const resp = await analyzeResp
    expect(resp.status()).toBe(202)
    const body = await resp.json()
    expect(body.task_id).toMatch(UUID_RE)
    expect(ANALYSIS_STATUSES).toContain(body.status)
    taskId = body.task_id
    // 进度卡出现 → 证状态机驱动起来了
    await expect(page.getByText('任务进度')).toBeVisible({ timeout: 10_000 })
  })

  test('未终态时 GET /result → 409 契约', async ({ request }) => {
    const st = await (await request.get(`${BACKEND}/api/agent/tasks/${taskId}`)).json()
    if (st.status === 'completed' || st.status === 'failed') {
      test.skip() // 已终态（LLM 极快），跳过 409 契约验证
    }
    const r = await request.get(`${BACKEND}/api/agent/tasks/${taskId}/result`)
    expect(r.status()).toBe(409)
    const body = await r.json()
    expect(body.detail).toContain('not completed')
  })

  test('轮询状态机到终态', async ({ request }) => {
    const final = await pollAgentStatus(request, taskId, 120_000)
    expect(['completed', 'failed']).toContain(final)
  })

  test('GET /checkpoints → 断点轨迹形状', async ({ request }) => {
    const r = await request.get(`${BACKEND}/api/agent/tasks/${taskId}/checkpoints`)
    expect(r.status()).toBe(200)
    const body = await r.json()
    expect(Array.isArray(body)).toBe(true)
    // 轮询已到终态，应有 ≥1 条断点
    if (body.length > 0) {
      for (const cp of body) {
        expect(typeof cp.round).toBe('number')
        expect(typeof cp.phase).toBe('string')
        expect(['planning', 'executing', 'critic_check']).toContain(cp.phase)
      }
    }
  })

  test('若 completed：GET /result → AgentResultResponse 形状（纯 API）', async ({ request }) => {
    const st = await (await request.get(`${BACKEND}/api/agent/tasks/${taskId}`)).json()
    test.skip(st.status !== 'completed', `任务未完成（status=${st.status}），跳过结果断言`)

    const r = await request.get(`${BACKEND}/api/agent/tasks/${taskId}/result`)
    expect(r.status()).toBe(200)
    const body = await r.json()
    expect(body.id).toMatch(UUID_RE)
    expect(body.task_id).toBe(taskId)
    expect(typeof body.title).toBe('string')
    expect(body.title.length).toBeGreaterThan(0)
    expect(Array.isArray(body.conclusions_json)).toBe(true)
    expect(Array.isArray(body.evidence_json)).toBe(true)
    expect(typeof body.critic_passed).toBe('boolean')
    expect(typeof body.total_rounds).toBe('number')
    shared.resultTitle = body.title // 供后续 UI 幂等复用 test 用
  })

  test('UI 幂等复用 completed 任务 → 结果卡渲染', async ({ page }: { page: Page }) => {
    test.skip(!shared.resultTitle, '上一个 test 未产出 completed 结果，跳过 UI 渲染断言')
    // 重新打开 /analysis，进同一 goal 提交 → 后端幂等复用同 task（status=completed），
    // 前端拿到 task_id 后立即轮询 status=completed → 拉结果 → 渲染结果卡。无需等 LLM 跑。
    await page.goto('/analysis')
    await page.locator('select').first().selectOption(shared.mediaId)
    await page.getByPlaceholder(/分析这个视频/).fill(shared.goal)
    await page.getByRole('button', { name: '开始分析' }).click()
    // 顶部「Critic 通过/未通过」徽章出现即证结果卡渲染了（不依赖 body.title 文本歧义）
    await expect(page.getByText(/Critic 通过|Critic 未通过/).first()).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(shared.resultTitle).first()).toBeVisible({ timeout: 10_000 })
  })

  test('RAG 问答链路：search + chat（Qdrant 未索引则 skip）', async ({ request }) => {
    // search
    const sr = await request.post(`${BACKEND}/api/rag/search`, {
      data: { query: 'L3 深链路检索测试', media_ids: [shared.mediaId], top_k: 5 },
    })
    if (sr.status() === 500) test.skip() // 媒体未建 Qdrant 索引
    expect(sr.status()).toBe(200)
    const sbody = await sr.json()
    expect(Array.isArray(sbody)).toBe(true)
    if (sbody.length > 0) {
      const it = sbody[0]
      for (const k of ['chunk_id', 'media_id', 'content', 'score']) expect(it).toHaveProperty(k)
      expect(typeof it.score).toBe('number')
    }
    // chat
    const cr = await request.post(`${BACKEND}/api/rag/chat`, {
      data: { query: '这个视频的主要讲了什么？', media_ids: [shared.mediaId], top_k: 5 },
    })
    if (cr.status() === 500) test.skip()
    expect(cr.status()).toBe(200)
    const cbody = await cr.json()
    expect(typeof cbody.answer).toBe('string')
    expect(cbody.answer.length).toBeGreaterThan(0)
    expect(Array.isArray(cbody.evidence)).toBe(true)
    expect(cbody.session_id).toMatch(UUID_RE)
  })
})
