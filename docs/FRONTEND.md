# VideoMind 前端工作台设计

> Vue 3 + Vite + SSE 实时工作台：视频库管理、Agent 分析工作台、流式 Markdown 渲染、证据卡片、键盘快捷键
> 核心参考：Ragent `web/` + DOVideo-AI 前端 + VidLens UI 组件库

---

## 1. 前端架构总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            VideoMind Web Workbench                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  App Shell (Layout)                                                 │   │
│  │  ┌─────────┐ ┌─────────────────────────────────────────────────┐  │   │
│  │  │ Sidebar │ │              Main View (RouterView)              │  │   │
│  │  │         │ │  ┌─────────────┐ ┌─────────────┐ ┌───────────┐  │  │   │
│  │  │ Navigation│ │  Video      │ │  Agent      │ │  RAG      │  │  │   │
│  │  │  Tree     │ │  Library    │ │  Workbench  │ │  Chat     │  │  │   │
│  │  │           │ │  (Grid/List)│ │  (SSE Stream)          │  │  │   │
│  │  └─────────┘ └─────────────────────────────────────────────────┘  │   │
│  │  ┌─────────────────────────────────────────────────────────────┐  │   │
│  │  │  Global SSE Connection Manager (EventSource + Reconnect)   │  │   │
│  │  └─────────────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
            ┌────────────┐  ┌────────────┐  ┌────────────┐
            │  FastAPI   │  │  FastAPI   │  │  FastAPI   │
            │  /api/v1/  │  │  /api/v1/  │  │  /api/v1/  │
            │  videos    │  │  tasks     │  │  rag       │
            │  (REST)    │  │  (SSE)     │  │  (SSE)     │
            └────────────┘  └────────────┘  └────────────┘
