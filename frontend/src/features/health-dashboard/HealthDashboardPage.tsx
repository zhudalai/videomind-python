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
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/utils'
import { Activity, Database, HardDrive, Layers, RefreshCw, AlertCircle, CheckCircle, Server } from 'lucide-react'

const COMPONENT_META: Record<
  'postgres' | 'redis' | 'qdrant' | 'minio',
  { label: string; icon: React.ComponentType<{ className?: string }> }
> = {
  postgres: { label: 'PostgreSQL', icon: Database },
  redis: { label: 'Redis', icon: Server },
  qdrant: { label: 'Qdrant', icon: Layers },
  minio: { label: 'MinIO', icon: HardDrive },
}

const POLL_MS = 30000
const HISTORY_CAP = 30 // 保留最近 30 次轮询（≈15 分钟）

interface HistoryPoint {
  t: number // epoch ms
  up: number // UP 组件数 (0-4)
}

export function HealthDashboardPage() {
  const [history, setHistory] = useState<HistoryPoint[]>([])
  const lastTsRef = useRef<number | null>(null)

  const { data: health, isLoading, isFetching, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['health', 'ready', 'dashboard'],
    queryFn: () => healthApi.ready(),
    refetchInterval: POLL_MS,
  })

  // 每次拿到新数据就追加点历史（按数据更新时间戳去重，避免 React StrictMode 双触发重复计数）
  useEffect(() => {
    if (!health || dataUpdatedAt === lastTsRef.current) return
    lastTsRef.current = dataUpdatedAt
    const upCount = Object.values(health.components).filter((v) => v === 'UP').length
    setHistory((prev) => {
      const next = [...prev, { t: dataUpdatedAt, up: upCount }]
      return next.length > HISTORY_CAP ? next.slice(next.length - HISTORY_CAP) : next
    })
  }, [health, dataUpdatedAt])

  const components = health?.components
  const overallUp = health?.status === 'UP'
  const lastUpdated = dataUpdatedAt ? new Date(dataUpdatedAt) : null

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
            <Activity className="h-7 w-7" />
            系统健康监控
          </h1>
          <p className="text-muted-foreground mt-1">
            基础设施组件连通性与可用性，每 {POLL_MS / 1000} 秒自动刷新
          </p>
        </div>
        <Button variant="outline" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw className={cn('h-4 w-4 mr-2', isFetching && 'animate-spin')} />
          手动刷新
        </Button>
      </div>

      {/* 整体状态横幅 */}
      {health && !overallUp && (
        <div className="flex items-center gap-3 p-4 rounded-lg border border-destructive/50 bg-destructive/10 text-destructive">
          <AlertCircle className="h-5 w-5 shrink-0" />
          <span className="font-medium">
            检测到基础设施异常：部分组件 DOWN，相关功能可能不可用。
          </span>
        </div>
      )}

      {/* 组件状态卡片 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {(Object.keys(COMPONENT_META) as Array<keyof typeof COMPONENT_META>).map((key) => {
          const meta = COMPONENT_META[key]
          const value = components?.[key]
          const up = value === 'UP'
          const Icon = meta.icon
          return (
            <Card key={key} className={cn(up ? 'border-success/30' : 'border-destructive/40')}>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Icon className={cn('h-5 w-5', up ? 'text-success' : 'text-destructive')} />
                    <CardTitle className="text-base">{meta.label}</CardTitle>
                  </div>
                  <Badge variant={up ? 'success' : 'destructive'} className="gap-1">
                    {up ? <CheckCircle className="h-3 w-3" /> : <AlertCircle className="h-3 w-3" />}
                    {value ?? '—'}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">
                  {isLoading
                    ? '检测中…'
                    : up
                      ? '连接正常'
                      : '无法连接，请检查容器是否在运行'}
                </p>
              </CardContent>
            </Card>
          )
        })}
      </div>

      {/* 可用性时间线 —— 前端本地累加轮询结果，非后端延迟数据 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" />
            可用性时间线
          </CardTitle>
          <CardDescription>
            最近 {history.length} 次轮询中 UP 组件数量（0–4）。轮询结果由前端本地累加，无后端延迟数据源。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {history.length === 0 ? (
            <div className="h-[240px] flex items-center justify-center text-muted-foreground">
              等待首轮轮询数据…
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={history} margin={{ top: 8, right: 16, bottom: 8, left: -16 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={['dataMin', 'dataMax']}
                  scale="time"
                  tickFormatter={(v) => new Date(v).toLocaleTimeString()}
                  stroke="currentColor"
                  className="text-xs text-muted-foreground"
                />
                <YAxis domain={[0, 4]} ticks={[0, 1, 2, 3, 4]} stroke="currentColor" className="text-xs text-muted-foreground" />
                <Tooltip
                  labelFormatter={(v) => new Date(Number(v)).toLocaleString()}
                  formatter={(v) => [`${v} / 4 个组件 UP`, '健康度']}
                  contentStyle={{ borderRadius: 8 }}
                />
                <Line
                  type="stepAfter"
                  dataKey="up"
                  stroke={overallUp ? '#16a34a' : '#dc2626'}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* 元信息 */}
      <p className="text-xs text-muted-foreground">
        上次更新：{lastUpdated ? lastUpdated.toLocaleString() : '尚未获取'} ·
        轮询间隔 {POLL_MS / 1000}s · 端点 <code className="px-1 py-0.5 rounded bg-muted">GET /api/health/ready</code>
      </p>
    </div>
  )
}
