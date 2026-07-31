import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { userApi } from '@/lib/api'
import i18n from '@/i18n'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/utils'
import { Settings, Loader2, Check, Sun, Moon, Monitor, Save, AlertCircle, UserCircle } from 'lucide-react'

type Theme = 'light' | 'dark' | 'system'

export function SettingsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [theme, setTheme] = useState<Theme>('system')
  const [defaultModel, setDefaultModel] = useState('')
  const [language, setLanguage] = useState('en-US')
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
      setLanguage(config.language ?? 'en-US')
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
    updateMutation.mutate({ theme, default_model: defaultModel || null })
  }

  // Language switch is auto-saved (no separate "Save" needed): the dropdown is the only UX, and
  // DB sync keeps LanguageContext from forcing a re-hydrate on the next /query refetch.
  const handleLanguageChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const prevLang = language
    const newLang = e.target.value
    setLanguage(newLang)
    void i18n.changeLanguage(newLang)
    // Optimistically update query cache so LanguageContext won't re-emit a stale DB value.
    queryClient.setQueryData<typeof config>(['user', 'config'], (old) =>
      old ? { ...old, language: newLang } : old,
    )
    updateMutation.mutate(
      { language: newLang },
      {
        onError: () => {
          setLanguage(prevLang)
          void i18n.changeLanguage(prevLang)
          queryClient.setQueryData<typeof config>(['user', 'config'], (old) =>
            old ? { ...old, language: prevLang } : old,
          )
        },
      },
    )
  }

  const markDirty = () => setDirty(true)

  const themeOptions: { value: Theme; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { value: 'light', label: t('settings.themeLight'), icon: Sun },
    { value: 'dark', label: t('settings.themeDark'), icon: Moon },
    { value: 'system', label: t('settings.themeSystem'), icon: Monitor },
  ]

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
          <Settings className="h-7 w-7" />
          {t('settings.title')}
        </h1>
        <p className="text-muted-foreground mt-1">{t('settings.subtitle')}</p>
      </div>

      {/* 当前身份 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UserCircle className="h-5 w-5" /> {t('settings.currentIdentity')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="animate-pulse h-6 w-40 bg-muted rounded" />
          ) : config ? (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">{t('settings.devIdentity')}</span>
              <code className="px-2 py-0.5 rounded bg-muted">{config.user_id}</code>
              <Badge variant="secondary">dev</Badge>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{t('utils.unknownError')}</p>
          )}
        </CardContent>
      </Card>

      {/* 偏好配置 */}
      <Card>
        <CardHeader>
          <CardTitle>{t('settings.preferences')}</CardTitle>
          <CardDescription>{t('settings.preferencesDesc')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* 主题 */}
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('settings.theme')}</label>
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
            <label className="text-sm font-medium">{t('settings.defaultModel')}</label>
            <Input
              value={defaultModel}
              onChange={(e) => { setDefaultModel(e.target.value); markDirty() }}
              placeholder={t('settings.defaultModelPlaceholder')}
            />
            <p className="text-xs text-muted-foreground">{t('settings.defaultModelHint')}</p>
          </div>

          {/* Language */}
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('settings.language')}</label>
            <select
              value={language}
              onChange={handleLanguageChange}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="zh-CN">{t('settings.languageZh')}</option>
              <option value="en-US">{t('settings.languageEn')}</option>
            </select>
          </div>

          {updateMutation.isError && (
            <p className="text-sm text-destructive flex items-center gap-1">
              <AlertCircle className="h-4 w-4" />
              {t('settings.saveFailed', { error: (updateMutation.error as Error)?.message ?? t('utils.unknownError') })}
            </p>
          )}
          {updateMutation.isSuccess && !dirty && (
            <p className="text-sm text-success flex items-center gap-1">
              <Check className="h-4 w-4" /> {t('utils.saved')}
            </p>
          )}

          <div className="flex justify-end">
            <Button onClick={handleSave} disabled={!dirty || updateMutation.isPending}>
              {updateMutation.isPending ? (
                <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> {t('utils.saving')}</>
              ) : (
                <><Save className="h-4 w-4 mr-2" /> {t('settings.save')}</>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