```

---

## 2. 技术栈与依赖

```json
{
  "dependencies": {
    "vue": "^3.4.0",
    "vue-router": "^4.3.0",
    "pinia": "^2.1.0",
    "@vueuse/core": "^10.9.0",
    "markdown-it": "^14.1.0",
    "katex": "^0.16.9",
    "mermaid": "^10.9.0",
    "hljs": "^11.9.0",
    "axios": "^1.6.0",
    "mitt": "^3.0.1"
  },
  "devDependencies": {
    "vite": "^5.2.0",
    "@vitejs/plugin-vue": "^5.0.0",
    "typescript": "^5.4.0",
    "vue-tsc": "^2.0.0",
    "tailwindcss": "^3.4.0",
    "@headlessui/vue": "^1.7.0",
    "@heroicons/vue": "^2.1.0"
  }
}
```

---

## 3. 核心页面与路由

```typescript
// router/index.ts
const routes = [
  {
    path: '/',
    redirect: '/library'
  },
  {
    path: '/library',
    name: 'VideoLibrary',
    component: () => import('@/views/VideoLibrary.vue'),
    meta: { title: '视频库', icon: 'video-camera' }
  },
  {
    path: '/library/:videoId',
    name: 'VideoDetail',
    component: () => import('@/views/VideoDetail.vue'),
    meta: { title: '视频详情', hideInMenu: true }
  },
  {
    path: '/analysis',
    name: 'AnalysisWorkbench',
    component: () => import('@/views/AnalysisWorkbench.vue'),
    meta: { title: 'Agent 分析', icon: 'cpu-chip' }
  },
  {
    path: '/analysis/:taskId',
    name: 'AnalysisSession',
    component: () => import('@/views/AnalysisSession.vue'),
    meta: { title: '分析会话', hideInMenu: true }
  },
  {
    path: '/rag',
    name: 'RAGChat',
    component: () => import('@/views/RAGChat.vue'),
    meta: { title: '智能问答', icon: 'chat-bubble-left-right' }
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('@/views/Settings.vue'),
    meta: { title: '设置', icon: 'cog-6-tooth' }
  }
]
```

---

## 4. 状态管理

### 4.1 视频库 Store

```typescript
// stores/videoLibrary.ts
export const useVideoLibraryStore = defineStore('videoLibrary', () => {
  const videos = ref<Video[]>([])
  const loading = ref(false)
  const filters = ref<VideoFilter>({
    query: '',
    status: 'all',
    dateRange: null,
    tags: []
  })
  const pagination = ref({ page: 1, pageSize: 20, total: 0 })
  
  async function fetchVideos() {
    loading.value = true
    try {
      const res = await api.videos.list({
        ...filters.value,
        page: pagination.value.page,
        page_size: pagination.value.pageSize
      })
      videos.value = res.items
      pagination.value.total = res.total
    } finally {
      loading.value = false
    }
  }
  
  async function deleteVideo(id: string) {
    await api.videos.delete(id)
    await fetchVideos()
  }
  
  async function reIngestVideo(id: string) {
    await api.tasks.create({ type: 'ingestion', video_id: id })
    // SSE 进度在 TaskMonitor 中处理
  }
  
  return { videos, loading, filters, pagination, fetchVideos, deleteVideo, reIngestVideo }
})
```

### 4.2 SSE 任务监控 Store

```typescript
// stores/taskMonitor.ts
export const useTaskMonitorStore = defineStore('taskMonitor', () => {
  const activeTasks = ref<Map<string, TaskProgress>>(new Map())
  const eventSources = ref<Map<string, EventSource>>(new Map())
  const emitter = mitt<TaskEvents>()
  
  function connect(taskId: string) {
    if (eventSources.value.has(taskId)) return
    
    const es = new EventSource(`/api/v1/tasks/${taskId}/stream`, {
      withCredentials: true
    })
    
    es.onmessage = (event) => {
      const data = JSON.parse(event.data) as TaskProgress
      activeTasks.value.set(taskId, data)
      emitter.emit('progress', taskId, data)
      
      if (data.phase === 'COMPLETED' || data.phase === 'FAILED') {
        disconnect(taskId)
      }
    }
    
    es.onerror = () => {
      // 自动重连逻辑
      scheduleReconnect(taskId)
    }
    
    eventSources.value.set(taskId, es)
  }
  
  function disconnect(taskId: string) {
    const es = eventSources.value.get(taskId)
    if (es) {
      es.close()
      eventSources.value.delete(taskId)
    }
  }
  
  function getProgress(taskId: string) {
    return activeTasks.value.get(taskId)
  }
  
  return { activeTasks, connect, disconnect, getProgress, on: emitter.on }
})
```

---

## 5. 核心组件

### 5.1 视频库网格/列表

```vue
<!-- components/video/VideoGrid.vue -->
<script setup lang="ts">
interface Props {
  videos: Video[]
  viewMode: 'grid' | 'list'
  onPlay: (video: Video) => void
  onAnalyze: (video: Video) => void
  onDelete: (video: Video) => void
}

const props = defineProps<Props>()
const emit = defineEmits<{}>()

const formatDuration = (sec: number) => {
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  return h > 0 ? `${h}:${m.toString().padStart(2,'0')}:${s.toString().padStart(2,'0')}` : `${m}:${s.toString().padStart(2,'0')}`
}
</script>

<template>
  <div class="video-grid" :class="viewMode">
    <VideoCard
      v-for="video in videos"
      :key="video.id"
      :video="video"
      :view-mode="viewMode"
      @play="onPlay"
      @analyze="onAnalyze"
      @delete="onDelete"
    />
  </div>
  
  <div v-if="!videos.length" class="empty-state">
    <HeroIcon name="video-camera-slash" class="h-12 w-12 text-gray-400" />
    <p class="mt-2 text-gray-500">暂无视频，点击「添加视频」开始</p>
  </div>
</template>
```

```vue
<!-- components/video/VideoCard.vue -->
<script setup lang="ts">
interface Props {
  video: Video
  viewMode: 'grid' | 'list'
}

