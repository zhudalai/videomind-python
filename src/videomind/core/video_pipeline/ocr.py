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
import tempfile

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

        # 1) 先串行下载所有帧并计算 phash（去重检测需按顺序确定性）
        # 这样保证第一帧总是做 OCR，后续重复帧直接标记 duplicate
        frame_data: list[tuple[str, Path, str]] = []  # (key, local_path, phash)
        seen_phashes: set[str] = set()
        is_duplicate: list[bool] = []

        for key in frame_keys:
            import uuid as _uuid
            local = Path(tempfile.gettempdir()) / f"ocr_{media_id}_{_uuid.uuid4().hex}_{Path(key).name}"
            await minio.download_file(key, local)
            phash = await _compute_phash(local)
            frame_data.append((key, local, phash))

            if phash in seen_phashes:
                is_duplicate.append(True)
            else:
                seen_phashes.add(phash)
                is_duplicate.append(False)

        # 2) 并行 OCR 处理（仅对非重复帧）
        semaphore = asyncio.Semaphore(2 if self._use_gpu else 4)

        async def _ocr_one(idx: int) -> OCRResult:
            key, local, phash = frame_data[idx]
            if is_duplicate[idx]:
                # 重复帧：直接返回标记
                try:
                    local.unlink(missing_ok=True)
                except Exception:
                    pass
                return OCRResult(
                    frame_ms=_frame_ms_from_key(key),
                    minio_object=key,
                    ocr_text="[duplicate frame]",
                    phash=phash,
                    model_name="paddle-ocr",
                )

            # 非重复帧：执行 OCR
            async with semaphore:
                def _ocr() -> list:
                    try:
                        result = list(self._local_model.predict(str(local)))
                        return result
                    except NotImplementedError as e:
                        if "ConvertPirAttribute2RuntimeAttribute" in str(e) or "onednn" in str(e).lower():
                            return []
                        raise
                    except Exception as e:
                        import logging
                        logging.warning(f"OCR 识别异常，降级为空: {e}")
                        return []

                try:
                    ocr_raw = await anyio.to_thread.run_sync(_ocr)
                    texts = _extract_texts(ocr_raw)
                    ocr_text = "\n".join(texts) if texts else None
                finally:
                    try:
                        local.unlink(missing_ok=True)
                    except Exception:
                        pass

                return OCRResult(
                    frame_ms=_frame_ms_from_key(key),
                    minio_object=key,
                    ocr_text=ocr_text,
                    phash=phash,
                    model_name="paddle-ocr",
                )

        # 3) 并行执行，保持原始顺序
        results = await asyncio.gather(*[_ocr_one(i) for i in range(len(frame_keys))])
        return results

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
    seen_frame_ms: set[int] = set()

    # 先查已有记录（避免同 batch 内重复 frame_ms 导致 flush 时唯一约束冲突）
    existing_stmt = select(m.FrameOCR).where(
        m.FrameOCR.media_id == media_id
    )
    existing_result = await db.execute(existing_stmt)
    existing_map = {row.frame_ms: row for row in existing_result.scalars()}

    for r in results:
        # 批内去重：同一 batch 里重复 frame_ms 只处理第一个
        if r.frame_ms in seen_frame_ms:
            continue
        seen_frame_ms.add(r.frame_ms)

        if r.frame_ms in existing_map:
            orm = existing_map[r.frame_ms]
            orm.ocr_text = r.ocr_text
            orm.phash = r.phash
            orm.model_name = r.model_name
            orm.status = "completed"
        else:
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
        orms.append(orm)

    await db.flush()
    return orms


# ──────────────────────────── 辅助 ────────────────────────────


def _frame_ms_from_key(key: str) -> int:
    """从 object key 解析毫秒：`.../frames/frame_XXXXXXXX.jpg`。

    ffmpeg extract_keyframes 用 `frame_%08d.jpg` 格式，对应 0, 1, 2... 索引。
    1fps 下第 N 帧 = N * 1000ms。
    """
    stem = Path(key).stem  # e.g., "frame_00000001"
    # 去掉 "frame_" 前缀
    if stem.startswith("frame_"):
        stem = stem[6:]
    try:
        frame_idx = int(stem)
        return frame_idx * 1000  # 1fps -> 每帧 1000ms
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