import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { pipelineApi } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Progress } from '@/components/ui/Progress'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { cn, STAGE_LABELS, STAGE_ORDER } from '@/lib/utils'
import {
  CheckCircle,
  XCircle,
  Loader2,
  AlertCircle,
  Download,
  FileText,
  Search,
  Database,
  ArrowLeft,
  RotateCcw,
} from 'lucide-react'
import type { PipelineStatusResponse, IngestionStage, ProgressEvent } from '@/types/api'

interface StageStatus {
  stage: IngestionStage
  label: string
  icon: React.ReactNode
  progress: number
  status: 'pending' | 'active' | 'completed' | 'failed'
  startTime?: string
  endTime?: string
  message?: string
}

const STAGE_ICONS: Record<IngestionStage, React.ReactNode> = {
  claimed: <Download className="h-5 w-5" />,
  downloading: <Download className="h-5 w-5" />,
  downloaded: <Download className="h-5 w-5" />,
  transcoding: <FileText className="h-5 w-5" />,
  transcoded: <FileText className="h-5 w-5" />,
  asr: <Search className="h-5 w-5" />,
  ocr: <Search className="h-5 w-5" />,
  indexing: <Database className="h-5 w-5" />,
  completed: <CheckCircle className="h-5 w-5 text-green-500" />,
  failed: <XCircle className="h-5 w-5 text-red-500" />,
}

// Helper to create empty record with all stages
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