const props = defineProps<Props>()
const emit = defineEmits<{ play: [Video], analyze: [Video], delete: [Video] }>()

const statusColors: Record<string, string> = {
  completed: 'bg-green-100 text-green-700',
  processing: 'bg-yellow-100 text-yellow-700',
  failed: 'bg-red-100 text-red-700',
  pending: 'bg-gray-100 text-gray-700'
}
</script>

<template>
  <article class="video-card group relative bg-white rounded-xl border border-gray-200 overflow-hidden transition-shadow hover:shadow-lg" :class="{ 'flex flex-row': viewMode === 'list', 'flex-col': viewMode === 'grid' }">
    <!-- 缩略图 -->
    <div class="relative aspect-video bg-gray-100 overflow-hidden" :class="{ 'w-full': viewMode === 'grid', 'w-64 flex-shrink-0': viewMode === 'list' }">
      <img :src="video.thumbnail_url" :alt="video.title" class="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105" loading="lazy" />
      
      <!-- 播放图标覆盖 -->
      <button @click="emit('play', props.video)" class="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity">
        <HeroIcon name="play-circle" class="h-16 w-16 text-white" />
      </button>
      
      <!-- 状态徽章 -->
      <span class="absolute top-2 right-2 px-2 py-1 text-xs font-medium rounded-full" :class="statusColors[video.status]">
        {{ video.status }}
      </span>
      
      <!-- 时长 -->
      <span class="absolute bottom-2 right-2 px-1.5 py-0.5 text-xs bg-black/70 text-white rounded">
        {{ formatDuration(video.duration) }}
      </span>
    </div>
    
    <!-- 信息区 -->
    <div class="p-4 flex-1 flex flex-col" :class="{ 'min-w-0': viewMode === 'list' }">
      <h3 class="font-semibold text-gray-900 line-clamp-2" :class="{ 'line-clamp-1': viewMode === 'list' }">{{ video.title }}</h3>
      
      <div class="mt-2 flex items-center gap-3 text-sm text-gray-500">
        <span class="flex items-center gap-1">
          <HeroIcon name="user-circle" class="h-4 w-4" />
          {{ video.author }}
        </span>
        <span class="flex items-center gap-1">
          <HeroIcon name="calendar-days" class="h-4 w-4" />
          {{ formatDate(video.published_at) }}
        </span>
      </div>
      
      <p v-if="video.description" class="mt-2 text-sm text-gray-600 line-clamp-2" :class="{ 'line-clamp-1': viewMode === 'list' }">{{ video.description }}</p>
      
      <!-- 标签 -->
      <div class="mt-3 flex flex-wrap gap-1">
        <span v-for="tag in video.tags.slice(0, 4)" :key="tag" class="px-2 py-0.5 text-xs bg-gray-100 text-gray-600 rounded">{{ tag }}</span>
        <span v-if="video.tags.length > 4" class="px-2 py-0.5 text-xs bg-gray-100 text-gray-400 rounded">+{{ video.tags.length - 4 }}</span>
      </div>
      
      <!-- 操作按钮 -->
      <div class="mt-4 flex items-center gap-2 pt-3 border-t border-gray-100">
        <button @click="emit('analyze', props.video)" class="flex-1 btn-primary text-sm py-1.5">
          <HeroIcon name="sparkles" class="h-4 w-4 mr-1" />
          分析
        </button>
        <button @click="emit('delete', props.video)" class="btn-ghost text-sm py-1.5 px-3 text-red-600 hover:bg-red-50">
          <HeroIcon name="trash" class="h-4 w-4" />
        </button>
      </div>
    </div>
  </article>
