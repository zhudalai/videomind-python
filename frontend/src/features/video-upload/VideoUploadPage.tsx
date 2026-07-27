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

// Validation schemas
const urlSchema = z.object({
  url: z.string().url('请输入有效的 URL').min(5, 'URL 太短'),
})

const fileSchema = z.object({
  file: z.instanceof(File, { message: '请选择视频文件' }).refine(
    (f) => f.size <= 2 * 1024 * 1024 * 1024,
    '文件大小不能超过 2GB'
  ).refine(
    (f) => f.type.startsWith('video/'),
    '请选择视频文件'
  ),
})

type UrlFormData = z.infer<typeof urlSchema>
type FileFormData = z.infer<typeof fileSchema>

const SUPPORTED_SITES = [
  { name: 'YouTube', pattern: /youtube\.com|youtu\.be/, icon: '🎬' },
  { name: 'Bilibili', pattern: /bilibili\.com|b23\.tv/, icon: '📺' },
  { name: '直接视频链接', pattern: /\.(mp4|mov|avi|mkv|webm|flv)(\?.*)?$/i, icon: '🔗' },
]

export function VideoUploadPage() {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<'url' | 'file'>('url')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
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
      setError(err.detail || '提交失败，请重试')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleFileSubmit = async (data: FileFormData) => {
    if (!data.file) {
      setError('请选择视频文件')
      return
    }
    setIsSubmitting(true)
    setError(null)
    try {
      // 走 multipart/form-data → MinIO → pipeline_task(skip_download=true)
      const response = await pipelineApi.uploadFile(data.file)
      if (response.media_id) {
        navigate(`/videos/${response.media_id}/progress`)
      }
    } catch (err: any) {
      setError(err.detail || '上传失败，请重试')
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
      fileForm.setValue('file', e.dataTransfer.files[0])
      setSelectedFile(e.dataTransfer.files[0])
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      fileForm.setValue('file', e.target.files[0])
      setSelectedFile(e.target.files[0])
    }
  }

  const detectSite = (url: string) => {
    return SUPPORTED_SITES.find(site => site.pattern.test(url))
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">上传视频</h1>
        <p className="text-muted-foreground mt-1">
          支持 YouTube、Bilibili 链接或直接上传本地视频文件
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
            视频链接
          </TabsTrigger>
          <TabsTrigger value="file">
            <Upload className="h-4 w-4 mr-2" />
            本地文件
          </TabsTrigger>
        </TabsList>

        {/* URL Tab */}
        <TabsContent value="url" className="mt-4">
          <form onSubmit={urlForm.handleSubmit(handleUrlSubmit)} className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>提交视频链接</CardTitle>
                <CardDescription>
                  支持 YouTube、Bilibili 等主流视频平台，或直接视频文件直链
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="url">视频链接</Label>
                  <Input
                    id="url"
                    placeholder="https://www.youtube.com/watch?v=... 或 https://www.bilibili.com/video/..."
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
                          <span>检测到: {site.name}</span>
                          <CheckCircle className="h-3 w-3 text-green-500" />
                        </Badge>
                      ) : (
                        <Badge variant="outline">
                          通用视频链接
                        </Badge>
                      )
                    })()}
                  </div>
                )}

                <Button type="submit" className="w-full" size="lg" loading={isSubmitting}>
                  <Loader2 className="h-4 w-4 mr-2" />
                  开始处理
                </Button>
              </CardContent>
            </Card>

            {/* Supported sites */}
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">支持的平台</CardTitle>
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
                <CardTitle>上传本地视频</CardTitle>
                <CardDescription>
                  支持 MP4、MOV、AVI、MKV、WebM、FLV 格式，最大 2GB
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div
                  className={cn(
                    'border-2 border-dashed rounded-lg p-8 text-center transition-colors',
                    dragActive ? 'border-primary bg-primary/5' : 'border-muted-foreground/25',
                    selectedFile ? 'border-green-500 bg-green-500/5' : ''
                  )}
                  onDragEnter={handleDrag}
                  onDragLeave={handleDrag}
                  onDragOver={handleDrag}
                  onDrop={handleDrop}
                >
                  <input
                    type="file"
                    id="video-file"
                    accept="video/*"
                    onChange={handleFileChange}
                    className="hidden"
                    disabled={isSubmitting}
                    ref={(el) => el && fileForm.register('file').ref(el)}
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
                      {selectedFile ? '已选择文件' : '点击或拖拽上传视频文件'}
                    </p>
                    {selectedFile && (
                      <div className="mt-2 text-sm text-muted-foreground">
                        <p>{selectedFile.name}</p>
                        <p>{(selectedFile.size / 1024 / 1024).toFixed(1)} MB</p>
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
                  disabled={!selectedFile || isSubmitting}
                >
                  <Loader2 className="h-4 w-4 mr-2" />
                  上传并处理
                </Button>
              </CardContent>
            </Card>

            {/* File upload note */}
            <Card className="border-border/50">
              <CardContent className="pt-6">
                <div className="flex items-start gap-3 text-sm text-muted-foreground">
                  <span className="flex-shrink-0 mt-0.5">ℹ️</span>
                  <div>
                    <p className="font-medium text-foreground mb-1">注意</p>
                    <p>
                      文件上传需要后端实现 <code className="px-1 bg-muted rounded">/api/videos/upload</code> 端点，
                      支持 multipart/form-data 上传到 MinIO 并返回 content_hash，
                      前端再调用 pipeline 提交接口。
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