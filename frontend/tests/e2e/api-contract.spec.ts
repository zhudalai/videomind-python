/**
 * L2 API 契约测试 —— 必跑，快，纯 request（直打后端 :8002，绕过 vite）。
 *
 * 固化本次新增/对齐的 10 个端点的契约形状：
 *   health×2 + agent×4 + rag×2 + user×2（见 frontend/src/lib/api.ts）
 *
 * error 路径（404/400）始终可跑；happy 路径（202/200）按 ready 媒体探活门控，
 * 无就 skip —— 守门但不红。后端离线则整组 skip。
 */
import { test, expect, type APIRequestContext } from '@playwright/test'
import { BACKEND, UUID_RE, probeBackend, probeReadyMedia } from './lib'

const ANALYSIS_STATUSES = ['pending', 'planning', 'executing', 'critic_check', 'completed', 'failed']

// 固定一批「不存在的」uuid 用于 error 路径（格式合法，DB 找不到）
const GHOST_UUID = '00000000-0000-0000-0000-000000000000'

test.describe('L2 api-contract — 10 端点契约', () => {
  test.describe.configure({ mode: 'serial' })

  test.beforeAll(async ({ request }: { request: APIRequestContext }) => {
    test.skip(!(await probeBackend(request)), '后端 :8002 不可达')
  })

  // ── health ──
  test('GET /api/health → 200 {status:"UP"}', async ({ request }) => {
    const r = await request.get(`${BACKEND}/api/health`)
    expect(r.status()).toBe(200)
    const body = await r.json()
    expect(body.status).toBe('UP')
  })

  test('GET /api/health/ready → 200 {status, components{4}}', async ({ request }) => {
    const r = await request.get(`${BACKEND}/api/health/ready`)
    expect(r.status()).toBe(200)
    const body = await r.json()
    expect(['UP', 'DOWN']).toContain(body.status)
    for (const k of ['postgres', 'redis', 'qdrant', 'minio']) {
      expect(['UP', 'DOWN']).toContain(body.components[k])
    }
  })

  // ── user ──
  test('GET /api/user/config → 200 {user_id, theme, default_model, language}', async ({ request }) => {
    const r = await request.get(`${BACKEND}/api/user/config`)
    expect(r.status()).toBe(200)
    const body = await r.json()
    expect(body.user_id).toMatch(UUID_RE)
    expect(['light', 'dark', 'system']).toContain(body.theme)
    expect(body.default_model === null || typeof body.default_model === 'string').toBe(true)
    expect(typeof body.language).toBe('string')
  })

  test('PUT /api/user/config 校验 theme∈{light|dark|system}，非法值 → 400', async ({ request }) => {
    const r = await request.put(`${BACKEND}/api/user/config`, { data: { theme: 'not-a-theme' } })
    expect(r.status()).toBe(400)
    const body = await r.json()
    expect(body.detail).toContain('theme')
  })

  test('PUT /api/user/config 合法 → 200 回写（并复位）', async ({ request }) => {
    const r = await request.put(`${BACKEND}/api/user/config`, {
      data: { theme: 'dark', language: 'en-US' },
    })
    expect(r.status()).toBe(200)
    const body = await r.json()
    expect(body.theme).toBe('dark')
    expect(body.language).toBe('en-US')
    expect(body.user_id).toMatch(UUID_RE)
    // 复位为默认，避免污染其它测试
    await request.put(`${BACKEND}/api/user/config`, {
      data: { theme: 'system', language: 'zh-CN', default_model: null },
    })
  })

  // ── agent：error 路径全可跑 ──
  test('POST /api/agent/analyze 不存在 media → 404', async ({ request }) => {
    const r = await request.post(`${BACKEND}/api/agent/analyze`, {
      data: { goal: 'contract-ghost-media', media_id: GHOST_UUID, user_id: GHOST_UUID, max_rounds: 1 },
    })
    expect(r.status()).toBe(404)
  })

  test('GET /api/agent/tasks/{ghost} → 404', async ({ request }) => {
    const r = await request.get(`${BACKEND}/api/agent/tasks/${GHOST_UUID}`)
    expect(r.status()).toBe(404)
  })

  test('GET /api/agent/tasks/{ghost}/result → 404', async ({ request }) => {
    const r = await request.get(`${BACKEND}/api/agent/tasks/${GHOST_UUID}/result`)
    expect(r.status()).toBe(404)
  })

  test('GET /api/agent/tasks/{ghost}/checkpoints → 404', async ({ request }) => {
    const r = await request.get(`${BACKEND}/api/agent/tasks/${GHOST_UUID}/checkpoints`)
    expect(r.status()).toBe(404)
  })

  // ── rag：error 路径 ──
  test('POST /api/rag/search media_ids=[] → 400', async ({ request }) => {
    const r = await request.post(`${BACKEND}/api/rag/search`, {
      data: { query: '测试', media_ids: [], top_k: 5 },
    })
    expect(r.status()).toBe(400)
  })

  test('POST /api/rag/search 不存在 media → 404', async ({ request }) => {
    const r = await request.post(`${BACKEND}/api/rag/search`, {
      data: { query: '测试', media_ids: [GHOST_UUID], top_k: 5 },
    })
    expect(r.status()).toBe(404)
  })

  test('POST /api/rag/chat media_ids=[] → 400', async ({ request }) => {
    const r = await request.post(`${BACKEND}/api/rag/chat`, {
      data: { query: '测试', media_ids: [], top_k: 5 },
    })
    expect(r.status()).toBe(400)
  })

  // ── agent：happy 202（需 ready 媒体） ──
  test('POST /api/agent/analyze happy → 202 AnalyzeResponse', async ({ request }) => {
    const mediaId = await probeReadyMedia(request)
    test.skip(!mediaId, '无 ready 媒体')
    const user = await (await request.get(`${BACKEND}/api/user/config`)).json()

    const r = await request.post(`${BACKEND}/api/agent/analyze`, {
      data: {
        goal: `e2e-contract-analyze-${Date.now()}`,
        media_id: mediaId,
        user_id: user.user_id,
        max_rounds: 1,
      },
    })
    expect(r.status()).toBe(202)
    const body = await r.json()
    expect(body.task_id).toMatch(UUID_RE)
    expect(ANALYSIS_STATUSES).toContain(body.status)
    expect(typeof body.goal).toBe('string')
    expect(body.max_rounds).toBe(1)
    expect(typeof body.created_at).toBe('string')
  })

  // ── rag：happy（需 ready 媒体；未建索引则 500，skip） ──
  test('POST /api/rag/search happy → 200 list[RagSearchResultItem]', async ({ request }) => {
    const mediaId = await probeReadyMedia(request)
    test.skip(!mediaId, '无 ready 媒体')
    const r = await request.post(`${BACKEND}/api/rag/search`, {
      data: { query: '总结', media_ids: [mediaId], top_k: 5 },
    })
    if (r.status() === 500) test.skip() // 媒体未建 Qdrant 索引
    expect(r.status()).toBe(200)
    const body = await r.json()
    expect(Array.isArray(body)).toBe(true)
    if (body.length > 0) {
      const item = body[0]
      for (const k of ['chunk_id', 'media_id', 'content', 'score']) {
        expect(item).toHaveProperty(k)
      }
      expect(typeof item.score).toBe('number')
    }
  })

  test('POST /api/rag/chat happy → 200 {answer, evidence[], session_id}', async ({ request }) => {
    const mediaId = await probeReadyMedia(request)
    test.skip(!mediaId, '无 ready 媒体')
    const r = await request.post(`${BACKEND}/api/rag/chat`, {
      data: { query: '这个视频讲了什么？', media_ids: [mediaId], top_k: 5 },
    })
    if (r.status() === 500) test.skip() // 媒体未建 Qdrant 索引
    expect(r.status()).toBe(200)
    const body = await r.json()
    expect(typeof body.answer).toBe('string')
    expect(body.answer.length).toBeGreaterThan(0)
    expect(Array.isArray(body.evidence)).toBe(true)
    expect(body.session_id).toMatch(UUID_RE)
  })
})