</template>
```

### 5.2 Agent 分析工作台（SSE 流式）

```vue
<!-- views/AnalysisWorkbench.vue -->
<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useTaskMonitorStore } from '@/stores/taskMonitor'
import { useVideoLibraryStore } from '@/stores/videoLibrary'
import StreamingMarkdown from '@/components/analysis/StreamingMarkdown.vue'
import EvidenceCard from '@/components/analysis/EvidenceCard.vue'
import PhaseIndicator from '@/components/analysis/PhaseIndicator.vue'

const route = useRoute()
const router = useRouter()
const taskMonitor = useTaskMonitorStore()
const videoLibrary = useVideoLibraryStore()

const taskId = ref(route.params.taskId as string)
const goal = ref('')
const videoId = ref('')
const phases = ref<TaskPhase[]>([])
const currentPhase = ref('')
const answer = ref('')
// 证据卡片数据契约（与后端 AGENT-LOOP Evidence 对齐：后端字段 id 在前端序列化为 evidence_id）
interface Evidence {
  evidence_id: string    // = 后端 Evidence.id，EID_{chunk_id[:8]}_{idx:02d}（INTENT-ROUTING make_evidence_id 产出）
  timestamp_ms: number    // 视频时间戳（毫秒）
  source: 'asr' | 'ocr' | 'frame'
  content: string        // 原文片段（≤500）
  chunk_id: string       // UUID，关联 chunk 表，用于跳转视频片段
}

const evidence = ref<Evidence[]>([])
const isStreaming = ref(false)
const error = ref('')

async function startAnalysis() {
  if (!videoId.value || !goal.value.trim()) return
  
  try {
    isStreaming.value = true
    error.value = ''
    answer.value = ''
    evidence.value = []
    phases.value = []
    
    const res = await api.tasks.create({
      type: 'analysis',
      video_id: videoId.value,
      goal: goal.value
    })
    
    taskId.value = res.task_id
    router.replace({ name: 'AnalysisSession', params: { taskId: res.task_id } })
    
    // 连接 SSE
    taskMonitor.connect(res.task_id)
    taskMonitor.on('progress', handleProgress)
    
  } catch (e) {
    error.value = e.message
    isStreaming.value = false
  }
}

function handleProgress(taskId: string, progress: TaskProgress) {
  currentPhase.value = progress.phase
  phases.value = progress.phases
  
  if (progress.phase === 'EXECUTING' && progress.partial_answer) {
    answer.value = progress.partial_answer
  }
  
  if (progress.evidence) {
    evidence.value = progress.evidence
  }
  
  if (progress.phase === 'COMPLETED') {
    isStreaming.value = false
    answer.value = progress.final_answer
    evidence.value = progress.evidence
  } else if (progress.phase === 'FAILED') {
    isStreaming.value = false
    error.value = progress.error
  }
}

onUnmounted(() => {
  if (taskId.value) taskMonitor.disconnect(taskId.value)
})
</script>

