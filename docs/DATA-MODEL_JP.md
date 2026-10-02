# VideoMind データモデル設計

> コア 18 テーブル、ER 関係、インデックス戦略、マイグレーション方針、Pydantic/SQLAlchemy マッピング

---

## 1. ER 図の概要

```
┌─────────────┐       ┌─────────────┐       ┌─────────────┐
│    user     │───────│ user_ai_cfg │       │ membership  │
└─────────────┘       └─────────────┘       └─────────────┘
       │
       │ 1:N
       ▼
┌─────────────┐       ┌─────────────┐       ┌─────────────┐
│ media_file  │───────│video_segment│───────│   chunk     │
└─────────────┘       └─────────────┘       └─────────────┘
       │                   │                   │
       │ 1:N               │ 1:N               │ 1:N
       ▼                   ▼                   ▼
┌─────────────┐       ┌─────────────┐       ┌─────────────┐
│ transcription│      │ frame_ocr   │       │ rag_trace   │
└─────────────┘       └─────────────┘       └─────────────┘
       │
       │ 1:N
       ▼
┌─────────────┐       ┌─────────────┐       ┌─────────────┐
│analysis_task│───────│agent_checkpt│       │agent_result │
└─────────────┘       └─────────────┘       └─────────────┘
       │
       │ 1:N
       ▼
┌─────────────┐       ┌─────────────┐
│celery_task  │       │ingestion_task│
└─────────────┘       └─────────────┘
```

---

## 2. テーブル定義詳細

### 2.1 ユーザー認証ドメイン

#### `user` — ユーザーマスタテーブル
```sql
CREATE TABLE "user" (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(64) NOT NULL UNIQUE,
    email           VARCHAR(128) NOT NULL UNIQUE,
    phone           VARCHAR(20),                     -- フィールド単位の暗号化（EncryptedString、SECURITY.md の DataEncryption 参照）
    real_name       VARCHAR(100),                    -- フィールド単位の暗号化（EncryptedString、SECURITY.md の DataEncryption 参照）
    password_hash   VARCHAR(256) NOT NULL,          -- bcrypt
    full_name       VARCHAR(128),
    avatar_url      VARCHAR(512),
    is_active       BOOLEAN DEFAULT TRUE,
    is_superuser    BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);
CREATE INDEX ix_user_email ON "user"(email);
CREATE INDEX ix_user_username ON "user"(username);
```

#### `user_ai_config` — ユーザー単位の AI 設定（マルチテナント持ち込み Key）
```sql
CREATE TABLE user_ai_config (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    provider            VARCHAR(32) NOT NULL,       -- ollama/openai/anthropic/custom
    model_name          VARCHAR(128) NOT NULL,
    api_key_encrypted   BYTEA,                      -- AES-GCM 暗号化保存
    api_key_nonce       BYTEA,                      -- GCM nonce
    api_base_url        VARCHAR(512),
    embedding_model     VARCHAR(128),               -- embedding を個別に設定
    embedding_provider  VARCHAR(32),
    asr_model           VARCHAR(64),                -- whisper-large-v3 など
    ocr_model           VARCHAR(64),                -- paddle-ocr など
    temperature         REAL DEFAULT 0.3,
    max_tokens          INTEGER DEFAULT 4096,
    is_default          BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, provider, model_name)
);
CREATE INDEX ix_uac_user ON user_ai_config(user_id);
```

#### `membership` — 会員/クォータ
```sql
CREATE TABLE membership (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    tier            VARCHAR(16) NOT NULL DEFAULT 'free',  -- free/pro/enterprise
    status          VARCHAR(16) NOT NULL DEFAULT 'active',-- active/expired/cancelled
    monthly_token_quota  BIGINT DEFAULT 5000000,          -- 月次 token クォータ（MODEL-GATEWAY の TokenAccounting と口径一致）
    monthly_token_used   BIGINT DEFAULT 0,
    quota_reset_at       TIMESTAMPTZ DEFAULT NOW(),
    stripe_customer_id VARCHAR(128),
    stripe_subscription_id VARCHAR(128),
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_mem_user ON membership(user_id);
```

---

### 2.2 メディアナレッジドメイン

