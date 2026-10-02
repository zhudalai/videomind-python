# VideoMind フロントエンド ワークベンチ設計（React 実装版）

> React 18 + TypeScript + Vite + SSE リアルタイムワークベンチ：動画ライブラリ管理、動画アップロード、パイプライン進捗モニタリング、RAG Q&A、Agent 分析、ヘルスダッシュボード
> **本ドキュメントは現在のコードの実際の実装を記述するもの。** 設計書で計画されたが未実装の部分は [§12](#12-未実装設計書の残滓) に集約しています。本ドキュメント以外の内容から実装済みの能力を推測しないでください。
> 初期設計の参照元：Ragent `web/` + DOVideo-AI フロントエンド + VidLens UI コンポーネントライブラリ

---

## 1. 技術スタックと依存関係

`frontend/package.json`（package name `videomind-frontend`、`"type": "module"`）：

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

> ⚠️ `zustand`、`markdown-it`、`highlight.js`、`date-fns` は `dependencies` に含まれていますが、**ソースコードでは参照ゼロ（未使用）** です。詳細は [§12](#12-未実装設計書の残滓) を参照。

---

## 2. ディレクトリ構成

`frontend/src/` は全 37 ファイルで、`src/store/`、`src/pages/`、`src/router/` の各ディレクトリは**存在しません**。

```
frontend/src/
├── main.tsx                     # エントリ：Provider の組み立て
├── vite-env.d.ts                # ImportMetaEnv 型（VITE_API_BASE）
├── app/
│   └── App.tsx                  # <Routes> ルート定義（唯一のルート定義箇所）
├── components/
│   ├── layout/
│   │   ├── index.ts             # MainLayout as Layout を再エクスポート
│   │   ├── MainLayout.tsx       # アプリシェル：Sidebar + Header + <Outlet/>
│   │   ├── Sidebar.tsx          # 折りたたみ可能ナビゲーション（7 項目の NavLink）
│   │   └── Header.tsx           # トップバー：検索ボックス / テーマ切替 / 通知
│   └── ui/                      # UI プリミティブ（Button/Badge/Card/Input/Label/
│                                #   Progress/Tabs/Textarea/ScrollArea/PlaceholderPage）
├── contexts/
│   └── LanguageContext.tsx      # 言語コンテキスト（一回限りの DB 同期を含む）
├── features/                    # ページレベルコンポーネント、1 ディレクトリ 1 ページ
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
│   ├── useApi.ts                # QUERY_KEYS ファクトリ + 20 個の hooks（⚠️ 参照なし）
│   └── useSSE.ts                # usePipelineSSE / useEventSource（⚠️ 参照なし）
├── i18n/
│   ├── index.ts                 # i18next 初期化
│   ├── en-US.json
│   └── zh-CN.json
├── lib/
│   ├── api.ts                   # axios インスタンス + 6 つの endpoint group（実際に使用）
│   ├── endpoints.ts             # ENDPOINTS 定数テーブル（⚠️ 参照なし）
│   └── utils.ts                 # cn()、フォーマット、ステージマッピング、サムネイル
├── styles/
│   └── globals.css              # デザイン token + .prose + .scrollbar-hide
└── types/
    └── api.ts                   # Zod schema + z.infer による型エクスポート
```

**設計方針**：**機能ドメイン**（`features/<domain>/`）単位で分割し、技術種別（views / components / stores）ごとの分割は行いません。ページは自己完結し、中間コンポーネントを相互に共有しません。UI プリミティブは `components/ui/` に集約します。

---

## 3. ルーティング

React Router v6 の**宣言型 `<Routes>`** です（`createBrowserRouter` ではなく、独立したルート設定ファイルも存在しません）。`frontend/src/app/App.tsx` で定義されています：

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

| パス | ページ | 説明 |
|---|---|---|
| `/` | `Dashboard` | ホーム：ヘルスカード + ショートカット + 最近の動画 |
| `/upload` | `VideoUploadPage` | URL / ローカルファイルの 2 Tab アップロード |
| `/videos` | `VideoLibraryPage` | 動画ライブラリグリッド |
| `/videos/:id` | `VideoDetailPage` | 詳細：転写 / セグメント / OCR の 3 Tab |
| `/videos/:id/progress` | `PipelineProgressPage` | パイプライン進捗（SSE） |
| `/chat` | `RAGChatPage` | RAG Q&A |
| `/analysis` | `AgentAnalysisPage` | Agent 分析ワークベンチ |
| `/health` | `HealthDashboardPage` | 依存コンポーネントのヘルスダッシュボード |
| `/settings` | `SettingsPage` | ユーザー設定 |

**Provider の組み立て順**（`main.tsx`）：

```
React.StrictMode
└─ QueryClientProvider        (+ ReactQueryDevtools)
   └─ I18nextProvider
      └─ LanguageProvider
         └─ BrowserRouter
            └─ App
```

---

## 4. 状態管理

### 4.1 TanStack Query が唯一の状態レイヤー

`QueryClient` は `main.tsx` 内でインラインに生成されます：

```ts
new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,      // 5 分
      gcTime: 30 * 60 * 1000,        // 30 分
      retry: (failureCount, error) =>
        error?.status === 404 ? false : failureCount < 3,
      refetchOnWindowFocus: false,
    },
  },
})
```

- **サーバー状態** → TanStack Query（クエリ / ミューテーション / ポーリング）
- **UI ローカル状態** → `useState`（チャットメッセージ、フォーム、フィルタ条件）
- **コンポーネント横断状態** → React Context は `LanguageContext` の 1 つのみ
- **Zustand 未使用**：依存は存在するが store ファイルなし、import なし。Vite/TS の `@/store` エイリアスは**存在しないディレクトリ**を指す

### 4.2 ポーリング戦略

一部の状態には SSE プッシュがなく、代わりに `refetchInterval` によるポーリングを使用します：

| シナリオ | 箇所 | 間隔 | 条件 |
|---|---|---|---|
| Agent タスク状態 | `AgentAnalysisPage.tsx:67-75` | 2000ms | `pending`/`planning`/`executing`/`critic_check` の間のみ |
| ヘルスチェック | `HealthDashboardPage.tsx:46-50` | 30000ms | 常駐 |
| パイプライン状態（REST フォールバック） | `PipelineProgressPage.tsx:221` | 5000ms | SSE と並行 |

---

## 5. API レイヤー

### 5.1 axios インスタンス

`frontend/src/lib/api.ts:12-20`：

```ts
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})
```

`frontend/.env` の内容は `VITE_API_BASE=/api`（`.env.development` / `.env.production` のバリアントなし）。開発時は Vite proxy でバックエンドへ転送されます。

### 5.2 インターセプター

- **リクエスト**：`localStorage.access_token` が存在すれば `Authorization: Bearer <token>` を注入
- **レスポンス**：**`response.data` をアンパック**し、呼び出し側は payload を直接受け取る
- **エラー**：`Error & { status?, detail? }` に統一正規化。`detail` は FastAPI の `data.detail` から取得
- **401**：ローカル token をクリア

### 5.3 Endpoint グループ

すべて `/api` 基準で、バックエンドルート（`src/videomind/interface/routes/`）と 1 対 1 に対応します：

| グループ | メソッド + パス | 対応バックエンド |
|---|---|---|
| `pipelineApi` | `POST /videos/pipeline` | `video.py:236` URL パイプライン投入 |
| | `POST /videos/upload` | `video.py:88` multipart アップロード（`onUploadProgress` 付き） |
| | `GET /videos/pipeline/{mediaId}` | `video.py:265` パイプライン状態 |
| `videoApi` | `GET /videos` | `video.py:441` 一覧（page/page_size/search/status） |
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

> 初期の設計書では `/api/v1/...` プレフィックスを使用していましたが、**実際のバックエンドは `/api`** です（`interface/__init__.py:72-77`）。

---

## 6. リアルタイム進捗（SSE）

### 6.1 仕組み：ネイティブ `EventSource`

**パイプライン進捗**のためだけに使用されます。フロントエンド全体のどこにも `fetch + ReadableStream` はなく、**token レベルの回答ストリーミング出力もありません**。

実装は `frontend/src/features/pipeline-monitor/PipelineProgressPage.tsx:139-199` にあります：

```ts
const apiBase = import.meta.env.VITE_API_BASE || '/api'
const es = new EventSource(`${apiBase}/videos/pipeline/${mediaId}/progress`)

// 名前付きイベント 'progress' をリッスン（onmessage ではない）
es.addEventListener('progress', (event) => {
  const progressEvent: ProgressEvent = JSON.parse(event.data)
  setSseEvents(prev => [...prev, progressEvent])
  if (progressEvent.stage === 'completed' || progressEvent.progress_pct >= 100) es.close()
  else if (progressEvent.stage === 'failed' || progressEvent.progress_pct < 0) es.close()
})

es.onerror = () => { /* close + 最大 5 回リトライ、バックオフ 3000*(n+1) ms */ }
```

対応するバックエンドは `sse.py:22` の `GET /api/videos/pipeline/{media_id}/progress` です。

### 6.2 デュアルチャネル設計

SSE が**リアルタイム差分イベント**を提供する一方、5 秒間隔で `pipelineApi.getStatus` を REST ポーリングする**フォールバック**も並行します。SSE が切断したりイベントをロストしても、ページは依然として権威ある状態から復旧できます。両者は UI 上で 9 段階縦型 stepper + 全体進捗バー + リアルタイムイベントログとして統合レンダリングされます。

`ProgressEvent` 構造（`types/api.ts`）：

```ts
{ stage: string; progress_pct: number; message: string; timestamp: string; metadata?: object }
```

---

## 7. 主要画面

| ページ | 主要実装 |
|---|---|
| **Dashboard** | ヘルスカード（`/health/ready`、30s ポーリング）+ 4 つのショートカット + 最近の `ready` 動画グリッド（削除可能） |
| **VideoUploadPage** | 2 Tab：URL / ローカルファイル。`react-hook-form` + `zodResolver`、ローカル zod schema バリデーション（URL 形式、ファイル ≤2GB かつ `video/*`）。URL は `pipelineApi.submit`、ファイルは `pipelineApi.uploadFile` を使用し、成功後に `/videos/{id}/progress` へ遷移 |
| **VideoLibraryPage** | 動画カードグリッド + 検索 + 状態フィルタ + ページング。**フィルタ条件は URL search params に同期**（共有・巻き戻し可能）。サムネイルは YouTube CDN 経由（`getVideoThumbnailUrl`）。削除は確認ダイアログとエラーバナー付き |
| **VideoDetailPage** | ステージ進捗リスト。`ready` 後に 3 つの Tab を表示：転写全文 / セグメント（`useInfiniteQuery`、展開可能）/ OCR フレーム（`useInfiniteQuery`） |
| **PipelineProgressPage** | 9 段階 stepper（REST 状態 + SSE イベント統合）、全体進捗、リアルタイムイベントログ、切断時の再接続（最大 5 回）、完了後に詳細へ遷移 |
| **RAGChatPage** | 楽観的更新のユーザーバブル → アシスタントバブル内に**折りたたみ可能なエビデンスカード**を埋め込み（関連度パーセンテージ、時間区間 `start_ms-end_ms`、`evidence_id` バッジ、内容プレビュー、`RAGChatPage.tsx:393-422` で定義）。メディア範囲セレクター + 推奨質問。**単発リクエスト/レスポンスで、ストリーミングなし** |
| **AgentAnalysisPage** | 目標入力 + 最大 4 つの `ready` 動画選択 + `max_rounds`（1–3）。送信後は 5 段階の状態フロー（`pending→planning→executing→critic_check→completed`）を 2 秒間隔でポーリング。実行トレースカード（checkpoints から）、最終結果カード（critic 合格/不合格バッジ、コスト、信頼度を含む結論、エビデンスリスト、提案） |
| **HealthDashboardPage** | PostgreSQL / Redis / Qdrant / MinIO の 4 枚のコンポーネントカード。30s ポーリング。**recharts 折れ線グラフ**で可用性履歴を表示（クライアント側で維持、上限 30 点）。手動リフレッシュ + 異常バナー |
| **SettingsPage** | 開発用アイデンティティカード、テーマの 3 状態切替、デフォルトモデル、言語選択（楽観的更新 + 失敗時ロールバック） |

---

## 8. UI コンポーネント、スタイル、i18n

### 8.1 UI プリミティブ（`components/ui/`）

`Button`（cva バリアント default/destructive/outline/secondary/ghost/link、Radix `Slot` による `asChild` 対応、内蔵 loading spinner）、`Badge`（success/warning/info を含む）、`Card`（Card/Header/Title/Description/Content/Footer）、`Input`、`Label`、`Textarea`、`Progress`（純 div プログレスバー）、`Tabs`（**自前実装**、Radix ではない）。

### 8.2 スタイル

- `tailwind.config.ts`：`darkMode: ['class']`、shadcn 風 **HSL CSS 変数**マッピング（`hsl(var(--primary))` など）、プラグイン `tailwindcss-animate`
- `styles/globals.css`：`:root`（ライト）と `.dark` の 2 セットの token（プライマリ `221.2 83.2% 53.3%`）、`.prose` コンポーネントレイヤー、`.scrollbar-hide` ユーティリティクラス
- PostCSS：`tailwindcss` + `autoprefixer`
- Radix は `@radix-ui/react-scroll-area` のみ直接依存（しかも未使用の `ScrollArea.tsx` でのみ参照）。`Button` が使う `@radix-ui/react-slot` は推移的依存
- アイコン `lucide-react`。グラフ `recharts`（ヘルスダッシュボードのみ）

### 8.3 i18n

`i18n/index.ts`：`i18next` + `initReactI18next` + `i18next-browser-languagedetector`。

- 言語：**`en-US`（デフォルト、fallback）と `zh-CN`**。リソースは静的インポートの JSON（各 337 行、15 の名前空間：`nav, app, header, videoLibrary, videoCard, dashboard, settings, ragChat, agentAnalysis, pipeline, health, videoUpload, videoDetail, placeholder, utils`）
- 検出順 `['localStorage', 'navigator']`、キャッシュキー `i18next_lng`
- `LanguageContext` は `{currentLanguage, setLanguage}` を提供し、`hasSyncedRef` でガードして DB（`GET /user/config`）から言語を**一回限り**同期
- ステージ/状態ラベルは i18n の **key** として `lib/utils.ts:91-102` の `STAGE_LABELS` に格納され、言語に応じてリアクティブに切り替わります

---

## 9. 型定義

すべての DTO は `frontend/src/types/api.ts`（296 行）に集約され、**Zod schema + `z.infer`** で記述されています。

> ⚠️ **これらの schema は型推論のみ（ランタイム検証なし）のソースであり、実行時に検証されることはありません**。`api.ts` は `import type` で参照しており、ソース中に `.parse(` の呼び出しは一切ありません。ランタイムで Zod を使用する唯一の場所は `VideoUploadPage.tsx:19-31` のフォームローカル schema です。

主要型：`MediaStatus`（`pending / downloading / downloaded / transcoding / transcoded / asr / ocr / indexing / ready / failed`）、`AnalysisStatus`（`pending / planning / executing / critic_check / completed / failed`）、`ProgressEvent`、`MediaFileResponse`、`VideoSegmentResponse`、`OCRResultResponse`、`AnalysisTaskRequest`（`goal` ≤5000、`media_ids` 1–4、`max_rounds` 1–3）、`AgentResultResponse`（`conclusions_json` / `evidence_json` / `suggestions_json` / `critic_passed` / `critic_feedback` / `token_usage` / `cost_usd`）、`HealthReadyResponse`、`RagChatResponse`、`UserConfig`。

---

## 10. テスト

| レイヤー | ツール | 状態 |
|---|---|---|
| **L1 スモーク** | Playwright `tests/e2e/smoke.spec.ts` | 5 ページのシェルレンダリング + 実リクエスト `/api/health/ready`、`/api/user/config`（バックエンドオフライン時はスキップ） |
| **L2 コントラクト** | Playwright `tests/e2e/api-contract.spec.ts` | 10 endpoint のレスポンス構造を固定（バックエンド `http://localhost:8002` に直結）、404/400 エラーパスを含む |
| **L3 フルチェーン** | Playwright `tests/e2e/full-chain.spec.ts` | `VM_E2E_FULL=1` + `ready` 状態のメディアのゲートで制御：アップロード→SSE→詳細→Agent 分析→ポーリング→結果→RAG |
| 単体テスト | Vitest + Testing Library | **設定済みだがテストファイルゼロ**（`vitest.config.ts` の include は `src/**/*.test.{ts,tsx}`、実際にはマッチなし） |

`playwright.config.ts`：chromium のみ、`baseURL http://localhost:4000`、`webServer: npm run dev`（CI 以外では起動済みサービスを再利用）、タイムアウト 60s。

---

## 11. 開発とビルド

```bash
cd frontend
npm install
npm run dev        # Vite dev server → http://127.0.0.1:4000（strictPort）
npm run build      # tsc -b && vite build
npm run typecheck  # tsc --noEmit
npm run lint       # eslint --max-warnings 0
npm run test:e2e   # playwright test
```

### ⚠️ `vite.config.js` と `vite.config.ts` の併存

Vite 5 は `DEFAULT_CONFIG_FILES` の順序で設定を解決し、**`vite.config.js` が `.ts` より先**に来るため、**実際に有効なのは `vite.config.js`** です（`vite.config.ts` は完全に無視されます）。

両者には実質的な差異が 1 か所のみ——**proxy のターゲット**：

| ファイル | 有効 | proxy `/api`、`/sse` のターゲット |
|---|---|---|
| `vite.config.js` | ✅ **実際に有効** | `http://127.0.0.1:8011` |
| `vite.config.ts` | ❌ 無視される | `http://127.0.0.1:8002` |

**帰結**：ローカル結合テスト時、バックエンドは **8011** でリッスンする必要があります。一方 `playwright.config.ts` と `tests/e2e/lib.ts` はバックエンドが **8002** にあると想定しています。この 2 か所のポート不整合は、現在未解決の技術的負債です。

その他の有効な設定：ポート `4000`、`strictPort: true`、host `127.0.0.1`、プラグイン `@vitejs/plugin-react`、エイリアス `@ → src`（`@/components`、`@/features`、`@/hooks`、`@/lib`、`@/store`、`@/types` を含む。うち `@/store` は存在しないディレクトリを指す）。

---

## 12. 未実装・設計書の残滓

以下は初期の Vue 設計書に登場するもので、**現在のコードでは未実装**です。誤解を招かないよう列挙します：

| 設計書の内容 | 実際の状況 |
|---|---|
| ストリーミング Markdown レンダラー（`StreamingMarkdown`、KaTeX / Mermaid / コードハイライト / カーソルアニメーションを含む） | **未実装**。`markdown-it`、`highlight.js` は `dependencies` に含まれるが**ソースでは参照ゼロ（未使用）**。アシスタントメッセージは `<p className="whitespace-pre-wrap">{content}</p>` でプレーンテキストとしてレンダリング（`RAGChatPage.tsx:346`） |
| Zustand store（`useVideoLibraryStore` / `useTaskMonitorStore`） | **未実装**。store ファイルなし、import なし。状態は TanStack Query + ローカル `useState` が担う |
| グローバルキーボードショートカット（`useKeyboardShortcuts`、`g l` / `g a` / `Cmd+K` コマンドパレットを含む） | **未実装** |
| SSE タスク監視 Store（`EventSource` Map + `mitt` イベントバス + 自動再接続） | **未実装**。実際はページ内でインラインの `EventSource`、名前付き `progress` イベント + REST ポーリングのフォールバック |
| RAG 回答の token レベルのストリーミング出力 | **未実装**。`POST /rag/chat` は単発リクエスト/レスポンスで、全文を一括受信後にレンダリング |
| `ScrollArea`（Radix）、`PlaceholderPage` | コンポーネントは書かれているが**参照ゼロ（未使用）** |
| `hooks/useApi.ts`（QUERY_KEYS + 20 hooks）、`hooks/useSSE.ts`、`lib/endpoints.ts` | いずれも実装済みだが**参照ゼロ（未使用）**。各ページは `useQuery`/`useMutation` と文字列リテラル key を各自インラインで使用 |
| 独立した `EvidenceCard.vue` コンポーネント | 実際は `RAGChatPage.tsx:393-422` でインライン定義 |
| `/api/v1/...` パスプレフィックス | 実際は `/api` |
| nginx + Docker フロントエンドイメージ | リポジトリに `docker/frontend.Dockerfile` / `nginx.conf` は**存在しない**。フロントエンドは現在ローカルの `npm run dev` でのみ実行 |

---

## 13. 既知の不整合

| # | 問題 | 影響 |
|---|---|---|
| 1 | `vite.config.js` / `vite.config.ts` の併存、`.js` が有効 | `.ts` を変更しても一切効果なし。proxy ポートと Playwright の想定が不整合（§11 を参照） |
| 2 | `@/store` エイリアスが存在しないディレクトリを指す | このエイリアスを参照するとビルドが失敗 |
| 3 | 4 つの依存（zustand / markdown-it / highlight.js / date-fns）+ 5 ファイルがデッドコード | パッケージサイズとメンテナンスノイズ |
| 4 | Vitest は設定済みだがテストファイルゼロ | `npm test` に実質的な検証能力なし |
| 5 | ログインフローなし：token 注入インターセプターのみで、アイデンティティは `GET /user/config` の開発ユーザー由来 | 認証パスを検証できない |
| 6 | `frontend/test-upload.cjs`、`test-upload.spec.ts` は残留スクリプトで、廃止済みポートを指す | 誤用されやすい |
| 7 | `frontend.log`、`dist/`、`tsconfig*.tsbuildinfo` はビルド/実行の残留物 | `.gitignore` への追加を推奨 |

---

## 14. 参考

- バックエンドインターフェース契約：[ARCHITECTURE_JP.md](ARCHITECTURE_JP.md)、[TASK-ORCHESTRATION_JP.md](TASK-ORCHESTRATION_JP.md)
- 設計↔実装の全体的な乖離記録：[DECISIONS_JP.md](DECISIONS_JP.md)
- RAG エビデンスデータ構造：[RAG-RETRIEVAL_JP.md](RAG-RETRIEVAL_JP.md)
- Agent 結果構造：[AGENT-LOOP_JP.md](AGENT-LOOP_JP.md)