<template>
  <div class="analysis-workbench h-screen flex flex-col">
    <!-- 顶部栏 -->
    <header class="bg-white border-b border-gray-200 px-6 py-4">
      <div class="max-w-7xl mx-auto flex items-center justify-between">
        <h1 class="text-2xl font-bold text-gray-900">Agent 视频分析工作台</h1>
        <PhaseIndicator :phases="phases" :current="currentPhase" />
      </div>
    </header>
    
    <main class="flex-1 overflow-hidden flex">
      <!-- 左侧：视频选择 + 目标输入 -->
      <aside class="w-96 bg-gray-50 border-r border-gray-200 p-6 overflow-y-auto" v-if="!taskId">
        <div class="space-y-6">
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-2">选择视频</label>
            <VideoSelector v-model="videoId" :videos="videoLibrary.videos" />
          </div>
          
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-2">分析目标</label>
            <textarea v-model="goal" rows="4" class="w-full p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent" 
              placeholder="例如：总结视频核心观点，并指出论据是否充分&#10;例如：找出视频中提到的所有数据指标及其来源&#10;例如：对比视频前后两个版本方案的优劣" />
            <p class="mt-1 text-xs text-gray-500">支持复杂推理、跨片段对比、因果分析等</p>
          </div>
          
          <button @click="startAnalysis" :disabled="!videoId || !goal.trim() || isStreaming" class="w-full btn-primary py-3 text-lg">
            <span v-if="isStreaming" class="flex items-center justify-center gap-2">
              <HeroIcon name="spinner" class="h-5 w-5 animate-spin" />
              启动中...
            </span>
            <span v-else>开始分析</span>
          </button>
          
          <div v-if="error" class="text-red-600 text-sm p-3 bg-red-50 rounded">{{ error }}</div>
        </div>
      </aside>
      
      <!-- 右侧：流式结果区 -->
      <section class="flex-1 flex flex-col overflow-hidden" v-if="taskId">
        <!-- 进行中提示 -->
        <div v-if="isStreaming && currentPhase" class="p-4 bg-blue-50 border-b border-blue-100">
          <div class="flex items-center gap-2 text-blue-700">
            <HeroIcon name="arrow-path" class="h-5 w-5 animate-spin" />
            <span>正在执行：{{ currentPhase }}...</span>
          </div>
        </div>
        
        <!-- 结果区 -->
        <div class="flex-1 overflow-y-auto p-6 space-y-6">
          <!-- 流式 Markdown 回答 -->
          <div class="prose prose-lg max-w-none" v-if="answer">
            <StreamingMarkdown :content="answer" :is-streaming="isStreaming" />
          </div>
          
          <!-- 证据卡片 -->
          <div v-if="evidence.length" class="border-t border-gray-200 pt-6">
            <h3 class="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <HeroIcon name="document-magnifying-glass" class="h-5 w-5" />
              引用证据 ({{ evidence.length }})
            </h3>
            <div class="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
              <EvidenceCard v-for="e in evidence" :key="e.evidence_id" :evidence="e" />
            </div>
          </div>
          
          <div v-else-if="!isStreaming && !answer" class="text-center py-12 text-gray-500">
            <HeroIcon name="sparkles" class="h-12 w-12 mx-auto text-gray-300" />
            <p class="mt-2">分析完成后将在此显示结果</p>
          </div>
        </div>
        
        <!-- 底部操作栏 -->
        <div class="p-4 border-t border-gray-200 bg-gray-50 flex items-center justify-end gap-3">
          <button @click="copyAnswer" class="btn-secondary" :disabled="!answer">
            <HeroIcon name="document-duplicate" class="h-4 w-4 mr-1" />
            复制回答
          </button>
          <button @click="exportMarkdown" class="btn-secondary" :disabled="!answer">
            <HeroIcon name="arrow-down-tray" class="h-4 w-4 mr-1" />
            导出 Markdown
          </button>
          <router-link :to="{ name: 'AnalysisWorkbench' }" class="btn-primary">
            <HeroIcon name="plus" class="h-4 w-4 mr-1" />
            新建分析
          </router-link>
        </div>
      </section>
    </main>
  </div>
</template>
```

### 5.3 流式 Markdown 渲染器

```vue
<!-- components/analysis/StreamingMarkdown.vue -->
<script setup lang="ts">
import { ref, onMounted, watch, nextTick } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github-dark.css'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import mermaid from 'mermaid'

const props = defineProps<{
  content: string
  isStreaming: boolean
}>()

const containerRef = ref<HTMLDivElement>()
const md = ref<MarkdownIt>()

onMounted(() => {
  md.value = new MarkdownIt({
    html: true,
    linkify: true,
    typographer: true,
    highlight: (str, lang) => {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(str, { language: lang }).value
      }
      return ''
    }
  })
  
  // 数学公式
  md.value.use(require('markdown-it-katex'), { katex })
  
  // Mermaid 图表
  md.value.use(require('markdown-it-mermaid'), { mermaid })
  
  mermaid.initialize({ startOnLoad: false, theme: 'default' })
  
  render()
})

