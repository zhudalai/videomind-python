# Bilingual i18n Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 319 hardcoded Chinese strings in the React frontend with react-i18next `t()` calls, loading translations from `zh-CN.json` and `en-US.json`, with language driven by the existing DB `UserConfig.language` field (default en-US on first visit).

**Architecture:** react-i18next + JSON locale files + LanguageContext that watches the TanStack Query user config and calls `i18n.changeLanguage()`.

**Tech Stack:** i18next, react-i18next, i18next-browser-languagedetector

## Global Constraints

- No backend API changes — language field already exists in `UserConfig` model (`String(16)`, default `"zh-CN"`, stored via `UserConfigUpdate` with `language: str | None = Field(None)`)
- SettingsPage language dropdown already saves to DB — no change needed there
- First-visit default: **en-US** (per decision in spec)
- Fallback language: **en-US** (never show blank UI)
- All 17 files with hardcoded Chinese must be updated consistently
- Translation keys use dotted camelCase paths grouped by feature area

---

## File Map

### New files
| File | Purpose |
|------|---------|
| `frontend/src/i18n/index.ts` | i18n init (i18next + browser-languagedetector + react-i18next) |
| `frontend/src/i18n/zh-CN.json` | Chinese translations |
| `frontend/src/i18n/en-US.json` | English translations |
| `frontend/src/contexts/LanguageContext.tsx` | Watches DB `userConfig.language` → calls `i18n.changeLanguage()` |

### Modified files (create translations + replace `t()`)
| File | Purpose |
|------|---------|
| `frontend/src/main.tsx` | Wrap `<App />` with `I18nextProvider` and `<LanguageContextProvider>` |
| `frontend/src/App.tsx` | Any hardcoded nav strings |
| `frontend/src/components/layout/Sidebar.tsx` | Nav items |
| `frontend/src/components/ui/Button.tsx` | Button label if any |
| `frontend/src/components/ui/PlaceholderPage.tsx` | Placeholder text |
| `frontend/src/features/settings/SettingsPage.tsx` | Translate UI labels (keep `'zh-CN'`/`'en-US'` option values) |
| `frontend/src/features/dashboard/Dashboard.tsx` | Quick action labels, status text |
| `frontend/src/features/rag-chat/RAGChatPage.tsx` | Chat placeholder, suggested prompts |
| `frontend/src/features/video-library/VideoLibraryPage.tsx` | Search, filter, table headers |
| `frontend/src/features/video-library/VideoDetailPage.tsx` | Detail labels |
| `frontend/src/features/agent-analysis/AgentAnalysisPage.tsx` | Analysis UI strings |
| `frontend/src/features/pipeline-monitor/PipelineProgressPage.tsx` | Stage labels, status text |
| `frontend/src/features/health-dashboard/HealthDashboardPage.tsx` | Health status text |
| `frontend/src/features/video-upload/VideoUploadPage.tsx` | Upload UI strings |
| `frontend/src/lib/utils.ts` | `formatRelativeTime` return strings + `STAGE_LABELS` |
| `frontend/src/lib/api.ts` | Comments only |
| `frontend/src/hooks/useSSE.ts` | Comments only |
| `frontend/src/types/api.ts` | Comments only |

---

### Task 1: Install i18n dependencies

**Files:** `frontend/package.json` (modified by npm install)

- [ ] **Step 1: Install dependencies**
  Run: `cd frontend && npm install i18next react-i18next i18next-browser-languagedetector`
  Expected: `i18next`, `react-i18next`, `i18next-browser-languagedetector` added to `package.json` `dependencies`

- [ ] **Step 2: Verify install**
  Run: `cd frontend && npm ls i18next react-i18next i18next-browser-languagedetector`
  Expected: All three listed with versions, no warnings

- [ ] **Step 3: Commit**
  ```bash
  cd "d:/shu_e/Documents/Video MInd python"
  git add frontend/package.json frontend/package-lock.json
  git commit -m "chore: add i18next react-i18next i18next-browser-languagedetector"
  ```

---

### Task 2: Create i18n directory and init file

**Files:** Create `frontend/src/i18n/index.ts`