#### `media_file` — 動画/メディアファイルマスタ
```sql
CREATE TABLE media_file (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    source_type         VARCHAR(16) NOT NULL,          -- upload/url
    source_url          TEXT,                          -- オリジナル URL（重複排除キー）
    content_hash        CHAR(64) NOT NULL,             -- SHA256 コンテンツフィンガープリント
    filename            VARCHAR(256) NOT NULL,
    mime_type           VARCHAR(64) NOT NULL,
    file_size           BIGINT NOT NULL,
    duration_ms         BIGINT,                        -- ミリ秒
    width               INTEGER,
    height              INTEGER,
    fps                 REAL,
    minio_bucket        VARCHAR(64) NOT NULL,
    minio_object        VARCHAR(512) NOT NULL,         -- オブジェクトキー
    thumbnail_object    VARCHAR(512),                  -- サムネイルのオブジェクトキー
    status              VARCHAR(32) NOT NULL DEFAULT 'pending',
                                                -- pending/downloading/downloaded/transcribing/ocr/indexing/ready/failed
    error_message       TEXT,
    meta_json           JSONB DEFAULT '{}',            -- 拡張メタデータ
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);
CREATE UNIQUE INDEX ux_media_content_hash ON media_file(content_hash);
CREATE INDEX ix_media_user ON media_file(user_id);
CREATE INDEX ix_media_status ON media_file(status);
CREATE INDEX ix_media_source_url ON media_file(source_url) WHERE source_url IS NOT NULL;
```

#### `video_segment` — 60 秒ウィンドウのマルチモーダルセグメント（ASR+OCR 統合後）
```sql
CREATE TABLE video_segment (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id            UUID NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    segment_index       INTEGER NOT NULL,              -- 0-based
    start_ms            BIGINT NOT NULL,
    end_ms              BIGINT NOT NULL,
    transcript          TEXT,                          -- ASR テキスト
    ocr_texts           TEXT[],                        -- このウィンドウ内のすべての OCR テキスト
    evidence_frames     JSONB DEFAULT '[]',            -- [{"frame_ms":, "minio_object":, "ocr_text":}]
    token_count         INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(media_id, segment_index)
);
CREATE INDEX ix_vs_media ON video_segment(media_id);
CREATE INDEX ix_vs_time ON video_segment(media_id, start_ms);
```

#### `transcription` — 動画全体の文字起こし記録
```sql
CREATE TABLE transcription (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id            UUID NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    full_text           TEXT NOT NULL,
    language            VARCHAR(16) DEFAULT 'zh',
    model_name          VARCHAR(64) NOT NULL,          -- whisper-large-v3
    duration_sec        REAL,                          -- 処理所要時間
    chunk_count         INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(media_id)
);
CREATE INDEX ix_tr_media ON transcription(media_id);
```

#### `transcription_chunk` — 分割文字起こしチャンク（中断からの再開に対応）
```sql
CREATE TABLE transcription_chunk (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id            UUID NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    chunk_index         INTEGER NOT NULL,
    start_ms            BIGINT NOT NULL,
    end_ms              BIGINT NOT NULL,
    text                TEXT,
    status              VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending/running/completed/failed
    error_message       TEXT,
    audio_object        VARCHAR(512),                    -- MinIO 音声チャンクオブジェクト
    model_name          VARCHAR(64),
    duration_sec        REAL,
    retry_count         INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(media_id, chunk_index)
);
CREATE INDEX ix_tc_media ON transcription_chunk(media_id);
CREATE INDEX ix_tc_status ON transcription_chunk(status);
```

#### `frame_ocr` — キーフレーム OCR 記録
```sql
CREATE TABLE frame_ocr (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id            UUID NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    frame_ms            BIGINT NOT NULL,                 -- フレームのタイムスタンプ
    minio_object        VARCHAR(512) NOT NULL,           -- フレーム画像オブジェクト
    ocr_text            TEXT,
    phash               CHAR(16),                        -- 知覚ハッシュ（重複排除用）
    model_name          VARCHAR(64),                     -- paddle-ocr
    status              VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(media_id, frame_ms)
);
CREATE INDEX ix_fo_media ON frame_ocr(media_id);
CREATE INDEX ix_fo_phash ON frame_ocr(phash) WHERE phash IS NOT NULL;
```