watch(() => props.content, () => {
  if (!props.isStreaming) {
    // 非流式时防抖渲染
    clearTimeout(renderTimer)
    renderTimer = setTimeout(render, 50)
  } else {
    // 流式时节流渲染（每 100ms）
    if (!renderThrottle) {
      renderThrottle = setTimeout(() => {
        render()
        renderThrottle = null
      }, 100)
    }
  }
}, { deep: true })

let renderTimer: number
let renderThrottle: number

function render() {
  if (!containerRef.value || !md.value) return
  
  const html = md.value.render(props.content)
  containerRef.value.innerHTML = html
  
  // 代码高亮
  containerRef.value.querySelectorAll('pre code').forEach((block) => {
    hljs.highlightElement(block as HTMLElement)
  })
  
  // Mermaid 渲染
  containerRef.value.querySelectorAll('.mermaid').forEach((el) => {
    mermaid.run({ nodes: [el] })
  })
  
  // KaTeX 已由 markdown-it-katex 处理
}
</script>

<template>
  <div ref="containerRef" class="streaming-markdown" v-show="!isStreaming || content.length > 50">
    <!-- 流式时显示光标 -->
    <span v-if="isStreaming" class="cursor-blink" aria-hidden="true">█</span>
  </div>
  
  <style scoped>
  .streaming-markdown {
    min-height: 200px;
  }
  .cursor-blink {
    display: inline-block;
    width: 2px;
    height: 1.2em;
    background: currentColor;
    animation: blink 1s infinite;
    margin-left: 2px;
    vertical-align: text-bottom;
  }
  @keyframes blink { 0%, 50% { opacity: 1; } 51%, 100% { opacity: 0; } }
  
  /* 代码块复制按钮 */
  pre { position: relative; }
  pre::before {
    content: "复制";
    position: absolute;
    top: 8px; right: 8px;
    padding: 2px 8px;
    font-size: 11px;
    background: #374151;
    color: #9ca3af;
    border-radius: 4px;
    opacity: 0;
    transition: opacity 0.2s;
  }
  pre:hover::before { opacity: 1; }
  pre:hover::before:hover { background: #4b5563; color: white; cursor: pointer; }
  </style>
</template>
```

### 5.4 证据卡片

```vue
<!-- components/analysis/EvidenceCard.vue -->
<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  evidence: Evidence
}

const props = defineProps<Props>()

// source ∈ {ASR, OCR},对应后端 Evidence.source
const evidenceSourceIcons: Record<string, string> = {
  ASR: 'waveform',
  OCR: 'document-text'
}

const evidenceSourceLabels: Record<string, string> = {
  ASR: '语音转录',
  OCR: '画面文字'
}

// 由 timestamp_ms(ms)派生秒级时间,默认 10 秒窗口展示
const startSec = computed(() => Math.floor(props.evidence.timestamp_ms / 1000))
const endSec = computed(() => startSec.value + 10)

function formatTimestamp(seconds: number) {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  return `${h > 0 ? h + ':' : ''}${m.toString().padStart(2,'0')}:${s.toString().padStart(2,'0')}`
}

function openVideoAtTimestamp() {
  // 通过 chunk_id 跳转视频精确时间点(timestamp_ms 已派生秒级 t)
  router.push({ name: 'VideoDetail', params: { chunkId: props.evidence.chunk_id }, query: { t: startSec.value } })
}
</script>

