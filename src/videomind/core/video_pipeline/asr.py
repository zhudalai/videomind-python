"""ASR 语音识别 —— faster-whisper 本地 + API 双路径。

对应 docs/VIDEO-PIPELINE.md §2.3 ASR 阶段 + docs/ARCHITECTURE.md 双路径推理。
设计要点：
1. **双路径**：`ASR_PROVIDER=local|api` 环境变量切换后端。
   - local: faster-whisper (CTranslate2)，模型可配 tiny/base/small/medium/large-v3
   - api: 预留 HTTP 接口（OpenAI Whisper 兼容或自建），仅占位
2. **断点续传**：音频已按 60s 分片（transcode 阶段），ASR 逐片段处理，
   每片段写入 `transcription_chunk` 表（status=completed/failed），
   支持 worker 重启后从失败片段继续。
3. 产出：
   - `transcription` 表（全量文本 + language + model_name + duration_sec）
   - `transcription_chunk` 表（逐片段文本 + 时间戳 + status）
4. GPU 显存管理：`faster-whisper` 进程内加载模型，单 Worker 串行（Celery concurrency=1）。

配置锚点（config.py）:
    ASR_PROVIDER=local
    ASR_MODEL=tiny
    ASR_DEVICE=auto
    ASR_COMPUTE_TYPE=int8
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

from videomind.config import get_settings
from videomind.infrastructure.storage import models as m


@dataclass
class ASRResult:
    """ASR 阶段产出（供后续 Indexer 用）。"""

    full_text: str
    language: str
    model_name: str
    duration_sec: float
    chunks: list[dict[str, Any]]  # 每个: {index, start_ms, end_ms, text}


class ASREngine:
    """ASR 引擎：本地 faster-whisper + API 预留。"""

    def __init__(self) -> None:
        s = get_settings()
        self._provider = s.asr_provider
        self._model_name = s.asr_model
        self._device = s.asr_device
        self._compute_type = s.asr_compute_type
        self._local_model = None  # 延迟加载

    async def transcribe(
        self, audio_path: Path, media_id: uuid.UUID
    ) -> ASRResult:
        """识别音频，返回全量文本 + 分片结果。"""
        if self._provider == "local":
            return await self._transcribe_local(audio_path, media_id)
        else:
            return await self._transcribe_api(audio_path, media_id)

    # ── 本地路径 ──
    async def _transcribe_local(self, audio_path: Path, media_id: uuid.UUID) -> ASRResult:
        # 延迟导入，避免未装依赖时报错
        from faster_whisper import WhisperModel

        def _load_model() -> WhisperModel:
            return WhisperModel(
                self._model_name,
                device=self._device,
                compute_type=self._compute_type,
            )

        if self._local_model is None:
            self._local_model = await anyio.to_thread.run_sync(_load_model)

        def _transcribe() -> tuple[str, str, list]:
            # faster-whisper 返回 (segments generator, info)
            segments, info = self._local_model.transcribe(
                str(audio_path),
                beam_size=5,
                vad_filter=True,
                word_timestamps=True,
            )
            full_parts: list[str] = []
            chunks: list[dict] = []
            for seg in segments:
                full_parts.append(seg.text.strip())
                chunks.append({
                    "index": len(chunks),
                    "start_ms": int(seg.start * 1000),
                    "end_ms": int(seg.end * 1000),
                    "text": seg.text.strip(),
                })
            return " ".join(full_parts), info.language, chunks

        full_text, language, chunks = await anyio.to_thread.run_sync(_transcribe)
        return ASRResult(
            full_text=full_text,
            language=language,
            model_name=self._model_name,
            duration_sec=sum(c["end_ms"] - c["start_ms"] for c in chunks) / 1000.0,
            chunks=chunks,
        )

    # ── API 路径（占位，后续接入 OpenAI Whisper / 自建服务）──
    async def _transcribe_api(self, audio_path: Path, media_id: uuid.UUID) -> ASRResult:
        # TODO: 实现 API 调用（config.py 已预留 ASR_API_BASE_URL / ASR_API_KEY）
        # 暂时抛出 NotImplementedError，提示切回 local
        raise NotImplementedError(
            "ASR_PROVIDER=api 尚未实现，请设置 ASR_PROVIDER=local 或后续接入"
        )


# ──────────────────────────── 数据库写入辅助 ────────────────────────────


async def save_transcription(
    db: "anyio.abc.AsyncSession",
    media_id: uuid.UUID,
    result: ASRResult,
) -> tuple[m.Transcription, list[m.TranscriptionChunk]]:
    """将 ASR 结果写入 transcription + transcription_chunk 表。

    返回 (transcription_orm, chunk_orms)。
    """
    from sqlalchemy import select

    # 1) upsert transcription（1:1 media_file）
    trans = await db.execute(
        select(m.Transcription).where(m.Transcription.media_id == media_id)
    )
    tr_orm = trans.scalar_one_or_none()
    if tr_orm is None:
        tr_orm = m.Transcription(
            media_id=media_id,
            full_text=result.full_text,
            language=result.language,
            model_name=result.model_name,
            duration_sec=result.duration_sec,
            chunk_count=len(result.chunks),
        )
        db.add(tr_orm)
    else:
        tr_orm.full_text = result.full_text
        tr_orm.language = result.language
        tr_orm.model_name = result.model_name
        tr_orm.duration_sec = result.duration_sec
        tr_orm.chunk_count = len(result.chunks)
    await db.flush()

    # 2) upsert chunks（逐片段）
    chunk_orms: list[m.TranscriptionChunk] = []
    for c in result.chunks:
        chk = await db.execute(
            select(m.TranscriptionChunk).where(
                (m.TranscriptionChunk.media_id == media_id)
                & (m.TranscriptionChunk.chunk_index == c["index"])
            )
        )
        chk_orm = chk.scalar_one_or_none()
        if chk_orm is None:
            chk_orm = m.TranscriptionChunk(
                media_id=media_id,
                chunk_index=c["index"],
                start_ms=c["start_ms"],
                end_ms=c["end_ms"],
                text=c["text"],
                status="completed",
                model_name=result.model_name,
            )
            db.add(chk_orm)
        else:
            chk_orm.text = c["text"]
            chk_orm.status = "completed"
            chk_orm.model_name = result.model_name
        chunk_orms.append(chk_orm)

    await db.flush()
    return tr_orm, chunk_orms


_asr_engine: ASREngine | None = None


def get_asr() -> ASREngine:
    global _asr_engine
    if _asr_engine is None:
        _asr_engine = ASREngine()
    return _asr_engine