# VideoMind 数据模型设计

> 18 张核心表、ER 关系、索引策略、迁移方案、Pydantic/SQLAlchemy 映射

---

## 1. ER 图概览

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

## 2. 表定义详情

### 2.1 用户认证域

#### `user` — 用户主表
```sql
CREATE TABLE "user" (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(64) NOT NULL UNIQUE,
    email           VARCHAR(128) NOT NULL UNIQUE,
    phone           VARCHAR(20),                     -- 字段级加密（EncryptedString，见 SECURITY.md DataEncryption）
    real_name       VARCHAR(100),                    -- 字段级加密（EncryptedString，见 SECURITY.md DataEncryption）
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

#### `user_ai_config` — 用户级 AI 配置（多租户自带 Key）
```sql
CREATE TABLE user_ai_config (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    provider            VARCHAR(32) NOT NULL,       -- ollama/openai/anthropic/custom
    model_name          VARCHAR(128) NOT NULL,
    api_key_encrypted   BYTEA,                      -- AES-GCM 加密存储
    api_key_nonce       BYTEA,                      -- GCM nonce
    api_base_url        VARCHAR(512),
    embedding_model     VARCHAR(128),               -- 独立配置 embedding
    embedding_provider  VARCHAR(32),
    asr_model           VARCHAR(64),                -- whisper-large-v3 等
    ocr_model           VARCHAR(64),                -- paddle-ocr 等
    temperature         REAL DEFAULT 0.3,
    max_tokens          INTEGER DEFAULT 4096,
    is_default          BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, provider, model_name)
);
CREATE INDEX ix_uac_user ON user_ai_config(user_id);
```

#### `membership` — 会员/配额
```sql
CREATE TABLE membership (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    tier            VARCHAR(16) NOT NULL DEFAULT 'free',  -- free/pro/enterprise
    status          VARCHAR(16) NOT NULL DEFAULT 'active',-- active/expired/cancelled
    monthly_token_quota  BIGINT DEFAULT 5000000,          -- 月度 token 配额（与 MODEL-GATEWAY TokenAccounting 口径一致）
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

### 2.2 媒体知识域

#### `media_file` — 视频/媒体文件主表
```sql
CREATE TABLE media_file (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    source_type         VARCHAR(16) NOT NULL,          -- upload/url
    source_url          TEXT,                          -- 原始 URL（去重键）
    content_hash        CHAR(64) NOT NULL,             -- SHA256 内容指纹
    filename            VARCHAR(256) NOT NULL,
    mime_type           VARCHAR(64) NOT NULL,
    file_size           BIGINT NOT NULL,
    duration_ms         BIGINT,                        -- 毫秒
    width               INTEGER,
    height              INTEGER,
    fps                 REAL,
    minio_bucket        VARCHAR(64) NOT NULL,
    minio_object        VARCHAR(512) NOT NULL,         -- 对象键
    thumbnail_object    VARCHAR(512),                  -- 缩略图对象键
    status              VARCHAR(32) NOT NULL DEFAULT 'pending',
                                                -- pending/downloading/downloaded/transcribing/ocr/indexing/ready/failed
    error_message       TEXT,
    meta_json           JSONB DEFAULT '{}',            -- 扩展元数据
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);
CREATE UNIQUE INDEX ux_media_content_hash ON media_file(content_hash);
CREATE INDEX ix_media_user ON media_file(user_id);
CREATE INDEX ix_media_status ON media_file(status);
CREATE INDEX ix_media_source_url ON media_file(source_url) WHERE source_url IS NOT NULL;
```

#### `video_segment` — 60s 窗口多模态片段（ASR+OCR 合并后）
```sql
CREATE TABLE video_segment (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id            UUID NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    segment_index       INTEGER NOT NULL,              -- 0-based
    start_ms            BIGINT NOT NULL,
    end_ms              BIGINT NOT NULL,
    transcript          TEXT,                          -- ASR 文本
    ocr_texts           TEXT[],                        -- 该窗口内所有 OCR 文本
    evidence_frames     JSONB DEFAULT '[]',            -- [{"frame_ms":, "minio_object":, "ocr_text":}]
    token_count         INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(media_id, segment_index)
);
CREATE INDEX ix_vs_media ON video_segment(media_id);
CREATE INDEX ix_vs_time ON video_segment(media_id, start_ms);
```

#### `transcription` — 视频全量转写记录
```sql
CREATE TABLE transcription (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id            UUID NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    full_text           TEXT NOT NULL,
    language            VARCHAR(16) DEFAULT 'zh',
    model_name          VARCHAR(64) NOT NULL,          -- whisper-large-v3
    duration_sec        REAL,                          -- 处理耗时
    chunk_count         INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(media_id)
);
CREATE INDEX ix_tr_media ON transcription(media_id);
```

#### `transcription_chunk` — 分段转写片段（支持断点续传）
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
    audio_object        VARCHAR(512),                    -- MinIO 音频片段对象
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

#### `frame_ocr` — 关键帧 OCR 记录
```sql
CREATE TABLE frame_ocr (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id            UUID NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    frame_ms            BIGINT NOT NULL,                 -- 帧时间戳
    minio_object        VARCHAR(512) NOT NULL,           -- 帧图片对象
    ocr_text            TEXT,
    phash               CHAR(16),                        -- 感知哈希（去重用）
    model_name          VARCHAR(64),                     -- paddle-ocr
    status              VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(media_id, frame_ms)
);
CREATE INDEX ix_fo_media ON frame_ocr(media_id);
CREATE INDEX ix_fo_phash ON frame_ocr(phash) WHERE phash IS NOT NULL;
```

#### `chunk` — RAG 检索块（向量+关键词双索引）
```sql
CREATE TABLE chunk (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    media_id            UUID NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    segment_id          UUID REFERENCES video_segment(id) ON DELETE SET NULL,
    chunk_index         INTEGER NOT NULL,
    content             TEXT NOT NULL,
    content_hash        CHAR(32) NOT NULL,               -- MD5 内容哈希（稳定引用锚点）
    token_count         INTEGER NOT NULL,
    start_ms            BIGINT,                          -- 对应视频时间戳
    end_ms              BIGINT,
    source_type         VARCHAR(16) NOT NULL,            -- asr/ocr/mixed
    qdrant_point_id     UUID,                            -- Qdrant 向量 ID (UUID v5)
    manifest_sha256     CHAR(64),                        -- ChunkManifest SHA256（可复现性）
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(media_id, chunk_index)
);
CREATE INDEX ix_chunk_media ON chunk(media_id);
CREATE INDEX ix_chunk_content_hash ON chunk(content_hash);
CREATE INDEX ix_chunk_qdrant_id ON chunk(qdrant_point_id) WHERE qdrant_point_id IS NOT NULL;
```

#### `knowledge_base` — 知识库（文档 RAG 扩展预留）
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

#### `document` — 知识库文档（预留）
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

### 2.3 任务域

#### `analysis_task` — Agent 分析任务
```sql
CREATE TABLE analysis_task (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    media_id            UUID NOT NULL REFERENCES media_file(id) ON DELETE CASCADE,
    goal                TEXT NOT NULL,                   -- 用户分析目标
    goal_hash           CHAR(64) NOT NULL,               -- SHA256(goal) 幂等键
    status              VARCHAR(32) NOT NULL DEFAULT 'pending',
                                                -- pending/running/planning/executing/critic_check/completed/failed
    current_round       INTEGER DEFAULT 0,
    max_rounds          INTEGER DEFAULT 2,
    final_result_json   JSONB,                           -- AnalysisResult 完整 JSON
    error_message       TEXT,
    trace_id            CHAR(32),                        -- 全链路 Trace ID
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

#### `agent_checkpoint` — AgentLoop 断点恢复（PostgreSQL 真源 + Redis 热缓存）
```sql
CREATE TABLE agent_checkpoint (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id             UUID NOT NULL REFERENCES analysis_task(id) ON DELETE CASCADE,
    trace_id            TEXT,                           -- W3C Trace ID（32位hex），日志/Trace 串联，与 task_id 并存
    round               INTEGER NOT NULL,
    phase               VARCHAR(32) NOT NULL,            -- planning/executing/critic_check
    agent_state_json    JSONB NOT NULL,                  -- AgentState 完整序列化
    video_context_ref   JSONB,                           -- VideoContext 引用（media_id + segment_ids）
    plan_json           JSONB,                           -- AgentPlan
    critique_json       JSONB,                           -- CriticResult
    result_json         JSONB,                           -- AnalysisResult (阶段性)
    feedback            TEXT,                            -- Critic 反馈
    required_timestamps BIGINT[],                        -- 需补证据的时间戳
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(task_id, round, phase)
);
CREATE INDEX ix_acp_task ON agent_checkpoint(task_id);
```

#### `agent_result` — 最终结构化分析结果
```sql
CREATE TABLE agent_result (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id             UUID NOT NULL REFERENCES analysis_task(id) ON DELETE CASCADE,
    title               VARCHAR(256) NOT NULL,
    conclusions_json    JSONB NOT NULL,                  -- [{"point":, "evidence_ids":[]}]
    evidence_json       JSONB NOT NULL,                  -- [{"timestamp_ms":, "source":, "content":, "chunk_id":}]
    suggestions_json    JSONB,                           -- 后续追问建议
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

### 2.4 RAG/Agent 追踪域

#### `rag_trace` — 检索链路追踪（评测/调试用）
```sql
CREATE TABLE rag_trace (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id             UUID REFERENCES analysis_task(id) ON DELETE SET NULL,
    user_id             UUID REFERENCES "user"(id) ON DELETE SET NULL,
    query               TEXT NOT NULL,
    rewritten_queries   JSONB,                           -- 子问题列表
    intent_path         JSONB,                           -- 意图树路径
    retrieval_channels  JSONB,                           -- 各通道原始结果
    fused_results       JSONB,                           -- RRF 融合后 TopK
    expanded_results    JSONB,                           -- ContextExpander 后
    reranked_results    JSONB,                           -- Rerank 后
    final_context       JSONB,                           -- 送入 LLM 的上下文
    latency_ms          INTEGER,                         -- 总耗时
    token_usage         JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_rt_task ON rag_trace(task_id);
CREATE INDEX ix_rt_user ON rag_trace(user_id);
CREATE INDEX ix_rt_created ON rag_trace(created_at);
```

#### `session` — 会话/追问上下文
```sql
CREATE TABLE session (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    media_id            UUID REFERENCES media_file(id) ON DELETE SET NULL,
    title               VARCHAR(256),
    context_summary     TEXT,                            -- 摘要压缩的历史上下文
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

### 2.5 异步任务域

#### `celery_task` — Celery 任务记录（可观测/重试/死信）
```sql
CREATE TABLE celery_task (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_name           VARCHAR(128) NOT NULL,           -- download_video_task 等
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

#### `ingestion_task` — 入库管线任务（视频处理各阶段）
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

### 2.6 扩展表（安全与计费，非核心 18 张内）

> 以下 4 张表为安全与计费扩展，**不计入核心 18 张表**，由 `core/auth`、`core/llm` 模块持有。

#### `api_key` — API Key 鉴权（见 SECURITY.md）
```sql
CREATE TABLE api_key (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    name            VARCHAR(64) NOT NULL,
    key_hash        TEXT NOT NULL,                       -- argon2id，验证用
    encrypted_key   TEXT NOT NULL,                      -- AES-GCM base64，管理界面显示解密用
    key_prefix      CHAR(12) NOT NULL,                   -- vk_live_xxxxxxxx 前缀检索
    key_suffix      CHAR(4),                            -- 后 4 位明文显示
    permissions     JSONB DEFAULT '[]'::jsonb,
    ip_whitelist    JSONB DEFAULT '[]'::jsonb,
    rate_limit      INT DEFAULT 1000,                    -- 每分钟
    env             VARCHAR(8) NOT NULL DEFAULT 'live',  -- live/test/dev
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    is_active       BOOLEAN DEFAULT TRUE
);
CREATE UNIQUE INDEX ux_api_key_prefix ON api_key(key_prefix) WHERE is_active = TRUE;
CREATE INDEX ix_api_key_user ON api_key(user_id);
```

#### `role` — 角色定义（RBAC，见 SECURITY.md）
```sql
CREATE TABLE role (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(32) NOT NULL UNIQUE,         -- viewer/user/analyst/admin
    description     VARCHAR(255),
    permissions     JSONB DEFAULT '[]'::jsonb,           -- 权限码列表
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

#### `user_role` — 用户-角色关联（user.roles 多对多）
```sql
CREATE TABLE user_role (
    user_id         UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    role_id         UUID NOT NULL REFERENCES role(id) ON DELETE CASCADE,
    granted_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, role_id)
);
CREATE INDEX ix_ur_role ON user_role(role_id);
```

#### `ai_call_logs` — AI 调用 Token 计费日志（见 MODEL-GATEWAY.md）
```sql
CREATE TABLE ai_call_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID REFERENCES "user"(id) ON DELETE SET NULL,  -- nullable 支持 API Key 调用
    api_key_id          UUID REFERENCES api_key(id) ON DELETE SET NULL,
    provider            VARCHAR(32) NOT NULL,            -- ollama/openai/deepseek/anthropic
    model               VARCHAR(64) NOT NULL,
    task_type           VARCHAR(16) NOT NULL,             -- thinking/normal/fast/embedding/rerank
    prompt_tokens       INT NOT NULL DEFAULT 0,
    completion_tokens   INT NOT NULL DEFAULT 0,
    total_tokens        INT NOT NULL DEFAULT 0,
    cost_usd            NUMERIC(10,6) DEFAULT 0,
    trace_id            TEXT,                            -- 串联日志
    status              VARCHAR(16) NOT NULL DEFAULT 'success',
    error_code          VARCHAR(64),
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_acl_user_time ON ai_call_logs(user_id, created_at);
CREATE INDEX ix_acl_provider_model ON ai_call_logs(provider, model, created_at);
CREATE INDEX ix_acl_created ON ai_call_logs(created_at);
```

> 以上 4 张扩展表承载安全（RBAC/API Key）与计费（Token 日志）职责，由 core/auth、core/llm 模块持有，核心 18 张表不含此 4 张。

---

## 3. 索引策略总结

| 表 | 核心索引 | 用途 |
|----|----------|------|
| `media_file` | `content_hash` (UNIQUE) | 内容级去重 |
| `media_file` | `user_id + status` | 用户视频列表筛选 |
| `video_segment` | `media_id + start_ms` | 时间范围查询 |
| `transcription_chunk` | `media_id + status` | 断点续传扫描未完成片段 |
| `frame_ocr` | `phash` | 感知哈希去重 |
| `chunk` | `content_hash` | 稳定引用锚点 |
| `chunk` | `qdrant_point_id` | 向量库双写一致性校验 |
| `analysis_task` | `media_id + goal_hash` (UNIQUE) | 幂等：同视频同目标不重复 |
| `agent_checkpoint` | `task_id + round + phase` | 断点恢复精确定位 |
| `celery_task` | `celery_id` | Celery 原生 ID 关联 |
| `rag_trace` | `task_id + created_at` | 链路回溯 |

---

## 4. 迁移策略

- **工具**：Alembic + `alembic.ini` + `env.py`（支持 `sync`/`async` 双模式）
- **命名**：`YYYYMMDD_HHMMSS_<short_desc>.py`
- **约定**：
  - 每次模型变更生成一条迁移，**不手写 SQL**，用 `op.*` API
  - 破坏性变更（Drop Column、Rename）分两步：先 Add 新列/表 → 数据迁移 → Drop 旧列
  - 索引变更单独迁移，`CONCURRENTLY` 避免锁表（PostgreSQL）
  - 种子数据用 `alembic upgrade head && python scripts/seed.py`

```bash
# 常用命令
alembic revision --autogenerate -m "add frame_ocr phash index"
alembic upgrade head
alembic downgrade -1
alembic history --verbose
```

---

## 5. Pydantic ↔ SQLAlchemy 映射规范

| 场景 | 方案 |
|------|------|
| **ORM → API 响应** | `model_dump(mode='json')` + `Config.from_attributes = True` |
| **API 请求 → ORM** | `model_dump(exclude_unset=True)` 仅更新传入字段 |
| **JSONB 字段** | SQLAlchemy `JSONB` + Pydantic `Dict[str, Any]` / 自定义 `TypeDecorator` |
| **数组字段** | PostgreSQL `ARRAY(Text)` + Pydantic `List[str]` |
| **枚举** | Python `StrEnum` + SQLAlchemy `Enum(StrEnum, native_enum=False)` |
| **UUID** | `uuid.UUID` 双向透传 |

```python
# 典型模式
class MediaFileRead(MediaFileBase):
    id: UUID
    created_at: datetime
    class Config: from_attributes = True

# Service 层
def to_read(orm: MediaFile) -> MediaFileRead:
    return MediaFileRead.model_validate(orm)
```

---

## 6. 关键数据完整性约束

| 约束 | 实现方式 |
|------|----------|
| **内容去重** | `media_file.content_hash` UNIQUE + 应用层上传前 `HEAD` 查询 |
| **幂等分析** | `analysis_task(media_id, goal_hash)` UNIQUE |
| **段序唯一** | `video_segment(media_id, segment_index)` UNIQUE |
| **Chunk 序唯一** | `chunk(media_id, chunk_index)` UNIQUE |
| **检查点唯一** | `agent_checkpoint(task_id, round, phase)` UNIQUE |
| **外键级联** | `ON DELETE CASCADE` 仅用于强所属关系，弱引用用 `SET NULL` |
| **软删除** | 核心表不物理删，`status='deleted'` + 视图过滤 |

---

> **关联文档**：[ARCHITECTURE.md](ARCHITECTURE.md) · [VIDEO-PIPELINE.md](VIDEO-PIPELINE.md) · [RAG-RETRIEVAL.md](RAG-RETRIEVAL.md) · [AGENT-LOOP.md](AGENT-LOOP.md) · [TASK-ORCHESTRATION.md](TASK-ORCHESTRATION.md)