<template>
  <article class="evidence-card bg-white border border-gray-200 rounded-lg p-4 hover:border-primary-300 hover:shadow-md transition-all cursor-pointer" @click="openVideoAtTimestamp">
    <div class="flex items-start gap-3">
      <!-- 来源图标 -->
      <div class="flex-shrink-0 w-10 h-10 rounded-lg bg-primary-50 flex items-center justify-center">
        <component :is="HeroIcon" :name="evidenceSourceIcons[props.evidence.source]" class="h-5 w-5 text-primary-600" />
      </div>
      
      <!-- 内容 -->
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 text-xs text-gray-500 mb-1">
          <span class="px-2 py-0.5 bg-gray-100 rounded">{{ evidenceSourceLabels[props.evidence.source] }}</span>
          <span class="font-mono">{{ formatTimestamp(startSec) }} - {{ formatTimestamp(endSec) }}</span>
        </div>
        
        <p class="text-sm text-gray-700 line-clamp-3">{{ props.evidence.content }}</p>
        
        <!-- 证据 ID 与 chunk_id(chunk_id 用于跳转视频时间点)-->
        <div class="mt-2 flex items-center gap-2 text-xs text-gray-400">
          <kbd class="px-1.5 py-0.5 bg-gray-100 rounded font-mono">{{ props.evidence.evidence_id }}</kbd>
          <span class="flex-1 truncate font-mono">chunk: {{ props.evidence.chunk_id }}</span>
        </div>
      </div>
      
    </div>
  </article>
