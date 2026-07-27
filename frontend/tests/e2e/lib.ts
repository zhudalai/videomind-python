import type { APIRequestContext } from '@playwright/test'

/** 后端 FastAPI（dev 在 :8002， авто-load）。L2/L3 直接打它，绕过 vite。 */
export const BACKEND = 'http://localhost:8002'

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** 后端是否在线（GET /api/health）。3s 超时，连接失败算离线。 */
export async function probeBackend(request: APIRequestContext): Promise<boolean> {
  try {
    const r = await request.get(`${BACKEND}/api/health`, { timeout: 3000 })
    return r.ok()
  } catch {
    return false
  }
}

/**
 * 找一个 status='ready' 的 media_id（L2/L3 happy path 前置）。
 * 先用后端 status 过滤，再客户端兜底过滤（防止路由未实现 status 过滤）。
 */
export async function probeReadyMedia(request: APIRequestContext): Promise<string | null> {
  try {
    const r = await request.get(`${BACKEND}/api/videos`, {
      params: { status: 'ready', page: 1, page_size: 20 },
      timeout: 5000,
    })
    if (!r.ok()) return null
    const body = await r.json()
    const items: Array<{ id?: string; status?: string }> = body.items ?? body.data ?? []
    const ready = items.find((x) => x?.id && x.status === 'ready') ?? items.find((x) => x?.id)
    return ready?.id ? String(ready.id) : null
  } catch {
    return null
  }
}

/** 轮询 agent 任务状态直到终态（completed/failed）或超时。返回最终 status 字符串。 */
export async function pollAgentStatus(
  request: APIRequestContext,
  taskId: string,
  timeoutMs = 90_000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs
  let status = 'pending'
  while (Date.now() < deadline) {
    const r = await request.get(`${BACKEND}/api/agent/tasks/${taskId}`, { timeout: 10_000 })
    if (r.ok()) {
      const body = await r.json()
      status = body.status
      if (status === 'completed' || status === 'failed') return status
    }
    await new Promise((resolve) => setTimeout(resolve, 2000))
  }
  return status
}
