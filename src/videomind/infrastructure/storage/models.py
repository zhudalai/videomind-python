"""SQLAlchemy 2.0 ORM 模型 —— 与 docs/DATA-MODEL.md 严格一一对应。

表清单（18 核心 + 4 扩展）：
    2.1 用户认证域  : user, user_ai_config, membership                    (3)
    2.2 媒体知识域  : media_file, video_segment, transcription,
                     transcription_chunk, frame_ocr, chunk,
                     knowledge_base, document                              (8)
    2.3 任务域      : analysis_task, agent_checkpoint, agent_result        (3)
    2.4 RAG 追踪域  : rag_trace, session                                   (2)
    2.5 异步任务域  : celery_task, ingestion_task                           (2)
    ───────────────────────── 核心合计：18 张 ─────────────────────────
    2.6 扩展(安全/计费): api_key, role, user_role, ai_call_logs             (4) 非核心18张内

迁移用 Alembic 据此 autogenerate；接口契约见 docs/DATA-MODEL.md §5 Pydantic↔ORM。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from videomind.infrastructure.storage.database import Base

# ──────────────────────────── StrEnum ────────────────────────────
# 对应 docs/DATA-MODEL.md §5「枚举 = StrEnum + SQLAlchemy(Enum(..., native_enum=False))」。


class MediaStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    TRANSCRIBING = "transcribing"
    OCR = "ocr"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class SourceType(StrEnum):
    UPLOAD = "upload"
    URL = "url"


class ChunkSourceType(StrEnum):
    ASR = "asr"
    OCR = "ocr"
    MIXED = "mixed"


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PLANNING = "planning"
    EXECUTING = "executing"
    CRITIC_CHECK = "critic_check"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentPhase(StrEnum):
    PLANNING = "planning"
    EXECUTING = "executing"
    CRITIC_CHECK = "critic_check"


class CeleryStatus(StrEnum):
    PENDING = "pending"
    STARTED = "started"
    RETRY = "retry"
    SUCCESS = "success"
    FAILURE = "failure"


class ChunkTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class IngestionStage(StrEnum):
    DOWNLOAD = "download"
    TRANSCRIBE = "transcribe"
    OCR = "ocr"
    BUILD_CONTEXT = "build_context"
    INDEX = "index"


# ──────────────────────────── 2.1 用户认证域 ────────────────────────────


class User(Base):
    __tablename__ = "user"  # PostgreSQL 保留字，SQLAlchemy 自动 quote

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # 字段级加密（EncryptedString，见 SECURITY.md DataEncryption）—— 密文以字符串存储
    phone: Mapped[str | None] = mapped_column(String(64))
    real_name: Mapped[str | None] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(256))  # bcrypt
    full_name: Mapped[str | None] = mapped_column(String(128))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserAIConfig(Base):
    __tablename__ = "user_ai_config"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))  # ollama/openai/anthropic/custom
    model_name: Mapped[str] = mapped_column(String(128))
    api_key_encrypted: Mapped[bytes | None] = mapped_column(BYTEA)  # AES-GCM 密文
    api_key_nonce: Mapped[bytes | None] = mapped_column(BYTEA)  # GCM nonce
    api_base_url: Mapped[str | None] = mapped_column(String(512))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    embedding_provider: Mapped[str | None] = mapped_column(String(32))
    asr_model: Mapped[str | None] = mapped_column(String(64))
    ocr_model: Mapped[str | None] = mapped_column(String(64))
    temperature: Mapped[float] = mapped_column(Float, default=0.3)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "provider", "model_name", name="uq_uac_provider_model"),
    )


class UserConfig(Base):
    """用户偏好配置（主题 / 默认模型 / 语言）。auth 前 dev bootstrap 用户一行。

    与 UserAIConfig（LLM provider 凭证）正交：此表只管 UI 偏好。
    """

    __tablename__ = "user_config"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), unique=True, index=True
    )
    theme: Mapped[str] = mapped_column(String(16), default="system")  # light | dark | system
    default_model: Mapped[str | None] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Membership(Base):
    __tablename__ = "membership"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    tier: Mapped[str] = mapped_column(String(16), default="free")  # free/pro/enterprise
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/expired/cancelled
    # 月度 token 配额（与 MODEL-GATEWAY TokenAccounting 口径一致）
    monthly_token_quota: Mapped[int] = mapped_column(BigInteger, default=5_000_000)
    monthly_token_used: Mapped[int] = mapped_column(BigInteger, default=0)
    quota_reset_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(128))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ──────────────────────────── 2.2 媒体知识域 ────────────────────────────


class MediaFile(Base):
    __tablename__ = "media_file"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), index=True, nullable=True
    )
    source_type: Mapped[str] = mapped_column(String(16))  # upload/url
    source_url: Mapped[str | None] = mapped_column(Text)  # 原始 URL（去重键）
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA256
    filename: Mapped[str] = mapped_column(String(256))
    mime_type: Mapped[str] = mapped_column(String(64))
    file_size: Mapped[int] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    minio_bucket: Mapped[str] = mapped_column(String(64))
    minio_object: Mapped[str] = mapped_column(String(512))
    thumbnail_object: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    meta_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_media_source_url",
            "source_url",
            postgresql_where=text("source_url IS NOT NULL"),
        ),
    )


class VideoSegment(Base):
    """60s 窗口多模态片段（ASR+OCR 合并后）。"""

    __tablename__ = "video_segment"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_file.id", ondelete="CASCADE"), index=True
    )
    segment_index: Mapped[int] = mapped_column(Integer)  # 0-based
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    transcript: Mapped[str | None] = mapped_column(Text)
    ocr_texts: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    evidence_frames: Mapped[list] = mapped_column(JSONB, default=list)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("media_id", "segment_index", name="uq_vs_media_index"),
        Index("ix_vs_time", "media_id", "start_ms"),
    )


class Transcription(Base):
    """视频全量转写记录（1:1 media_file）。"""

    __tablename__ = "transcription"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_file.id", ondelete="CASCADE"), index=True
    )
    full_text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16), default="zh")
    model_name: Mapped[str] = mapped_column(String(64))  # whisper-large-v3
    duration_sec: Mapped[float | None] = mapped_column(Float)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("media_id", name="uq_tr_media"),)


class TranscriptionChunk(Base):
    """分段转写片段（支持断点续传）。"""

    __tablename__ = "transcription_chunk"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_file.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    audio_object: Mapped[str | None] = mapped_column(String(512))  # MinIO 音频片段
    model_name: Mapped[str | None] = mapped_column(String(64))
    duration_sec: Mapped[float | None] = mapped_column(Float)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("media_id", "chunk_index", name="uq_tc_media_index"),
    )


class FrameOCR(Base):
    """关键帧 OCR 记录。"""

    __tablename__ = "frame_ocr"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_file.id", ondelete="CASCADE"), index=True
    )
    frame_ms: Mapped[int] = mapped_column(BigInteger)
    minio_object: Mapped[str] = mapped_column(String(512))
    ocr_text: Mapped[str | None] = mapped_column(Text)
    phash: Mapped[str | None] = mapped_column(String(16))  # 感知哈希（去重用）
    model_name: Mapped[str | None] = mapped_column(String(64))  # paddle-ocr
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("media_id", "frame_ms", name="uq_fo_media_frame"),
        Index("ix_fo_phash", "phash", postgresql_where=text("phash IS NOT NULL")),
    )


class Chunk(Base):
    """RAG 检索块（向量+关键词双索引）。

    content_hash 是 MD5 (CHAR32)，作为稳定引用锚点；
    qdrant_point_id 是 UUID v5，双写一致性校验用。
    """

    __tablename__ = "chunk"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_file.id", ondelete="CASCADE"), index=True
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_segment.id", ondelete="SET NULL")
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(32), index=True)  # MD5
    token_count: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int | None] = mapped_column(BigInteger)
    end_ms: Mapped[int | None] = mapped_column(BigInteger)
    source_type: Mapped[str] = mapped_column(String(16))  # asr/ocr/mixed
    qdrant_point_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("media_id", "chunk_index", name="uq_chunk_media_index"),
        Index(
            "ix_chunk_qdrant_id",
            "qdrant_point_id",
            postgresql_where=text("qdrant_point_id IS NOT NULL"),
        ),
    )


class KnowledgeBase(Base):
    """知识库（文档 RAG 扩展预留）。"""

    __tablename__ = "knowledge_base"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(String(128))
    chunk_size: Mapped[int] = mapped_column(Integer, default=800)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=120)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Document(Base):
    """知识库文档（预留）。"""

    __tablename__ = "document"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_base.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(16))
    source_url: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str | None] = mapped_column(String(256))
    mime_type: Mapped[str | None] = mapped_column(String(64))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    minio_object: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ──────────────────────────── 2.3 任务域 ────────────────────────────


class AnalysisTask(Base):
    """Agent 分析任务。goal_hash 唯一 → 同视频同目标幂等。"""

    __tablename__ = "analysis_task"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_file.id", ondelete="CASCADE"), index=True
    )
    goal: Mapped[str] = mapped_column(Text)
    goal_hash: Mapped[str] = mapped_column(String(64))  # SHA256(goal)
    media_ids_hash: Mapped[str] = mapped_column(String(64), index=True)
    # 多视频幂等键：sha256(sorted media_ids 逗号连接)；单视频 == sha256(str(media_id))。
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    max_rounds: Mapped[int] = mapped_column(Integer, default=2)
    final_result_json: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(32))  # 全链路 Trace ID (hex)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("media_ids_hash", "goal_hash", name="uq_at_mediaids_goal"),
    )


class AnalysisTaskMedia(Base):
    """AnalysisTask ↔ MediaFile 关联表（多视频支持）。

    单条记录表示一个 media 参与一个分析任务；position=0 为 primary media。
    """

    __tablename__ = "analysis_task_media"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_task.id", ondelete="CASCADE"), index=True
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_file.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)  # 0 = primary
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("task_id", "media_id", name="uq_atm_task_media"),
    )


class AgentCheckpoint(Base):
    """AgentLoop 断点恢复（PostgreSQL 真源 + Redis 热缓存）。

    task_id UUID 是 analysis_task.id 外键；trace_id TEXT 是 W3C Trace ID（32位hex），
    两者并存 —— task_id 用于 DB 关系完整性，trace_id 用于日志/Trace 串联。
    """

    __tablename__ = "agent_checkpoint"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_task.id", ondelete="CASCADE"),
        index=True,
    )
    trace_id: Mapped[str | None] = mapped_column(Text)  # W3C 32位hex，日志串联
    round: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(32))  # planning/executing/critic_check
    agent_state_json: Mapped[dict] = mapped_column(JSONB)
    video_context_ref: Mapped[dict | None] = mapped_column(JSONB)
    plan_json: Mapped[dict | None] = mapped_column(JSONB)
    critique_json: Mapped[dict | None] = mapped_column(JSONB)
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    feedback: Mapped[str | None] = mapped_column(Text)
    required_timestamps: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("task_id", "round", "phase", name="uq_acp_task_round_phase"),
    )


class AgentResult(Base):
    """最终结构化分析结果。"""

    __tablename__ = "agent_result"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_task.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(256))
    conclusions_json: Mapped[list] = mapped_column(JSONB)
    evidence_json: Mapped[list] = mapped_column(JSONB)
    suggestions_json: Mapped[list | None] = mapped_column(JSONB)
    critic_passed: Mapped[bool] = mapped_column(Boolean)
    critic_feedback: Mapped[str | None] = mapped_column(Text)
    total_rounds: Mapped[int] = mapped_column(Integer)
    token_usage: Mapped[dict | None] = mapped_column(JSONB)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ──────────────────────────── 2.4 RAG/Agent 追踪域 ────────────────────────────


class RagTrace(Base):
    """检索链路追踪（评测/调试用）。"""

    __tablename__ = "rag_trace"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_task.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    query: Mapped[str] = mapped_column(Text)
    rewritten_queries: Mapped[list | None] = mapped_column(JSONB)
    intent_path: Mapped[dict | None] = mapped_column(JSONB)
    retrieval_channels: Mapped[dict | None] = mapped_column(JSONB)
    fused_results: Mapped[list | None] = mapped_column(JSONB)
    expanded_results: Mapped[list | None] = mapped_column(JSONB)
    reranked_results: Mapped[list | None] = mapped_column(JSONB)
    final_context: Mapped[dict | None] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    token_usage: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Session(Base):
    """会话/追问上下文。"""

    __tablename__ = "session"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    media_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_file.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(256))
    context_summary: Mapped[str | None] = mapped_column(Text)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ──────────────────────────── 2.5 异步任务域 ────────────────────────────


class CeleryTask(Base):
    """Celery 任务记录（可观测/重试/死信）。"""

    __tablename__ = "celery_task"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    task_name: Mapped[str] = mapped_column(String(128), index=True)
    celery_id: Mapped[str] = mapped_column(String(64), unique=True)
    args_json: Mapped[dict | None] = mapped_column(JSONB)
    kwargs_json: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    traceback: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    queue_name: Mapped[str | None] = mapped_column(String(64))
    worker_hostname: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IngestionTask(Base):
    """入库管线任务（视频处理各阶段）。"""

    __tablename__ = "ingestion_task"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_file.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32))  # download/transcribe/ocr/build_context/index
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    celery_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("celery_task.id", ondelete="SET NULL")
    )
    input_json: Mapped[dict | None] = mapped_column(JSONB)
    output_json: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("media_id", "stage", name="uq_it_media_stage"),)


# ──────────────────────────── 2.6 扩展表（安全/计费，非核心 18 张内）────────────────────────────
# 由 core/auth、core/llm 模块持有；此处定义以承载钩子完整性。


class ApiKey(Base):
    """API Key 鉴权（见 SECURITY.md）。"""

    __tablename__ = "api_key"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    key_hash: Mapped[str] = mapped_column(Text)  # argon2id
    encrypted_key: Mapped[str] = mapped_column(Text)  # AES-GCM base64
    key_prefix: Mapped[str] = mapped_column(String(12))  # vk_live_xxxxxxxx
    key_suffix: Mapped[str | None] = mapped_column(String(4))
    permissions: Mapped[list] = mapped_column(JSONB, default=list)
    ip_whitelist: Mapped[list] = mapped_column(JSONB, default=list)
    rate_limit: Mapped[int] = mapped_column(Integer, default=1000)  # 每分钟
    env: Mapped[str] = mapped_column(String(8), default="live")  # live/test/dev
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index(
            "ux_api_key_prefix",
            "key_prefix",
            unique=True,
            postgresql_where=text("is_active = TRUE"),
        ),
    )


class Role(Base):
    """角色定义（RBAC，见 SECURITY.md）。"""

    __tablename__ = "role"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(32), unique=True)  # viewer/user/analyst/admin
    description: Mapped[str | None] = mapped_column(String(255))
    permissions: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UserRole(Base):
    """用户-角色关联（支撑 user.roles 多对多）。"""

    __tablename__ = "user_role"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("role.id", ondelete="CASCADE"),
        primary_key=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_ur_role", "role_id"),)


class AiCallLog(Base):
    """AI 调用 Token 计费日志（见 MODEL-GATEWAY.md）。"""

    __tablename__ = "ai_call_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_key.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    task_type: Mapped[str] = mapped_column(String(16))  # thinking/normal/fast/embedding/rerank
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    trace_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="success")
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_acl_user_time", "user_id", "created_at"),
        Index("ix_acl_provider_model", "provider", "model", "created_at"),
        Index("ix_acl_created", "created_at"),
    )


__all__ = [
    # 枚举
    "MediaStatus",
    "SourceType",
    "ChunkSourceType",
    "AnalysisStatus",
    "AgentPhase",
    "CeleryStatus",
    "ChunkTaskStatus",
    "IngestionStage",
    # 2.1 用户认证域
    "User",
    "UserAIConfig",
    "UserConfig",
    "Membership",
    # 2.2 媒体知识域
    "MediaFile",
    "VideoSegment",
    "Transcription",
    "TranscriptionChunk",
    "FrameOCR",
    "Chunk",
    "KnowledgeBase",
    "Document",
    # 2.3 任务域
    "AnalysisTask",
    "AgentCheckpoint",
    "AgentResult",
    # 2.4 RAG 追踪域
    "RagTrace",
    "Session",
    # 2.5 异步任务域
    "CeleryTask",
    "IngestionTask",
    # 2.6 扩展表（安全/计费）
    "ApiKey",
    "Role",
    "UserRole",
    "AiCallLog",
]
