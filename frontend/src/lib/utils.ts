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

export function formatRelativeTime(date: string | Date): string {
  const now = new Date()
  const then = new Date(date)
  const diffMs = now.getTime() - then.getTime()
  const diffSecs = Math.floor(diffMs / 1000)
  const diffMins = Math.floor(diffSecs / 60)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffSecs < 60) return '刚刚'
  if (diffMins < 60) return `${diffMins}分钟前`
  if (diffHours < 24) return `${diffHours}小时前`
  if (diffDays < 7) return `${diffDays}天前`
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

export const STAGE_LABELS: Record<IngestionStage, string> = {
  claimed: '已认领',
  downloading: '下载中',
  downloaded: '下载完成',
  transcoding: '转码中',
  transcoded: '转码完成',
  asr: '语音识别',
  ocr: '文字识别',
  indexing: '索引构建',
  completed: '完成',
  failed: '失败',
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