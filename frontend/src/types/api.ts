import { z } from 'zod'

// ============ Enums ============

export const MediaStatusSchema = z.enum([
  'pending',
  'downloading',
  'downloaded',
  'transcoding',
  'transcoded',
  'asr',
  'ocr',
  'indexing',
  'ready',
  'failed',
])
export type MediaStatus = z.infer<typeof MediaStatusSchema>

export const IngestionStageSchema = z.enum([
  'claimed',
  'downloading',
  'downloaded',
  'transcoding',
  'transcoded',
  'asr',
  'ocr',
  'indexing',
  'completed',
  'failed',
])
export type IngestionStage = z.infer<typeof IngestionStageSchema>

export const AnalysisStatusSchema = z.enum([
  'pending',
  'planning',
  'executing',
  'critic_check',
  'completed',
  'failed',
])
export type AnalysisStatus = z.infer<typeof AnalysisStatusSchema>

export const SourceTypeSchema = z.enum(['upload', 'url'])
export type SourceType = z.infer<typeof SourceTypeSchema>

// ============ Request/Response Schemas ============

// Pipeline Submit
export const PipelineSubmitRequestSchema = z.object({
  source_url: z.string().url().min(5).max(2048),
  user_id: z.string().uuid().optional(),
})
export type PipelineSubmitRequest = z.infer<typeof PipelineSubmitRequestSchema>

export const PipelineSubmitResponseSchema = z.object({
  media_id: z.string().uuid(),
  status: MediaStatusSchema,
  chain_id: z.string().optional(),
})
export type PipelineSubmitResponse = z.infer<typeof PipelineSubmitResponseSchema>

// Media File
export const MediaFileResponseSchema = z.object({
  id: z.string().uuid(),
  user_id: z.string().uuid().nullable(),
  source_type: SourceTypeSchema,
  source_url: z.string().nullable(),
  filename: z.string(),
  mime_type: z.string(),
  file_size: z.number(),
  duration_ms: z.number().nullable(),
  width: z.number().nullable(),
  height: z.number().nullable(),
  fps: z.number().nullable(),
  status: z.string(),
  error_message: z.string().nullable(),
  created_at: z.string().datetime(),
  updated_at: z.string().datetime(),
  completed_at: z.string().datetime().nullable(),
  thumbnail_object: z.string().nullable().optional(),
  // yt-dlp 抓取的视频标题，后端存于 meta_json.title 并作为 computed_field 暴露
  title: z.string().nullable().optional(),
})
export type MediaFileResponse = z.infer<typeof MediaFileResponseSchema>

// Video List
export const VideoListResponseSchema = z.object({
  items: z.array(MediaFileResponseSchema),
  total: z.number(),
  page: z.number(),
  page_size: z.number(),
  total_pages: z.number(),
})
export type VideoListResponse = z.infer<typeof VideoListResponseSchema>

// Pipeline Status
export const PipelineStatusResponseSchema = z.object({
  media_id: z.string().uuid(),
  source_url: z.string(),
  status: z.string(),
  stage_progress: z.record(z.string(), z.number()),
  error_message: z.string().nullable(),
})
export type PipelineStatusResponse = z.infer<typeof PipelineStatusResponseSchema>

// Pipeline Progress (SSE)
export const ProgressEventSchema = z.object({
  stage: IngestionStageSchema,
  progress_pct: z.number().int().min(-1).max(100),
  message: z.string(),
  timestamp: z.string().datetime(), // ISO8601
  metadata: z.record(z.unknown()).optional(),
})
export type ProgressEvent = z.infer<typeof ProgressEventSchema>

// Video Segments
export const VideoSegmentResponseSchema = z.object({
  id: z.string().uuid(),
  segment_index: z.number().int(),
  start_ms: z.number().int(),
  end_ms: z.number().int(),
  transcript: z.string().nullable(),
  ocr_texts: z.array(z.string()).nullable(),
  evidence_frames: z.array(z.unknown()).nullable(),
  token_count: z.number().int(),
  confidence: z.number().nullable(),
  speaker: z.string().nullable(),
  created_at: z.string().datetime(),
})
export type VideoSegmentResponse = z.infer<typeof VideoSegmentResponseSchema>

// Paginated Segments Response
export const SegmentsListResponseSchema = z.object({
  items: z.array(VideoSegmentResponseSchema),
  total: z.number(),
  page: z.number(),
  page_size: z.number(),
  has_more: z.boolean(),
})
export type SegmentsListResponse = z.infer<typeof SegmentsListResponseSchema>

// OCR Result — 对齐后端 FrameOCR 真实列：
// id / media_id / frame_ms / minio_object / ocr_text(可空) / phash(可空) /
// model_name(可空) / status / created_at
// 旧 schema 虚构了 frame_index / timestamp_ms / confidence / bbox，这些列在
// FrameOCR 上不存在，曾导致 GET /videos/{id} 详情 500，连累转录 tab 不渲染。
export const OCRResultResponseSchema = z.object({
  id: z.string().uuid(),
  media_id: z.string().uuid(),
  frame_ms: z.number().int(),
  minio_object: z.string(),
  ocr_text: z.string().nullable(),
  phash: z.string().nullable(),
  model_name: z.string().nullable(),
  status: z.string(),
  created_at: z.string().datetime(),
})
export type OCRResultResponse = z.infer<typeof OCRResultResponseSchema>

