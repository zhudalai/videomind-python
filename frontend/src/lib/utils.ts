import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import type { IngestionStage } from '@/types/api'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000)
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60

  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }
  return `${minutes}:${secs.toString().padStart(2, '0')}`
}

export function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`
}

export function formatDate(date: string | Date): string {
  return new Date(date).toLocaleString('zh-CN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

import i18n from '@/i18n'

export function formatRelativeTime(date: string | Date): string {
  const now = new Date()
  const then = new Date(date)
  const diffMs = now.getTime() - then.getTime()
  const diffSecs = Math.floor(diffMs / 1000)
  const diffMins = Math.floor(diffSecs / 60)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)
  const t = i18n.t.bind(i18n)

  if (diffSecs < 60) return t('utils.justNow')
  if (diffMins < 60) return t('utils.minutesAgo', { count: diffMins })
  if (diffHours < 24) return t('utils.hoursAgo', { count: diffHours })
  if (diffDays < 7) return t('utils.daysAgo', { count: diffDays })
  return formatDate(date)
}

export function getStatusColor(status: string): string {
  const colors: Record<string, string> = {
    pending: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
    downloading: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
    transcoding: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
    asr: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400',
    ocr: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400',
    indexing: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-400',
    ready: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
    failed: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
    error: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  }
  return colors[status] || 'bg-gray-100 text-gray-800 dark:bg-gray-900/30 dark:text-gray-400'
}

export function getStageProgress(stage: string): number {
  const progress: Record<string, number> = {
    claimed: 5,
    downloading: 10,
    downloaded: 20,
    transcoding: 30,
    transcoded: 40,
    asr: 60,
    ocr: 75,
    indexing: 90,
    completed: 100,
    failed: -1,
  }
  return progress[stage] ?? 0
}

// Stage → i18n key map. Consumers render with t(STAGE_LABELS[stage]).
// Storing keys (not localized strings) keeps labels reactive to language switches.
export const STAGE_LABELS: Record<IngestionStage, string> = {
  claimed: 'pipeline.stageClaimed',
  downloading: 'pipeline.stageDownloading',
  downloaded: 'pipeline.stageDownloaded',
  transcoding: 'pipeline.stageTranscoding',
  transcoded: 'pipeline.stageTranscoded',
  asr: 'pipeline.stageAsr',
  ocr: 'pipeline.stageOcr',
  indexing: 'pipeline.stageIndexing',
  completed: 'pipeline.stageCompleted',
  failed: 'pipeline.stageFailed',
}

export const STAGE_ORDER: IngestionStage[] = [
  'claimed',
  'downloading',
  'downloaded',
  'transcoding',
  'transcoded',
  'asr',
  'ocr',
  'indexing',
  'completed',
]

/**
 * 从 axios 抛出的 Error 里抽出给用户看的 detail。
 * api.ts 拦截器已经把 error.response?.data?.detail 挂在 err.detail，
 * 否则降级到 err.message，再否则笼统文案。
 */
export function extractErrorMessage(error: unknown): string {
  if (error && typeof error === 'object') {
    const e = error as { detail?: unknown; message?: unknown }
    if (typeof e.detail === 'string' && e.detail) return e.detail
    if (typeof e.detail === 'object' && e.detail !== null) return JSON.stringify(e.detail)
    if (typeof e.message === 'string' && e.message) return e.message
  }
  if (typeof error === 'string') return error
  return i18n.t('utils.operationFailed')
}

/**
 * 从 YouTube URL 中提取 video id（11 位 base64-style）。支持：
 *  - https://www.youtube.com/watch?v=ID
 *  - https://youtu.be/ID
 *  - https://www.youtube.com/embed/ID
 *  - https://m.youtube.com/watch?v=ID
 *  - https://www.youtube.com/shorts/ID
 * 无法解析时返回 null。
 */
export function extractYouTubeVideoId(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const u = new URL(url)
    const host = u.hostname.replace(/^www\./, '').replace(/^m\./, '')
    if (host === 'youtu.be') {
      const seg = u.pathname.split('/').filter(Boolean)[0]
      return seg && /^[A-Za-z0-9_-]{11}$/.test(seg) ? seg : null
    }
    if (host.endsWith('youtube.com') || host === 'youtube-nocookie.com') {
      const v = u.searchParams.get('v')
      if (v && /^[A-Za-z0-9_-]{11}$/.test(v)) return v
      const parts = u.pathname.split('/').filter(Boolean)
      if (parts.length >= 2 && /^(embed|shorts|v)$/.test(parts[0])) {
        const id = parts[1]
        if (/^[A-Za-z0-9_-]{11}$/.test(id)) return id
      }
    }
  } catch {
    // 非 URL 字符串：退到正则
    const m = url.match(/[?&]v=([A-Za-z0-9_-]{11})/)
    if (m) return m[1]
    const m2 = url.match(/youtu\.be\/([A-Za-z0-9_-]{11})/)
    if (m2) return m2[1]
  }
  return null
}

/**
 * 生成 YouTube 视频缩略图 URL（来自 YouTube CDN，无需后端存储）。
 * quality: 'maxres' | 'hq' | 'mq' | 'sd' | 'default'，返回 null 时表示 URL 来源不可识别。
 */
export function getYouTubeThumbnailUrl(
  url: string | null | undefined,
  quality: 'maxres' | 'hq' | 'mq' | 'sd' | 'default' = 'hq'
): string | null {
  const id = extractYouTubeVideoId(url)
  if (!id) return null
  const file =
    quality === 'maxres'
      ? 'maxresdefault.jpg'
      : quality === 'hq'
        ? 'hqdefault.jpg'
        : quality === 'mq'
          ? 'mqdefault.jpg'
          : quality === 'sd'
            ? 'sddefault.jpg'
            : 'default.jpg'
  return `https://img.youtube.com/vi/${id}/${file}`
}

/**
 * 统一解析"视频缩略图 URL"。
 *  - 如果给了 thumbnail_object（MinIO 对象 key），优先用 /api/videos/{id} 的代理
 *    —— 后端目前未提供直读 MinIO 缩略图通道，这里留作扩展点；
 *  - 否则对 YouTube 链接回退到 YouTube CDN 缩略图。
 * 返回 null 让调用方决定降级到默认占位。
 */
export function getVideoThumbnailUrl(opts: {
  source_url?: string | null
  thumbnail_object?: string | null
  mime_type?: string | null
}): string | null {
  if (opts.thumbnail_object) {
    // 留作未来后端 MinIO 代理实现。当前 MinIO 未对外开放 HTTP，前端暂时也用 YouTube 兜底。
    if (opts.source_url) {
      const yt = getYouTubeThumbnailUrl(opts.source_url, 'hq')
      if (yt) return yt
    }
    return null
  }
  if (opts.source_url) {
    const yt = getYouTubeThumbnailUrl(opts.source_url, 'hq')
    if (yt) return yt
  }
  return null
}