"""全局配置 —— pydantic-settings v2。

设计要点（对应 docs/ARCHITECTURE.md 与 docs/DEPLOYMENT.md）：
1. **双路径推理**：ASR / OCR / Embedding 三类推理各有一个 `*_PROVIDER` env
   （取值 `local` | `api`），同一份接口、env 切后端实现 —— 这是本作品集的
   架构亮点之一（求职面试可讲：为什么不用单路径，双路径的代价与收益）。
2. 所有配置从 `.env` 注入；`Settings()` 在应用生命周期单例化（见 `get_settings`）。
3. 部分字段做了 `model_validator` 约束校验（如 provider 取值合法性）。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置。字段名与 `.env` 变量名一一对应（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── 应用 ──
    app_name: str = "videomind"
    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    secret_key: str = Field(min_length=16)

    # ── PostgreSQL ──
    database_url: str = "postgresql+asyncpg://videomind:videomind@localhost:15432/videomind"
    postgres_user: str = "videomind"
    postgres_password: str = "videomind"
    postgres_db: str = "videomind"

    # ── Redis / Celery ──
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    redis_password: str = ""
    gpu_lock_ttl: int = 30

    # ── Qdrant ──
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "videomind_chunks"

    # ── MinIO ──
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "videomind"
    minio_secure: bool = False

    # ── 推理双路径（核心架构特性）──
    # local | api：同一接口，env 切后端。详见各 core/* 模块工厂函数。
    asr_provider: Literal["local", "api"] = "local"
    ocr_provider: Literal["local", "api"] = "local"
    embedding_provider: Literal["local", "api"] = "local"

    # ── 本地推理配置 ──
    asr_model: str = "tiny"
    asr_device: Literal["auto", "cuda", "cpu"] = "auto"
    asr_compute_type: str = "int8"
    # ASR 初始热词提示：透传 faster-whisper initial_prompt，偏置领域词/专有名词识别。
    # 多语言中立：默认留空（engine 实现里空 → None，不留下语言偏置）；
    # 配成中/英混编热词串可显著提升专有名词召回。
    asr_initial_prompt: str = ""
    ocr_use_gpu: bool = False
    ocr_lang: str = "ch"
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: Literal["auto", "cuda", "cpu"] = "cpu"
    embedding_dim: int = 1024

    # ── API 路径推理配置（provider=api 时生效）──
    asr_api_base_url: str = ""
    asr_api_key: str = ""
    # ASR API 模型名（默认 Groq 上的 whisper-large-v3-turbo，与本地档位一致以便对比）
    asr_api_model: str = "whisper-large-v3-turbo"
    ocr_api_base_url: str = ""
    ocr_api_key: str = ""
    embedding_api_base_url: str = ""
    embedding_api_key: str = ""
    embedding_api_model: str = ""

    # ── ASR 加标点后处理（方案 2，opt-in）──
    # Whisper 系列在中文上不产标点，用轻量 LLM 给 full_text 补标点恢复可读性。
    # 独立于 LLM_* 通用档：加标点用专属模型配额池，默认 OpenRouter 免费 ling。
    # 默认关：opt-in，不破坏现有 ASR 行为；改字/异常自动回退裸原文（punctuate.py 守护）。
    asr_punctuate: bool = False
    asr_punctuate_base_url: str = ""
    asr_punctuate_api_key: str = ""
    # 默认 OpenRouter inclusionai/ling-3.0-flash:free（实测 6.83s、防改字通过、无限流）
    # 注意：free 档多为 reasoning 模型，max_tokens 须给足（思考吃 token，实测需 ≥4096，默认 8192）
    asr_punctuate_model: str = "inclusionai/ling-3.0-flash:free"
    asr_punctuate_max_tokens: int = 8192
    asr_punctuate_temperature: float = 0.1

    # ── LLM 通用配置（Model Gateway / Agent / Intent 共用，不绑 provider 字段）──
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash-free"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096
    llm_timeout_s: float = 60.0
    llm_first_packet_timeout_s: float = 10.0

    # ── 视频处理 ──
    video_segment_window_ms: int = 60000
    video_chunk_overlap_ms: int = 1000
    ffmpeg_path: str = "ffmpeg"

    # ── 下载 ──
    download_dir: str = "./_downloads"
    ytdlp_proxy: str = ""

    # ───────────────────────── 校验 ─────────────────────────
    @field_validator("asr_provider", "ocr_provider", "embedding_provider")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        if v not in ("local", "api"):
            raise ValueError(f"provider 必须 local 或 api，收到 {v!r}")
        return v

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"

    @property
    def redis_url_for_celery(self) -> str:
        """Celery 用独立的 DB index（1=broker,2=result），避免与缓存 DB0 争用。"""
        return self.celery_broker_url


@lru_cache
def get_settings() -> Settings:
    """获取单例 Settings。FastAPI Depends 与普通代码都可用。

    lru_cache 保证同进程只构造一次；测试中可用 `get_settings.cache_clear()` 重置。
    """
    return Settings()  # type: ignore[call-arg]