// Paginated OCR Results
export const OcrResultsListResponseSchema = z.object({
  items: z.array(OCRResultResponseSchema),
  total: z.number(),
  page: z.number(),
  page_size: z.number(),
  has_more: z.boolean(),
})
export type OcrResultsListResponse = z.infer<typeof OcrResultsListResponseSchema>

// Transcription
export const TranscriptionResponseSchema = z.object({
  id: z.string().uuid(),
  media_id: z.string().uuid(),
  full_text: z.string(),
  language: z.string(),
  model_name: z.string(),
  duration_sec: z.number().nullable(),
  chunk_count: z.number().int(),
  created_at: z.string().datetime(),
})
export type TranscriptionResponse = z.infer<typeof TranscriptionResponseSchema>

// Analysis Task
export const AnalysisTaskRequestSchema = z.object({
  goal: z.string().min(1).max(5000),
  media_ids: z.array(z.string().uuid()).min(1).max(4),
  user_id: z.string().uuid(),
  max_rounds: z.number().int().min(1).max(3).default(2),
})
export type AnalysisTaskRequest = z.infer<typeof AnalysisTaskRequestSchema>

export const AnalysisTaskResponseSchema = z.object({
  task_id: z.string().uuid(),
  status: AnalysisStatusSchema,
  goal: z.string(),
  max_rounds: z.number().int(),
  created_at: z.string().datetime(),
})
export type AnalysisTaskResponse = z.infer<typeof AnalysisTaskResponseSchema>

export const AnalysisTaskStatusResponseSchema = z.object({
  task_id: z.string().uuid(),
  status: AnalysisStatusSchema,
  current_round: z.number().int(),
  max_rounds: z.number().int(),
  goal: z.string(),
  final_result_json: z.record(z.unknown()).nullable(),
  error_message: z.string().nullable(),
  created_at: z.string().datetime(),
  started_at: z.string().datetime().nullable(),
  completed_at: z.string().datetime().nullable(),
})
export type AnalysisTaskStatusResponse = z.infer<typeof AnalysisTaskStatusResponseSchema>

export const AgentResultResponseSchema = z.object({
  id: z.string().uuid(),
  task_id: z.string().uuid(),
  title: z.string(),
  conclusions_json: z.array(z.unknown()),
  evidence_json: z.array(z.unknown()),
  suggestions_json: z.array(z.unknown()).nullable(),
  critic_passed: z.boolean(),
  critic_feedback: z.string().nullable(),
  total_rounds: z.number().int(),
  token_usage: z.record(z.unknown()).nullable(),
  cost_usd: z.number().nullable(),
  created_at: z.string().datetime(),
})
export type AgentResultResponse = z.infer<typeof AgentResultResponseSchema>

// Health —— 对齐后端 src/videomind/interface/routes/health.py 实际返回
export const HealthResponseSchema = z.object({
  status: z.literal('UP'),
  version: z.string().optional(),
})
export type HealthResponse = z.infer<typeof HealthResponseSchema>

export const HealthReadyResponseSchema = z.object({
  status: z.enum(['UP', 'DOWN']),
  components: z.object({
    postgres: z.enum(['UP', 'DOWN']),
    redis: z.enum(['UP', 'DOWN']),
    qdrant: z.enum(['UP', 'DOWN']),
    minio: z.enum(['UP', 'DOWN']),
  }),
})
export type HealthReadyResponse = z.infer<typeof HealthReadyResponseSchema>

// RAG Search/Chat (to be implemented in backend)
export const RagSearchRequestSchema = z.object({
  query: z.string().min(1).max(1000),
  media_ids: z.array(z.string().uuid()).optional(),
  top_k: z.number().int().min(1).max(50).default(10),
})
export type RagSearchRequest = z.infer<typeof RagSearchRequestSchema>

export const RagSearchResultSchema = z.object({
  chunk_id: z.string().uuid(),
  media_id: z.string().uuid(),
  content: z.string(),
  score: z.number(),
  start_ms: z.number().int().nullable(),
  end_ms: z.number().int().nullable(),
  evidence_id: z.string().optional(),
})
export type RagSearchResult = z.infer<typeof RagSearchResultSchema>

export const RagChatRequestSchema = z.object({
  query: z.string().min(1).max(2000),
  media_ids: z.array(z.string().uuid()).optional(),
  session_id: z.string().uuid().optional(),
  top_k: z.number().int().min(1).max(50).default(10),
})
export type RagChatRequest = z.infer<typeof RagChatRequestSchema>

export const RagChatResponseSchema = z.object({
  answer: z.string(),
  evidence: z.array(RagSearchResultSchema),
  session_id: z.string().uuid(),
})
export type RagChatResponse = z.infer<typeof RagChatResponseSchema>

// User Config
export const UserConfigSchema = z.object({
  theme: z.enum(['light', 'dark', 'system']).default('system'),
  default_model: z.string().optional(),
  language: z.string().default('zh-CN'),
})
export type UserConfig = z.infer<typeof UserConfigSchema>

// Error
export const ApiErrorSchema = z.object({
  detail: z.string(),
  status_code: z.number().int(),
})
export type ApiError = z.infer<typeof ApiErrorSchema>