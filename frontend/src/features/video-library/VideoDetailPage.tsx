import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useInfiniteQuery } from '@tanstack/react-query'
import { videoApi } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Progress } from '@/components/ui/Progress'
import { cn, formatRelativeTime, getStatusColor, formatFileSize, formatDuration, STAGE_LABELS, STAGE_ORDER } from '@/lib/utils'
import {
  ArrowLeft,
  Video,
  FileText,
  Search,
  Database,
  Download,
  Clock,
  FileText as FileTextIcon,
  Settings,
  Copy,
  ChevronDown,
  Loader2,
} from 'lucide-react'
import type { VideoSegmentResponse, SegmentsListResponse, OCRResultResponse } from '@/types/api'
import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'

export function VideoDetailPage() {
  const { t } = useTranslation()
  const { id: mediaId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('transcription')

  const { data: video, isLoading: videoLoading, error: videoError } = useQuery({
    queryKey: ['video', 'detail', mediaId],
    queryFn: () => videoApi.get(mediaId!),
    enabled: !!mediaId,
  })

  const { data: transcription, isLoading: transcriptionLoading } = useQuery({
    queryKey: ['video', 'transcription', mediaId],
    queryFn: () => videoApi.getTranscription(mediaId!),
    enabled: !!mediaId && video?.status === 'ready',
  })

  // Segments with infinite query for pagination
  const {
    data: segmentsData,
    isLoading: segmentsLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ['video', 'segments', mediaId],
    queryFn: ({ pageParam = 1 }) => videoApi.getSegments(mediaId!, { page: pageParam, page_size: 50 }),
    enabled: !!mediaId && video?.status === 'ready',
    initialPageParam: 1,
    getNextPageParam: (lastPage: SegmentsListResponse) => {
      return lastPage.has_more ? lastPage.page + 1 : undefined
    },
  })

  // OCR results with pagination
  const {
    data: ocrData,
    isLoading: ocrLoading,
    fetchNextPage: fetchNextOcrPage,
    hasNextPage: hasNextOcrPage,
    isFetchingNextPage: isFetchingNextOcrPage,
  } = useInfiniteQuery({
    queryKey: ['video', 'ocr', mediaId],
    queryFn: ({ pageParam = 1 }) => videoApi.getOcrResults(mediaId!, { page: pageParam, page_size: 50 }),
    enabled: !!mediaId && video?.status === 'ready',
    initialPageParam: 1,
    getNextPageParam: (lastPage: { items: OCRResultResponse[]; total: number; page: number; page_size: number; has_more: boolean }) => {
      return lastPage.has_more ? lastPage.page + 1 : undefined
    },
  })

  // Flatten segments from all pages
  const allSegments = useMemo(() => {
    if (!segmentsData) return []
    return segmentsData.pages.flatMap(page => page.items)
  }, [segmentsData])

  // Flatten OCR results from all pages
  const allOcrResults = useMemo(() => {
    if (!ocrData) return []
    return ocrData.pages.flatMap(page => page.items)
  }, [ocrData])

  if (!mediaId) {
    return <div className="flex items-center justify-center h-64 text-muted-foreground">{t('videoDetail.invalidId')}</div>
  }

  if (videoLoading) {
    return <VideoDetailSkeleton />
  }

  if (videoError || !video) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-center">
        <Video className="h-12 w-12 text-muted-foreground/50 mb-4" />
        <h2 className="text-xl font-medium mb-2">{t('videoDetail.notFound')}</h2>
        <p className="text-muted-foreground mb-4">{t('videoDetail.notFoundSub', { id: mediaId.slice(0, 8) })}</p>
        <Button onClick={() => navigate(-1)}><ArrowLeft className="h-4 w-4 mr-2" /> {t('videoDetail.back')}</Button>
      </div>
    )
  }

  const statusColor = getStatusColor(video.status)

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" onClick={() => navigate(-1)}>
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <div>
            <h1 className="text-2xl font-bold truncate max-w-2xl">{video.filename}</h1>
            <div className="flex items-center gap-3 mt-1 text-sm text-muted-foreground">
              <Badge className={statusColor}>{video.status}</Badge>
              {video.duration_ms && (
                <span className="flex items-center gap-1">
                  <Clock className="h-3.5 w-3.5" />
                  {formatDuration(video.duration_ms)}
                </span>
              )}
              {video.file_size && (
                <span className="flex items-center gap-1">
                  <FileTextIcon className="h-3.5 w-3.5" />
                  {formatFileSize(video.file_size)}
                </span>
              )}
              <span>{formatRelativeTime(video.created_at)}</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {video.status !== 'ready' && video.status !== 'failed' && (
            <Button variant="outline" onClick={() => navigate(`/videos/${mediaId}/progress`)}>
              <Settings className="h-4 w-4 mr-2" />
              {t('videoDetail.viewProgress')}
            </Button>
          )}
          {video.status === 'ready' && (
            <Button onClick={() => navigate(`/videos/${mediaId}/progress`)}>
              <FileText className="h-4 w-4 mr-2" />
              {t('videoDetail.details')}
            </Button>
          )}
        </div>
      </div>

      {/* Source Info */}
      {video.source_url && (
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <Download className="h-4 w-4" />
              <span className="font-medium">{t('videoDetail.sourceLink')}</span>
              <a href={video.source_url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline truncate flex-1">
                {video.source_url}
              </a>
              <Button variant="ghost" size="icon" onClick={() => navigator.clipboard.writeText(video.source_url!)}>
                <Copy className="h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Processing Stages Progress */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            {t('videoDetail.processingStages')}
          </CardTitle>
          <CardDescription>{t('videoDetail.processingDesc')}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {STAGE_ORDER.map((stage, index) => {
              const stageProgress = (video as any).stage_progress?.[stage] ?? 0
              const isCompleted = stageProgress >= 100 || (video.status === 'ready' && index < STAGE_ORDER.length - 1)
              const isCurrent = !isCompleted && (video.status === stage || stageProgress > 0)
              const isFailed = video.status === 'failed' && video.error_message?.includes(stage)

              return (
                <div key={stage} className="flex items-center gap-4">
                  <div className={cn(
                    'w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium flex-shrink-0',
                    isCompleted ? 'bg-green-500 text-white' :
                    isCurrent ? 'bg-primary text-white animate-pulse' :
                    isFailed ? 'bg-destructive text-white' :
                    'bg-muted text-muted-foreground'
                  )}>
                    {isCompleted ? (
                      <ChevronDown className="h-4 w-4" />
                    ) : isCurrent ? (
                      <div className="w-3 h-3 border-2 border-white/50 border-t-transparent rounded-full animate-spin" />
                    ) : (
                      index + 1
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between text-sm mb-1">
                      <span className={cn('font-medium', isCurrent && 'text-primary', isFailed && 'text-destructive')}>
                        {t(STAGE_LABELS[stage] || stage)}
                      </span>
                      <span className="text-muted-foreground">
                        {isCompleted ? '100%' : stageProgress >= 0 ? `${Math.round(stageProgress)}%` : t('utils.waiting')}
                      </span>
                    </div>
                    <Progress value={Math.max(0, isCompleted ? 100 : stageProgress)} className="h-1.5" />
                  </div>
                </div>
              )
            })}
          </div>
        </CardContent>
      </Card>

      {/* Error Message */}
      {video.error_message && video.status === 'failed' && (
        <Card className="border-destructive/50">
          <CardContent className="pt-6">
            <div className="flex items-start gap-3 p-4 bg-destructive/10 rounded-lg">
              <Settings className="h-5 w-5 text-destructive flex-shrink-0 mt-0.5" />
              <div>
                <p className="font-medium text-destructive">{t('videoDetail.processingFailed')}</p>
                <p className="text-sm text-muted-foreground mt-1">{t('videoDetail.errorMessage', { msg: video.error_message })}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Tabs for Content */}
      {video.status === 'ready' && (
        <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="transcription">
              <FileText className="h-4 w-4 mr-2" />
              {t('videoDetail.transcription')}
            </TabsTrigger>
            <TabsTrigger value="segments">
              <Search className="h-4 w-4 mr-2" />
              {t('videoDetail.segments')}
            </TabsTrigger>
            <TabsTrigger value="ocr">
              <Database className="h-4 w-4 mr-2" />
              {t('videoDetail.ocr')}
            </TabsTrigger>
          </TabsList>

          {/* Transcription Tab */}
          <TabsContent value="transcription" className="space-y-4">
            {transcriptionLoading ? (
              <div className="flex items-center justify-center h-64">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
              </div>
            ) : transcription ? (
              <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                  <CardTitle>{t('videoDetail.fullText')}</CardTitle>
                  <Badge variant="secondary">{transcription.language || t('videoDetail.unknown')}</Badge>
                </CardHeader>
                <CardContent>
                  <div className="prose max-w-none whitespace-pre-wrap text-sm leading-relaxed">
                    {transcription.full_text || t('videoDetail.noTranscription')}
                  </div>
                  <div className="mt-4 flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={() => navigator.clipboard.writeText(transcription.full_text || '')}>
                      <Copy className="h-4 w-4 mr-1" /> {t('videoDetail.copyAll')}
                    </Button>
                    <span className="text-sm text-muted-foreground">
                      {t('videoDetail.characterCount', { count: transcription.full_text?.length || 0 })}
                    </span>
                  </div>
                </CardContent>
              </Card>
            ) : (
              <Card>
                <CardContent className="py-12 text-center text-muted-foreground">
                  {t('videoDetail.noTranscription')}
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* Segments Tab */}
          <TabsContent value="segments" className="space-y-4">
            {segmentsLoading ? (
              <div className="flex items-center justify-center h-64">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
              </div>
            ) : allSegments.length > 0 ? (
              <Card>
                <CardHeader>
                  <CardTitle>{t('videoDetail.segments')}</CardTitle>
                  <CardDescription>{t('videoDetail.totalSegments', { count: allSegments.length })}</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3 max-h-[600px] overflow-y-auto">
                    {allSegments.map((segment: VideoSegmentResponse) => (
                      <SegmentItem key={segment.id} segment={segment} />
                    ))}
                  </div>
                  {hasNextPage && (
                    <div className="text-center mt-4">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => fetchNextPage()}
                        disabled={isFetchingNextPage}
                      >
                        {isFetchingNextPage ? (
                          <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                        ) : (
                          <>
                            {t('utils.loading')}
                            <ChevronDown className="h-4 w-4 ml-1" />
                          </>
                        )}
                      </Button>
                    </div>
                  )}
                </CardContent>
              </Card>
            ) : (
              <Card>
                <CardContent className="py-12 text-center text-muted-foreground">
                  {t('videoDetail.noSegmentData')}
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* OCR Tab */}
          <TabsContent value="ocr" className="space-y-4">
            {ocrLoading ? (
              <div className="flex items-center justify-center h-64">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
              </div>
            ) : allOcrResults.length > 0 ? (
              <Card>
                <CardHeader>
                  <CardTitle>{t('videoDetail.ocr')}</CardTitle>
                  <CardDescription>{t('videoDetail.totalSegments', { count: ocrData?.pages.reduce((acc, p) => acc + p.items.length, 0) || 0 })}</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-4 max-h-[600px] overflow-y-auto">
                    {allOcrResults.map((ocr: OCRResultResponse) => (
                      <OcrItem key={ocr.id} ocr={ocr} />
                    ))}
                  </div>
                  {hasNextOcrPage && (
                    <div className="text-center mt-4">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => fetchNextOcrPage()}
                        disabled={isFetchingNextOcrPage}
                      >
                        {isFetchingNextOcrPage ? (
                          <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                        ) : (
                          <>
                            {t('utils.loading')}
                            <ChevronDown className="h-4 w-4 ml-1" />
                          </>
                        )}
                      </Button>
                    </div>
                  )}
                </CardContent>
              </Card>
            ) : (
              <Card>
                <CardContent className="py-12 text-center text-muted-foreground">
                  <Search className="h-12 w-12 mx-auto text-muted-foreground/50 mb-4" />
                  <p>{t('videoDetail.noOcrData')}</p>
                  <p className="text-sm mt-1">{t('videoDetail.ocrWillAppear')}</p>
                </CardContent>
              </Card>
            )}
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}

function SegmentItem({ segment }: { segment: VideoSegmentResponse }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="border rounded-lg overflow-hidden bg-muted/30">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full p-4 flex items-start gap-3 hover:bg-muted/50 transition-colors text-left"
      >
        <div className="flex-shrink-0 w-24 text-right text-sm text-muted-foreground font-mono">
          {formatTime(segment.start_ms)} - {formatTime(segment.end_ms)}
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-medium line-clamp-1">{segment.transcript || ''}</p>
          <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
            <span>{t('videoDetail.confidence')}: {Math.round((segment.confidence || 0) * 100)}%</span>
            <span>{t('videoDetail.duration')}: {formatDuration(segment.end_ms - segment.start_ms)}</span>
            {segment.speaker && <span>{t('videoDetail.speaker')}: {segment.speaker}</span>}
          </div>
        </div>
        <ChevronDown className={cn('h-4 w-4 text-muted-foreground flex-shrink-0 mt-1', expanded && 'rotate-180')} />
      </button>
      {expanded && (
        <div className="px-4 pb-4 border-t bg-muted/20 text-sm text-muted-foreground">
          <p className="font-medium mb-2">{t('videoDetail.fullTextLabel')}</p>
          <p className="whitespace-pre-wrap">{segment.transcript || ''}</p>
        </div>
      )}
    </div>
  )
}

function OcrItem({ ocr }: { ocr: OCRResultResponse }) {
  const { t } = useTranslation()
  return (
    <div className="border rounded-lg p-4 bg-muted/30">
      <div className="flex items-start gap-3">
        <div className="flex-shrink-0 w-20 text-right text-sm text-muted-foreground font-mono">
          {formatTime(ocr.frame_ms)}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-2">
            <Badge
              variant={ocr.status === 'completed' ? 'success' : ocr.status === 'failed' ? 'destructive' : 'secondary'}
              className="text-xs"
            >
              {ocr.status}
            </Badge>
            {ocr.model_name && (
              <span className="text-xs text-muted-foreground">{ocr.model_name}</span>
            )}
          </div>
          {ocr.ocr_text ? (
            <p className="font-mono text-sm whitespace-pre-wrap bg-muted p-2 rounded">{ocr.ocr_text}</p>
          ) : (
            <p className="font-mono text-sm text-muted-foreground italic bg-muted/50 p-2 rounded">
              {t('videoDetail.noTextRecognized')}
            </p>
          )}
          {ocr.phash && (
            <p className="text-xs text-muted-foreground mt-1 font-mono">{t('videoDetail.phash', { hash: ocr.phash })}</p>
          )}
        </div>
      </div>
    </div>
  )
}

function VideoDetailSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="flex gap-4">
        <div className="h-10 w-10 bg-muted rounded" />
        <div className="flex-1">
          <div className="h-6 bg-muted rounded w-1/3 mb-2" />
          <div className="h-4 bg-muted rounded w-1/2" />
        </div>
      </div>
      <div className="h-32 bg-muted rounded" />
      <div className="h-64 bg-muted rounded" />
    </div>
  )
}

function formatTime(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  const msPart = Math.floor((ms % 1000) / 100)
  return `${minutes}:${seconds.toString().padStart(2, '0')}.${msPart}`
}