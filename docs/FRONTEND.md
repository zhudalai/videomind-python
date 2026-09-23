# VideoMind 前端工作台设计（React 实现版）

> React 18 + TypeScript + Vite + SSE 实时工作台：视频库管理、视频上传、管线进度监控、RAG 问答、Agent 分析、健康看板
> **本文档描述当前代码的实际实现。** 设计稿中规划但尚未落地的部分集中在 [§12](#12-未实装--设计稿遗留)，请勿据本文档之外的内容推断已有能力。
> 早期设计参考：Ragent `web/` + DOVideo-AI 前端 + VidLens UI 组件库

---

## 1. 技术栈与依赖

`frontend/package.json`（package name `videomind-frontend`，`"type": "module"`）：

```json
{
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.2",
    "@tanstack/react-query": "^5.56.2",
    "axios": "^1.7.7",
    "i18next": "^26.3.6",
    "react-i18next": "^17.0.11",
    "i18next-browser-languagedetector": "^8.2.1",
    "react-hook-form": "^7.53.0",
    "@hookform/resolvers": "^3.9.0",
    "zod": "^3.23.8",
    "class-variance-authority": "^0.7.0",
    "clsx": "^2.1.1",
    "tailwind-merge": "^2.5.2",
    "@radix-ui/react-scroll-area": "^1.1.0",
    "lucide-react": "^0.441.0",
    "recharts": "^2.12.7"
  },
  "devDependencies": {
    "vite": "^5.4.6",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.6.2",
    "tailwindcss": "^3.4.11",
    "tailwindcss-animate": "^1.0.7",
    "postcss": "^8.4.45",
    "autoprefixer": "^10.4.20",
    "eslint": "^9.10.0",
    "prettier": "^3.3.3",
    "vitest": "^2.1.1",
    "jsdom": "^25.0.0",
    "@testing-library/react": "^16.0.1",
    "@testing-library/jest-dom": "^6.5.0",
    "@playwright/test": "^1.47.2",
    "@tanstack/react-query-devtools": "^5.101.4"
  }
}
```

> ⚠️ `zustand`、`markdown-it`、`highlight.js`、`date-fns` 虽在 `dependencies` 中，但**源码中零引用**。详见 [§12](#12-未实装--设计稿遗留)。

---

## 2. 目录结构

`frontend/src/` 共 37 个文件。**没有** `src/store/`、`src/pages/`、`src/router/` 目录。

```
frontend/src/
├── main.tsx                     # 入口：Provider 装配
├── vite-env.d.ts                # ImportMetaEnv 类型（VITE_API_BASE）
├── app/
│   └── App.tsx                  # <Routes> 路由表（唯一路由定义处）
├── components/
│   ├── layout/
│   │   ├── index.ts             # 再导出 MainLayout as Layout
│   │   ├── MainLayout.tsx       # 应用外壳：Sidebar + Header + <Outlet/>
│   │   ├── Sidebar.tsx          # 可折叠导航栏（7 项 NavLink）
│   │   └── Header.tsx           # 顶栏：搜索框 / 主题切换 / 通知
│   └── ui/                      # UI 原语（Button/Badge/Card/Input/Label/
│                                #   Progress/Tabs/Textarea/ScrollArea/PlaceholderPage）
├── contexts/
│   └── LanguageContext.tsx      # 语言上下文（含一次性 DB 同步）
├── features/                    # 页面级组件，一目录一页面
│   ├── dashboard/Dashboard.tsx
│   ├── video-upload/VideoUploadPage.tsx
│   ├── video-library/VideoLibraryPage.tsx
│   ├── video-library/VideoDetailPage.tsx
│   ├── pipeline-monitor/PipelineProgressPage.tsx
│   ├── rag-chat/RAGChatPage.tsx
│   ├── agent-analysis/AgentAnalysisPage.tsx
│   ├── health-dashboard/HealthDashboardPage.tsx
│   └── settings/SettingsPage.tsx
├── hooks/
│   ├── useApi.ts                # QUERY_KEYS 工厂 + 20 个 hooks（⚠️ 无引用）
│   └── useSSE.ts                # usePipelineSSE / useEventSource（⚠️ 无引用）
├── i18n/
│   ├── index.ts                 # i18next 初始化
│   ├── en-US.json
│   └── zh-CN.json
├── lib/
│   ├── api.ts                   # axios 实例 + 6 个 endpoint group（实际使用）
│   ├── endpoints.ts             # ENDPOINTS 常量表（⚠️ 无引用）
│   └── utils.ts                 # cn()、格式化、阶段映射、缩略图
├── styles/
│   └── globals.css              # 设计 token + .prose + .scrollbar-hide
└── types/
    └── api.ts                   # Zod schema + z.infer 导出类型
```

**设计取向**：按**功能域**（`features/<domain>/`）而非按技术类型（views / components / stores）切分，页面内聚、彼此不共享中间组件。UI 原语集中在 `components/ui/`。

---

## 3. 路由

React Router v6 **声明式 `<Routes>`**（非 `createBrowserRouter`，无独立路由配置文件）。定义于 `frontend/src/app/App.tsx`：

```tsx
<Routes>
  <Route path="/" element={<Layout />}>            {/* MainLayout */}
    <Route index element={<Dashboard />} />
    <Route path="upload" element={<VideoUploadPage />} />
    <Route path="videos" element={<VideoLibraryPage />} />
    <Route path="videos/:id" element={<VideoDetailPage />} />
    <Route path="videos/:id/progress" element={<PipelineProgressPage />} />
    <Route path="chat" element={<RAGChatPage />} />
    <Route path="analysis" element={<AgentAnalysisPage />} />
    <Route path="health" element={<HealthDashboardPage />} />
    <Route path="settings" element={<SettingsPage />} />
  </Route>
  <Route path="*" element={<Navigate to="/" replace />} />
</Routes>
```

| 路径 | 页面 | 说明 |
|---|---|---|
| `/` | `Dashboard` | 首页：健康卡片 + 快捷入口 + 最近视频 |
| `/upload` | `VideoUploadPage` | URL / 本地文件双 Tab 上传 |
| `/videos` | `VideoLibraryPage` | 视频库网格 |
| `/videos/:id` | `VideoDetailPage` | 详情：转写 / 分段 / OCR 三 Tab |
| `/videos/:id/progress` | `PipelineProgressPage` | 管线进度（SSE） |
| `/chat` | `RAGChatPage` | RAG 问答 |
| `/analysis` | `AgentAnalysisPage` | Agent 分析工作台 |
| `/health` | `HealthDashboardPage` | 依赖组件健康看板 |
| `/settings` | `SettingsPage` | 用户配置 |

**Provider 装配顺序**（`main.tsx`）：

```
React.StrictMode
└─ QueryClientProvider        (+ ReactQueryDevtools)
   └─ I18nextProvider
      └─ LanguageProvider
         └─ BrowserRouter
            └─ App
```

---

## 4. 状态管理

### 4.1 TanStack Query 是唯一的状态层

`QueryClient` 在 `main.tsx` 内联创建：

```ts
new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,      // 5 分钟
      gcTime: 30 * 60 * 1000,        // 30 分钟
      retry: (failureCount, error) =>
        error?.status === 404 ? false : failureCount < 3,
      refetchOnWindowFocus: false,
    },
  },
})
```

- **服务端状态** → TanStack Query（查询 / 变更 / 轮询）
- **UI 局部状态** → `useState`（聊天消息、表单、筛选条件）
- **跨组件状态** → 仅 `LanguageContext` 一个 React Context
- **Zustand 未使用**：依赖存在但无 store 文件、无 import；Vite/TS 中的 `@/store` 别名指向**不存在的目录**

### 4.2 轮询策略

部分状态没有 SSE 推送，改用 `refetchInterval` 轮询：

| 场景 | 位置 | 间隔 | 条件 |
|---|---|---|---|
| Agent 任务状态 | `AgentAnalysisPage.tsx:67-75` | 2000ms | 仅 `pending`/`planning`/`executing`/`critic_check` |
| 健康检查 | `HealthDashboardPage.tsx:46-50` | 30000ms | 常驻 |
| 管线状态（REST 兜底） | `PipelineProgressPage.tsx:221` | 5000ms | 与 SSE 并行 |

---

## 5. API 层

### 5.1 axios 实例

`frontend/src/lib/api.ts:12-20`：

```ts
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})
```

`frontend/.env` 内容为 `VITE_API_BASE=/api`（无 `.env.development` / `.env.production` 变体）。开发态由 Vite proxy 转发到后端。

### 5.2 拦截器

- **请求**：若 `localStorage.access_token` 存在则注入 `Authorization: Bearer <token>`
- **响应**：**解包 `response.data`**，调用方直接拿到 payload
- **错误**：统一规范为 `Error & { status?, detail? }`，`detail` 取自 FastAPI 的 `data.detail`
- **401**：清除本地 token

### 5.3 Endpoint 分组

全部相对于 `/api`。与后端路由（`src/videomind/interface/routes/`）逐一对齐：

| 分组 | 方法 + 路径 | 对应后端 |
|---|---|---|
| `pipelineApi` | `POST /videos/pipeline` | `video.py:236` 提交 URL 管线 |
| | `POST /videos/upload` | `video.py:88` multipart 上传（带 `onUploadProgress`） |
| | `GET /videos/pipeline/{mediaId}` | `video.py:265` 管线状态 |
| `videoApi` | `GET /videos` | `video.py:441` 列表（page/page_size/search/status） |
| | `GET \| DELETE /videos/{id}` | `video.py:499` / `:559` |
| | `GET /videos/{id}/transcription` | `video.py:580` |
| | `GET /videos/{id}/segments` | `video.py:601` |
| | `GET /videos/{id}/ocr` | `video.py:639` |
| `analysisApi` | `POST /agent/analyze` | `agent.py:105`（202） |
| | `GET /agent/tasks/{taskId}` | `agent.py:201` |
| | `GET /agent/tasks/{taskId}/result` | `agent.py:220` |
| | `GET /agent/tasks/{taskId}/checkpoints` | `agent.py:237` |
| `ragApi` | `POST /rag/search` | `rag.py:64` |
| | `POST /rag/chat` | `rag.py:109` → `{answer, evidence[], session_id}` |
| `healthApi` | `GET /health` / `GET /health/ready` | `health.py:14` / `:20` |
| `userApi` | `GET \| PUT /user/config` | `user.py:92` / `:104` |

> 早期设计稿使用 `/api/v1/...` 前缀，**实际后端为 `/api`**（`interface/__init__.py:72-77`）。

---

## 6. 实时进度（SSE）

### 6.1 机制：原生 `EventSource`

仅用于**管线进度**。全前端无 `fetch + ReadableStream`，也**没有 token 级的答案流式输出**。

实现位于 `frontend/src/features/pipeline-monitor/PipelineProgressPage.tsx:139-199`：

```ts
const apiBase = import.meta.env.VITE_API_BASE || '/api'
const es = new EventSource(`${apiBase}/videos/pipeline/${mediaId}/progress`)

// 监听具名事件 'progress'（非 onmessage）
es.addEventListener('progress', (event) => {
  const progressEvent: ProgressEvent = JSON.parse(event.data)
  setSseEvents(prev => [...prev, progressEvent])
  if (progressEvent.stage === 'completed' || progressEvent.progress_pct >= 100) es.close()
  else if (progressEvent.stage === 'failed' || progressEvent.progress_pct < 0) es.close()
})

es.onerror = () => { /* close + 最多重试 5 次，退避 3000*(n+1) ms */ }
```

对应后端 `sse.py:22` 的 `GET /api/videos/pipeline/{media_id}/progress`。

### 6.2 双通道设计

SSE 提供**实时增量事件**，同时以 5 秒间隔 REST 轮询 `pipelineApi.getStatus` 作为**兜底**——SSE 断线或事件丢失时，页面仍能从权威状态恢复。二者在 UI 上合并渲染为 9 阶段竖向 stepper + 总体进度条 + 实时事件日志。

`ProgressEvent` 结构（`types/api.ts`）：

```ts
{ stage: string; progress_pct: number; message: string; timestamp: string; metadata?: object }
```

---

## 7. 主要画面

| 页面 | 关键实现 |
|---|---|
| **Dashboard** | 健康卡片（`/health/ready`，30s 轮询）+ 4 个快捷入口 + 最近 `ready` 视频网格（可删除） |
| **VideoUploadPage** | 双 Tab：URL / 本地文件。`react-hook-form` + `zodResolver`，本地 zod schema 校验（URL 格式；文件 ≤2GB 且 `video/*`）。URL 走 `pipelineApi.submit`，文件走 `pipelineApi.uploadFile`，成功后跳 `/videos/{id}/progress` |
| **VideoLibraryPage** | 视频卡片网格 + 搜索 + 状态筛选 + 分页，**筛选条件同步到 URL search params**（可分享/回退）；缩略图走 YouTube CDN（`getVideoThumbnailUrl`）；删除带确认与错误横幅 |
| **VideoDetailPage** | 阶段进度列表；`ready` 后展示 3 个 Tab：转写全文 / 分段（`useInfiniteQuery`，可展开）/ OCR 帧（`useInfiniteQuery`） |
| **PipelineProgressPage** | 9 阶段 stepper（REST 状态 + SSE 事件合并）、总体进度、实时事件日志、断线重连（最多 5 次）、完成后跳详情 |
| **RAGChatPage** | 乐观更新的用户气泡 → 助手气泡内嵌**可折叠证据卡片**（相关度百分比、时间区间 `start_ms-end_ms`、`evidence_id` 徽章、内容预览，定义于 `RAGChatPage.tsx:393-422`）；媒体范围选择器 + 推荐问题。**单次请求/响应，无流式** |
| **AgentAnalysisPage** | 目标输入 + 最多 4 个 `ready` 视频选择 + `max_rounds`（1–3）；提交后 5 步状态流（`pending→planning→executing→critic_check→completed`）2 秒轮询；执行轨迹卡片（来自 checkpoints）；最终结果卡片（critic 通过/未通过徽章、成本、结论含置信度、证据列表、建议） |
| **HealthDashboardPage** | PostgreSQL / Redis / Qdrant / MinIO 四张组件卡；30s 轮询；**recharts 折线图**展示可用性历史（客户端维护，上限 30 点）；手动刷新 + 异常横幅 |
| **SettingsPage** | 开发用身份卡片、主题三态切换、默认模型、语言选择（乐观更新 + 失败回滚） |

---

## 8. UI 组件、样式与 i18n

### 8.1 UI 原语（`components/ui/`）

`Button`（cva 变体 default/destructive/outline/secondary/ghost/link，Radix `Slot` 支持 `asChild`，内建 loading spinner）、`Badge`（含 success/warning/info）、`Card`（Card/Header/Title/Description/Content/Footer）、`Input`、`Label`、`Textarea`、`Progress`（纯 div 进度条）、`Tabs`（**手写**，非 Radix）。

### 8.2 样式

- `tailwind.config.ts`：`darkMode: ['class']`，shadcn 风格 **HSL CSS 变量**映射（`hsl(var(--primary))` 等），插件 `tailwindcss-animate`
- `styles/globals.css`：`:root`（亮色）与 `.dark` 两套 token（主色 `221.2 83.2% 53.3%`）、`.prose` 组件层、`.scrollbar-hide` 工具类
- PostCSS：`tailwindcss` + `autoprefixer`
- Radix 仅直接依赖 `@radix-ui/react-scroll-area`（且只在未使用的 `ScrollArea.tsx` 中引用）；`Button` 用到的 `@radix-ui/react-slot` 为传递依赖
- 图标 `lucide-react`；图表 `recharts`（仅健康看板）

### 8.3 i18n

`i18n/index.ts`：`i18next` + `initReactI18next` + `i18next-browser-languagedetector`。

- 语言：**`en-US`（默认、fallback）与 `zh-CN`**，资源为静态导入的 JSON（各 337 行，15 个命名空间：`nav, app, header, videoLibrary, videoCard, dashboard, settings, ragChat, agentAnalysis, pipeline, health, videoUpload, videoDetail, placeholder, utils`）
- 检测顺序 `['localStorage', 'navigator']`，缓存键 `i18next_lng`
- `LanguageContext` 提供 `{currentLanguage, setLanguage}`，并用 `hasSyncedRef` 守卫**一次性**从 DB（`GET /user/config`）同步语言
- 阶段/状态标签以 i18n **key** 形式存于 `lib/utils.ts:91-102` 的 `STAGE_LABELS`，随语言响应式切换

---

## 9. 类型定义

全部 DTO 集中在 `frontend/src/types/api.ts`（296 行），以 **Zod schema + `z.infer`** 书写。

> ⚠️ **这些 schema 只作为类型推导来源，运行时从不执行校验**——`api.ts` 以 `import type` 引入，源码中无任何 `.parse(` 调用。唯一运行时使用 Zod 的地方是 `VideoUploadPage.tsx:19-31` 的表单局部 schema。

关键类型：`MediaStatus`（`pending / downloading / downloaded / transcoding / transcoded / asr / ocr / indexing / ready / failed`）、`AnalysisStatus`（`pending / planning / executing / critic_check / completed / failed`）、`ProgressEvent`、`MediaFileResponse`、`VideoSegmentResponse`、`OCRResultResponse`、`AnalysisTaskRequest`（`goal` ≤5000、`media_ids` 1–4、`max_rounds` 1–3）、`AgentResultResponse`（`conclusions_json` / `evidence_json` / `suggestions_json` / `critic_passed` / `critic_feedback` / `token_usage` / `cost_usd`）、`HealthReadyResponse`、`RagChatResponse`、`UserConfig`。

---

## 10. 测试

| 层 | 工具 | 状态 |
|---|---|---|
| **L1 冒烟** | Playwright `tests/e2e/smoke.spec.ts` | 5 个页面外壳渲染 + 真实请求 `/api/health/ready`、`/api/user/config`（后端离线时跳过） |
| **L2 契约** | Playwright `tests/e2e/api-contract.spec.ts` | 10 个 endpoint 的响应结构固定（直连后端 `http://localhost:8002`），含 404/400 错误路径 |
| **L3 全链路** | Playwright `tests/e2e/full-chain.spec.ts` | 由 `VM_E2E_FULL=1` + 就绪媒体门控：上传→SSE→详情→Agent 分析→轮询→结果→RAG |
| 单元测试 | Vitest + Testing Library | **已配置但零测试文件**（`vitest.config.ts` include `src/**/*.test.{ts,tsx}`，实际无匹配） |

`playwright.config.ts`：仅 chromium，`baseURL http://localhost:4000`，`webServer: npm run dev`（非 CI 下复用已启动服务），超时 60s。

---

## 11. 开发与构建

```bash
cd frontend
npm install
npm run dev        # Vite dev server → http://127.0.0.1:4000（strictPort）
npm run build      # tsc -b && vite build
npm run typecheck  # tsc --noEmit
npm run lint       # eslint --max-warnings 0
npm run test:e2e   # playwright test
```

### ⚠️ `vite.config.js` 与 `vite.config.ts` 并存

Vite 5 按 `DEFAULT_CONFIG_FILES` 顺序解析配置，**`vite.config.js` 排在 `.ts` 之前**，因此**实际生效的是 `vite.config.js`**（`vite.config.ts` 被完全忽略）。

两者仅有一处实质差异——**proxy 目标**：

| 文件 | 生效 | proxy `/api`、`/sse` 目标 |
|---|---|---|
| `vite.config.js` | ✅ **实际生效** | `http://127.0.0.1:8011` |
| `vite.config.ts` | ❌ 被忽略 | `http://127.0.0.1:8002` |

**后果**：本地联调时后端必须监听 **8011**；而 `playwright.config.ts` 与 `tests/e2e/lib.ts` 假设后端在 **8002**——两处端口不一致，是当前待清理的技术债。

其余生效配置：端口 `4000`、`strictPort: true`、host `127.0.0.1`、插件 `@vitejs/plugin-react`、别名 `@ → src`（含 `@/components`、`@/features`、`@/hooks`、`@/lib`、`@/store`、`@/types`，其中 `@/store` 指向不存在的目录）。

---

## 12. 未实装 / 设计稿遗留

以下内容出现在早期 Vue 设计稿中，**当前代码并未实现**。列出以免误导：

| 设计稿内容 | 实际状况 |
|---|---|
| 流式 Markdown 渲染器（`StreamingMarkdown`，含 KaTeX / Mermaid / 代码高亮 / 光标动画） | **未实现**。`markdown-it`、`highlight.js` 在 `dependencies` 中但**源码零引用**；助手消息以 `<p className="whitespace-pre-wrap">{content}</p>` 纯文本渲染（`RAGChatPage.tsx:346`） |
| Zustand store（`useVideoLibraryStore` / `useTaskMonitorStore`） | **未实现**。无 store 文件、无 import；状态由 TanStack Query + 局部 `useState` 承担 |
| 全局键盘快捷键（`useKeyboardShortcuts`，含 `g l` / `g a` / `Cmd+K` 命令面板） | **未实现** |
| SSE 任务监控 Store（`EventSource` Map + `mitt` 事件总线 + 自动重连） | **未实现**。实际为页面内联 `EventSource`，具名 `progress` 事件 + REST 轮询兜底 |
| RAG 答案 token 级流式输出 | **未实现**。`POST /rag/chat` 单次请求/响应，整段返回后渲染 |
| `ScrollArea`（Radix）、`PlaceholderPage` | 组件已写但**无任何引用** |
| `hooks/useApi.ts`（QUERY_KEYS + 20 hooks）、`hooks/useSSE.ts`、`lib/endpoints.ts` | 均已实现但**无任何引用**——页面各自内联 `useQuery`/`useMutation` + 字符串字面量 key |
| 独立 `EvidenceCard.vue` 组件 | 实际为 `RAGChatPage.tsx:393-422` 内联定义 |
| `/api/v1/...` 路径前缀 | 实际为 `/api` |
| nginx + Docker 前端镜像 | 仓库中**不存在** `docker/frontend.Dockerfile` / `nginx.conf`；前端目前仅本地 `npm run dev` 运行 |

---

## 13. 已知不整合

| # | 问题 | 影响 |
|---|---|---|
| 1 | `vite.config.js` / `vite.config.ts` 并存，`.js` 生效 | 改 `.ts` 无任何效果；proxy 端口与 Playwright 假设不一致（见 §11） |
| 2 | `@/store` 别名指向不存在的目录 | 引用该别名会构建失败 |
| 3 | 4 个依赖（zustand / markdown-it / highlight.js / date-fns）+ 5 个文件为死代码 | 包体积与维护噪声 |
| 4 | Vitest 已配置但零测试文件 | `npm test` 无实际校验能力 |
| 5 | 无登录流程：仅有 token 注入拦截器，身份来自 `GET /user/config` 的开发用户 | 无法验证鉴权路径 |
| 6 | `frontend/test-upload.cjs`、`test-upload.spec.ts` 为残留脚本，指向已废弃端口 | 易误用 |
| 7 | `frontend.log`、`dist/`、`tsconfig*.tsbuildinfo` 为构建/运行残留 | 建议加入 `.gitignore` |

---

## 14. 参考

- 后端接口契约：[ARCHITECTURE.md](ARCHITECTURE.md)、[TASK-ORCHESTRATION.md](TASK-ORCHESTRATION.md)
- 设计↔实现的整体偏离记录：[DECISIONS.md](DECISIONS.md)
- RAG 证据数据结构：[RAG-RETRIEVAL.md](RAG-RETRIEVAL.md)
- Agent 结果结构：[AGENT-LOOP.md](AGENT-LOOP.md)