export function PipelineProgressPage() {
  const { id: mediaId } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatusResponse | null>(null)
  const [sseEvents, setSseEvents] = useState<ProgressEvent[]>([])
  const [connectionState, setConnectionState] = useState<'connecting' | 'open' | 'closed' | 'error'>('connecting')
  const [error, setError] = useState<string | null>(null)
  const [isComplete, setIsComplete] = useState(false)
  const [retryCount, setRetryCount] = useState(0)

  const eventSourceRef = useRef<EventSource | null>(null)
  const mountedRef = useRef(true)

  // Build stage statuses from pipeline data and SSE events
  const stageStatuses = useMemo((): StageStatus[] => {
    if (!pipelineStatus) return []

    const stageProgress = createEmptyStageRecord<number>()
    const stageMessages = createEmptyStageRecord<string>()

    // From SSE events
    sseEvents.forEach(event => {
      const stage = event.stage as IngestionStage
      if (STAGE_ORDER.includes(stage)) {
        stageProgress[stage] = event.progress_pct
        stageMessages[stage] = event.message
      }
    })

    // From REST API
    if (pipelineStatus.stage_progress) {
      Object.entries(pipelineStatus.stage_progress).forEach(([stage, progress]) => {
        const s = stage as IngestionStage
        if (STAGE_ORDER.includes(s)) {
          stageProgress[s] = progress
        }
      })
    }

    // Determine current stage
    let currentStage: IngestionStage = 'claimed'
    for (const s of STAGE_ORDER) {
      if (stageProgress[s] !== undefined && stageProgress[s] > 0) {
        currentStage = s
      }
    }

    // If completed or failed, that's the current stage
    if (pipelineStatus.status === 'ready') currentStage = 'completed'
    if (pipelineStatus.status === 'failed') currentStage = 'failed'

    return STAGE_ORDER.map((stage): StageStatus => {
      const progress = stageProgress[stage] ?? 0
      let status: StageStatus['status'] = 'pending'

      if (stage === currentStage) {
        if (progress < 0 || stage === 'failed') status = 'failed'
        else if (progress >= 100 || stage === 'completed') status = 'completed'
        else status = 'active'
      } else if (STAGE_ORDER.indexOf(stage) < STAGE_ORDER.indexOf(currentStage)) {
        status = 'completed'
      }

      return {
        stage,
        label: STAGE_LABELS[stage] || stage,
        icon: STAGE_ICONS[stage],
        progress: progress < 0 ? 0 : progress,
        status,
        message: stageMessages[stage],
      }
    })
  }, [pipelineStatus, sseEvents])

  // Connect to SSE
  const connectSSE = useCallback(() => {
    if (!mediaId) return

    if (eventSourceRef.current) {
      eventSourceRef.current.close()
    }

    setConnectionState('connecting')

    try {
      // 用后端绝对地址（VITE_API_BASE）建 EventSource，绕过 vite dev server 对
      // text/event-stream 长连接的 buffering（否则浏览器 EventSource 卡 CONNECTING 永不 OPEN）。
      // 与 axios baseURL 同源，后端已开 CORS，跨域 GET 可正常握 OPEN。
      const apiBase = import.meta.env.VITE_API_BASE || '/api'
      const es = new EventSource(`${apiBase}/videos/pipeline/${mediaId}/progress`)
      eventSourceRef.current = es

      es.onopen = () => {
        if (!mountedRef.current) return
        setConnectionState('open')
        setRetryCount(0)
      }

      // 后端 sse.py 发的是 named event（sse_starlette event_generator yield
      // {"event": "progress", "data": ...}），浏览器 EventSource 命中 addEventListener('progress')，
      // 命中默认的 `onmessage`。之前用 onmessage → 事件数永远 0。这里对齐后端 named event。
      es.addEventListener('progress', (event) => {
        if (!mountedRef.current) return

        try {
          const progressEvent: ProgressEvent = JSON.parse(event.data)

          setSseEvents(prev => [...prev, progressEvent])

          // Check for completion
          if (progressEvent.stage === 'completed' || progressEvent.progress_pct >= 100) {
            setIsComplete(true)
            setConnectionState('closed')
            es.close()
          } else if (progressEvent.stage === 'failed' || progressEvent.progress_pct < 0) {
            setIsComplete(true)
            setConnectionState('closed')
            es.close()
          }
        } catch (err) {
          console.error('Failed to parse SSE message:', err)
        }
      })

      es.onerror = (_err) => {
        if (!mountedRef.current) return
        setConnectionState('error')
        es.close()

        // Auto-retry up to 5 times
        if (retryCount < 5) {
          setTimeout(() => {
            if (mountedRef.current) {
              setRetryCount(c => c + 1)
              connectSSE()
            }
          }, 3000 * (retryCount + 1))
        }
      }
    } catch (err) {
      console.error('Failed to create EventSource:', err)
      setConnectionState('error')
    }
  }, [mediaId, retryCount])

  // Fetch initial status via REST
  const fetchStatus = useCallback(async () => {
    if (!mediaId) return
    try {
      const status = await pipelineApi.getStatus(mediaId)
      setPipelineStatus(status)
    } catch (err: any) {
      setError(err.detail || '获取状态失败')
    }
  }, [mediaId])

  // Initial load
  useEffect(() => {
    if (!mediaId) return

    // React 18 StrictMode 在 dev 双跑 effect：mount → cleanup → remount。
    // cleanup 把 mountedRef.current 置 false，若重挂载时不在 effect 起手把它重置回 true，
    // 二次 mount 里 connectSSE 建的 EventSource 的 onopen 会因 `if (!mountedRef.current) return`
    // 直接被吞掉，UI 永远停在 'connecting'。所以这里务必先重置。
    mountedRef.current = true

    fetchStatus()
    connectSSE()

    // Poll REST API as fallback
    const interval = setInterval(fetchStatus, 5000)

    return () => {
      mountedRef.current = false
      clearInterval(interval)
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
      }
    }
  }, [mediaId, fetchStatus, connectSSE])

  // Reconnect handler
  const handleReconnect = () => {
    setRetryCount(0)
    connectSSE()
    fetchStatus()
  }

  // Navigate to detail when complete
  const handleViewDetail = () => {
    navigate(`/videos/${mediaId}`)
  }

  if (!mediaId) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-muted-foreground">无效的视频 ID</p>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" onClick={() => navigate(-1)}>
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <div>
            <h1 className="text-2xl font-bold">处理进度</h1>
            <p className="text-muted-foreground">实时监控视频处理管线</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Badge variant={connectionState === 'open' ? 'success' : connectionState === 'error' ? 'destructive' : 'secondary'}>
            {connectionState === 'open' && <span className="flex items-center gap-1"><Loader2 className="h-3 w-3 animate-spin" /> 连接中</span>}
            {connectionState === 'connecting' && <span>连接中...</span>}
            {connectionState === 'closed' && <span>已断开</span>}
            {connectionState === 'error' && <span>连接失败</span>}
          </Badge>

          {isComplete && pipelineStatus?.status === 'ready' && (
            <Button onClick={handleViewDetail}>
              <CheckCircle className="h-4 w-4 mr-2" />
              查看详情
            </Button>
          )}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-4 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive">
          <AlertCircle className="h-5 w-5 flex-shrink-0" />
          <span>{error}</span>
          <Button variant="ghost" size="sm" onClick={handleReconnect}>
            <RotateCcw className="h-4 w-4 mr-1" />
            重试
          </Button>
        </div>
      )}

      {/* Pipeline Stages */}
      <Card>
        <CardHeader>
          <CardTitle>处理阶段</CardTitle>
          <CardDescription>
            视频处理包含 5 个主要阶段，每个阶段完成后自动进入下一阶段
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {stageStatuses.map((stage, index) => (
              <div key={stage.stage} className="relative">
                {/* Connector line */}
                {index > 0 && (
                  <div className="absolute left-5 top-0 bottom-0 w-0.5" style={{ top: '32px' }}>
                    <div
                      className="h-full bg-gradient-to-b"
                      style={{
                        background: stage.status === 'completed'
                          ? 'linear-gradient(to bottom, hsl(var(--primary)), hsl(var(--primary)))'
                          : 'linear-gradient(to bottom, hsl(var(--muted-foreground)/0.3), hsl(var(--muted-foreground)/0.3))',
                      }}
                    />
                  </div>
                )}

                <div className="flex items-start gap-4 pl-12">
                  {/* Stage Icon */}
                  <div
                    className={cn(
                      'flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center border-2 transition-all',
                      stage.status === 'completed' && 'border-green-500 bg-green-500 text-white',
                      stage.status === 'active' && 'border-primary bg-primary/10 text-primary animate-pulse',
                      stage.status === 'failed' && 'border-red-500 bg-red-500 text-white',
                      stage.status === 'pending' && 'border-muted-foreground/30 text-muted-foreground/50',
                    )}
                  >
                    {stage.status === 'completed' ? (
                      <CheckCircle className="h-5 w-5" />
                    ) : stage.status === 'failed' ? (
                      <XCircle className="h-5 w-5" />
                    ) : stage.status === 'active' ? (
                      <Loader2 className="h-5 w-5 animate-spin" />
                    ) : (
                      stage.icon
                    )}
                  </div>

                  {/* Stage Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h4 className={cn(
                        'font-medium',
                        stage.status === 'active' && 'text-primary',
                        stage.status === 'failed' && 'text-destructive',
                      )}>
                        {stage.label}
                      </h4>
                      {stage.status === 'active' && (
                        <Badge variant="secondary" className="text-xs">
                          进行中
                        </Badge>
                      )}
                      {stage.status === 'completed' && (
                        <Badge variant="success" className="text-xs">
                          完成
                        </Badge>
                      )}
                      {stage.status === 'failed' && (
                        <Badge variant="destructive" className="text-xs">
                          失败
                        </Badge>
                      )}
                    </div>

                    {/* Progress Bar */}
                    <div className="mt-2">
                      <div className="flex items-center justify-between text-sm mb-1">
                        <span className="text-muted-foreground">{stage.message || ''}</span>
                        <span className="font-medium">
                          {stage.progress >= 0 ? `${Math.round(stage.progress)}%` : '等待中'}
                        </span>
                      </div>
                      <Progress value={Math.max(0, stage.progress)} className="h-2" />
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Overall Progress */}
      <Card>
        <CardHeader>
          <CardTitle>总体进度</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-lg font-bold">
                {pipelineStatus?.status === 'ready' ? '100%' :
                  pipelineStatus?.status === 'failed' ? '失败' :
                  `${Math.round(pipelineStatus?.stage_progress?.indexing || 0)}%`}
              </span>
              <Badge variant={pipelineStatus?.status === 'ready' ? 'success' : pipelineStatus?.status === 'failed' ? 'destructive' : 'secondary'}>
                {pipelineStatus?.status || '未知'}
              </Badge>
            </div>
            <Progress value={pipelineStatus?.status === 'ready' ? 100 : pipelineStatus?.stage_progress?.indexing || 0} className="h-4" />

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div>
                <p className="text-muted-foreground">当前阶段</p>
                <p className="font-medium">{STAGE_LABELS[pipelineStatus?.status as IngestionStage] || pipelineStatus?.status}</p>
              </div>
              <div>
                <p className="text-muted-foreground">视频 ID</p>
                <p className="font-medium font-mono text-xs">{mediaId?.slice(0, 8)}...</p>
              </div>
              <div>
                <p className="text-muted-foreground">连接状态</p>
                <p className="font-medium capitalize">{connectionState}</p>
              </div>
              <div>
                <p className="text-muted-foreground">SSE 事件数</p>
                <p className="font-medium">{sseEvents.length}</p>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Event Log */}
      {sseEvents.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>实时事件日志</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="max-h-64 overflow-y-auto space-y-2">
              {sseEvents.slice().reverse().map((event, idx) => (
                <div
                  key={idx}
                  className={cn(
                    'p-3 rounded-lg text-sm font-mono border',
                    event.stage === 'failed' && 'bg-destructive/10 border-destructive/20',
                    event.stage === 'completed' && 'bg-green-500/10 border-green-500/20',
                    'bg-muted/50 border-muted/50'
                  )}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <Badge variant="outline" className="text-xs">
                      {STAGE_LABELS[event.stage] || event.stage}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {new Date(event.timestamp).toLocaleTimeString()}
                    </span>
                    <span className="ml-auto font-medium">
                      {event.progress_pct >= 0 ? `${event.progress_pct}%` : '错误'}
                    </span>
                  </div>
                  <p className="text-muted-foreground">{event.message}</p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}