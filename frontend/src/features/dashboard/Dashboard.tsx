import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { healthApi, videoApi } from '@/lib/api'
import { cn, getStatusColor, formatDuration, formatFileSize, formatRelativeTime, getVideoThumbnailUrl } from '@/lib/utils'
import { Link, useNavigate } from 'react-router-dom'
import type { MediaFileResponse } from '@/types/api'
import {
  Video,
  Upload,
  MessageSquare,
  Brain,
  Activity,
  CheckCircle,
  AlertCircle,
  Clock,
  FileText,
  Trash2,
  Eye,
  Loader2,
} from 'lucide-react'

const quickActions = [
  { name: 'dashboard.uploadVideoTitle', href: '/upload', icon: Upload, descriptionKey: 'dashboard.uploadVideoDesc' },
  { name: 'dashboard.manageVideosTitle', href: '/videos', icon: Video, descriptionKey: 'dashboard.manageVideosDesc' },
  { name: 'dashboard.smartQnATitle', href: '/chat', icon: MessageSquare, descriptionKey: 'dashboard.smartQnADesc' },
  { name: 'dashboard.deepAnalysisTitle', href: '/analysis', icon: Brain, descriptionKey: 'dashboard.deepAnalysisDesc' },
]

export function Dashboard() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const { data: health, isLoading: healthLoading } = useQuery({
    queryKey: ['health', 'ready'],
    queryFn: () => healthApi.ready(),
    refetchInterval: 30000,
  })

  const { data: videos, isLoading: videosLoading } = useQuery({
    queryKey: ['videos', 'recent'],
    queryFn: () => videoApi.list({ page: 1, page_size: 5, status: 'ready' }),
  })

  const deleteMutation = useMutation({
    mutationFn: (mediaId: string) => videoApi.delete(mediaId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['videos'] })
    },
  })

  const handleDelete = (mediaId: string) => {
    if (!confirm(t('dashboard.deleteConfirm'))) return
    deleteMutation.mutate(mediaId)
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">VideoMind</h1>
          <p className="text-muted-foreground mt-1">{t('app.subtitle')}</p>
        </div>
        <Link to="/upload">
          <Button size="lg"><Upload className="h-4 w-4 mr-2" /> {t('dashboard.newVideo')}</Button>
        </Link>
      </div>

      {/* System Health */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" />
            {t('dashboard.systemHealth')}
          </CardTitle>
          {healthLoading ? (
            <span className="animate-pulse h-4 w-20 bg-muted rounded" />
          ) : (
            <Badge variant={health?.status === 'UP' ? 'success' : 'destructive'} className="gap-1">
              {health?.status === 'UP' ? (
                <>
                  <CheckCircle className="h-3 w-3" />
                  {t('dashboard.allHealthy')}
                </>
              ) : (
                <>
                  <AlertCircle className="h-3 w-3" />
                  {t('dashboard.someIssues')}
                </>
              )}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {health?.components && Object.entries(health.components).map(([key, value]) => (
              <div key={key} className="flex items-center gap-3 p-3 rounded-lg bg-muted/50">
                <Badge variant={value === 'UP' ? 'success' : 'destructive'} className="gap-1">
                  {value === 'UP' ? <CheckCircle className="h-3 w-3" /> : <AlertCircle className="h-3 w-3" />}
                  {key.charAt(0).toUpperCase() + key.slice(1)}
                </Badge>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Quick Actions */}
      <div>
        <h2 className="text-xl font-semibold mb-4">{t('dashboard.quickActions')}</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {quickActions.map(action => (
            <Link key={action.href} to={action.href}>
              <Card className="h-full hover:shadow-md transition-shadow cursor-pointer">
                <CardContent className="p-6 flex flex-col items-center text-center">
                  <action.icon className="h-10 w-10 text-primary mb-3" />
                  <h3 className="font-medium">{t(action.name)}</h3>
                  <p className="text-sm text-muted-foreground mt-1">{t(action.descriptionKey)}</p>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </div>

      {/* Recent Videos */}
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">{t('dashboard.recentVideos')}</h2>
        <Link to="/videos" className="text-sm text-primary hover:underline">
          {t('videoLibrary.viewAll')}
        </Link>
      </div>

      {videosLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3].map(i => (
            <Card key={i} className="animate-pulse">
              <CardContent className="pt-6">
                <div className="h-32 bg-muted rounded-lg mb-4" />
                <div className="h-4 bg-muted rounded w-3/4 mb-2" />
                <div className="h-3 bg-muted rounded w-1/2" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : !videos?.items?.length ? (
        <Card>
          <CardContent className="py-12 text-center">
            <Video className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
            <h3 className="text-lg font-medium mb-2">{t('dashboard.noRecentVideos')}</h3>
            <p className="text-muted-foreground mb-4">{t('dashboard.startExploring')}</p>
            <Link to="/upload">
              <Button><Upload className="h-4 w-4 mr-2" /> {t('dashboard.uploadVideo')}</Button>
            </Link>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {videos?.items?.map(video => (
            <VideoCard key={video.id} video={video} onDelete={(v) => handleDelete(v.id)} isDeleting={deleteMutation.isPending && deleteMutation.variables === video.id} />
          ))}
        </div>
      )}
    </div>
  )
}

function VideoCard({ video, onDelete, isDeleting }: { video: MediaFileResponse; onDelete: (video: MediaFileResponse) => void; isDeleting: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [thumbBroken, setThumbBroken] = useState(false)
  const videoId = video.id
  useEffect(() => { setThumbBroken(false) }, [videoId])

  const isReady = video.status === 'ready'
  const thumbUrl = getVideoThumbnailUrl({
    source_url: video.source_url,
    thumbnail_object: video.thumbnail_object ?? null,
    mime_type: video.mime_type,
  })
  const showThumb = !!thumbUrl && !thumbBroken

  const displayTitle = (v: MediaFileResponse): string => {
    const title = (v.title || '').trim()
    if (title) return title
    const fname = (v.filename || '').trim()
    if (v.source_url) {
      try { const u = new URL(v.source_url); return `${u.hostname.replace(/^www\./, '')} · ${fname || v.source_url}` } catch { return v.source_url }
    }
    return fname || '(untitled)'
  }

  return (
    <Card className={cn('group relative overflow-hidden', isDeleting && 'opacity-70')}>
      {/* deleting overlay */}
      {isDeleting && (
        <div className="absolute inset-0 z-10 bg-background/60 flex items-center justify-center pointer-events-none">
          <Loader2 className="h-6 w-6 animate-spin text-primary" />
        </div>
      )}

      {/* Thumbnail */}
      <div className="aspect-video bg-muted relative overflow-hidden">
        <div className="absolute inset-0 flex items-center justify-center">
          {!showThumb && <Video className="h-12 w-12 text-muted-foreground/50" />}
          {showThumb && (
            <img
              src={thumbUrl!}
              alt={displayTitle(video)}
              loading="lazy"
              referrerPolicy="no-referrer"
              className="absolute inset-0 h-full w-full object-cover"
              onError={() => setThumbBroken(true)}
            />
          )}
        </div>
        <div className="absolute top-2 right-2">
          <Badge className={cn(getStatusColor(video.status), 'gap-1')}>
            {isReady && <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />}
            {video.status}
          </Badge>
        </div>
        {isReady && (
          <div className="absolute inset-0 bg-black/30 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
            <Button variant="secondary" size="icon" onClick={(e) => { e.stopPropagation(); navigate(`/videos/${video.id}`) }}>
              <Eye className="h-4 w-4" />
            </Button>
          </div>
        )}
      </div>

      <CardContent className="p-4 space-y-3">
        <h3 className="font-medium line-clamp-1" title={video.filename}>{displayTitle(video)}</h3>

        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          {video.duration_ms != null && (
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {formatDuration(video.duration_ms)}
            </span>
          )}
          {video.file_size > 0 && (
            <span className="flex items-center gap-1">
              <FileText className="h-3 w-3" />
              {formatFileSize(video.file_size)}
            </span>
          )}
          <span>{formatRelativeTime(video.created_at)}</span>
        </div>

        {video.source_url && (
          <p className="text-xs text-muted-foreground/70 truncate" title={video.source_url}>
            {t('videoCard.source')}: {video.source_url}
          </p>
        )}

        {video.error_message && (
          <p className="text-xs text-destructive line-clamp-1">{video.error_message}</p>
        )}

        <div className="flex items-center justify-between pt-2 border-t">
          <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); navigate(`/videos/${video.id}`) }}>
            <Eye className="h-3.5 w-3.5 mr-1" />
            {t('videoLibrary.details')}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            disabled={isDeleting}
            onClick={(e) => { e.stopPropagation(); onDelete(video) }}
            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}