- [ ] **Step 1: Create `frontend/src/i18n/index.ts`**
  Content: i18n instance init with `react-i18next` plugin, `i18next-browser-languagedetector` plugin, fallbackLng `'en-US'`, resources loaded statically from `./zh-CN.json` and `./en-US.json`, `interpolation: { escapeValue: false }` (React already escapes), `returnEmptyString: false`. Export `i18n` instance, `t` hook, and `useTranslation` from `react-i18next`.

- [ ] **Step 2: Create empty stub locales**
  Create `frontend/src/i18n/zh-CN.json` with `{}`
  Create `frontend/src/i18n/en-US.json` with `{}`

- [ ] **Step 3: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
  Expected: No errors (empty stubs + proper init)

- [ ] **Step 4: Commit**
  ```bash
  git add frontend/src/i18n/
  git commit -m "feat(i18n): create i18n init file and empty locale stubs"
  ```

---

### Task 3: Create LanguageContext

**Files:** Create `frontend/src/contexts/LanguageContext.tsx`

- [ ] **Step 1: Create `frontend/src/contexts/LanguageContext.tsx`**
  - Uses `useQuery` to fetch `userApi.getConfig()` (TanStack Query, same as SettingsPage)
  - On successful config load, calls `i18n.changeLanguage(config.language)` to sync DB preference to i18n
  - Exposes `contextValue` with `currentLanguage: string` and `setLanguage: (lang: string) => void` (setLanguage only calls i18n.changeLanguage, does NOT save to DB — DB save is handled by SettingsPage)
  - Wraps with `React.createContext`
  - Provider component `LanguageProvider` that accepts `children`

- [ ] **Step 2: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
  Expected: No errors

- [ ] **Step 3: Commit**
  ```bash
  git add frontend/src/contexts/LanguageContext.tsx
  git commit -m "feat(i18n): create LanguageContext to sync DB language to i18n"
  ```

---

### Task 4: Wrap App with i18n + LanguageProvider in main.tsx

**Files:** `frontend/src/main.tsx` (modified)

- [ ] **Step 1: Modify `frontend/src/main.tsx`**
  - Import `I18nextProvider` from `react-i18next`
  - Import `i18n` from `@/i18n`
  - Import `LanguageProvider` from `@/contexts/LanguageContext`
  - Wrap `<App />` with `<I18nextProvider i18n={i18n}><LanguageProvider><App /></LanguageProvider></I18nextProvider>`

- [ ] **Step 2: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
  Expected: No errors

- [ ] **Step 3: Commit**
  ```bash
  git add frontend/src/main.tsx
  git commit -m "feat(i18n): wrap App with I18nextProvider and LanguageProvider"
  ```

---

### Task 5: Populate en-US.json (English translations)

**Files:** Create/modify `frontend/src/i18n/en-US.json`

- [ ] **Step 1: Extract all English UI strings**
  Go file by file (see File Map above) and extract every user-facing English string. Write them into `en-US.json` with dotted camelCase keys. Group by feature area:
  - `nav.dashboard`, `nav.upload`, `nav.videos`, `nav.chat`, `nav.analysis`, `nav.health`, `nav.settings`
  - `settings.theme`, `settings.theme.light`, `settings.theme.dark`, `settings.theme.system`
  - `settings.language`, `settings.language.zhCN`, `settings.language.enUS`, `settings.defaultModel`, `settings.save`, `settings.saving`, `settings.saved`
  - `videoLibrary.searchPlaceholder`, `videoLibrary.allStatus`, `videoLibrary.noVideos`, `videoLibrary.tryFilters`, `videoLibrary.uploadFirst`, `videoLibrary.details`, `videoLibrary.progress`, `videoLibrary.more`, `videoLibrary.viewDetails`, `videoLibrary.delete`
  - `videoCard.duration`, `videoCard.size`, `videoCard.source`, `videoCard.error`, `videoCard.status.ready`, `videoCard.status.transcoding`, `videoCard.status.downloading`, `videoCard.status.asr`, `videoCard.status.ocr`, `videoCard.status.indexing`, `videoCard.status.failed`
  - `pipeline.stage.claimed`, `pipeline.stage.downloading`, `pipeline.stage.downloaded`, `pipeline.stage.transcoding`, `pipeline.stage.transcoded`, `pipeline.stage.asr`, `pipeline.stage.ocr`, `pipeline.stage.indexing`, `pipeline.stage.completed`, `pipeline.stage.failed`
  - `dashboard.quickActions.upload`, `dashboard.quickActions.videos`, `dashboard.quickActions.chat`, `dashboard.quickActions.analysis`
  - `dashboard.systemHealthy`, `dashboard.systemWarning`, `dashboard.healthStatus`
  - (continue for all 17 files — all user-facing strings extracted into keys)