#### `chunk` — RAG 検索チャンク（ベクトル+キーワードの二重インデックス）
```sql
CREATE TABLE chunk (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id            UUID NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    segment_id          UUID REFERENCES video_segment(id) ON DELETE SET NULL,
    chunk_index         INTEGER NOT NULL,
    content             TEXT NOT NULL,
    content_hash        CHAR(32) NOT NULL,               -- MD5 コンテンツハッシュ（安定した参照アンカー）
    token_count         INTEGER NOT NULL,
    start_ms            BIGINT,                          -- 対応する動画のタイムスタンプ
    end_ms              BIGINT,
    source_type         VARCHAR(16) NOT NULL,            -- asr/ocr/mixed
    qdrant_point_id     UUID,                            -- Qdrant ベクトル ID (UUID v5)
    manifest_sha256     CHAR(64),                        -- ChunkManifest SHA256（再現性）
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(media_id, chunk_index)
);
CREATE INDEX ix_chunk_media ON chunk(media_id);
CREATE INDEX ix_chunk_content_hash ON chunk(content_hash);
CREATE INDEX ix_chunk_qdrant_id ON chunk(qdrant_point_id) WHERE qdrant_point_id IS NOT NULL;
```

#### `knowledge_base` — ナレッジベース（ドキュメント RAG 拡張用に予約）
```sql
CREATE TABLE knowledge_base (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    name            VARCHAR(128) NOT NULL,
    description     TEXT,
    embedding_model VARCHAR(128) NOT NULL,
    chunk_size      INTEGER DEFAULT 800,
    chunk_overlap   INTEGER DEFAULT 120,
    is_public       BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_kb_user ON knowledge_base(user_id);
```

#### `document` — ナレッジベースドキュメント（予約）
```sql
CREATE TABLE document (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id               UUID NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    source_type         VARCHAR(16) NOT NULL,          -- upload/url
    source_url          TEXT,
    content_hash        CHAR(64) NOT NULL,
    filename            VARCHAR(256),
    mime_type           VARCHAR(64),
    file_size           BIGINT,
    status              VARCHAR(32) NOT NULL DEFAULT 'pending',
    chunk_count         INTEGER DEFAULT 0,
    minio_object        VARCHAR(512),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_doc_kb ON document(kb_id);
CREATE INDEX ix_doc_user ON document(user_id);
```

---

### 2.3 タスクドメイン

#### `analysis_task` — Agent 分析タスク
```sql
CREATE TABLE analysis_task (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    media_id            UUID NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    goal                TEXT NOT NULL,                   -- ユーザーの分析ゴール
    goal_hash           CHAR(64) NOT NULL,               -- SHA256(goal) 冪等性キー
    status              VARCHAR(32) NOT NULL DEFAULT 'pending',
                                                -- pending/running/planning/executing/critic_check/completed/failed
    current_round       INTEGER DEFAULT 0,
    max_rounds          INTEGER DEFAULT 2,
    final_result_json   JSONB,                           -- AnalysisResult の完全な JSON
    error_message       TEXT,
    trace_id            CHAR(32),                        -- エンドツーエンドの Trace ID
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    UNIQUE(media_id, goal_hash)
);
CREATE INDEX ix_at_user ON analysis_task(user_id);
CREATE INDEX ix_at_media ON analysis_task(media_id);
CREATE INDEX ix_at_status ON analysis_task(status);
```

#### `agent_checkpoint` — AgentLoop チェックポイントによる中断・復帰（PostgreSQL 真のソース + Redis ホットキャッシュ）
```sql
CREATE TABLE agent_checkpoint (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id             UUID NOT NULL REFERENCES analysis_task(id) ON DELETE CASCADE,
    trace_id            TEXT,                           -- W3C Trace ID（32 桁 hex）、ログ/Trace の連結用、task_id と併存
    round               INTEGER NOT NULL,
    phase               VARCHAR(32) NOT NULL,            -- planning/executing/critic_check
    agent_state_json    JSONB NOT NULL,                  -- AgentState の完全なシリアライズ
    video_context_ref   JSONB,                           -- VideoContext 参照（media_id + segment_ids）
    plan_json           JSONB,                           -- AgentPlan
    critique_json       JSONB,                           -- CriticResult
    result_json         JSONB,                           -- AnalysisResult（その段階のもの）
    feedback            TEXT,                            -- Critic のフィードバック
    required_timestamps BIGINT[],                        -- 追加エビデンスが必要なタイムスタンプ
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(task_id, round, phase)
);
CREATE INDEX ix_acp_task ON agent_checkpoint(task_id);
```

