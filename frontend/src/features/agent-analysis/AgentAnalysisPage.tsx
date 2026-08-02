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
  Film,
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
import { useTranslation } from 'react-i18next'

const STATUS_FLOW = ['pending', 'planning', 'executing', 'critic_check', 'completed'] as const
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
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [goal, setGoal] = useState('')
  const [selectedMediaIds, setSelectedMediaIds] = useState<string[]>([])
  const [showMediaSelector, setShowMediaSelector] = useState(false)
  const [maxRounds, setMaxRounds] = useState<number>(2)
  const [taskId, setTaskId] = useState<string | null>(null)

  const handleMediaToggle = (id: string) =>
    setSelectedMediaIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )

  const isAtMaxVideos = selectedMediaIds.length >= 2

  const { data: userConfig } = useQuery({
    queryKey: ['user', 'config', 'dev'],
    queryFn: () => userApi.getConfig(),
    staleTime: 5 * 60 * 1000,
  })

  const { data: videos } = useQuery({
    queryKey: ['videos', 'recent', 'analysis'],
    queryFn: () => videoApi.list({ page: 1, page_size: 20, status: 'ready' }),
    staleTime: 30000,
  })

  // 任务状态轮询
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
        media_ids: selectedMediaIds,
        user_id: userConfig!.user_id,
        max_rounds: maxRounds,
      }),
    onSuccess: (data) => {
      setTaskId(data.task_id)
      queryClient.invalidateQueries({ queryKey: ['agent', 'task', data.task_id] })
    },
  })

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

  const ctaDisabled = !goal.trim() || selectedMediaIds.length === 0 || !userConfig || createMutation.isPending
  const canSubmit = !taskId || hasTerminal

  const handleSubmit = () => {
    if (!goal.trim() || selectedMediaIds.length === 0 || !userConfig) return
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
          {t('agentAnalysis.title')}
        </h1>
        <p className="text-muted-foreground mt-1">
          {t('agentAnalysis.subtitle')}
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t('agentAnalysis.analysisGoal')}</CardTitle>
          <CardDescription>{t('agentAnalysis.goalPlaceholder')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('agentAnalysis.analysisGoal')}</label>
            <Textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder={t('agentAnalysis.goalPlaceholder')}
              className="min-h-[120px]"
              maxLength={5000}
            />
            <p className="text-xs text-muted-foreground text-right">{goal.length} / 5000</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-sm font-medium">{t('agentAnalysis.videoSources')}</label>
                <Button type="button" variant="ghost" size="sm"
                  onClick={() => setShowMediaSelector((v) => !v)}>
                  {t(showMediaSelector ? 'agentAnalysis.collapseVideoSources' : 'agentAnalysis.expandVideoSources')}
                </Button>
              </div>
              {selectedMediaIds.length > 0 && (
                <Badge variant="secondary" className="gap-1 w-fit">
                  <Brain className="h-3 w-3" />
                  {t('agentAnalysis.videoSourcesCount', { count: selectedMediaIds.length })}
                </Badge>
              )}
              {showMediaSelector && (
                <div className="border rounded-lg p-2 max-h-64 overflow-y-auto space-y-1">
                  {videos?.items?.map((v) => {
                    const isSelected = selectedMediaIds.includes(v.id)
                    const isDisabled = isAtMaxVideos && !isSelected
                    return (
                      <label key={v.id} className={cn(
                        'flex items-center gap-3 p-2 rounded-lg cursor-pointer transition-colors',
                        isSelected
                          ? 'bg-primary/10 border border-primary/20'
                          : isDisabled
                          ? 'opacity-50 cursor-not-allowed'
                          : 'hover:bg-muted/50',
                      )}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => handleMediaToggle(v.id)}
                          disabled={isDisabled}
                          className="h-4 w-4 rounded border-input text-primary focus:ring-primary disabled:cursor-not-allowed" />
                        <span className="flex-1 text-sm">{v.filename}</span>
                        {v.duration_ms != null && (
                          <span className="text-xs text-muted-foreground">
                            {(v.duration_ms / 1000).toFixed(0)}s
                          </span>
                        )}
                      </label>
                    )
                  })}
                  {isAtMaxVideos && (
                    <p className="text-xs text-muted-foreground p-2 text-center">
                      {t('agentAnalysis.maxVideosReached')}
                    </p>
                  )}
                  {videos && !videos.items?.length && (
                    <p className="text-xs text-muted-foreground p-2">
                      {t('agentAnalysis.noVideosForAnalysis')}
                    </p>
                  )}
                </div>
              )}
              {!showMediaSelector && (
                <p className="text-xs text-muted-foreground">{t('agentAnalysis.selectAtLeastOne')}</p>
              )}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">{t('agentAnalysis.maxRounds')}</label>
              <select
                value={maxRounds}
                onChange={(e) => setMaxRounds(Number(e.target.value))}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value={1}>{t('agentAnalysis.oneRound')}</option>
                <option value={2}>{t('agentAnalysis.twoRounds')}</option>
              </select>
            </div>
          </div>

          {createMutation.isError && (
            <p className="text-sm text-destructive flex items-center gap-1">
              <AlertCircle className="h-4 w-4" />
              {t('utils.operationFailed')}
            </p>
          )}

          <div className="flex gap-2">
            <Button onClick={handleSubmit} disabled={ctaDisabled || !canSubmit}>
              {createMutation.isPending ? (
                <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> {t('agentAnalysis.submitting')}</>
              ) : (
                <><Play className="h-4 w-4 mr-2" /> {taskId && !hasTerminal ? t('agentAnalysis.alreadySubmitted') : t('agentAnalysis.startAnalysis')}</>
              )}
            </Button>
            {taskId && (
              <Button variant="outline" onClick={handleReset}>
                {t('agentAnalysis.reset')}
              </Button>
            )}
          </div>
          {!userConfig && <p className="text-xs text-muted-foreground">{t('agentAnalysis.devIdentityHint')}</p>}
        </CardContent>
      </Card>

      {taskId && status && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span className="flex items-center gap-2">
                {isRunning && <Loader2 className="h-5 w-5 animate-spin" />}
                {isCompleted && <CheckCircle className="h-5 w-5 text-success" />}
                {isFailed && <XCircle className="h-5 w-5 text-destructive" />}
                {t('agentAnalysis.taskProgress')}
              </span>
              <Badge variant={isCompleted ? 'success' : isFailed ? 'destructive' : 'info'} className="gap-1">
                {status.status}
              </Badge>
            </CardTitle>
            <CardDescription>
              {t('agentAnalysis.roundInfo', { current: status.current_round, max: status.max_rounds, date: formatDate(status.created_at) })}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
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
                    <span className="mt-1 text-xs text-muted-foreground">{s}</span>
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

      {Array.isArray(checkpoints) && checkpoints.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ScrollText className="h-5 w-5" />
              {t('agentAnalysis.executionTrace')}
            </CardTitle>
            <CardDescription>{t('agentAnalysis.traceDesc')}</CardDescription>
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
                      <p className="text-muted-foreground">{t('agentAnalysis.planningTasks', { count: cp.plan_json.tasks?.length ?? 0 })}</p>
                    )}
                    {!cp.feedback && !cp.plan_json && cp.result_json && (
                      <p className="text-muted-foreground">{t('agentAnalysis.resultGenerated', { title: cp.result_json.title || t('agentAnalysis.completed') })}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      // 最终结果
      {result && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span className="flex items-center gap-2">
                <Sparkles className="h-5 w-5" />
                {result.title}
              </span>
              <Badge variant={result.critic_passed ? 'success' : 'warning'} className="gap-1">
                {result.critic_passed ? (
                  <><ShieldCheck className="h-3 w-3" /> {t('agentAnalysis.criticPassed')}</>
                ) : (
                  <><ShieldAlert className="h-3 w-3" /> {t('agentAnalysis.criticFailed')}</>
                )}
              </Badge>
            </CardTitle>
            <CardDescription>
              {t('agentAnalysis.roundInfo', { current: result.total_rounds, max: result.total_rounds, date: '' })} · {result.cost_usd != null ? `$${result.cost_usd.toFixed(4)}` : t('agentAnalysis.costPending')}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {result.critic_feedback && (
              <div className="text-sm bg-muted/50 p-3 rounded-md">
                <span className="font-medium">{t('agentAnalysis.criticFeedback')}:</span> {result.critic_feedback}
              </div>
            )}

            {Array.isArray(result.conclusions_json) && result.conclusions_json.length > 0 && (
              <section>
                <h3 className="font-medium mb-2 flex items-center gap-2">
                  <Brain className="h-4 w-4" /> {t('agentAnalysis.conclusions')}
                </h3>
                <ul className="space-y-2">
                  {(result.conclusions_json as any[]).map((c, i) => (
                    <li key={i} className="text-sm p-2 rounded-md bg-muted/30">
                      <p>{c.point ?? c}</p>
                      {typeof c.confidence === 'number' && (
                        <p className="text-xs text-muted-foreground mt-1">{t('agentAnalysis.confidence', { pct: (c.confidence * 100).toFixed(0) })}</p>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {Array.isArray(result.evidence_json) && result.evidence_json.length > 0 && (
              <section>
                <h3 className="font-medium mb-2 flex items-center gap-2">
                  <Eye className="h-4 w-4" /> {t('agentAnalysis.evidence')}
                </h3>
                <ul className="space-y-1.5 text-sm">
                  {(result.evidence_json as any[]).map((e, i) => (
                    <li key={i} className="flex flex-col gap-1 text-sm">
                      <span className="flex items-center gap-2 flex-wrap">
                        {e.media_title && (
                          <Badge variant="secondary" className="shrink-0 text-xs gap-1">
                            <Film className="h-3 w-3" /> {e.media_title}
                          </Badge>
                        )}
                        {e.source_type && (
                          <Badge variant="outline" className="shrink-0 text-xs">{e.source_type}</Badge>
                        )}
                      </span>
                      <span className="text-muted-foreground pl-1">
                        {e.content ?? JSON.stringify(e)}
                        {(typeof e.start_ms === 'number' || typeof e.end_ms === 'number') && (
                          <span className="ml-2 text-xs">
                            · {e.start_ms != null ? (e.start_ms / 1000).toFixed(1) : '—'}
                            –{e.end_ms != null ? (e.end_ms / 1000).toFixed(1) : '—'}s
                          </span>
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
                  <Sparkles className="h-4 w-4" /> {t('agentAnalysis.suggestions')}
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