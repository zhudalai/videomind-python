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
        # initial_prompt：空串 → None（多语言中立，不留下语言偏置）；
        # 配了热词 → 原样透传，偏置领域词识别。
        self._initial_prompt: str | None = s.asr_initial_prompt.strip() or None
        self._local_model = None  # 延迟加载
        # API 路径（provider=api 时生效，对接 Groq/OpenAI 兼容 transcription）
        self._api_base_url = s.asr_api_base_url
        self._api_key = s.asr_api_key
        self._api_model = s.asr_api_model

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
            # 质量关键参数（对应 asr_unit 契约测试）：
            # - condition_on_previous_text=False：faster-whisper 默认 True 会用上一段
            #   文本当下文，跨段幻觉传染（多语言/小模型上乱字串主因），显式关闭。
            # - vad_filter=True：保留 VAD，静音段不喂模型（减幻觉 + 加速）。
            # - language=None：保持自动检测（多语言契约，禁止硬编码单一语言）。
            # - initial_prompt：None=多语言中立；配了热词则偏置领域词识别。
            segments, info = self._local_model.transcribe(
                str(audio_path),
                beam_size=5,
                vad_filter=True,
                word_timestamps=True,
                condition_on_previous_text=False,
                language=None,
                initial_prompt=self._initial_prompt,
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

    # ── API 路径（Groq OpenAI 兼容 transcription，作 provider=api 后备）──
    async def _transcribe_api(self, audio_path: Path, media_id: uuid.UUID) -> ASRResult:
        """通过 Groq/OpenAI 兼容 transcription 端点识别（与 local 路径产出同 shape）。

        对接端点：POST {base_url}/audio/transcriptions
          - multipart：file=音频字节，model=asr_api_model，response_format=verbose_json
          - 多语言中立：不传 language（让 Groq/whisper 自动检测），prompt 空则不传
          - verbose_json 含 segments 时间戳 → 组装回 ASRResult.chunks
        """
        if not self._api_base_url or not self._api_key:
            raise RuntimeError(
                "ASR_PROVIDER=api 需配置 ASR_API_BASE_URL 与 ASR_API_KEY"
                "（Groq：https://api.groq.com/openai/v1 + console.groq.com 申请的 key）"
            )
        import httpx

        audio_bytes = await anyio.to_thread.run_sync(audio_path.read_bytes)
        url, data, files = _build_groq_request(
            self._api_base_url,
            audio_bytes,
            audio_path.name,
            self._api_model,
            self._initial_prompt,
        )
        # 转录可能较长（大音频 + 推理），给 300s 上限；connect 10s
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=httpx.Timeout(300.0, connect=10.0),
        ) as client:
            r = await client.post(url, data=data, files=files)
            r.raise_for_status()
            return _parse_groq_response(r.json(), model_name=self._api_model)


# ──────────────────────────── Groq API 请求/响应辅助 ────────────────────────────


def _build_groq_request(
    base_url: str,
    audio_bytes: bytes,
    filename: str,
    model: str,
    prompt: str | None,
) -> tuple[str, dict[str, str], dict[str, tuple[str, bytes, str]]]:
    """构建 Groq/OpenAI 兼容 transcription 请求的 (url, data, files)。

    - url = {base_url 去尾斜杠}/audio/transcriptions
    - data: model + response_format=verbose_json（拿 segments 时间戳）；prompt 非空才传
      多语言中立：不传 language，让 Groq/whisper 自动检测
    - files: httpx multipart 三元组，filename 透传（Groq 按扩展名判音频格式）
    - 注意：Content-Type 不在此设 —— httpx files= 会自动加 boundary，手设反而缺
      boundary 致 400；Authorization 由 AsyncClient 默认 headers 带
    """
    url = base_url.rstrip("/") + "/audio/transcriptions"
    data: dict[str, str] = {"model": model, "response_format": "verbose_json"}
    if prompt:
        data["prompt"] = prompt
    files = {"file": (filename, audio_bytes, "audio/wav")}
    return url, data, files


def _parse_groq_response(data: dict[str, Any], model_name: str) -> ASRResult:
    """解析 Groq verbose_json 响应回 ASRResult（与 local 路径同 shape）。

    verbose_json 顶层：text / language / duration(+ segments[{start,end,text}, ...])
    - start/end 秒 → chunks 毫秒（对齐 local 路径 seg.start*1000）
    - duration 缺失 → 用最后一段 end 兜底，再缺 → 0.0
    - language：Groq 返回全称（如 'chinese'），local 返回短码（如 'zh'）；provider 差异，
      直接透传，前端只展示
    """
    full_text = data.get("text") or ""
    language = data.get("language") or ""
    segs = data.get("segments") or []
    chunks: list[dict[str, Any]] = []
    for i, seg in enumerate(segs):
        chunks.append({
            "index": i,
            "start_ms": int(round((seg.get("start") or 0.0) * 1000)),
            "end_ms": int(round((seg.get("end") or 0.0) * 1000)),
            "text": (seg.get("text") or "").strip(),
        })
    duration_sec = data.get("duration")
    if duration_sec is None:
        duration_sec = (chunks[-1]["end_ms"] / 1000.0) if chunks else 0.0
    return ASRResult(
        full_text=full_text,
        language=language,
        model_name=model_name,
        duration_sec=float(duration_sec),
        chunks=chunks,
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