</template>
```

---

## 6. 键盘快捷键

```typescript
// composables/useKeyboardShortcuts.ts
export function useKeyboardShortcuts() {
  const shortcuts: Record<string, { keys: string[], action: () => void, description: string }> = {
    // 全局
    'new-analysis': { keys: ['n'], action: () => router.push('/analysis'), description: '新建分析' },
    'search': { keys: ['/'], action: () => focusSearch(), description: '聚焦搜索' },
    'command-palette': { keys: ['meta', 'k'], action: () => openCommandPalette(), description: '命令面板' },
    
    // 视频库
    'play-selected': { keys: ['enter'], action: () => playSelectedVideo(), description: '播放选中视频', context: 'library' },
    'analyze-selected': { keys: ['a'], action: () => analyzeSelectedVideo(), description: '分析选中视频', context: 'library' },
    'delete-selected': { keys: ['delete'], action: () => deleteSelectedVideo(), description: '删除选中视频', context: 'library' },
    
    // 分析工作台
    'focus-goal': { keys: ['meta', 'enter'], action: () => focusGoalInput(), description: '聚焦分析目标输入', context: 'analysis' },
    'copy-answer': { keys: ['meta', 'c'], action: () => copyAnswer(), description: '复制回答', context: 'analysis' },
    'export-md': { keys: ['meta', 'e'], action: () => exportMarkdown(), description: '导出 Markdown', context: 'analysis' },
    'new-session': { keys: ['meta', 'n'], action: () => router.push('/analysis'), description: '新建分析会话', context: 'analysis' },
    
    // 导航
    'goto-library': { keys: ['g', 'l'], action: () => router.push('/library'), description: '去视频库' },
    'goto-analysis': { keys: ['g', 'a'], action: () => router.push('/analysis'), description: '去分析工作台' },
    'goto-rag': { keys: ['g', 'r'], action: () => router.push('/rag'), description: '去智能问答' },
  }
  
  onMounted(() => {
    window.addEventListener('keydown', handleKeydown)
  })
  
  onUnmounted(() => {
    window.removeEventListener('keydown', handleKeydown)
  })
  
  function handleKeydown(e: KeyboardEvent) {
    // 忽略输入框中的按键
    if (isInputFocused(e.target as HTMLElement)) return
    
    for (const [id, shortcut] of Object.entries(shortcuts)) {
      if (matchKeys(e, shortcut.keys)) {
        e.preventDefault()
        shortcut.action()
        break
      }
    }
  }
  
  function matchKeys(e: KeyboardEvent, keys: string[]): boolean {
    return keys.every(k => {
      if (k === 'meta') return e.metaKey || e.ctrlKey
      if (k === 'shift') return e.shiftKey
      if (k === 'alt') return e.altKey
      return e.key.toLowerCase() === k.toLowerCase()
    })
  }
  
  function isInputFocused(el: HTMLElement): boolean {
    return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable
  }
}
```

---

## 7. 样式规范

```css
/* styles/tailwind.css */
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer components {
  .btn-primary {
    @apply px-4 py-2 bg-primary-600 text-white rounded-lg font-medium 
           hover:bg-primary-700 focus:ring-2 focus:ring-primary-500 focus:ring-offset-2
           disabled:opacity-50 disabled:cursor-not-allowed transition-colors;
  }
  
  .btn-secondary {
    @apply px-4 py-2 bg-white text-gray-700 border border-gray-300 rounded-lg font-medium
           hover:bg-gray-50 focus:ring-2 focus:ring-primary-500 focus:ring-offset-2
           disabled:opacity-50 disabled:cursor-not-allowed transition-colors;
  }
  
  .btn-ghost {
    @apply px-3 py-1.5 text-gray-600 rounded-lg font-medium
           hover:bg-gray-100 focus:ring-2 focus:ring-primary-500 focus:ring-offset-2
           transition-colors;
  }
  
  .input-field {
    @apply w-full px-3 py-2 border border-gray-300 rounded-lg
           focus:ring-2 focus:ring-primary-500 focus:border-transparent
           placeholder:text-gray-400 transition-shadow;
  }
  
  .card {
    @apply bg-white rounded-xl border border-gray-200 shadow-sm
           hover:shadow-md transition-shadow;
  }
  
  .prose {
    @apply text-gray-900 leading-relaxed;
  }
  .prose h1 { @apply text-3xl font-bold mt-8 mb-4 text-gray-900; }
  .prose h2 { @apply text-2xl font-semibold mt-8 mb-3 text-gray-900; }
  .prose h3 { @apply text-xl font-medium mt-6 mb-2 text-gray-900; }
  .prose p { @apply mb-4; }
  .prose code { @apply bg-gray-100 px-1.5 py-0.5 rounded text-sm font-mono text-pink-600; }
  .prose pre { @apply bg-gray-900 rounded-lg p-4 overflow-x-auto mb-4; }
  .prose pre code { @apply bg-transparent p-0 text-gray-100; }
  .prose blockquote { @apply border-l-4 border-primary-500 pl-4 italic text-gray-600 my-4; }
  .prose ul { @apply list-disc list-inside mb-4 space-y-1; }
  .prose ol { @apply list-decimal list-inside mb-4 space-y-1; }
  .prose a { @apply text-primary-600 hover:underline; }
  .prose table { @apply w-full border-collapse mb-4; }
  .prose th, .prose td { @apply border border-gray-300 px-3 py-2 text-left; }
  .prose th { @apply bg-gray-100 font-semibold; }
}
```

---

## 8. 环境变量

```env
# .env
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_WS_BASE_URL=ws://localhost:8000
VITE_APP_TITLE=VideoMind
VITE_ENABLE_MOCK=false
```

---

## 9. 部署构建

```yaml
# docker/frontend.Dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

```nginx
# nginx.conf
server {
    listen 80;
    server_name localhost;
    root /usr/share/nginx/html;
    index index.html;
    
    location / {
        try_files $uri $uri/ /index.html;
    }
    
    location /api/ {
        proxy_pass http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
    
    # SSE 专用配置
    location /api/v1/tasks/ {
        proxy_pass http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache off;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
    
    gzip on;
    gzip_types text/plain application/javascript application/json text/css;
}
```

---

> **关联文档**：[ARCHITECTURE.md](ARCHITECTURE.md) · [AGENT-LOOP.md](AGENT-LOOP.md) · [RAG-RETRIEVAL.md](RAG-RETRIEVAL.md) · [TASK-ORCHESTRATION.md](TASK-ORCHESTRATION.md) · [DEPLOYMENT.md](DEPLOYMENT.md)