export const ENDPOINTS = {
  // Health
  HEALTH: '/health',
  HEALTH_READY: '/health/ready',
  HEALTH_LIVE: '/health/live',

  // Video Pipeline
  VIDEO_PIPELINE: '/videos/pipeline',
  VIDEO_PIPELINE_STATUS: (mediaId: string) => `/videos/pipeline/${mediaId}`,
  VIDEO_PIPELINE_PROGRESS_SSE: (mediaId: string) => `/videos/pipeline/${mediaId}/progress`,

  // Video Library (to be implemented in backend)
  VIDEOS_LIST: '/videos',
  VIDEO_DETAIL: (mediaId: string) => `/videos/${mediaId}`,
  VIDEO_DELETE: (mediaId: string) => `/videos/${mediaId}`,
  VIDEO_SEGMENTS: (mediaId: string) => `/videos/${mediaId}/segments`,
  VIDEO_TRANSCRIPTION: (mediaId: string) => `/videos/${mediaId}/transcription`,

  // RAG (to be implemented in backend)
  RAG_SEARCH: '/rag/search',
  RAG_CHAT: '/rag/chat',

  // Agent (to be implemented in backend)
  AGENT_ANALYZE: '/agent/analyze',
  AGENT_TASK_STATUS: (taskId: string) => `/agent/tasks/${taskId}`,
  AGENT_TASK_RESULT: (taskId: string) => `/agent/tasks/${taskId}/result`,

  // User config (to be implemented)
  USER_CONFIG: '/user/config',
  USER_AI_CONFIGS: '/user/ai-configs',
} as const

export type EndpointKey = keyof typeof ENDPOINTS