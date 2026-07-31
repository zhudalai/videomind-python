import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useNavigate } from 'react-router-dom'
import { pipelineApi } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import { Badge } from '@/components/ui/Badge'
import { Upload, Link2, AlertCircle, CheckCircle, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useTranslation } from 'react-i18next'

// Validation schemas — messages are in English as fallback since zod schemas
// are module-level constants initialized before i18n is available.
const urlSchema = z.object({
  url: z.string().url('Invalid URL').min(5, 'URL too short'),
})

const fileSchema = z.object({
  file: z.instanceof(File, { message: 'Please select a video file' }).refine(
    (f) => f.size <= 2 * 1024 * 1024 * 1024,
    'File size must not exceed 2GB'
  ).refine(
    (f) => f.type.startsWith('video/'),
    'Please select a video file'
  ),
})

type UrlFormData = z.infer<typeof urlSchema>
type FileFormData = z.infer<typeof fileSchema>

const SUPPORTED_SITES = [
  { name: 'YouTube', pattern: /youtube\.com|youtu\.be/, icon: '🎬' },
  { name: 'Bilibili', pattern: /bilibili\.com|b23\.tv/, icon: '📺' },
  { name: 'Generic Video Link', pattern: /\.(mp4|mov|avi|mkv|webm|flv)(\?.*)?$/i, icon: '🔗' },
]