#### `agent_result` — 最終的な構造化分析結果
```sql
CREATE TABLE agent_result (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id             UUID NOT NULL REFERENCES analysis_task(id) ON DELETE CASCADE,
    title               VARCHAR(256) NOT NULL,
    conclusions_json    JSONB NOT NULL,                  -- [{"point":, "evidence_ids":[]}]
    evidence_json       JSONB NOT NULL,                  -- [{"timestamp_ms":, "source":, "content":, "chunk_id":}]
    suggestions_json    JSONB,                           -- 後続のフォローアップ質問の提案
    critic_passed       BOOLEAN NOT NULL,
    critic_feedback     TEXT,
    total_rounds        INTEGER NOT NULL,
    token_usage         JSONB,                           -- {"prompt":, "completion":, "total":}
    cost_usd            REAL,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_ar_task ON agent_result(task_id);
```

---

### 2.4 RAG/Agent トレースドメイン

#### `rag_trace` — 検索パイプライントレース（評価/デバッグ用）
```sql
CREATE TABLE rag_trace (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id             UUID REFERENCES analysis_task(id) ON DELETE SET NULL,
    user_id             UUID REFERENCES "user"(id) ON DELETE SET NULL,
    query               TEXT NOT NULL,
    rewritten_queries   JSONB,                           -- サブクエリのリスト
    intent_path         JSONB,                           -- インテントツリーのパス
    retrieval_channels  JSONB,                           -- 各チャネルの生の結果
    fused_results       JSONB,                           -- RRF フュージョン後の TopK
    expanded_results    JSONB,                           -- ContextExpander 通過後
    reranked_results    JSONB,                           -- Rerank 後
    final_context       JSONB,                           -- LLM に入力するコンテキスト
    latency_ms          INTEGER,                         -- 合計所要時間
    token_usage         JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_rt_task ON rag_trace(task_id);
CREATE INDEX ix_rt_user ON rag_trace(user_id);
CREATE INDEX ix_rt_created ON rag_trace(created_at);
```

#### `session` — セッション/フォローアップコンテキスト
```sql
CREATE TABLE session (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    media_id            UUID REFERENCES media_file(id) ON DELETE SET NULL,
    title               VARCHAR(256),
    context_summary     TEXT,                            -- 要約圧縮された履歴コンテキスト
    message_count       INTEGER DEFAULT 0,
    total_tokens        INTEGER DEFAULT 0,
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    expires_at          TIMESTAMPTZ
);
CREATE INDEX ix_sess_user ON session(user_id);
CREATE INDEX ix_sess_media ON session(media_id);
```

---

### 2.5 非同期タスクドメイン

#### `celery_task` — Celery タスク記録（可観測性/リトライ/デッドレター）
```sql
CREATE TABLE celery_task (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_name           VARCHAR(128) NOT NULL,           -- download_video_task など
    celery_id           VARCHAR(64) NOT NULL UNIQUE,     -- Celery task_id
    args_json           JSONB,
    kwargs_json         JSONB,
    status              VARCHAR(32) NOT NULL DEFAULT 'pending',
                                                -- pending/started/retry/success/failure
    result_json         JSONB,
    traceback           TEXT,
    retry_count         INTEGER DEFAULT 0,
    max_retries         INTEGER DEFAULT 3,
    queue_name          VARCHAR(64),
    worker_hostname     VARCHAR(128),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_ct_celery_id ON celery_task(celery_id);
CREATE INDEX ix_ct_status ON celery_task(status);
CREATE INDEX ix_ct_name ON celery_task(task_name);
```

