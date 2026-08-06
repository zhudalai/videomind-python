"""OCR 文字识别 —— PaddleOCR 本地 + ocr.space API 双路径。

对应 docs/VIDEO-PIPELINE.md §2.4 OCR 阶段 + docs/ARCHITECTURE.md 双路径推理。
设计要点：
1. **双路径**：`OCR_PROVIDER=local|api` 环境变量切换后端。
   - local: PaddleOCR，GPU/CPU 可配（`OCR_USE_GPU`）—— 需 `uv sync --extra ocr` 装 paddle（wheel 仅 cp39–cp313，故本项目将其放 optional extras，Python 需 ≤3.13）
   - api: ocr.space 远程 OCR（POST /parse/image），仅需 httpx，无本地重依赖
2. **输入**：转码阶段产出的关键帧（1fps JPG）在 MinIO；
   OCR 阶段从 MinIO 下载 → 识别 → 写入 `frame_ocr` 表。local/api 共用下载与去重。
3. **去重**：感知哈希（phash）去重，同一视频相似帧只识别一次（省算力/API 配额）。
4. **产出**：`frame_ocr` 表记录（frame_ms, minio_object, ocr_text, phash, model_name, status）。

配置锚点（config.py）:
    OCR_PROVIDER=local|api
    OCR_USE_GPU=false      # local
    OCR_LANG=ch            # local paddle 语种码
    OCR_API_BASE_URL=https://api.ocr.space   # api（端点 path 内部拼 /parse/image）
    OCR_API_KEY=           # api（ocr.space 免费 key）
    OCR_API_ENGINE=2       # api（1/2/3）
    OCR_API_LANGUAGE=auto  # api（三字母码，engine2/3 支持 auto）
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncio
import anyio
import httpx
import logging
import os
import tempfile

# ──────────────────────────── PaddlePaddle 兼容性垫片 ────────────────────────────
# 在某些 CPU + oneDNN 环境下，PaddlePaddle 3.x 的 PIR（PIR = Paddle IR）
# 执行器会抛 NotImplementedError: ConvertPirAttribute2RuntimeAttribute …
# 必须在 paddle/paddleocr import 之前设置这些 FLAGS，否则框架已固化其 executor。
# 仅 local 路径用到；这些 environs 无副作用，放顶层无碍 api 路径。
os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_enable_new_executor", "1")  # 走新版 executor
# 关闭 PaddleOCR 的多语言包下载提示（消除用户态噪声）
os.environ.setdefault("DISABLE_PADDLEOCR_DOWNLOAD", "1")

from videomind.config import get_settings
from videomind.infrastructure.media.minio import get_minio_client
from videomind.infrastructure.storage import models as m

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    """单帧 OCR 结果。"""

    frame_ms: int
    minio_object: str
    ocr_text: str | None
    phash: str | None
    model_name: str


@dataclass
class _FrameFile:
    """下载到本地的待识别帧 + 去重标记。local/api 共用。"""

    key: str
    local: Path
    phash: str
    is_duplicate: bool


async def _download_and_dedup(
    media_id: uuid.UUID, frame_keys: list[str]
) -> list[_FrameFile]:
    """串行下载关键帧到临时文件并按 phash 去重。

    串行保证确定性：第一帧总是参与下游识别，后续与已见 phash 重复的帧标 ``is_duplicate``
    由上层识别段直接标记 ``[duplicate frame]`` 跳过真正识别（省 paddle 算力 / API 配额）。

    Args:
        media_id: 视频标识，仅用于临时文件名隔离。
        frame_keys: MinIO object key 列表。

    Returns:
        ``_FrameFile`` 列表（顺序对应 frame_keys）。调用方负责识别后清理 ``local``。
    """
    minio = get_minio_client()
    seen: set[str] = set()
    out: list[_FrameFile] = []
    for key in frame_keys:
        local = Path(tempfile.gettempdir()) / f"ocr_{media_id}_{uuid.uuid4().hex}_{Path(key).name}"
        await minio.download_file(key, local)
        phash = await _compute_phash(local)
        if phash in seen:
            out.append(_FrameFile(key, local, phash, is_duplicate=True))
        else:
            seen.add(phash)
            out.append(_FrameFile(key, local, phash, is_duplicate=False))
    return out


def _unlink_quiet(local: Path) -> None:
    """静默清理临时帧文件，OCR 各识别分支 finally 里调用。"""
    try:
        local.unlink(missing_ok=True)
    except Exception:
        pass


class OCREngine:
    """OCR 引擎：本地 PaddleOCR + ocr.space API 双路径。"""

    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        """初始化引擎。

        Args:
            http_client: 可选预配置的 httpx 客户端（api 路径复用连接 / 测试注入 mock transport）；
                ``None`` 时 api 路径内部按需自建并负责关闭。
        """
        s = get_settings()
        self._provider = s.ocr_provider
        self._use_gpu = s.ocr_use_gpu
        self._lang = s.ocr_lang
        self._local_model = None  # 延迟加载（paddle 惰性 import，缺依赖不阻断 import 本模块）
        self._api_client = http_client  # api 路径复用 / 测试注入

    async def recognize_frames(
        self, media_id: uuid.UUID, frame_keys: list[str]
    ) -> list[OCRResult]:
        """批量识别一组关键帧。

        Args:
            media_id: 视频 ID
            frame_keys: MinIO object keys（如 `content_hash/frames/frame_00000001.jpg`）

        Returns:
            OCRResult 列表（顺序对应 frame_keys）
        """
        if not frame_keys:
            return []
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

        frames = await _download_and_dedup(media_id, frame_keys)

        # 并行 OCR 处理（仅对非重复帧跑 paddle）
        semaphore = asyncio.Semaphore(2 if self._use_gpu else 4)

        async def _ocr_one(ff: _FrameFile) -> OCRResult:
            if ff.is_duplicate:
                _unlink_quiet(ff.local)
                return OCRResult(
                    frame_ms=_frame_ms_from_key(ff.key),
                    minio_object=ff.key,
                    ocr_text="[duplicate frame]",
                    phash=ff.phash,
                    model_name="paddle-ocr",
                )

            # 非重复帧：执行 OCR
            async with semaphore:
                def _ocr() -> list:
                    try:
                        result = list(self._local_model.predict(str(ff.local)))
                        return result
                    except NotImplementedError as e:
                        if "ConvertPirAttribute2RuntimeAttribute" in str(e) or "onednn" in str(e).lower():
                            return []
                        raise
                    except Exception as e:
                        logger.warning(f"OCR 识别异常，降级为空: {e}")
                        return []

                try:
                    ocr_raw = await anyio.to_thread.run_sync(_ocr)
                    texts = _extract_texts(ocr_raw)
                    ocr_text = "\n".join(texts) if texts else None
                finally:
                    _unlink_quiet(ff.local)

                return OCRResult(
                    frame_ms=_frame_ms_from_key(ff.key),
                    minio_object=ff.key,
                    ocr_text=ocr_text,
                    phash=ff.phash,
                    model_name="paddle-ocr",
                )

        # 并行执行，保持原始顺序
        results = await asyncio.gather(*[_ocr_one(f) for f in frames])
        return results

    # ── API 路径（ocr.space）──
    async def _recognize_api(
        self, media_id: uuid.UUID, frame_keys: list[str]
    ) -> list[OCRResult]:
        s = get_settings()
        if not s.ocr_api_base_url or not s.ocr_api_key:
            raise NotImplementedError(
                "OCR_PROVIDER=api 但缺 OCR_API_BASE_URL/OCR_API_KEY，"
                "请在 .env 配置（ocr.space 免费档 key），或改 OCR_PROVIDER=local + 装可选 ocr extras"
            )

        url = s.ocr_api_base_url.rstrip("/") + "/parse/image"
        frames = await _download_and_dedup(media_id, frame_keys)
        # 并发上限保护 ocr.space 免费档（单 IP 500/日）+ 减少突发限流
        semaphore = asyncio.Semaphore(4)

        owns_client = self._api_client is None
        client = self._api_client or httpx.AsyncClient(timeout=s.ocr_timeout_s)

        async def _ocr_one(ff: _FrameFile) -> OCRResult:
            if ff.is_duplicate:
                _unlink_quiet(ff.local)
                return OCRResult(
                    frame_ms=_frame_ms_from_key(ff.key),
                    minio_object=ff.key,
                    ocr_text="[duplicate frame]",
                    phash=ff.phash,
                    model_name="ocr.space",
                )

            async with semaphore:
                try:
                    ocr_text = await self._call_ocr_space(client, url, ff.local, s)
                except Exception as e:
                    # 单帧 API 失败降级空（不炸整批）：网络/限流/5xx/识别错误
                    # 上层 ocr_task 还有一层兜底；此处只丢这一帧，其余帧继续
                    logger.warning("ocr.space 单帧识别失败，降级空: %s: %s", type(e).__name__, e)
                    ocr_text = None
                finally:
                    _unlink_quiet(ff.local)

                return OCRResult(
                    frame_ms=_frame_ms_from_key(ff.key),
                    minio_object=ff.key,
                    ocr_text=ocr_text,
                    phash=ff.phash,
                    model_name="ocr.space",
                )

        try:
            return await asyncio.gather(*[_ocr_one(f) for f in frames])
        finally:
            if owns_client:
                await client.aclose()

    async def _call_ocr_space(
        self,
        client: httpx.AsyncClient,
        url: str,
        local: Path,
        s: Any,
    ) -> str | None:
        """对单帧调 ocr.space /parse/image，返回纯文本或 None（无文字/错误）。

        Raises:
            httpx 异常：网络/超时/5xx 向上抛，由 _recognize_api 捕获降级该帧。
        """
        import base64

        data_uri = "data:image/jpeg;base64," + base64.b64encode(local.read_bytes()).decode()
        r = await client.post(
            url,
            data={
                "apikey": s.ocr_api_key,
                "base64Image": data_uri,
                "language": s.ocr_api_language,
                "OCREngine": str(s.ocr_api_engine),
                "isOverlayRequired": "false",
            },
        )
        r.raise_for_status()
        j = r.json()

        # ocr.space 错误语义：顶层 IsErroredOnProcessing=true 或 OCRExitCode>=3
        if j.get("IsErroredOnProcessing"):
            logger.warning("ocr.space 处理错误: %s | %s", j.get("ErrorMessage"), j.get("ErrorDetails"))
            return None
        # 只取每页解析成功的文字（FileParseExitCode==1）
        texts: list[str] = []
        for pr in j.get("ParsedResults", []) or []:
            if pr.get("FileParseExitCode") == 1:
                t = (pr.get("ParsedText") or "").strip()
                if t:
                    texts.append(t)
        return "\n".join(texts) if texts else None


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
