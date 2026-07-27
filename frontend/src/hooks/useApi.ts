import { useQuery, useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  pipelineApi,
  videoApi,
  analysisApi,
  ragApi,
  healthApi,
  userApi,
} from '@/lib/api'
import type {
  RagSearchRequest,
  RagChatRequest,
  AnalysisTaskRequest,
  UserConfig,
} from '@/types/api'

// Query Keys
export const QUERY_KEYS = {
  videos: (params?: { page?: number; page_size?: number; search?: string; status?: string; user_id?: string }) =>
    ['videos', params] as const,
  video: (mediaId: string) => ['video', mediaId] as const,
  videoTranscription: (mediaId: string) => ['video', 'transcription', mediaId] as const,
  videoSegments: (mediaId: string, params?: { page?: number; page_size?: number }) =>
    ['video', 'segments', mediaId, params] as const,
  videoOcr: (mediaId: string, params?: { page?: number; page_size?: number }) =>
    ['video', 'ocr', mediaId, params] as const,
  videoProgress: (mediaId: string) => ['video', 'progress', mediaId] as const,
  pipelineStatus: (mediaId: string) => ['pipeline', 'status', mediaId] as const,
  ragSearch: (request: RagSearchRequest) => ['rag', 'search', request] as const,
  ragChat: (sessionId?: string) => ['rag', 'chat', sessionId] as const,
  agentTask: (taskId: string) => ['agent', 'task', taskId] as const,
  agentResult: (taskId: string) => ['agent', 'result', taskId] as const,
  agentCheckpoints: (taskId: string) => ['agent', 'checkpoints', taskId] as const,
  health: () => ['health'] as const,
  healthReady: () => ['health', 'ready'] as const,
  userConfig: () => ['user', 'config'] as const,
}

// Pipeline hooks
export function useSubmitPipeline() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: pipelineApi.submit,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['videos'] })
    },
  })
}

export function usePipelineStatus(mediaId: string, enabled = true) {
  return useQuery({
    queryKey: QUERY_KEYS.pipelineStatus(mediaId),
    queryFn: () => pipelineApi.getStatus(mediaId),
    enabled: enabled && !!mediaId,
    refetchInterval: (query) => {
      const data = query.state.data
      if (!data) return 2000
      const isTerminal = data.status === 'ready' || data.status === 'failed'
      return isTerminal ? false : 2000
    },
  })
}

// Video hooks
export function useVideos(params?: {
  page?: number
  page_size?: number
  user_id?: string
  search?: string
  status?: string
}) {
  return useQuery({
    queryKey: QUERY_KEYS.videos(params),
    queryFn: () => videoApi.list(params),
    placeholderData: (prev) => prev,
  })
}

export function useVideo(mediaId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.video(mediaId),
    queryFn: () => videoApi.get(mediaId),
    enabled: !!mediaId,
  })
}

export function useVideoTranscription(mediaId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.videoTranscription(mediaId),
    queryFn: () => videoApi.getTranscription(mediaId),
    enabled: !!mediaId,
  })
}

export function useVideoSegments(mediaId: string, params?: { page?: number; page_size?: number }) {
  return useQuery({
    queryKey: QUERY_KEYS.videoSegments(mediaId, params),
    queryFn: () => videoApi.getSegments(mediaId, params),
    enabled: !!mediaId,
  })
}

export function useInfiniteVideoSegments(mediaId: string, pageSize = 50) {
  return useInfiniteQuery({
    queryKey: ['video', 'segments', 'infinite', mediaId],
    queryFn: ({ pageParam = 1 }) => videoApi.getSegments(mediaId, { page: pageParam, page_size: pageSize }),
    enabled: !!mediaId,
    initialPageParam: 1,
    getNextPageParam: (lastPage: { items: any[]; total: number; page: number; page_size: number; has_more: boolean }) => {
      return lastPage.has_more ? lastPage.page + 1 : undefined
    },
  })
}

export function useVideoOcrResults(mediaId: string, params?: { page?: number; page_size?: number }) {
  return useQuery({
    queryKey: QUERY_KEYS.videoOcr(mediaId, params),
    queryFn: () => videoApi.getOcrResults(mediaId, params),
    enabled: !!mediaId,
  })
}

export function useVideoProgress(mediaId: string, enabled = true) {
  return useQuery({
    queryKey: QUERY_KEYS.videoProgress(mediaId),
    queryFn: () => videoApi.getProgress(mediaId),
    enabled: enabled && !!mediaId,
    refetchInterval: (query) => {
      const data = query.state.data
      if (!data) return 2000
      const isTerminal = data.status === 'ready' || data.status === 'failed'
      return isTerminal ? false : 2000
    },
  })
}

export function useDeleteVideo() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (mediaId: string) => videoApi.delete(mediaId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['videos'] })
    },
  })
}

// RAG hooks
export function useRagSearch() {
  return useMutation({
    mutationFn: (request: RagSearchRequest) => ragApi.search(request),
  })
}

export function useRagChat() {
  return useMutation({
    mutationFn: (request: RagChatRequest) => ragApi.chat(request),
  })
}

// Agent hooks
export function useAgentAnalyze() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: AnalysisTaskRequest) => analysisApi.create(request),
    onSuccess: (data) => {
      queryClient.setQueryData(QUERY_KEYS.agentTask(data.task_id), data)
    },
  })
}

export function useAgentTaskStatus(taskId: string, enabled = true) {
  return useQuery({
    queryKey: QUERY_KEYS.agentTask(taskId),
    queryFn: () => analysisApi.getStatus(taskId),
    enabled: enabled && !!taskId,
    refetchInterval: (query) => {
      const data = query.state.data
      if (!data) return 2000
      const isTerminal = data.status === 'completed' || data.status === 'failed'
      return isTerminal ? false : 2000
    },
  })
}

export function useAgentTaskResult(taskId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.agentResult(taskId),
    queryFn: () => analysisApi.getResult(taskId),
    enabled: !!taskId,
  })
}

export function useAgentCheckpoints(taskId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.agentCheckpoints(taskId),
    queryFn: () => analysisApi.getCheckpoints(taskId),
    enabled: !!taskId,
  })
}

// Health hooks
export function useHealthCheck() {
  return useQuery({
    queryKey: QUERY_KEYS.health(),
    queryFn: () => healthApi.check(),
  })
}

export function useHealthReady() {
  return useQuery({
    queryKey: QUERY_KEYS.healthReady(),
    queryFn: () => healthApi.ready(),
    refetchInterval: 30000,
  })
}

// User config hooks
export function useUserConfig() {
  return useQuery({
    queryKey: QUERY_KEYS.userConfig(),
    queryFn: () => userApi.getConfig(),
  })
}

export function useUpdateUserConfig() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (config: Partial<UserConfig>) => userApi.updateConfig(config),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.userConfig() })
    },
  })
}