export function VideoUploadPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<'url' | 'file'>('url')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)

  // URL form
  const urlForm = useForm<UrlFormData>({
    resolver: zodResolver(urlSchema),
    defaultValues: { url: '' },
  })

  // File form
  const fileForm = useForm<FileFormData>({
    resolver: zodResolver(fileSchema),
    defaultValues: { file: undefined as any },
  })

  // File preview read directly from form state to avoid a separate selectedFile state that can drift out of sync
  const watchedFile = fileForm.watch('file') as File | undefined

  const handleUrlSubmit = async (data: UrlFormData) => {
    setIsSubmitting(true)
    setError(null)

    try {
      const response = await pipelineApi.submit({
        source_url: data.url,
      })

      if (response.media_id) {
        navigate(`/videos/${response.media_id}/progress`)
      }
    } catch (err: any) {
      setError(err.detail || t('utils.operationFailed'))
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleFileSubmit = async (data: FileFormData) => {
    if (!data.file) {
      setError(t('videoUpload.pleaseSelectFile'))
      return
    }
    setIsSubmitting(true)
    setError(null)
    try {
      // multipart/form-data → MinIO → pipeline_task(skip_download=true)
      const response = await pipelineApi.uploadFile(data.file)
      if (response.media_id) {
        navigate(`/videos/${response.media_id}/progress`)
      }
    } catch (err: any) {
      setError(err.detail || t('utils.operationFailed'))
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true)
    } else if (e.type === 'dragleave') {
      setDragActive(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(false)

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      fileForm.setValue('file', e.dataTransfer.files[0], { shouldValidate: true })
    }
  }

  // Trigger hidden file input (called by div onClick)
  const triggerFileInput = () => {
    if (isSubmitting) return
    const input = document.getElementById('video-file') as HTMLInputElement
    if (input && !input.disabled) {
      input.click()
    }
  }

  const detectSite = (url: string) => {
    return SUPPORTED_SITES.find(site => site.pattern.test(url))
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t('videoUpload.title')}</h1>
        <p className="text-muted-foreground mt-1">
          {t('videoUpload.subtitle')}
        </p>
      </div>

      {/* Error Alert */}
      {error && (
        <div className="flex items-center gap-2 p-4 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive">
          <AlertCircle className="h-5 w-5 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab as any}>
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="url">
            <Link2 className="h-4 w-4 mr-2" />
            {t('videoUpload.videoLink')}
          </TabsTrigger>
          <TabsTrigger value="file">
            <Upload className="h-4 w-4 mr-2" />
            {t('videoUpload.localFile')}
          </TabsTrigger>
        </TabsList>

        {/* URL Tab */}
        <TabsContent value="url" className="mt-4">
          <form onSubmit={urlForm.handleSubmit(handleUrlSubmit)} className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>{t('videoUpload.submitLink')}</CardTitle>
                <CardDescription>
                  {t('videoUpload.supportedPlatforms')}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="url">{t('videoUpload.videoLink')}</Label>
                  <Input
                    id="url"
                    placeholder={t('videoUpload.urlPlaceholder')}
                    {...urlForm.register('url')}
                    disabled={isSubmitting}
                  />
                  {urlForm.formState.errors.url && (
                    <p className="text-sm text-destructive">{urlForm.formState.errors.url.message}</p>
                  )}
                </div>

                {/* Detected site badge */}
                {urlForm.watch('url') && (
                  <div>
                    {(() => {
                      const site = detectSite(urlForm.watch('url'))
                      return site ? (
                        <Badge variant="secondary" className="gap-1">
                          <span>{site.icon}</span>
                          <span>{t('videoUpload.detectedSite', { site: site.name })}</span>
                          <CheckCircle className="h-3 w-3 text-green-500" />
                        </Badge>
                      ) : (
                        <Badge variant="outline">
                          {t('videoUpload.genericLink')}
                        </Badge>
                      )
                    })()}
                  </div>
                )}

                <Button type="submit" className="w-full" size="lg" loading={isSubmitting}>
                  <Loader2 className="h-4 w-4 mr-2" />
                  {t('videoUpload.startProcessing')}
                </Button>
              </CardContent>
            </Card>

            {/* Supported sites */}
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">{t('videoUpload.supportedPlatforms')}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-2">
                  {SUPPORTED_SITES.map(site => (
                    <Badge key={site.name} variant="outline" className="gap-1">
                      <span>{site.icon}</span>
                      {site.name}
                    </Badge>
                  ))}
                </div>
              </CardContent>
            </Card>
          </form>
        </TabsContent>

        {/* File Tab */}
        <TabsContent value="file" className="mt-4">
          <form onSubmit={fileForm.handleSubmit(handleFileSubmit)} className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>{t('videoUpload.fileTitle')}</CardTitle>
                <CardDescription>
                  {t('videoUpload.fileDesc')}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div
                  className={cn(
                    'border-2 border-dashed rounded-lg p-8 text-center transition-colors cursor-pointer',
                    dragActive ? 'border-primary bg-primary/5' : 'border-muted-foreground/25',
                    watchedFile ? 'border-green-500 bg-green-500/5' : ''
                  )}
                  onDragEnter={handleDrag}
                  onDragLeave={handleDrag}
                  onDragOver={handleDrag}
                  onDrop={handleDrop}
                  onClick={triggerFileInput}
                >
                  <input
                    type="file"
                    id="video-file"
                    accept="video/mp4,video/webm,video/quicktime,video/x-msvideo,video/x-matroska,video/x-flv"
                    onChange={(e) => {
                      const file = e.target.files?.[0]
                      // Don't use register('file') here: RHF would auto-insert the empty FileList at mount
                      // which makes watchedFile truthy but with undefined .name/.size, appearing as if a file
                      // was selected when it wasn't. Manually setValue with a single File (or undefined) so
                      // zod z.instanceof(File) validates correctly.
                      fileForm.setValue('file', file as any, { shouldValidate: true })
                    }}
                    style={{
                      position: 'absolute',
                      width: '1px',
                      height: '1px',
                      padding: 0,
                      margin: '-1px',
                      overflow: 'hidden',
                      clip: 'rect(0, 0, 0, 0)',
                      whiteSpace: 'nowrap',
                      border: 0,
                    }}
                    disabled={isSubmitting}
                  />
                  <label
                    htmlFor="video-file"
                    className={cn(
                      'cursor-pointer',
                      isSubmitting && 'opacity-50 cursor-not-allowed'
                    )}
                  >
                    <Upload className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
                    <p className="text-lg font-medium">
                      {watchedFile ? t('videoUpload.selectedFile') : t('videoUpload.clickOrDrag')}
                    </p>
                    {watchedFile && (
                      <div className="mt-2 text-sm text-muted-foreground">
                        <p>{watchedFile.name}</p>
                        <p>{(watchedFile.size / 1024 / 1024).toFixed(1)} MB</p>
                      </div>
                    )}
                  </label>
                </div>

                {fileForm.formState.errors.file && (
                  <p className="text-sm text-destructive text-center">
                    {fileForm.formState.errors.file.message}
                  </p>
                )}

                <Button
                  type="submit"
                  className="w-full"
                  size="lg"
                  loading={isSubmitting}
                  disabled={!watchedFile || isSubmitting}
                >
                  <Loader2 className="h-4 w-4 mr-2" />
                  {t('videoUpload.uploadingAndProcessing')}
                </Button>
              </CardContent>
            </Card>

            {/* File upload note */}
            <Card className="border-border/50">
              <CardContent className="pt-6">
                <div className="flex items-start gap-3 text-sm text-muted-foreground">
                  <span className="flex-shrink-0 mt-0.5">ℹ️</span>
                  <div>
                    <p className="font-medium text-foreground mb-1">{t('videoUpload.note')}</p>
                    <p>
                      {t('videoUpload.noteDesc')}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </form>
        </TabsContent>
      </Tabs>
    </div>
  )
}