- [ ] **Step 2: Write en-US.json**
  All strings mapped to their English equivalents. Keys follow convention: `featureArea.descriptiveName`.

- [ ] **Step 3: Typecheck i18n init loads en-US**
  Run: `cd frontend && npx tsc --noEmit`
  Expected: No errors

- [ ] **Step 4: Commit**
  ```bash
  git add frontend/src/i18n/en-US.json
  git commit -m "feat(i18n): add en-US translations"
  ```

---

### Task 6: Populate zh-CN.json (Chinese translations)

**Files:** Create/modify `frontend/src/i18n/zh-CN.json`

- [ ] **Step 1: Write zh-CN.json**
  Same key structure as `en-US.json`, all values translated to Simplified Chinese. Keys like `nav.dashboard` map to `仪表盘`, `videoCard.duration` to `时长`, etc.

- [ ] **Step 2: Typecheck i18n init loads zh-CN**
  Run: `cd frontend && npx tsc --noEmit`
  Expected: No errors
  (Note: tsc won't validate JSON content, just that files parse)

- [ ] **Step 3: Commit**
  ```bash
  git add frontend/src/i18n/zh-CN.json
  git commit -m "feat(i18n): add zh-CN translations"
  ```

---

### Task 7: Replace hardcoded Chinese strings in utils.ts

**Files:** `frontend/src/lib/utils.ts` (modified)

- [ ] **Step 1: Import `useTranslation` and `t` from `@/i18n` in utils.ts**
  Add `import { useTranslation } from '@/i18n'` at top of file.
  Since `utils.ts` has exported plain functions (not React components), add a `t` argument to each function that needs it, or use `i18n.t()` directly (import i18n instance).
  For `formatRelativeTime`: import `i18n` from `@/i18n`, use `i18n.t('utils.justNow')`, `i18n.t('utils.minutesAgo', { count: diffMins })`, etc.
  For `STAGE_LABELS`: replace Chinese values with `i18n.t('pipeline.stage.xxx')` calls or change to a function returning `t()` values.

- [ ] **Step 2: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
  Expected: No errors

- [ ] **Step 3: Commit**
  ```bash
  git add frontend/src/lib/utils.ts
  git commit -m "feat(i18n): replace utils.ts hardcoded Chinese with t() calls"
  ```

---

### Task 8: Replace hardcoded Chinese strings in Sidebar.tsx

**Files:** `frontend/src/components/layout/Sidebar.tsx` (modified)

- [ ] **Step 1: Import `useTranslation` from `@/i18n`**
- [ ] **Step 2: Replace all Chinese nav strings with `t()` calls**
  `name: '概览'` → `name: t('nav.dashboard')`, `name: '上传视频'` → `name: t('nav.upload')`, etc.
  Replace `"展开侧边栏"`, `"折叠侧边栏"`, `"主导航"`, `"系统运行正常"` with translations.
- [ ] **Step 3: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 4: Commit**

---

### Task 9: Replace hardcoded Chinese strings in SettingsPage.tsx

**Files:** `frontend/src/features/settings/SettingsPage.tsx` (modified)

- [ ] **Step 1: Import `useTranslation` from `@/i18n`**
- [ ] **Step 2: Replace all user-facing Chinese strings with `t()` calls**
  Keep option values `'zh-CN'`/`'en-US'` unchanged (they are machine values).
  Translate displayed labels: `"语言"` → `t('settings.language')`, `"简体中文"` → `t('settings.language.zhCN')`, `"English"` → `t('settings.language.enUS')`, `"主题"` → `t('settings.theme')`, `"浅色"` → etc., `"深色"`, `"跟随系统"`.
  Translate: `"偏好配置"`, `"主题、默认模型、语言。保存后立即生效。"`, `"当前身份"`, `"Dev 用户 ID："`, `"保存中"`, `"保存"`, `"保存失败"`, `"已保存"`.
  Keep option values as machine-readable codes (`'light'`, `'dark'`, `'system'`) — only translate the displayed labels.
- [ ] **Step 3: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 4: Commit**

---

### Task 10: Replace hardcoded Chinese strings in Dashboard.tsx

**Files:** `frontend/src/features/dashboard/Dashboard.tsx` (modified)

- [ ] **Step 1: Import `useTranslation` from `@/i18n`**
- [ ] **Step 2: Replace all user-facing Chinese strings with `t()` calls**
  Quick action labels (`'上传视频'`, `'视频库'`, `'智能问答'`, `'深度分析'`), status text (`'全部正常'`, `'部分异常'`), `"快速操作"`, `"最近视频"`, `"暂无视频"`, etc.
- [ ] **Step 3: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 4: Commit**

---

### Task 11: Replace hardcoded Chinese strings in RAGChatPage.tsx

**Files:** `frontend/src/features/rag-chat/RAGChatPage.tsx` (modified)

- [ ] **Step 1: Import `useTranslation` from `@/i18n`**
- [ ] **Step 2: Replace all user-facing Chinese strings with `t()` calls**
  Chat placeholder text, suggested prompts (`'这个视频讲了什么？'`, `'总结视频的核心观点'`, etc.), `"收起证据"`, `"查看 X 条证据"`, `"已复制"`, `"复制"`, `"X% 相关度"`, `"按 Enter 发送，Shift+Enter 换行"`.
- [ ] **Step 3: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 4: Commit**

---

### Task 12: Replace hardcoded Chinese strings in VideoLibraryPage.tsx

**Files:** `frontend/src/features/video-library/VideoLibraryPage.tsx` (modified)

- [ ] **Step 1: Import `useTranslation` from `@/i18n`**
- [ ] **Step 2: Replace all user-facing Chinese strings with `t()` calls**
  `"视频库"`, `"管理和浏览所有已处理的视频"`, `"搜索文件名、来源 URL..."`, `"全部状态"`, `"查看详情"`, `"删除"`, `"处理进度"`, `"确定要删除这个未完成的视频吗？"`, `"确定要删除这个视频吗？此操作不可恢复。"`, `"知道了"`, `"暂无视频"`, `"请调整搜索条件或筛选器"`, `"上传第一个视频开始体验"`, `"上传新视频"`, `"第 X 页 / 共 Y 页 (Z 条)"`, pagination labels.
- [ ] **Step 3: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 4: Commit**

---

### Task 13: Replace hardcoded Chinese strings in VideoDetailPage.tsx

**Files:** `frontend/src/features/video-library/VideoDetailPage.tsx` (modified)

- [ ] **Step 1: Import `useTranslation` from `@/i18n`**
- [ ] **Step 2: Replace all user-facing Chinese strings with `t()` calls**
  Detail page labels, stage status labels, error messages displayed to user.
- [ ] **Step 3: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 4: Commit**

---

### Task 14: Replace hardcoded Chinese strings in AgentAnalysisPage.tsx

**Files:** `frontend/src/features/agent-analysis/AgentAnalysisPage.tsx` (modified)

- [ ] **Step 1: Import `useTranslation` from `@/i18n`**
- [ ] **Step 2: Replace all user-facing Chinese strings with `t()` calls**
  Analysis result UI labels, agent step descriptions, evidence card strings.
- [ ] **Step 3: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 4: Commit**

---

### Task 15: Replace hardcoded Chinese strings in PipelineProgressPage.tsx

**Files:** `frontend/src/features/pipeline-monitor/PipelineProgressPage.tsx` (modified)

- [ ] **Step 1: Import `useTranslation` from `@/i18n`**
- [ ] **Step 2: Replace all user-facing Chinese strings with `t()` calls**
  `"处理进度"`, `"实时监控视频处理管线"`, stage names, connection status strings, event log empty state.
- [ ] **Step 3: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 4: Commit**

---

### Task 16: Replace hardcoded Chinese strings in HealthDashboardPage.tsx

**Files:** `frontend/src/features/health-dashboard/HealthDashboardPage.tsx` (modified)

- [ ] **Step 1: Import `useTranslation` from `@/i18n`**
- [ ] **Step 2: Replace all user-facing Chinese strings with `t()` calls**
  `"系统健康监控"`, `"基础设施组件连通性与可用性"`, `"手动刷新"`, status strings.
- [ ] **Step 3: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 4: Commit**

---

### Task 17: Replace hardcoded Chinese strings in VideoUploadPage.tsx

**Files:** `frontend/src/features/video-upload/VideoUploadPage.tsx` (modified)

- [ ] **Step 1: Import `useTranslation` from `@/i18n`**
- [ ] **Step 2: Replace all user-facing Chinese strings with `t()` calls**
  Upload UI strings, platform support list, validation error messages (`"请输入有效的 URL"`, `"URL 太短"`, etc.), `"已选择文件"`, `"点击或拖拽上传视频文件"`, `"上传并处理"`.
- [ ] **Step 3: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 4: Commit**

---

### Task 18: Replace hardcoded Chinese in remaining UI component files

**Files:** `frontend/src/components/ui/Button.tsx`, `frontend/src/components/ui/PlaceholderPage.tsx` (modified)

- [ ] **Step 1: Import `useTranslation` from `@/i18n` in any component with Chinese strings**
- [ ] **Step 2: Replace with `t()` calls**
  Check `Button.tsx` — usually no Chinese strings in Button component itself (it's a generic wrapper), but verify. `PlaceholderPage.tsx` may have Chinese text in its placeholder.
- [ ] **Step 3: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 4: Commit**

---

### Task 19: Replace hardcoded Chinese in comment-only files

**Files:** `frontend/src/lib/api.ts`, `frontend/src/hooks/useSSE.ts`, `frontend/src/types/api.ts` (modified)

- [ ] **Step 1: Replace Chinese comments with English**
  These files have Chinese comments — translate them to English for consistency since the UI is now bilingual and dev-facing comments should match the codebase default language.
- [ ] **Step 2: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 3: Commit**

---

### Task 20: Wire up SettingsPage language change to trigger i18n switch

**Files:** `frontend/src/features/settings/SettingsPage.tsx` (modified)

- [ ] **Step 1: Import `useTranslation` and `i18n` from `@/i18n`**
- [ ] **Step 2: In SettingsPage `handleSave` success callback, call `i18n.changeLanguage(language)`**
  This ensures when user changes language in settings and saves, the UI switches immediately without needing a page reload or full DB refetch cycle.
  Add: `onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['user', 'config'] }); setDirty(false); i18n.changeLanguage(language); }`
- [ ] **Step 3: Optionally also listen for `language` state change reactively in a `useEffect`**
  Add a `useEffect` that watches `language` state and calls `i18n.changeLanguage(language)` whenever it changes (even before save — gives instant preview).
- [ ] **Step 4: Typecheck**
  Run: `cd frontend && npx tsc --noEmit`
- [ ] **Step 5: Commit**

---

### Task 21: End-to-end verification

**Files:** No new files; verification only.

- [ ] **Step 1: Start dev server**
  Run: `cd frontend && npm run dev`
  Expected: Vite starts on port 3000, no compile errors

- [ ] **Step 2: Open page and verify default language is English**
  Open `http://localhost:3000/videos` in browser
  Expected: All UI text is in English (nav items, buttons, table headers, placeholder text)

- [ ] **Step 3: Switch to Chinese in Settings**
  Navigate to `/settings`, change language to "简体中文", save
  Expected: UI immediately switches to Chinese across all pages

- [ ] **Step 4: Switch back to English**
  Change to "English", save
  Expected: UI immediately switches back to English

- [ ] **Step 5: Verify no blank/missing translations**
  Check all 17 modified pages for any untranslated keys falling back to key name (e.g., showing `nav.dashboard` instead of `Dashboard`)
  If any key shows raw, add missing translation to both JSON files

- [ ] **Step 6: Run typecheck clean**
  Run: `cd frontend && npx tsc --noEmit`
  Expected: 0 errors

- [ ] **Step 7: Run existing tests**
  Run: `cd frontend && npm test` (or vitest run)
  Expected: All existing tests pass (no regressions)

- [ ] **Step 8: Commit**
  ```bash
  git add -A && git commit -m "feat(i18n): full bilingual zh-CN/en-US support"
  ```

---

### Task 22: Push to GitHub

- [ ] **Step 1: Push**
  ```bash
  git push origin main
  ```
  Expected: Push succeeds (no conflicts)

- [ ] **Step 2: Verify**
  Check GitHub repo → commits → confirm i18n commits appear at head of main
