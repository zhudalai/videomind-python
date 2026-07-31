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