#### `ingestion_task` — 取り込みパイプラインタスク（動画処理の各段階）
```sql
CREATE TABLE ingestion_task (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id            UUID NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    stage               VARCHAR(32) NOT NULL,            -- download/transcribe/ocr/build_context/index
    status              VARCHAR(32) NOT NULL DEFAULT 'pending',
    celery_task_id      UUID REFERENCES celery_task(id) ON DELETE SET NULL,
    input_json          JSONB,
    output_json         JSONB,
    error_message       TEXT,
    retry_count         INTEGER DEFAULT 0,
    max_retries         INTEGER DEFAULT 3,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(media_id, stage)
);
CREATE INDEX ix_it_media ON ingestion_task(media_id);
CREATE INDEX ix_it_status ON ingestion_task(status);
```

---

### 2.6 拡張テーブル（セキュリティと課金、コア 18 テーブルの範囲外）

> 以下の 4 テーブルはセキュリティと課金の拡張であり、**コア 18 テーブルには含まれません**。`core/auth`・`core/llm` モジュールが保持します。

#### `api_key` — API Key 認証（SECURITY.md 参照）
```sql
CREATE TABLE api_key (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    name            VARCHAR(64) NOT NULL,
    key_hash        TEXT NOT NULL,                       -- argon2id、検証用
    encrypted_key   TEXT NOT NULL,                      -- AES-GCM base64、管理画面表示のために復号して使用
    key_prefix      CHAR(12) NOT NULL,                   -- vk_live_xxxxxxxx プレフィックス検索
    key_suffix      CHAR(4),                            -- 末尾 4 桁を平文表示
    permissions     JSONB DEFAULT '[]'::jsonb,
    ip_whitelist    JSONB DEFAULT '[]'::jsonb,
    rate_limit      INT DEFAULT 1000,                    -- 1 分あたり
    env             VARCHAR(8) NOT NULL DEFAULT 'live',  -- live/test/dev
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    is_active       BOOLEAN DEFAULT TRUE
);
CREATE UNIQUE INDEX ux_api_key_prefix ON api_key(key_prefix) WHERE is_active = TRUE;
CREATE INDEX ix_api_key_user ON api_key(user_id);
```

