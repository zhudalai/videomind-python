# Bilingual i18n Design — VideoMind Frontend

## Date
2026-07-31

## Decision
Add react-i18next bilingual (zh-CN / en-US) support so the UI language is driven by the existing `UserConfig.language` field in the database.

## Background
- Frontend has 319 hardcoded Chinese strings across 17 files — all visible to users
- Backend `UserConfig` already has a `language: str` field (default `"zh-CN"`) persisted to DB
- `SettingsPage` already has a `zh-CN` / `en-US` dropdown that saves to DB — but switching does nothing to the UI
- No i18n library, no locale files, no translation infrastructure exists

## Requirements
1. First visit defaults to **en-US** (per user decision)
2. Returning users see the language their DB config specifies
3. Language switch (via SettingsPage dropdown) immediately re-renders the entire UI in the selected language
4. No blank/untranslated UI — i18next fallback chain ensures en-US is shown if any key is missing in zh-CN (and vice versa)
5. Language preference persists across sessions (DB + localStorage fallback)
6. No backend API changes required

## Architecture

### Libraries
- `i18next` — core i18n engine
- `react-i18next` — React bindings (`useTranslation` hook, `Trans` component)
- `i18next-browser-languagedetector` — detects browser language on first visit

### New files
```
frontend/src/i18n/
  index.ts           # i18n init, exports t() and useTranslation
  zh-CN.json          # Chinese translations (~30KB)
  en-US.json          # English translations (~35KB)
frontend/src/contexts/
  LanguageContext.tsx # Watches DB language → calls i18n.changeLanguage()
```

### Key implementation points

| Concern | Decision |
|---------|----------|
| i18n init | `i18n.init({ lng: 'en-US', fallbackLng: 'en-US', ... })` in `i18n/index.ts` |
| Plugin chain | `i18next-browser-languagedetector` (detects browser pref, first visit only) + `react-i18next` |
| Backend sync | `LanguageContext` reads `userConfig.language` via TanStack Query and calls `i18n.changeLanguage()` |
| Fallback | `fallbackLng: 'en-US'` so if any zh-CN key is missing or loading, English is shown — never blank |
| localStorage | `i18next-browser-languagedetector` caches first detected lang in `i18next_lng` |
| SettingsPage | Save to DB works already; `LanguageContext` picks up the change on next query refetch |

### Translation JSON structure
Keys are dotted paths grouped by feature area:
```json
{
  "nav": {
    "dashboard": "仪表盘",
    "videos": "视频库",
    ...
  },
  "video_card": {
    "duration": "时长",
    "size": "大小",
    "downloading": "下载中",
    ...
  },
  ...
}
```

### Files modified
All below will have hardcoded Chinese strings replaced with `t('key.path')` calls:
1. `app/App.tsx` — title?
2. `components/layout/Sidebar.tsx` — nav items
3. `components/ui/Button.tsx`, `PlaceholderPage.tsx`
4. `features/settings/SettingsPage.tsx` — labels, descriptions (keep values like `'zh-CN'`/`'en-US'` as values; translate displayed labels like "简体中文" → "Simplified Chinese", "English" → "English")
5. `features/dashboard/Dashboard.tsx`
6. `features/rag-chat/RAGChatPage.tsx`
7. `features/video-library/VideoLibraryPage.tsx`
8. `features/video-library/VideoDetailPage.tsx`
9. `features/agent-analysis/AgentAnalysisPage.tsx`
10. `features/pipeline-monitor/PipelineProgressPage.tsx`
11. `features/health-dashboard/HealthDashboardPage.tsx`
12. `features/video-upload/VideoUploadPage.tsx`
13. `lib/utils.ts` — `formatRelativeTime` stage labels
14. `lib/api.ts`, `hooks/useSSE.ts` (comments only)
15. `types/api.ts` (comments only)

### Translation keys convention
CamelCase dotted paths matching the feature area:
- `nav.dashboard`, `nav.upload`, `nav.settings`
- `videoLibrary.searchPlaceholder`, `videoLibrary.noVideos`
- `settings.language`, `settings.theme`, `settings.save`
- `pipeline.stage.downloading`, `pipeline.stage.transcoding`, ...
- `utils.justNow`, `utils.minutesAgo`, `utils.hoursAgo`, `utils.daysAgo`

## Spec self-review
- [x] No placeholders or "TBD" — all decisions made
- [x] No contradictions — single source of truth (DB language → context → i18n changeLanguage)
- [x] Scope is bounded to frontend only — no backend API changes
- [x] Fallback chain is explicit (en-US fallback guarantees no blank UI)
- [x] 17 files identified, effort is predictable

## Approval
Wait for user approval before transitioning to writing-plans.
