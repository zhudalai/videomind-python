import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { userApi } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/utils'
import { Settings, Loader2, Check, Sun, Moon, Monitor, Save, AlertCircle, UserCircle } from 'lucide-react'

type Theme = 'light' | 'dark' | 'system'

export function SettingsPage() {
  const queryClient = useQueryClient()
  const [theme, setTheme] = useState<Theme>('system')
  const [defaultModel, setDefaultModel] = useState('')
  const [language, setLanguage] = useState('zh-CN')
  const [dirty, setDirty] = useState(false)

  const { data: config, isLoading } = useQuery({
    queryKey: ['user', 'config'],
    queryFn: () => userApi.getConfig(),
  })

  // 首次拉到配置后填入本地表单
  useEffect(() => {
    if (config) {
      setTheme((config.theme as Theme) ?? 'system')
      setDefaultModel(config.default_model ?? '')
      setLanguage(config.language ?? 'zh-CN')
      setDirty(false)
    }
  }, [config])

  const updateMutation = useMutation({
    mutationFn: (patch: Record<string, unknown>) => userApi.updateConfig(patch),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['user', 'config'] })
      setDirty(false)
    },
  })

  const handleSave = () => {
    updateMutation.mutate({ theme, default_model: defaultModel || null, language })
  }

  const markDirty = () => setDirty(true)

  const themeOptions: { value: Theme; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { value: 'light', label: '浅色', icon: Sun },
    { value: 'dark', label: '深色', icon: Moon },
    { value: 'system', label: '跟随系统', icon: Monitor },
  ]

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
          <Settings className="h-7 w-7" />
          设置
        </h1>
        <p className="text-muted-foreground mt-1">用户偏好配置（auth 实现前绑定到 dev 用户）</p>
      </div>

      {/* 当前身份 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UserCircle className="h-5 w-5" /> 当前身份
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="animate-pulse h-6 w-40 bg-muted rounded" />
          ) : config ? (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Dev 用户 ID：</span>
              <code className="px-2 py-0.5 rounded bg-muted">{config.user_id}</code>
              <Badge variant="secondary">dev</Badge>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">无法加载用户配置</p>
          )}
        </CardContent>
      </Card>

      {/* 偏好配置 */}
      <Card>
        <CardHeader>
          <CardTitle>偏好</CardTitle>
          <CardDescription>主题、默认模型、语言。保存后立即生效。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* 主题 */}
          <div className="space-y-2">
            <label className="text-sm font-medium">主题</label>
            <div className="grid grid-cols-3 gap-2">
              {themeOptions.map((opt) => {
                const Icon = opt.icon
                const active = theme === opt.value
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => { setTheme(opt.value); markDirty() }}
                    className={cn(
                      'flex flex-col items-center gap-2 p-4 rounded-md border-2 transition-colors',
                      active
                        ? 'border-primary bg-primary/5'
                        : 'border-muted bg-muted/30 hover:border-muted-foreground/40',
                    )}
                  >
                    <Icon className="h-5 w-5" />
                    <span className="text-sm">{opt.label}</span>
                  </button>
                )
              })}
            </div>
          </div>

          {/* 默认模型 */}
          <div className="space-y-2">
            <label className="text-sm font-medium">默认模型</label>
            <Input
              value={defaultModel}
              onChange={(e) => { setDefaultModel(e.target.value); markDirty() }}
              placeholder="如 deepseek-v4-flash-free（留空使用后端默认）"
            />
            <p className="text-xs text-muted-foreground">Agent 分析与 RAG 对话使用的默认 LLM 模型名</p>
          </div>

          {/* 语言 */}
          <div className="space-y-2">
            <label className="text-sm font-medium">语言</label>
            <select
              value={language}
              onChange={(e) => { setLanguage(e.target.value); markDirty() }}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="zh-CN">简体中文</option>
              <option value="en-US">English</option>
            </select>
          </div>

          {updateMutation.isError && (
            <p className="text-sm text-destructive flex items-center gap-1">
              <AlertCircle className="h-4 w-4" />
              保存失败：{(updateMutation.error as Error)?.message ?? '未知错误'}
            </p>
          )}
          {updateMutation.isSuccess && !dirty && (
            <p className="text-sm text-success flex items-center gap-1">
              <Check className="h-4 w-4" /> 已保存
            </p>
          )}

          <div className="flex justify-end">
            <Button onClick={handleSave} disabled={!dirty || updateMutation.isPending}>
              {updateMutation.isPending ? (
                <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> 保存中</>
              ) : (
                <><Save className="h-4 w-4 mr-2" /> 保存</>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
