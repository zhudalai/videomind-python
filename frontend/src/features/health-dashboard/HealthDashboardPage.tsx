import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts'
import { healthApi } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/utils'
import { Activity, Database, HardDrive, Layers, RefreshCw, AlertCircle, Server } from 'lucide-react'
import { useTranslation } from 'react-i18next'

type ComponentKey = 'postgres' | 'redis' | 'qdrant' | 'minio'

const COMPONENT_META: Record<
  ComponentKey,
  { label: string; icon: React.ComponentType<{ className?: string }> }
> = {
  postgres: { label: 'PostgreSQL', icon: Database },
  redis: { label: 'Redis', icon: Server },
  qdrant: { label: 'Qdrant', icon: Layers },
  minio: { label: 'MinIO', icon: HardDrive },
}

const COMPONENT_KEYS: ComponentKey[] = ['postgres', 'redis', 'qdrant', 'minio']

const POLL_MS = 30000
const HISTORY_CAP = 30 // retain last 30 poll results (≈15 min)

interface HistoryPoint {
  up: number // number of UP components (0-4)
  timestamp: number
}

export function HealthDashboardPage() {
  const { t } = useTranslation()
  const [history, setHistory] = useState<HistoryPoint[]>([])
  const lastDataRef = useRef<string>('')

  const { data: healthData, isLoading, refetch } = useQuery({
    queryKey: ['health'],
    queryFn: () => healthApi.ready(),
    refetchInterval: POLL_MS,
  })

  // Push data to history on each new response
  useEffect(() => {
    if (!healthData) return

    // Dedup by JSON of data (React 18 StrictMode double-mount in dev)
    const key = JSON.stringify(healthData)
    if (key === lastDataRef.current) return
    lastDataRef.current = key

    const up = healthData.status === 'UP' ? 4 : Object.values(healthData.components ?? {}).filter(Boolean).length
    setHistory(prev => {
      const next = [...prev, { up, timestamp: Date.now() }]
      return next.length > HISTORY_CAP ? next.slice(-HISTORY_CAP) : next
    })
  }, [healthData])

  // Typed entries so the key keeps its literal type for indexing the strict components object
  const componentEntries = COMPONENT_KEYS.map(key => [key, COMPONENT_META[key]] as const)
  const allHealthy = healthData?.status === 'UP'
  const hasAnomaly = !allHealthy && healthData !== undefined

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
            <Activity className="h-7 w-7" />
            {t('health.title')}
          </h1>
          <p className="text-muted-foreground mt-1">{t('health.subtitle', { interval: POLL_MS / 1000 })}</p>
        </div>
        <Button variant="outline" onClick={() => refetch()} disabled={isLoading}>
          <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
          {t('health.manualRefresh')}
        </Button>
      </div>

      {/* Anomaly Banner */}
      {hasAnomaly && (
        <div className="flex items-center gap-2 p-4 rounded-lg bg-destructive/10 border border-destructive/30 text-destructive">
          <AlertCircle className="h-5 w-5 flex-shrink-0" />
          <span className="font-medium">{t('health.anomalyDetected')}</span>
          <span className="text-sm">{t('health.anomalyDesc')}</span>
        </div>
      )}

      {/* Component Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {componentEntries.map(([key, meta]) => {
          const Icon = meta.icon
          const value = healthData?.components?.[key]
          const isUp = value === 'UP'
          const isUnknown = value === undefined || value === null
          return (
            <Card key={key} className={cn(isUp && 'border-green-500/30', !isUp && !isUnknown && 'border-destructive/30')}>
              <CardContent className="p-4 flex items-center gap-3">
                <Icon className={cn('h-8 w-8', isUp ? 'text-green-500' : 'text-muted-foreground')} />
                <div>
                  <p className="font-medium text-sm">{meta.label}</p>
                  <p className="text-xs text-muted-foreground">
                    {isUnknown
                      ? t('health.checking')
                      : isUp
                        ? t('health.connected')
                        : t('health.disconnected')}
                  </p>
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      {/* If still loading skeleton */}
      {isLoading && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => (
            <Card key={i} className="animate-pulse"><CardContent className="p-4" /></Card>
          ))}
        </div>
      )}

      {/* Availability Timeline */}
      <Card>
        <CardHeader>
          <CardTitle>{t('health.availabilityTimeline')}</CardTitle>
          <CardDescription>
            {t('health.timelineDesc', { count: history.length })}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {history.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={history}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={(v) => new Date(v).toLocaleTimeString()}
                  tick={{ fontSize: 10 }}
                />
                <YAxis domain={[0, 4]} tickCount={5} tick={{ fontSize: 10 }} />
                <Tooltip
                  labelFormatter={(v) => new Date(v as number).toLocaleString()}
                  formatter={(v: number) => [t('health.upOfFour', { count: v }), t('health.healthy')]}
                />
                <Line
                  type="monotone"
                  dataKey="up"
                  stroke="hsl(var(--primary))"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4 }}
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-center text-muted-foreground py-8">{t('health.waiting')}</p>
          )}
        </CardContent>
      </Card>

      {/* Last Updated & Meta */}
      <p className="text-xs text-muted-foreground text-right">
        {t('health.lastUpdated', { time: healthData ? new Date().toLocaleString() : t('health.waiting') })} ·{' '}
        {t('health.pollInterval', { interval: POLL_MS / 1000 })} ·{' '}
        <code className="px-1 py-0.5 rounded bg-muted">{t('health.endpoint')}</code>
      </p>
    </div>
  )
}