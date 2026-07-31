import { useState, useEffect, useRef } from 'react'
import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { videoApi } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Input } from '@/components/ui/Input'
import { cn, formatRelativeTime, getStatusColor, formatDuration, formatFileSize, extractErrorMessage } from '@/lib/utils'
import {
  Video,
  Search,
  Filter,
  ChevronLeft,
  ChevronRight,
  MoreHorizontal,
  Trash2,
  Eye,
  Clock,
  FileText,
  Loader2,
} from 'lucide-react'
import type { MediaFileResponse } from '@/types/api'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

const STATUS_OPTIONS = ['all', 'ready', 'downloading', 'transcoding', 'asr', 'ocr', 'indexing', 'claimed', 'failed'] as const
type StatusFilter = typeof STATUS_OPTIONS[number]

export function VideoLibraryPage() {
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const [search, setSearch] = useState(searchParams.get('search') || '')
  const [status, setStatus] = useState<StatusFilter>((searchParams.get('status') as StatusFilter) || 'all')
  const [page, setPage] = useState(parseInt(searchParams.get('page') || '1', 10))
  const [pageSize] = useState(12)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  // Update URL params when filters change
  const updateParams = () => {
    const params = new URLSearchParams()
    if (search) params.set('search', search)
    if (status !== 'all') params.set('status', status)
    if (page > 1) params.set('page', page.toString())
    setSearchParams(params)
  }

  const { data: videos, isLoading, refetch } = useQuery({
    queryKey: ['videos', { page, page_size: pageSize, search: search || undefined, status: status !== 'all' ? status : undefined }],
    queryFn: () => videoApi.list({ page, page_size: pageSize, search: search || undefined, status: status !== 'all' ? status : undefined }),
    placeholderData: keepPreviousData,
    staleTime: 0,
    refetchOnMount: true,
  })

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    setPage(1)
    updateParams()
  }

  const handleStatusChange = (newStatus: StatusFilter) => {
    setStatus(newStatus)
    setPage(1)
    updateParams()
  }

  const handlePageChange = (newPage: number) => {
    setPage(newPage)
    updateParams()
  }

  const handleDelete = async (video: MediaFileResponse) => {
    const isInProgress = video.status !== 'ready' && video.status !== 'failed'
    const msg = isInProgress
      ? t('dashboard.deleteInProgressConfirm', { status: video.status })
      : t('dashboard.deleteConfirm')
    if (!confirm(msg)) return
    setDeletingId(video.id)
    try {
      await videoApi.delete(video.id)
      queryClient.removeQueries({ queryKey: ['video', video.id] })
      refetch()
    } catch (e: unknown) {
      const msg = extractErrorMessage(e)
      setDeleteError(msg)
      setTimeout(() => setDeleteError((curr) => (curr === msg ? null : curr)), 2600)
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{t('videoLibrary.title')}</h1>
          <p className="text-muted-foreground mt-1">{t('videoLibrary.subtitle')}</p>
        </div>
        <Link to="/upload">
          <Button size="lg"><Video className="h-4 w-4 mr-2" /> {t('videoLibrary.uploadNew')}</Button>
        </Link>
      </div>

      {/* delete error banner */}
      {deleteError && (
        <div
          role="alert"
          className="flex items-center gap-2 p-4 rounded-lg bg-destructive/10 border border-destructive/30 text-destructive"
        >
          <Trash2 className="h-5 w-5 flex-shrink-0" />
          <span className="flex-1 text-sm">{deleteError}</span>
          <button
            type="button"
            className="text-xs underline underline-offset-2 hover:opacity-80"
            onClick={() => setDeleteError(null)}
          >
            {t('utils.ok')}
          </button>
        </div>
      )}

      {/* Search & Filter */}
      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleSearch} className="flex flex-col sm:flex-row gap-4">
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder={t('videoLibrary.searchPlaceholder')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-10"
              />
            </div>
            <div className="flex items-center gap-2">
              <Filter className="h-4 w-4 text-muted-foreground" />
              <select
                value={status}
                onChange={(e) => handleStatusChange(e.target.value as StatusFilter)}
                className="px-3 py-2 border border-input rounded-lg bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              >
                {STATUS_OPTIONS.map(s => (
                  <option key={s} value={s}>
                    {s === 'all' ? t('videoLibrary.filterAll') : s.charAt(0).toUpperCase() + s.slice(1)}
                  </option>
                ))}
              </select>
            </div>
          </form>
        </CardContent>
      </Card>

      {/* Video Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {[...Array(8)].map((_, i) => (
            <VideoCardSkeleton key={i} />
          ))}
        </div>
      ) : videos?.items.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <Video className="h-16 w-16 mx-auto text-muted-foreground/50 mb-4" />
            <h3 className="text-lg font-medium mb-2">{t('videoLibrary.noVideos')}</h3>
            <p className="text-muted-foreground mb-4">
              {search || status !== 'all' ? t('videoLibrary.noResults') : t('videoLibrary.noVideosSub')}
            </p>
            {(!search && status === 'all') && (
              <Link to="/upload">
                <Button className="mt-2"><Video className="h-4 w-4 mr-2" /> {t('videoLibrary.uploadVideo')}</Button>
              </Link>
            )}
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {videos!.items.map(video => (
              <VideoCard
                key={video.id}
                video={video}
                onDelete={handleDelete}
                isDeleting={deletingId === video.id}
              />
            ))}
          </div>

          {/* Pagination */}
          {videos && videos.total_pages > 1 && (
            <div className="flex items-center justify-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handlePageChange(page - 1)}
                disabled={page <= 1}
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="px-4 text-sm text-muted-foreground">
                {t('videoLibrary.pageInfo', { page, totalPages: videos.total_pages, total: videos.total })}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handlePageChange(page + 1)}
                disabled={page >= videos.total_pages}
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function VideoCard({
  video,
  onDelete,
  isDeleting,
}: {
  video: MediaFileResponse
  onDelete: (video: MediaFileResponse) => void
  isDeleting: boolean
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [showMenu, setShowMenu] = useState(false)
  const cardRef = useRef<HTMLDivElement>(null)
  const moreRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!showMenu) return
    const handler = (e: MouseEvent) => {
      if (cardRef.current && !cardRef.current.contains(e.target as Node)) {
        setShowMenu(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showMenu])

  const handleClick = () => {
    if (isDeleting) return
    if (!showMenu) navigate(`/videos/${video.id}`)
  }

  const statusColor = getStatusColor(video.status)
  const isReady = video.status === 'ready'

  return (
    <Card
      ref={cardRef}
      className={cn('group relative overflow-hidden', isDeleting && 'opacity-70')}
      onClick={handleClick}
    >
      {/* deleting overlay + spinner */}
      {isDeleting && (
        <div className="absolute inset-0 z-10 bg-background/60 flex items-center justify-center pointer-events-none">
          <Loader2 className="h-6 w-6 animate-spin text-primary" />
        </div>
      )}

      {/* Thumbnail */}
      <div className="aspect-video bg-muted relative overflow-hidden">
        <div className="absolute inset-0 flex items-center justify-center">
          <Video className="h-12 w-12 text-muted-foreground/50" />
        </div>
        <div className="absolute top-2 right-2">
          <Badge className={`${statusColor} gap-1`}>
            {isReady && <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />}
            {video.status}
          </Badge>
        </div>
        {video.status === 'ready' && (
          <div className="absolute inset-0 bg-black/30 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
            <Button variant="secondary" size="icon" onClick={(e) => { e.stopPropagation(); navigate(`/videos/${video.id}`) }}>
              <Eye className="h-4 w-4" />
            </Button>
          </div>
        )}
      </div>

      <CardContent className="p-4 space-y-3">
        <h3 className="font-medium line-clamp-1" title={video.filename}>{video.filename}</h3>

        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          {video.duration_ms && (
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {formatDuration(video.duration_ms)}
            </span>
          )}
          {video.file_size && (
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
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); navigate(`/videos/${video.id}`) }}>
              <Eye className="h-3.5 w-3.5 mr-1" />
              {t('videoLibrary.details')}
            </Button>
            {isReady && (
              <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); navigate(`/videos/${video.id}/progress`) }}>
                <FileText className="h-3.5 w-3.5 mr-1" />
                {t('pipeline.viewDetails')}
              </Button>
            )}
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              disabled={isDeleting}
              onClick={(e) => { e.stopPropagation(); onDelete(video) }}
              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
              title={t('videoLibrary.delete')}
              aria-label={t('videoLibrary.delete')}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
            <div className="relative" ref={moreRef}>
              <Button
                variant="ghost"
                size="icon"
                disabled={isDeleting}
                onClick={(e) => { e.stopPropagation(); setShowMenu(!showMenu) }}
                aria-haspopup="menu"
                aria-expanded={showMenu}
                title={t('utils.moreActions')}
                aria-label={t('utils.moreActions')}
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
              {showMenu && !isDeleting && (
                <div
                  role="menu"
                  className="absolute right-0 top-full mt-1 z-20 w-40 bg-popover border rounded-lg shadow-lg py-1"
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    type="button"
                    role="menuitem"
                    className="w-full px-3 py-2 text-left text-sm hover:bg-accent flex items-center gap-2"
                    onClick={() => { navigate(`/videos/${video.id}`); setShowMenu(false) }}
                  >
                    <Eye className="h-4 w-4" /> {t('videoCard.view')}
                  </button>
                  {isReady && (
                    <button
                      type="button"
                      role="menuitem"
                      className="w-full px-3 py-2 text-left text-sm hover:bg-accent flex items-center gap-2"
                      onClick={() => { navigate(`/videos/${video.id}/progress`); setShowMenu(false) }}
                    >
                      <FileText className="h-4 w-4" /> {t('videoLibrary.progress')}
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function VideoCardSkeleton() {
  return (
    <Card className="animate-pulse">
      <div className="aspect-video bg-muted rounded-lg mb-4" />
      <div className="h-4 bg-muted rounded w-3/4 mb-2" />
      <div className="h-3 bg-muted rounded w-1/2 mb-2" />
      <div className="h-3 bg-muted rounded w-1/3 mb-4" />
      <div className="flex gap-2">
        <div className="h-8 bg-muted rounded flex-1" />
        <div className="h-8 bg-muted rounded w-20" />
      </div>
    </Card>
  )
}