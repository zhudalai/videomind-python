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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

from videomind.config import get_settings


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
        self._ydl_opts = {**YDL_DEFAULT_OPTS}

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

        await anyio.to_thread.run_sync(_do)

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
    raise FileNotFoundError(
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
