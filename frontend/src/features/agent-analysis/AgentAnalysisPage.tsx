import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { analysisApi, userApi, videoApi } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Textarea } from '@/components/ui/Textarea'
import { Progress } from '@/components/ui/Progress'
import { cn, formatDate } from '@/lib/utils'
import {
  Brain,
  Loader2,
  Play,
  CheckCircle,
  XCircle,
  AlertCircle,
  Eye,
  ScrollText,
  ShieldCheck,
  ShieldAlert,
  Sparkles,
} from 'lucide-react'

const STATUS_FLOW = ['pending', 'planning', 'executing', 'critic_check', 'completed'] as const
const STATUS_LABEL: Record<string, string> = {
  pending: '待启动',
  planning: '规划中',
  executing: '执行中',
  critic_check: '评审中',
  completed: '已完成',
  failed: '失败',
}

const STAGE_INDEX: Record<string, number> = {
  pending: 0,
  planning: 1,
  executing: 2,
  critic_check: 3,
  completed: 4,
  failed: -1,
}

const POLLING_STATUSES = new Set(['pending', 'planning', 'executing', 'critic_check'])

export function AgentAnalysisPage() {
  const queryClient = useQueryClient()
  const [goal, setGoal] = useState('')
  const [mediaId, setMediaId] = useState<string>('')
  const [maxRounds, setMaxRounds] = useState<number>(2)
  const [taskId, setTaskId] = useState<string | null>(null)

  // 拿 dev user id（无 auth 时后端 bootstrap 一个 dev 用户）
  const { data: userConfig } = useQuery({
    queryKey: ['user', 'config', 'dev'],
    queryFn: () => userApi.getConfig(),
    staleTime: 5 * 60 * 1000,
  })

  // 视频候选（仅 ready 可分析）
  const { data: videos } = useQuery({
    queryKey: ['videos', 'recent', 'analysis'],
    queryFn: () => videoApi.list({ page: 1, page_size: 20, status: 'ready' }),
    staleTime: 30000,
  })

  // 任务状态轮询 —— 顶层 useQuery，用 enabled/refetchInterval 控制
  const statusQuery = useQuery({
    queryKey: ['agent', 'task', taskId],
    queryFn: () => analysisApi.getStatus(taskId!),
    enabled: !!taskId,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s && POLLING_STATUSES.has(s) ? 2000 : false
    },
  })

  const status = statusQuery.data
  const isRunning = status ? POLLING_STATUSES.has(status.status) : false
  const isCompleted = status?.status === 'completed'
  const isFailed = status?.status === 'failed'
  const hasTerminal = isCompleted || isFailed

  // 提交分析任务
  const createMutation = useMutation({
    mutationFn: () =>
      analysisApi.create({
        goal: goal.trim(),
        media_id: mediaId,
        user_id: userConfig!.user_id,
        max_rounds: maxRounds,
      }),
    onSuccess: (data) => {
      setTaskId(data.task_id)
      queryClient.invalidateQueries({ queryKey: ['agent', 'task', data.task_id] })
    },
  })

  // 完成后拉取结果与断点
  const { data: result } = useQuery({
    queryKey: ['agent', 'task', taskId, 'result'],
    queryFn: () => analysisApi.getResult(taskId!),
    enabled: isCompleted,
  })

  const { data: checkpoints } = useQuery({
    queryKey: ['agent', 'task', taskId, 'checkpoints'],
    queryFn: () => analysisApi.getCheckpoints(taskId!),
    enabled: !!taskId && !!status && status.status !== 'pending',
  })

  const ctaDisabled = !goal.trim() || !mediaId || !userConfig || createMutation.isPending
  const canSubmit = !taskId || hasTerminal

  const handleSubmit = () => {
    if (!goal.trim() || !mediaId || !userConfig) return
    createMutation.mutate()
  }

  const handleReset = () => {
    setTaskId(null)
    setGoal('')
    queryClient.invalidateQueries({ queryKey: ['agent', 'task'] })
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
          <Brain className="h-7 w-7" />
          Agent 深度分析
        </h1>
        <p className="text-muted-foreground mt-1">
          目标驱动的多轮分析：Planner 规划 → Executor 执行 → Critic 评审，最多 2 轮闭环
        </p>
      </div>

      {/* 目标输入表单 */}
      <Card>
        <CardHeader>
          <CardTitle>分析目标</CardTitle>
          <CardDescription>输入你想分析的问题，选择目标视频后提交</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">分析目标</label>
            <Textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="例如：分析这个视频讲解的商业模式，总结核心论点与支撑证据"
              className="min-h-[120px]"
              maxLength={5000}
            />
            <p className="text-xs text-muted-foreground text-right">{goal.length} / 5000</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">目标视频</label>
              <select
                value={mediaId}
                onChange={(e) => setMediaId(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="">— 选择视频 —</option>
                {videos?.items?.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.filename}
                  </option>
                ))}
              </select>
              {videos && !videos.items?.length && (
                <p className="text-xs text-muted-foreground">暂无可分析视频，请先上传</p>
              )}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">最大轮数</label>
              <select
                value={maxRounds}
                onChange={(e) => setMaxRounds(Number(e.target.value))}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value={1}>1 轮（不重试）</option>
                <option value={2}>2 轮（Critic 不通过可重试）</option>
              </select>
            </div>
          </div>

          {createMutation.isError && (
            <p className="text-sm text-destructive flex items-center gap-1">
              <AlertCircle className="h-4 w-4" />
              提交失败：{(createMutation.error as Error)?.message ?? '未知错误'}
            </p>
          )}

          <div className="flex gap-2">
            <Button onClick={handleSubmit} disabled={ctaDisabled || !canSubmit}>
              {createMutation.isPending ? (
                <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> 提交中</>
              ) : (
                <><Play className="h-4 w-4 mr-2" /> {taskId && !hasTerminal ? '已提交' : '开始分析'}</>
              )}
            </Button>
            {taskId && (
              <Button variant="outline" onClick={handleReset}>
                清空重置
              </Button>
            )}
          </div>
          {!userConfig && <p className="text-xs text-muted-foreground">正在获取 dev 用户身份…</p>}
        </CardContent>
      </Card>

      {/* 任务进度 */}
      {taskId && status && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span className="flex items-center gap-2">
                {isRunning && <Loader2 className="h-5 w-5 animate-spin" />}
                {isCompleted && <CheckCircle className="h-5 w-5 text-success" />}
                {isFailed && <XCircle className="h-5 w-5 text-destructive" />}
                任务进度
              </span>
              <Badge variant={isCompleted ? 'success' : isFailed ? 'destructive' : 'info'} className="gap-1">
                {STATUS_LABEL[status.status] ?? status.status}
              </Badge>
            </CardTitle>
            <CardDescription>
              第 {status.current_round} / {status.max_rounds} 轮 · 创建于 {formatDate(status.created_at)}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* 状态机时间轴 */}
            <div className="flex items-center justify-between">
              {STATUS_FLOW.map((s, i) => {
                const currentIdx = STAGE_INDEX[status.status] ?? -1
                const done = i < currentIdx || (isCompleted && i === currentIdx)
                const active = i === currentIdx && isRunning
                return (
                  <div key={s} className="flex-1 flex flex-col items-center">
                    <div
                      className={cn(
                        'h-9 w-9 rounded-full flex items-center justify-center border-2 transition-colors',
                        done && 'bg-success border-success text-success-foreground',
                        active && 'border-primary bg-primary/10',
                        !done && !active && 'border-muted bg-muted',
                      )}
                    >
                      {done ? (
                        <CheckCircle className="h-5 w-5" />
                      ) : active ? (
                        <Loader2 className="h-5 w-5 animate-spin" />
                      ) : (
                        <span className="text-xs">{i + 1}</span>
                      )}
                    </div>
                    <span className="mt-1 text-xs text-muted-foreground">{STATUS_LABEL[s]}</span>
                  </div>
                )
              })}
            </div>
            <Progress value={isCompleted ? 100 : ((STAGE_INDEX[status.status] ?? 0) / 4) * 100} className="h-1.5" />

            {isFailed && status.error_message && (
              <p className="text-sm text-destructive bg-destructive/10 p-3 rounded-md">
                {status.error_message}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Checkpoints 执行轨迹 */}
      {Array.isArray(checkpoints) && checkpoints.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ScrollText className="h-5 w-5" />
              执行轨迹（Checkpoints）
            </CardTitle>
            <CardDescription>每轮每阶段的状态快照，支撑断点恢复与可观测</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {checkpoints.map((cp: any, i: number) => (
                <div key={cp.id ?? i} className="flex items-start gap-3 text-sm p-2 rounded-md bg-muted/40">
                  <Badge variant="outline" className="shrink-0">
                    R{cp.round} · {cp.phase}
                  </Badge>
                  <div className="flex-1 min-w-0">
                    {cp.feedback && <p className="text-muted-foreground line-clamp-2">{cp.feedback}</p>}
                    {!cp.feedback && cp.plan_json && (
                      <p className="text-muted-foreground">规划 {cp.plan_json.tasks?.length ?? 0} 个子任务</p>
                    )}
                    {!cp.feedback && !cp.plan_json && cp.result_json && (
                      <p className="text-muted-foreground">结果：{cp.result_json.title || '已生成'}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 最终结果 */}
      {result && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span className="flex items-center gap-2">
                <Sparkles className="h-5 w-5" />
                {result.title}
              </span>
              <Badge variant={result.critic_passed ? 'success' : 'warning'} className="gap-1">
                {result.critic_passed ? <ShieldCheck className="h-3 w-3" /> : <ShieldAlert className="h-3 w-3" />}
                {result.critic_passed ? 'Critic 通过' : 'Critic 未通过'}
              </Badge>
            </CardTitle>
            <CardDescription>
              共 {result.total_rounds} 轮 · {result.cost_usd != null ? `$${result.cost_usd.toFixed(4)}` : '费用待计'}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {result.critic_feedback && (
              <div className="text-sm bg-muted/50 p-3 rounded-md">
                <span className="font-medium">Critic 反馈：</span> {result.critic_feedback}
              </div>
            )}

            {Array.isArray(result.conclusions_json) && result.conclusions_json.length > 0 && (
              <section>
                <h3 className="font-medium mb-2 flex items-center gap-2">
                  <Brain className="h-4 w-4" /> 结论
                </h3>
                <ul className="space-y-2">
                  {(result.conclusions_json as any[]).map((c, i) => (
                    <li key={i} className="text-sm p-2 rounded-md bg-muted/30">
                      <p>{c.point ?? c}</p>
                      {typeof c.confidence === 'number' && (
                        <p className="text-xs text-muted-foreground mt-1">置信度 {(c.confidence * 100).toFixed(0)}%</p>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {Array.isArray(result.evidence_json) && result.evidence_json.length > 0 && (
              <section>
                <h3 className="font-medium mb-2 flex items-center gap-2">
                  <Eye className="h-4 w-4" /> 证据
                </h3>
                <ul className="space-y-1.5 text-sm">
                  {(result.evidence_json as any[]).map((e, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <Badge variant="outline" className="shrink-0 text-xs">
                        {e.source ?? 'evidence'}
                      </Badge>
                      <span className="text-muted-foreground">
                        {e.content ?? JSON.stringify(e)}
                        {typeof e.timestamp_ms === 'number' && (
                          <span className="ml-2 text-xs">· {(e.timestamp_ms / 1000).toFixed(1)}s</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {Array.isArray(result.suggestions_json) && result.suggestions_json.length > 0 && (
              <section>
                <h3 className="font-medium mb-2 flex items-center gap-2">
                  <Sparkles className="h-4 w-4" /> 建议
                </h3>
                <ul className="space-y-1.5 text-sm">
                  {(result.suggestions_json as string[]).map((s, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <span className="text-primary">•</span>
                      <span>{s}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
