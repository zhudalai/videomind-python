"""OCR 文字识别 —— PaddleOCR 本地 + API 双路径。

对应 docs/VIDEO-PIPELINE.md §2.4 OCR 阶段 + docs/ARCHITECTURE.md 双路径推理。
设计要点：
1. **双路径**：`OCR_PROVIDER=local|api` 环境变量切换后端。
   - local: PaddleOCR，GPU/CPU 可配（`OCR_USE_GPU`）
   - api: 预留 HTTP 接口，仅占位
2. **输入**：转码阶段产出的关键帧（1fps JPG）在 MinIO；
   OCR 阶段从 MinIO 下载 → 本地识别 → 写入 `frame_ocr` 表。
3. **去重**：感知哈希（phash）去重，同一视频相似帧只识别一次。
4. **产出**：`frame_ocr` 表记录（frame_ms, minio_object, ocr_text, phash, model_name, status）。

配置锚点（config.py）:
    OCR_PROVIDER=local
    OCR_USE_GPU=false
    OCR_LANG=ch
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncio
import anyio
import os

# ──────────────────────────── PaddlePaddle 兼容性垫片 ────────────────────────────
# 在某些 CPU + oneDNN 环境下，PaddlePaddle 3.x 的 PIR（PIR = Paddle IR）
# 执行器会抛 NotImplementedError: ConvertPirAttribute2RuntimeAttribute …
# 必须在 paddle/paddleocr import 之前设置这些 FLAGS，否则框架已固化其 executor。
os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_enable_new_executor", "1")  # 走新版 executor
# 关闭 PaddleOCR 的多语言包下载提示（消除用户态噪声）
os.environ.setdefault("DISABLE_PADDLEOCR_DOWNLOAD", "1")

from videomind.config import get_settings
from videomind.infrastructure.media.minio import get_minio_client
from videomind.infrastructure.storage import models as m


@dataclass
class OCRResult:
    """单帧 OCR 结果。"""

    frame_ms: int
    minio_object: str
    ocr_text: str | None
    phash: str | None
    model_name: str


class OCREngine:
    """OCR 引擎：本地 PaddleOCR + API 预留。"""

    def __init__(self) -> None:
        s = get_settings()
        self._provider = s.ocr_provider
        self._use_gpu = s.ocr_use_gpu
        self._lang = s.ocr_lang
        self._local_model = None  # 延迟加载

    async def recognize_frames(
        self, media_id: uuid.UUID, frame_keys: list[str]
    ) -> list[OCRResult]:
        """批量识别一组关键帧。

        Args:
            media_id: 视频 ID
            frame_keys: MinIO object keys（如 `content_hash/frames/00012345.jpg`）

        Returns:
            OCRResult 列表（顺序对应 frame_keys）
        """
        if self._provider == "local":
            return await self._recognize_local(media_id, frame_keys)
        else:
            return await self._recognize_api(media_id, frame_keys)

    # ── 本地路径 ──
    async def _recognize_local(
        self, media_id: uuid.UUID, frame_keys: list[str]
    ) -> list[OCRResult]:
        from paddleocr import PaddleOCR

        def _load_model() -> PaddleOCR:
            # PaddleOCR 3.x API - 使用新的参数格式
            return PaddleOCR(
                lang=self._lang,
                use_doc_orientation_classify=False,  # 替代 use_angle_cls
                use_doc_unwarping=False,
                use_textline_orientation=False,
                # use_gpu 在新版本中不再支持，需要通过环境变量或其他方式控制
            )

        if self._local_model is None:
            self._local_model = await anyio.to_thread.run_sync(_load_model)

        minio = get_minio_client()
        results: list[OCRResult] = []
        seen_phashes: set[str] = set()

        async def _process_one(key: str) -> OCRResult:
            # 1) 下载帧到本地临时
            local = Path(f"/tmp/ocr_{media_id}_{Path(key).name}")
            await minio.download_file(key, local)

            try:
                # 2) 计算 phash（去重）
                phash = await _compute_phash(local)
                if phash in seen_phashes:
                    # 重复帧：复用上一帧结果
                    return OCRResult(
                        frame_ms=_frame_ms_from_key(key),
                        minio_object=key,
                        ocr_text="[duplicate frame]",
                        phash=phash,
                        model_name="paddle-ocr",
                    )
                seen_phashes.add(phash)

                # 3) OCR 识别（PaddleOCR 3.x 用 predict() 替代 ocr()，cls 不再作为参数）
                def _ocr() -> list:
                    # PaddleOCR 3.x: predict() 返回 iterable of Result 对象 (含 rec_texts)
                    result = list(self._local_model.predict(str(local)))
                    return result

                ocr_raw = await anyio.to_thread.run_sync(_ocr)
                texts = _extract_texts(ocr_raw)
                ocr_text = "\n".join(texts) if texts else None

                return OCRResult(
                    frame_ms=_frame_ms_from_key(key),
                    minio_object=key,
                    ocr_text=ocr_text,
                    phash=phash,
                    model_name="paddle-ocr",
                )
            finally:
                # 清理临时文件
                try:
                    local.unlink(missing_ok=True)
                except Exception:
                    pass

        # 并行处理（但 PaddleOCR 内部可能已占 GPU，这里限制并发）
        semaphore = asyncio.Semaphore(2 if self._use_gpu else 4)

        async def _limited(key: str) -> OCRResult:
            async with semaphore:
                return await _process_one(key)

        results = await asyncio.gather(*[_limited(k) for k in frame_keys])
        return results

    # ── API 路径（占位）──
    async def _recognize_api(
        self, media_id: uuid.UUID, frame_keys: list[str]
    ) -> list[OCRResult]:
        raise NotImplementedError(
            "OCR_PROVIDER=api 尚未实现，请设置 OCR_PROVIDER=local 或后续接入"
        )


async def save_frame_ocr(
    db: "anyio.abc.AsyncSession",
    media_id: uuid.UUID,
    results: list[OCRResult],
) -> list[m.FrameOCR]:
    """将 OCR 结果写入 frame_ocr 表。"""
    from sqlalchemy import select

    orms: list[m.FrameOCR] = []
    for r in results:
        # upsert by (media_id, frame_ms)
        existing = await db.execute(
            select(m.FrameOCR).where(
                (m.FrameOCR.media_id == media_id)
                & (m.FrameOCR.frame_ms == r.frame_ms)
            )
        )
        orm = existing.scalar_one_or_none()
        if orm is None:
            orm = m.FrameOCR(
                media_id=media_id,
                frame_ms=r.frame_ms,
                minio_object=r.minio_object,
                ocr_text=r.ocr_text,
                phash=r.phash,
                model_name=r.model_name,
                status="completed",
            )
            db.add(orm)
        else:
            orm.ocr_text = r.ocr_text
            orm.phash = r.phash
            orm.model_name = r.model_name
            orm.status = "completed"
        orms.append(orm)

    await db.flush()
    return orms


# ──────────────────────────── 辅助 ────────────────────────────


def _frame_ms_from_key(key: str) -> int:
    """从 object key 解析毫秒：`.../frames/{frame_ms}.jpg`。"""
    stem = Path(key).stem  # e.g., "00012345"
    try:
        return int(stem)
    except ValueError:
        return 0


async def _compute_phash(image_path: Path) -> str:
    """计算感知哈希（16 字符 hex）。"""
    def _do() -> str:
        try:
            from PIL import Image
            import imagehash
            img = Image.open(image_path)
            return str(imagehash.phash(img))
        except Exception:
            # 兜底：文件内容 SHA256 前 16 字符
            import hashlib
            h = hashlib.sha256(image_path.read_bytes()).hexdigest()[:16]
            return h

    return await anyio.to_thread.run_sync(_do)


def _extract_texts(ocr_raw) -> list[str]:
    """把 PaddleOCR 原始输出转纯文本列表。

    兼容两种格式：
    - 旧版（PaddleOCR < 3.x）：ocr_raw = list[page]；page = list[line]；line = [bbox, (text, score)]
    - 新版（PaddleOCR >= 3.x）：ocr_raw = list[Result]；Result.rec_texts = list[str]
    """
    texts: list[str] = []
    if not ocr_raw:
        return texts
    # 新版：Result 对象（带 .rec_texts 属性）
    if hasattr(ocr_raw[0], "rec_texts"):
        for page in ocr_raw:
            for t in getattr(page, "rec_texts", []) or []:
                texts.append(str(t))
        return texts
    # 旧版：list[page]
    for page in ocr_raw or []:
        for line in page or []:
            if len(line) >= 2 and line[1]:
                texts.append(str(line[1][0]))
    return texts


_ocr_engine: OCREngine | None = None


def get_ocr() -> OCREngine:
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = OCREngine()
    return _ocr_engine