#### `role` — ロール定義（RBAC、SECURITY.md 参照）
```sql
CREATE TABLE role (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(32) NOT NULL UNIQUE,         -- viewer/user/analyst/admin
    description     VARCHAR(255),
    permissions     JSONB DEFAULT '[]'::jsonb,           -- 権限コードのリスト
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

#### `user_role` — ユーザー・ロール関連（user.roles 多対多）
```sql
CREATE TABLE user_role (
    user_id         UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    role_id         UUID NOT NULL REFERENCES role(id) ON DELETE CASCADE,
    granted_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, role_id)
);
CREATE INDEX ix_ur_role ON user_role(role_id);
```

#### `ai_call_logs` — AI 呼び出し Token 課金ログ（MODEL-GATEWAY.md 参照）
```sql
CREATE TABLE ai_call_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID REFERENCES "user"(id) ON DELETE SET NULL,  -- API Key 呼び出しに対応するため nullable
    api_key_id          UUID REFERENCES api_key(id) ON DELETE SET NULL,
    provider            VARCHAR(32) NOT NULL,            -- ollama/openai/deepseek/anthropic
    model               VARCHAR(64) NOT NULL,
    task_type           VARCHAR(16) NOT NULL,             -- thinking/normal/fast/embedding/rerank
    prompt_tokens       INT NOT NULL DEFAULT 0,
    completion_tokens   INT NOT NULL DEFAULT 0,
    total_tokens        INT NOT NULL DEFAULT 0,
    cost_usd            NUMERIC(10,6) DEFAULT 0,
    trace_id            TEXT,                            -- ログ連結用
    status              VARCHAR(16) NOT NULL DEFAULT 'success',
    error_code          VARCHAR(64),
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_acl_user_time ON ai_call_logs(user_id, created_at);
CREATE INDEX ix_acl_provider_model ON ai_call_logs(provider, model, created_at);
CREATE INDEX ix_acl_created ON ai_call_logs(created_at);
```

> 以上の 4 拡張テーブルはセキュリティ（RBAC/API Key）と課金（Token ログ）の責務を担い、core/auth、core/llm モジュールが保持します。コア 18 テーブルにはこの 4 テーブルは含まれません。

---

## 3. インデックス戦略のまとめ

| テーブル | コアインデックス | 用途 |
|----|----------|------|
| `media_file` | `content_hash` (UNIQUE) | コンテンツ単位の重複排除 |
| `media_file` | `user_id + status` | ユーザー動画リストの絞り込み |
| `video_segment` | `media_id + start_ms` | 時間範囲クエリ |
| `transcription_chunk` | `media_id + status` | レジューム時に未完了チャンクをスキャン |
| `frame_ocr` | `phash` | 知覚ハッシュによる重複排除 |
| `chunk` | `content_hash` | 安定した参照アンカー |
| `chunk` | `qdrant_point_id` | ベクトル DB 二重書き込みの一貫性検証 |
| `analysis_task` | `media_id + goal_hash` (UNIQUE) | 冪等性：同一動画・同一ゴールで重複実行しない |
| `agent_checkpoint` | `task_id + round + phase` | チェックポイントによる中断・復帰位置の精確な特定 |
| `celery_task` | `celery_id` | Celery ネイティブ ID との関連付け |
| `rag_trace` | `task_id + created_at` | 経路の遡行 |

---

## 4. マイグレーション戦略

- **ツール**：Alembic + `alembic.ini` + `env.py`（`sync`/`async` 双モード対応）
- **命名**：`YYYYMMDD_HHMMSS_<short_desc>.py`
- **規約**：
  - モデル変更のたびに 1 本のマイグレーションを生成。**SQL は手書きせず** `op.*` API を使用
  - 破壊的変更（Drop Column、Rename）は 2 段階に分割：先に新列/新テーブルを Add → データ移行 → 旧列を Drop
  - インデックス変更は単独マイグレーションとし、`CONCURRENTLY` でロックを回避（PostgreSQL）
  - シードデータは `alembic upgrade head && python scripts/seed.py` を使用

```bash
# よく使うコマンド
alembic revision --autogenerate -m "add frame_ocr phash index"
alembic upgrade head
alembic downgrade -1
alembic history --verbose
```

---

## 5. Pydantic ↔ SQLAlchemy マッピング規約

| シナリオ | 方式 |
|------|------|
| **ORM → API レスポンス** | `model_dump(mode='json')` + `Config.from_attributes = True` |
| **API リクエスト → ORM** | `model_dump(exclude_unset=True)` 渡されたフィールドのみ更新 |
| **JSONB フィールド** | SQLAlchemy `JSONB` + Pydantic `Dict[str, Any]` / 独自 `TypeDecorator` |
| **配列フィールド** | PostgreSQL `ARRAY(Text)` + Pydantic `List[str]` |
| **列挙型** | Python `StrEnum` + SQLAlchemy `Enum(StrEnum, native_enum=False)` |
| **UUID** | `uuid.UUID` を双方向にパススルー |

```python
# 典型的なパターン
class MediaFileRead(MediaFileBase):
    id: UUID
    created_at: datetime
    class Config: from_attributes = True

# Service 層
def to_read(orm: MediaFile) -> MediaFileRead:
    return MediaFileRead.model_validate(orm)
```

---

## 6. 主要なデータ整合性制約

| 制約 | 実現方式 |
|------|----------|
| **コンテンツ重複排除** | `media_file.content_hash` UNIQUE + アプリケーション層でのアップロード前 `HEAD` クエリ |
| **冪等な分析** | `analysis_task(media_id, goal_hash)` UNIQUE |
| **セグメント番号の一意性** | `video_segment(media_id, segment_index)` UNIQUE |
| **チャンク番号の一意性** | `chunk(media_id, chunk_index)` UNIQUE |
| **チェックポイントの一意性** | `agent_checkpoint(task_id, round, phase)` UNIQUE |
| **外部キーカスケード** | `ON DELETE CASCADE` は強い所有関係のみに使用、弱参照には `SET NULL` |
| **ソフトデリート** | コアテーブルは物理削除しない。`status='deleted'` + ビューによるフィルタリング |

---
> **関連ドキュメント**：[ARCHITECTURE_JP.md](ARCHITECTURE_JP.md) · [VIDEO-PIPELINE_JP.md](VIDEO-PIPELINE_JP.md) · [RAG-RETRIEVAL_JP.md](RAG-RETRIEVAL_JP.md) · [AGENT-LOOP_JP.md](AGENT-LOOP_JP.md) · [TASK-ORCHESTRATION_JP.md](TASK-ORCHESTRATION_JP.md)
