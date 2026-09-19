"""下载器 —— yt-dlp 通用下载（1800+ 站点）。

对应 docs/VIDEO-PIPELINE.md §2.1 下载阶段。
设计要点：
1. **首版仅 yt-dlp 通用下载**；抖音无 Cookie 解析（DouyinDownloader）与字幕下载
   （writesubtitles/writeautomaticsub）均按用户原始约束 **延后实现（Phase 2+）**，
   本版关闭，文本来源以本地 ASR 为准。对应配置项已保留占位但置 False。
2. yt-dlp 是同步阻塞库，用 anyio.to_thread 包成 async。
3. 下载完成后产出 video 本地路径 + 元信息（duration/width/height），交下游转码。
4. 内容级去重：`media_file.content_hash`（SHA256）是去重键，下载阶段计算。
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

from videomind.config import get_settings
from videomind.core.errors import NonRetryableError, RetryableError


# ──────────────────────────── yt-dlp 统一配置 ────────────────────────────
# 权威定义见 docs/VIDEO-PIPELINE.md §2.1「yt-dlp 统一配置」。
# 抖音 DouyinDownloader 与字幕下载（writesubtitles/writeautomaticsub）按用户原始约束
# ⏳ 延后实现（Phase 2+，非首版），本版均关闭，文本来源以本地 ASR 为准。

YDL_DEFAULT_OPTS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "extract_flat": False,
    # 限高 ≤720p，避免占满显存/带宽。
    # 末尾追加 `/best` 兜底：站点 URL（YouTube/B 站等）走带 height 的分流筛前两段；
    # 媒体直链（.mp4 CDN/bucket）generic extractor 识别为单格式且无 height 信息，
    # 前 two 分支匹配不到时回落到 `/best` 直接拉整文件，避免 "Requested format is not available"。
    "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "merge_output_format": "mp4",
    # ⏳ 延后实现（Phase 2+，非首版）：字幕下载本版不做，文本来源以本地 ASR 为准
    "writesubtitles": False,
    "writeautomaticsub": False,
    "subtitleslangs": ["zh-Hans", "zh-CN", "zh", "en"],
    "subtitlesformat": "vtt",
    "skip_download": False,
}


@dataclass
class DownloadResult:
    """下载阶段产出。"""

    url: str
    local_path: Path
    filename: str
    # 视频元信息（yt-dlp info_dict 提取）
    duration_ms: int
    width: int
    height: int
    fps: float
    title: str
    # 文件来源的 SHA256 内容指纹（media_file.content_hash 去重键）
    content_hash: str

    def as_media_meta(self) -> dict[str, Any]:
        """转成可填进 media_file 的元数据 dict。"""
        return {
            "duration_ms": self.duration_ms,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "title": self.title,
            "filename": self.filename,
            "content_hash": self.content_hash,
        }


class Downloader:
    """yt-dlp 通用下载器封装。

    抖音无 Cookie 解析（DouyinDownloader）为后续规划，本版不启用。
    """

    def __init__(self) -> None:
        s = get_settings()
        self._download_dir = Path(s.download_dir)
        self._proxy = s.ytdlp_proxy or None
        self._min_free_gb = float(s.download_min_free_gb)
        # yt-dlp 网络兜底：无 socket_timeout 时慢源可把 worker 挂死数小时；
        # retries/fragment_retries 让 yt-dlp 内部自愈，不消耗 Celery 重试次数
        self._ydl_opts = {
            **YDL_DEFAULT_OPTS,
            "socket_timeout": float(s.download_socket_timeout_s),
            "retries": int(s.download_retries),
            "fragment_retries": int(s.download_retries),
        }

    async def download(self, url: str, *, subdir: str | None = None) -> DownloadResult:
        """下载指定 URL 视频到本地，返回 DownloadResult。

        Args:
            url: 视频 URL（yt-dlp 支持的 1800+ 站点）
            subdir: 子目录（按 content_hash 分桶，避免文件名碰撞）
        """
        # 延迟 import：未安装 yt-dlp 时不影响其他模块 import
        from yt_dlp import YoutubeDL

        out_dir = self._download_dir / (subdir or "")
        out_dir.mkdir(parents=True, exist_ok=True)
        # 磁盘预检：720p 长视频可达数百 MB，盘满中途失败比开始前失败代价高
        _check_disk_space(out_dir, self._min_free_gb)

        opts = {
            **self._ydl_opts,
            "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
            "noprogress": True,
        }
        if self._proxy:
            opts["proxy"] = self._proxy

        info: dict[str, Any] = {}

        def _do() -> dict[str, Any]:
            nonlocal info
            with YoutubeDL(opts) as ydl:
                # 先 extract_info 拿元数据
                info = ydl.extract_info(url, download=True)
                return info

        try:
            await anyio.to_thread.run_sync(_do)
        except Exception as e:
            # 失败清理半成品：yt-dlp 的 .part/.ytdl 残留曾积数百 MB 磁盘垃圾；
            # 重试从零下载，半成品无复用价值。只清 subdir 专属目录——
            # subdir=None 时 out_dir 是共享下载根目录，绝不能删
            if subdir:
                shutil.rmtree(out_dir, ignore_errors=True)
            raise _translate_download_error(e) from e

        local_path = _resolve_downloaded_path(out_dir, info)
        # 下载后计算 SHA256（content_hash 是去重键，需精确）
        content_hash = await _sha256_file(local_path)

        return DownloadResult(
            url=url,
            local_path=local_path,
            filename=local_path.name,
            duration_ms=int((info.get("duration") or 0) * 1000),
            width=int(info.get("width") or 0),
            height=int(info.get("height") or 0),
            fps=float(info.get("fps") or 0.0),
            title=str(info.get("title") or "untitled"),
            content_hash=content_hash,
        )

    async def extract_info_only(self, url: str) -> dict[str, Any]:
        """只探测元信息不下载（用于预校验 URL / 计算 duration 估算转码耗时）。"""
        from yt_dlp import YoutubeDL

        opts = {**self._ydl_opts, "skip_download": True}

        def _do() -> dict[str, Any]:
            with YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        return await anyio.to_thread.run_sync(_do)


# ──────────────────────────── 单例 ────────────────────────────


_downloader: Downloader | None = None


def get_downloader() -> Downloader:
    global _downloader
    if _downloader is None:
        _downloader = Downloader()
    return _downloader


# ──────────────────────────── 辅助 ────────────────────────────

# yt-dlp DownloadError 文本中的确定性失败标记（小写子串匹配）：
# 视频 404/被删、私享、需登录、年龄墙、版权下架、地区封锁、不支持站点。
# 分类边界放下载现场：这些站点语义只有 yt-dlp 的错误消息携带，任务层猜不到。
_NONRETRYABLE_MARKERS: tuple[str, ...] = (
    "404",
    "not found",
    "not available",      # 含 "not available in your country"（地区封锁变体）
    # 收窄为 "video unavailable"：裸 "unavailable" 会误伤
    # "HTTP Error 503: Service Unavailable"（暂时性服务端故障，应退避重试）
    "video unavailable",
    "private",         # "This video is private" / "private video"
    "login", "sign in",  # 需登录（含多数年龄墙：sign in to confirm your age）
    "age-restricted", "age restricted",
    "unsupported url", "invalid url", "not a valid url",
    "no video",        # 无可下载格式（纯音频/被过滤光）
    "copyright",       # 版权下架
    "removed",         # 被上传者/平台删除
    "geo-restrict", "geo restricted",
)


def _translate_download_error(exc: Exception) -> Exception:
    """yt-dlp DownloadError → 可重试/不可重试 二分翻译（见 videomind.core.errors）。

    - 命中确定性标记（404/私享/需登录/地区封锁…）→ NonRetryableError：
      退避重试改变不了资源不存在的事实，盲重 3 次纯浪费算力。
    - 其余（网络抖动、源站 5xx、超时）→ RetryableError：退避后重试可恢复。
    - 非 DownloadError 原样返回：socket/OSError 交由 BaseVideoTask autoretry 基类兜底。
    """
    try:
        from yt_dlp.utils import DownloadError
    except ImportError:
        # yt-dlp 未安装：交由上层（原样抛出，import 错在调用侧更早暴露）
        return exc
    if not isinstance(exc, DownloadError):
        return exc
    msg = str(exc).lower()
    if any(marker in msg for marker in _NONRETRYABLE_MARKERS):
        return NonRetryableError(f"视频不可下载（确定性失败，不重试）: {exc}")
    return RetryableError(f"下载暂时失败（退避重试可恢复）: {exc}")


def _check_disk_space(out_dir: Path, min_free_gb: float) -> None:
    """下载前磁盘预检：剩余空间不足 → RetryableError（磁盘释放后重试可恢复）。

    预检本身失败（跨平台盘符/权限差异）不阻塞下载——真盘满时写入阶段仍会报错兜底。
    """
    try:
        free_gb = shutil.disk_usage(out_dir).free / (1 << 30)
    except OSError:
        return
    if free_gb < min_free_gb:
        raise RetryableError(
            f"磁盘剩余空间不足：{out_dir} 所在盘仅剩 {free_gb:.1f}GB"
            f"（下限 {min_free_gb}GB），释放空间后重试即可恢复"
        )


def _resolve_downloaded_path(out_dir: Path, info: dict[str, Any]) -> Path:
    """yt-dlp 下载后定位实际产物路径。

    优先用 requested_downloads（merge 后的最终路径），回退 info 的 ext 拼 id。
    """
    reqs = info.get("requested_downloads") or []
    if reqs and "filepath" in reqs[0]:
        return Path(reqs[0]["filepath"])
    ext = info.get("ext") or "mp4"
    vid = info.get("id") or "video"
    # 尝试常见后缀
    for e in (ext, "mp4", "mkv", "webm"):
        p = out_dir / f"{vid}.{e}"
        if p.exists():
            return p
    # 契约违规（yt-dlp 行为变更/产物被外部移动）：确定性失败，重试无意义
    raise NonRetryableError(
        f"yt-dlp 下载完成但找不到产物：{out_dir}/{vid}.{ext}"
    )


async def _sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    """异步计算文件 SHA256（分块，不阻塞事件循环）。"""
    h = hashlib.sha256()
    pf = await anyio.open_file(str(path), "rb")
    try:
        while True:
            buf = await pf.read(chunk)
            if not buf:
                break
            h.update(buf)
    finally:
        await pf.aclose()
    return h.hexdigest()
