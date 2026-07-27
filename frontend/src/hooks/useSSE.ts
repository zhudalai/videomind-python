import { useEffect, useRef, useState, useCallback } from 'react'
import type { ProgressEvent, IngestionStage } from '@/types/api'
import { STAGE_LABELS, STAGE_ORDER } from '@/lib/utils'

export interface SSEState {
  events: ProgressEvent[]
  connectionState: 'connecting' | 'open' | 'closed' | 'error'
  latestEvent: ProgressEvent | null
  stageProgress: Record<IngestionStage, number>
  currentStage: IngestionStage
  overallProgress: number
}

export interface UseSSEOptions {
  enabled?: boolean
  onStageChange?: (stage: IngestionStage, progress: number) => void
  onComplete?: (success: boolean) => void
  onError?: (error: Event) => void
  retryInterval?: number
}

const createEmptyStageRecord = <T,>(): Record<IngestionStage, T> => ({
  claimed: undefined as any,
  downloading: undefined as any,
  downloaded: undefined as any,
  transcoding: undefined as any,
  transcoded: undefined as any,
  asr: undefined as any,
  ocr: undefined as any,
  indexing: undefined as any,
  completed: undefined as any,
  failed: undefined as any,
})

export function usePipelineSSE(mediaId: string, options: UseSSEOptions = {}) {
  const {
    enabled = true,
    onStageChange,
    onComplete,
    onError,
    retryInterval = 5000,
  } = options

  const [state, setState] = useState<SSEState>({
    events: [],
    connectionState: 'connecting',
    latestEvent: null,
    stageProgress: createEmptyStageRecord<number>(),
    currentStage: 'claimed',
    overallProgress: 0,
  })

  const eventSourceRef = useRef<EventSource | null>(null)
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)

  const cleanup = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
      eventSourceRef.current = null
    }
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current)
      retryTimeoutRef.current = null
    }
  }, [])

  const connect = useCallback(() => {
    if (!enabled || !mediaId) return

    cleanup()

    setState((prev) => ({ ...prev, connectionState: 'connecting' }))

    try {
      // 用后端绝对地址（VITE_API_BASE）建 EventSource，而非相对路径 /api。
      // 原因：vite dev server 的 /api proxy 对 text/event-stream 长连接会 buffering，
      // 响应头迟迟不下发，浏览器 EventSource 卡在 CONNECTING 永不 OPEN。
      // 直连后端绝对地址（与 axios baseURL 同源）绕过该问题；后端已开 CORS，
      // EventSource 跨域 GET 可正常握 OPEN。生产构建时 VITE_API_BASE 通常为同源相对路径，
      // 同样兼容。
      const apiBase = import.meta.env.VITE_API_BASE || '/api'
      const es = new EventSource(`${apiBase}/videos/pipeline/${mediaId}/progress`)
      eventSourceRef.current = es

      es.onopen = () => {
        if (!mountedRef.current) return
        setState((prev) => ({ ...prev, connectionState: 'open' }))
      }

      es.onmessage = (event) => {
        if (!mountedRef.current) return

        try {
          const progressEvent: ProgressEvent = JSON.parse(event.data)

          setState((prev) => {
            const newEvents = [...prev.events, progressEvent]
            const stageProgress = { ...prev.stageProgress }
            const stage = progressEvent.stage as IngestionStage
            if (STAGE_ORDER.includes(stage)) {
              stageProgress[stage] = progressEvent.progress_pct
            }

            // Determine current stage
            let currentStage: IngestionStage = 'claimed'
            for (const s of STAGE_ORDER) {
              if (stageProgress[s] !== undefined) {
                currentStage = s
              }
            }

            // Calculate overall progress
            const overallProgress =
              progressEvent.progress_pct >= 0 ? progressEvent.progress_pct : prev.overallProgress

            return {
              ...prev,
              events: newEvents,
              latestEvent: progressEvent,
              stageProgress,
              currentStage,
              overallProgress,
            }
          })

          // Callbacks
          onStageChange?.(progressEvent.stage, progressEvent.progress_pct)

          // Check for completion
          if (progressEvent.stage === 'completed' || progressEvent.progress_pct >= 100) {
            onComplete?.(true)
            cleanup()
          } else if (progressEvent.stage === 'failed' || progressEvent.progress_pct < 0) {
            onComplete?.(false)
            cleanup()
          }
        } catch (err) {
          console.error('Failed to parse SSE message:', err)
        }
      }

      es.onerror = (err) => {
        if (!mountedRef.current) return
        setState((prev) => ({ ...prev, connectionState: 'error' }))
        onError?.(err)

        // Auto-retry
        if (enabled) {
          retryTimeoutRef.current = setTimeout(() => {
            if (mountedRef.current && enabled) {
              connect()
            }
          }, retryInterval)
        }
      }
    } catch (err) {
      console.error('Failed to create EventSource:', err)
      setState((prev) => ({ ...prev, connectionState: 'error' }))
    }
  }, [mediaId, enabled, onStageChange, onComplete, onError, retryInterval, cleanup])

  useEffect(() => {
    mountedRef.current = true
    connect()

    return () => {
      mountedRef.current = false
      cleanup()
    }
  }, [connect, cleanup])

  return {
    ...state,
    reconnect: connect,
    disconnect: cleanup,
    stageLabel: STAGE_LABELS[state.currentStage] || state.currentStage,
    isActive: state.connectionState === 'open',
    isCompleted: state.currentStage === 'completed' || state.currentStage === 'failed',
    isFailed: state.currentStage === 'failed',
  }
}

// Simplified hook for general SSE usage
export function useEventSource<T>(url: string, options: {
  enabled?: boolean
  onMessage?: (data: T) => void
  onError?: (error: Event) => void
} = {}) {
  const { enabled = true, onMessage, onError } = options
  const [data, setData] = useState<T | null>(null)
  const [readyState, setReadyState] = useState<EventSource['readyState']>(0)
  const eventSourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!enabled) return

    const es = new EventSource(url)
    eventSourceRef.current = es
    setReadyState(es.readyState)

    es.onopen = () => setReadyState(EventSource.OPEN)
    es.onmessage = (e) => {
      const parsed = JSON.parse(e.data) as T
      setData(parsed)
      onMessage?.(parsed)
    }
    es.onerror = (err) => {
      setReadyState(EventSource.CLOSED)
      onError?.(err)
    }

    return () => es.close()
  }, [url, enabled, onMessage, onError])

  return { data, readyState }
}