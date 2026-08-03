import { useState, useEffect, useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useQuery } from '@tanstack/react-query'
import { ragApi, videoApi } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Badge } from '@/components/ui/Badge'
import { cn, formatRelativeTime } from '@/lib/utils'
import {
  MessageSquare,
  Send,
  Search,
  Loader2,
  Copy,
  Check,
  Sparkles,
  Bot,
  User,
  ChevronDown,
} from 'lucide-react'
import type { RagSearchResult, RagChatRequest } from '@/types/api'
import { useTranslation } from 'react-i18next'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
  evidence?: RagSearchResult[]
  isLoading?: boolean
}

export function RAGChatPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [selectedMediaIds, setSelectedMediaIds] = useState<string[]>([])
  const [showMediaSelector, setShowMediaSelector] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  // Load recent videos for media selector
  const { data: recentVideos } = useQuery({
    queryKey: ['videos', 'recent', 'chat'],
    queryFn: () => videoApi.list({ page: 1, page_size: 20, status: 'ready' }),
    staleTime: 60000,
  })

  const chatMutation = useMutation({
    mutationFn: (request: RagChatRequest) => ragApi.chat(request),
    onMutate: async (newMessage) => {
      await queryClient.cancelQueries({ queryKey: ['chat', sessionId] })
      const optimisticMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content: newMessage.query,
        timestamp: new Date(),
      }
      setMessages(prev => [...prev, optimisticMessage])
      setInput('')
      setIsLoading(true)
    },
    onSuccess: (data) => {
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: data.answer,
        timestamp: new Date(),
        evidence: data.evidence,
      }
      setMessages(prev => [...prev, assistantMessage])
      if (!sessionId) setSessionId(data.session_id)
      setIsLoading(false)
    },
    onError: (error: any) => {
      const errorMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: t('ragChat.errorMessage') + (error.detail || error.message),
        timestamp: new Date(),
      }
      setMessages(prev => [...prev, errorMessage])
      setIsLoading(false)
    },
  })

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!input.trim() || isLoading) return

    chatMutation.mutate({
      query: input,
      media_ids: selectedMediaIds.length > 0 ? selectedMediaIds : undefined,
      session_id: sessionId || undefined,
      top_k: 10,
    })
  }

  const submitQuery = (query: string) => {
    setInput(query)
    chatMutation.mutate({
      query,
      media_ids: selectedMediaIds.length > 0 ? selectedMediaIds : undefined,
      session_id: sessionId || undefined,
      top_k: 10,
    })
  }

  const handleMediaToggle = (mediaId: string) => {
    setSelectedMediaIds(prev =>
      prev.includes(mediaId)
        ? prev.filter(id => id !== mediaId)
        : [...prev, mediaId]
    )
  }

  const handleNewChat = () => {
    setMessages([])
    setSessionId(null)
    setSelectedMediaIds([])
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
  }

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b bg-card">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-primary/10 rounded-lg">
            <Sparkles className="h-5 w-5 text-primary" />
          </div>
          <div>
            <h1 className="text-xl font-bold">{t('ragChat.title')}</h1>
            <p className="text-sm text-muted-foreground">{t('ragChat.subtitle')}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {selectedMediaIds.length > 0 && (
            <Badge variant="secondary" className="gap-1">
              <Search className="h-3 w-3" />
              {t('ragChat.videoSourcesCount', { count: selectedMediaIds.length })}
            </Badge>
          )}
          <Button variant="ghost" size="icon" onClick={handleNewChat} title={t('ragChat.newChat')}>
            <MessageSquare className="h-5 w-5" />
          </Button>
        </div>
      </div>

      {/* Media Selector Dropdown */}
      {showMediaSelector && recentVideos?.items && (
        <div className="p-4 border-b bg-popover shadow-md">
          <div className="flex items-center justify-between mb-3">
            <span className="font-medium">{t('ragChat.videoSources')}</span>
            <Button variant="ghost" size="sm" onClick={() => setShowMediaSelector(false)}>
              <ChevronDown className="h-4 w-4" />
            </Button>
          </div>
          <div className="max-h-48 overflow-y-auto space-y-2">
            {recentVideos.items.map(video => (
              <label
                key={video.id}
                className={cn(
                  'flex items-center gap-3 p-2 rounded-lg cursor-pointer transition-colors',
                  selectedMediaIds.includes(video.id)
                    ? 'bg-primary/10 border border-primary/20'
                    : 'hover:bg-muted/50'
                )}
              >
                <input
                  type="checkbox"
                  checked={selectedMediaIds.includes(video.id)}
                  onChange={() => handleMediaToggle(video.id)}
                  className="h-4 w-4 rounded border-input text-primary focus:ring-primary"
                />
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-sm truncate">{video.title || video.filename}</p>
                  <p className="text-xs text-muted-foreground truncate">{video.source_url || t('videoUpload.localUpload')}</p>
                </div>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Messages Area */}
      <div className="flex-1 p-4 space-y-6 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center text-muted-foreground">
            <Bot className="h-16 w-16 mb-4 opacity-50" />
            <h3 className="text-lg font-medium mb-2">{t('ragChat.startQuestioning')}</h3>
            <p className="text-sm max-w-md">
              {t('ragChat.startSubtitle')}
            </p>
            <div className="mt-4 flex flex-wrap gap-2 justify-center">
              {[
                t('ragChat.suggestions.whatDoesThisVideoCover'),
                t('ragChat.suggestions.summarizeCorePoints'),
                t('ragChat.suggestions.keyDataMentioned'),
                t('ragChat.suggestions.compareDifferentVideos'),
              ].map(suggestion => (
                <Button
                  key={suggestion}
                  variant="outline"
                  size="sm"
                  onClick={() => submitQuery(suggestion)}
                >
                  {suggestion}
                </Button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map(message => (
              <MessageBubble
                key={message.id}
                message={message}
                onCopy={copyToClipboard}
              />
            ))}
            <div ref={messagesEndRef} />
          </>
        )}

        {isLoading && (
          <div className="flex items-start gap-3 animate-pulse">
            <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
              <Bot className="h-4 w-4 text-primary" />
            </div>
            <div className="flex-1">
              <div className="bg-muted rounded-2xl rounded-bl-sm p-4 max-w-[80%]">
                <div className="flex gap-1">
                  <div className="w-2 h-2 rounded-full bg-primary/50 animate-bounce" style={{ animationDelay: '0ms' }} />
                  <div className="w-2 h-2 rounded-full bg-primary/50 animate-bounce" style={{ animationDelay: '150ms' }} />
                  <div className="w-2 h-2 rounded-full bg-primary/50 animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input Area */}
      <div className="p-4 border-t bg-card">
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setShowMediaSelector(!showMediaSelector)}
              className={cn(selectedMediaIds.length > 0 && 'border-primary text-primary')}
            >
              <Search className="h-4 w-4 mr-1" />
              {t('ragChat.videoSource')}
            </Button>
            <div className="flex-1 relative">
              <Input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={selectedMediaIds.length > 0
                  ? t('ragChat.placeholderWithCount', { count: selectedMediaIds.length })
                  : t('ragChat.placeholderAllVideos')}
                disabled={isLoading}
                className="pr-12"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    handleSubmit(e as unknown as React.FormEvent)
                  }
                }}
              />
            </div>
            <Button
              type="submit"
              disabled={!input.trim() || isLoading}
              size="icon"
              className="ml-2"
            >
              {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground text-center">
            {t('ragChat.enterToSend')}
          </p>
        </form>
      </div>
    </div>
  )
}

function MessageBubble({
  message,
  onCopy,
}: {
  message: ChatMessage
  onCopy: (text: string) => void
}) {
  const { t } = useTranslation()
  const [showEvidence, setShowEvidence] = useState(false)
  const [copied, setCopied] = useState(false)

  return (
    <div className={cn(
      'flex gap-3 animate-in fade-in slide-in-from-bottom-2',
      message.role === 'user' && 'flex-row-reverse'
    )}>
      <div
        className={cn(
          'w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5',
          message.role === 'user' ? 'bg-primary/10' : 'bg-muted'
        )}
      >
        {message.role === 'user' ? (
          <User className="h-4 w-4 text-primary" />
        ) : (
          <Bot className="h-4 w-4 text-muted-foreground" />
        )}
      </div>
      <div className={cn(
        'flex-1 max-w-[80%]',
        message.role === 'user' ? 'text-right' : 'text-left'
      )}>
        <div className={cn(
          'inline-block rounded-2xl px-4 py-2.5',
          message.role === 'user'
            ? 'bg-primary text-primary-foreground rounded-br-sm'
            : 'bg-muted rounded-bl-sm'
        )}>
          <p className="whitespace-pre-wrap">{message.content}</p>
        </div>

        {message.evidence && message.evidence.length > 0 && (
          <div className="mt-2 flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowEvidence(!showEvidence)}
              className="text-xs"
            >
              <Search className="h-3 w-3 mr-1" />
              {showEvidence ? t('ragChat.hideEvidence') : t('ragChat.showEvidence', { count: message.evidence.length })}
              <ChevronDown className={cn('h-3 w-3', showEvidence && 'rotate-180')} />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                onCopy(message.content)
                setCopied(true)
                setTimeout(() => setCopied(false), 2000)
              }}
              className="text-xs"
            >
              {copied ? <Check className="h-3 w-3 mr-1 text-green-500" /> : <Copy className="h-3 w-3 mr-1" />}
              {copied ? t('ragChat.copied') : t('ragChat.copy')}
            </Button>
          </div>
        )}

        {showEvidence && message.evidence && message.evidence.length > 0 && (
          <div className="mt-3 space-y-2 animate-in fade-in">
            {message.evidence.map((evidence, idx) => (
              <EvidenceCard key={evidence.chunk_id || idx} evidence={evidence} index={idx} />
            ))}
          </div>
        )}

        <p className="mt-1 text-xs text-muted-foreground/70">
          {formatRelativeTime(message.timestamp.toISOString())}
        </p>
      </div>
    </div>
  )
}

function EvidenceCard({ evidence, index }: { evidence: RagSearchResult; index: number }) {
  const { t } = useTranslation()
  return (
    <Card className="border-primary/20 bg-primary/5">
      <CardContent className="p-3">
        <div className="flex items-start gap-2">
          <span className="text-xs text-primary font-mono flex-shrink-0 mt-0.5">[{index + 1}]</span>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
              <Badge variant="outline" className="text-[10px]">
                {Math.round(evidence.score * 100)}% {t('ragChat.relevanceScore')}
              </Badge>
              {evidence.start_ms !== null && evidence.end_ms !== null && (
                <span className="font-mono">
                  {formatTime(evidence.start_ms)} - {formatTime(evidence.end_ms)}
                </span>
              )}
              {evidence.evidence_id && (
                <Badge variant="secondary" className="text-[10px]">
                  EID: {evidence.evidence_id.slice(0, 8)}...
                </Badge>
              )}
            </div>
            <p className="text-sm text-foreground line-clamp-3">{evidence.content}</p>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function formatTime(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}