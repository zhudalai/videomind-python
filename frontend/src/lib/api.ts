import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'
import type {
  ApiError,
  MediaFileResponse,
  VideoListResponse,
  TranscriptionResponse,
  OcrResultsListResponse,
  SegmentsListResponse,
  PipelineStatusResponse,
} from '@/types/api'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Request interceptor - inject auth token
api.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem('access_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error),
)

// Response interceptor - normalize errors and return data directly
api.interceptors.response.use(
  (response) => response.data, // Return data directly
  (error: AxiosError<ApiError>) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token')
    }
    return Promise.reject(normalizeError(error))
  },
)

function normalizeError(error: AxiosError<ApiError>): Error & { status?: number; detail?: string } {
  const err = new Error(error.response?.data?.detail || error.message) as Error & {
    status?: number
    detail?: string
  }
  err.status = error.response?.status
  err.detail = error.response?.data?.detail
  return err
}

// Type helper for API responses
type ApiResponse<T> = Promise<T>

// Pipeline endpoints
export const pipelineApi = {
  submit: (data: { source_url: string; user_id?: string }): ApiResponse<{ media_id: string; status: string; chain_id?: string }> =>
    api.post('/videos/pipeline', data),

  // 上传本地视频文件到 MinIO + 入库 + 触发管线（skip_download）
  // 后端契约：POST /api/videos/upload (multipart/form-data, field=file)
  //   → {media_id, content_hash, status: 'pending', size}
  uploadFile: (
    file: File,
    onProgress?: (pct: number) => void
  ): ApiResponse<{ media_id: string; content_hash: string; status: string; size: number }> => {
    const form = new FormData()
    form.append('file', file)
    return api.post('/videos/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (e) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded * 100) / e.total))
        }
      },
    })
  },

  getStatus: (mediaId: string): ApiResponse<PipelineStatusResponse> => api.get(`/videos/pipeline/${mediaId}`),
}

// Video endpoints
export const videoApi = {
  list: (params?: {
    page?: number
    page_size?: number
    user_id?: string
    search?: string
    status?: string
  }): ApiResponse<VideoListResponse> => api.get('/videos', { params }),

  get: (mediaId: string): ApiResponse<MediaFileResponse> => api.get(`/videos/${mediaId}`),

  delete: (mediaId: string): ApiResponse<void> => api.delete(`/videos/${mediaId}`),

  getSegments: (mediaId: string, params?: { page?: number; page_size?: number }): ApiResponse<SegmentsListResponse> =>
    api.get(`/videos/${mediaId}/segments`, { params }),

  getTranscription: (mediaId: string): ApiResponse<TranscriptionResponse> =>
    api.get(`/videos/${mediaId}/transcription`),

  getProgress: (mediaId: string): ApiResponse<PipelineStatusResponse> =>
    api.get(`/videos/${mediaId}/progress`),

  getOcrResults: (mediaId: string, params?: { page?: number; page_size?: number }): ApiResponse<OcrResultsListResponse> =>
    api.get(`/videos/${mediaId}/ocr`, { params }),
}

// Analysis endpoints
export const analysisApi = {
  create: (data: { goal: string; media_id: string; user_id: string; max_rounds?: number }): ApiResponse<{
    task_id: string
    status: string
    goal: string
    max_rounds: number
    created_at: string
  }> => api.post('/agent/analyze', data),

  getStatus: (taskId: string): ApiResponse<{
    task_id: string
    status: string
    current_round: number
    max_rounds: number
    goal: string
    final_result_json: Record<string, unknown> | null
    error_message: string | null
    created_at: string
    started_at: string | null
    completed_at: string | null
  }> => api.get(`/agent/tasks/${taskId}`),

  getResult: (taskId: string): ApiResponse<{
    id: string
    task_id: string
    title: string
    conclusions_json: unknown[]
    evidence_json: unknown[]
    suggestions_json: unknown[] | null
    critic_passed: boolean
    critic_feedback: string | null
    total_rounds: number
    token_usage: Record<string, unknown> | null
    cost_usd: number | null
    created_at: string
  }> => api.get(`/agent/tasks/${taskId}/result`),

  getCheckpoints: (taskId: string): ApiResponse<unknown[]> => api.get(`/agent/tasks/${taskId}/checkpoints`),
}

// RAG endpoints
export const ragApi = {
  search: (data: { query: string; media_ids?: string[]; top_k?: number }): ApiResponse<Array<{
    chunk_id: string
    media_id: string
    content: string
    score: number
    start_ms: number | null
    end_ms: number | null
    evidence_id?: string
  }>> => api.post('/rag/search', data),

  chat: (data: { query: string; media_ids?: string[]; session_id?: string; top_k?: number }): ApiResponse<{
    answer: string
    evidence: Array<{
      chunk_id: string
      media_id: string
      content: string
      score: number
      start_ms: number | null
      end_ms: number | null
      evidence_id?: string
    }>
    session_id: string
  }> => api.post('/rag/chat', data),
}

// Health endpoints —— 对齐后端 health.py：{status:"UP"|"DOWN", components:{postgres,redis,qdrant,minio:"UP"|"DOWN"}}
export const healthApi = {
  check: (): ApiResponse<{ status: 'UP'; version?: string }> => api.get('/health'),

  ready: (): ApiResponse<{
    status: 'UP' | 'DOWN'
    components: {
      postgres: 'UP' | 'DOWN'
      redis: 'UP' | 'DOWN'
      qdrant: 'UP' | 'DOWN'
      minio: 'UP' | 'DOWN'
    }
  }> => api.get('/health/ready'),
}

// User config —— getConfig 返回 dev bootstrap user 的 id（无 auth 时前端以此为操作主体）
export const userApi = {
  getConfig: (): ApiResponse<{
    user_id: string
    theme: 'light' | 'dark' | 'system'
    default_model: string | null
    language: string
  }> => api.get('/user/config'),

  updateConfig: (config: Record<string, unknown>): ApiResponse<void> => api.put('/user/config', config),
}