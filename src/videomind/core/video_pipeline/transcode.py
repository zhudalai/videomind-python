"""转码/分段阶段 —— FFmpeg 抽音、抽帧、场景变化检测。

对应 docs/VIDEO-PIPELINE.md §2.2 转码/分段阶段。
设计要点：
1. 输入：Downloader 产出的本地视频文件路径
2. 产出：
   - 音频文件（16kHz mono OGG，Whisper 友好）
   - 关键帧图片（1fps，OCR 用）
   - 场景变化点（感知哈希差异检测，frame_ocr 预留）
3. 全程在 GPU Worker 本地临时目录跑，完成后上传 MinIO。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio

from videomind.config import get_settings
from videomind.infrastructure.media.ffmpeg import get_ffmpeg
from videomind.infrastructure.media.minio import get_minio_client


@dataclass
class TranscodeResult:
    """转码阶段产出。"""

    video_local: Path
    audio_local: Path
    audio_minio: str          # MinIO object key
    keyframes_local: list[Path]
    duration_ms: int
    width: int
    height: int
    fps: float
    keyframes_minio: list[str] = field(default_factory=list)
    scene_changes_ms: list[int] = field(default_factory=list)

    def as_media_meta(self) -> dict[str, Any]:
        """可填进 media_file 的元数据。"""
        return {
            "duration_ms": self.duration_ms,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
        }


class Transcoder:
    """转码阶段执行器。"""

    # 常量对应 docs/VIDEO-PIPELINE.md §2.2
    SEGMENT_DURATION = 60          # 秒，Whisper 单次推理窗口
    AUDIO_SAMPLE_RATE = 16000
    AUDIO_CHANNELS = 1
    KEYFRAME_FPS = 1.0             # 1fps 抽关键帧
    SCENE_THRESHOLD = 0.35         # 感知哈希差异阈值
    SCENE_MIN_INTERVAL = 5.0       # 最小间隔秒
    SCENE_MAX_INTERVAL = 30.0      # 兜底采样间隔

    def __init__(self) -> None:
        s = get_settings()
        self._temp_dir = Path(s.download_dir) / "transcode_temp"
        self._ffmpeg = get_ffmpeg()
        self._minio = get_minio_client()

    async def execute(self, video_local: Path | str, content_hash: str) -> TranscodeResult:
        """执行转码/分段流程。

        Args:
            video_local: 下载器产出的本地视频路径（Path 或 str）
            content_hash: 内容 SHA256（MinIO 对象键前缀用）

        Returns:
            TranscodeResult，包含本地/MinIO 双路径
        """
        video_path = Path(video_local)
        # 确保 bucket 存在
        await self._minio.ensure_bucket()

        # 1. ffprobe 探测元信息
        probe = await self._ffmpeg.probe(video_path)

        # 2. 抽音：16kHz mono OGG
        audio_name = f"{content_hash}/audio.ogg"
        audio_local = self._temp_dir / f"{content_hash}_audio.ogg"
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        await self._ffmpeg.extract_audio(
            video_local, audio_local
        )

        # 3. 抽关键帧（1fps）
        frames_dir = self._temp_dir / f"{content_hash}_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        keyframes_local = await self._ffmpeg.extract_keyframes(
            video_local, frames_dir, fps=self.KEYFRAME_FPS
        )

        # 4. 场景变化检测（感知哈希，预留给 frame_ocr）
        scene_changes = await self._detect_scene_changes(video_local)

        # 5. 并行上传音频 + 关键帧到 MinIO
        import asyncio
        await asyncio.gather(
            self._minio.upload_file(audio_name, audio_local),
            self._upload_keyframes(keyframes_local, content_hash),
        )

        return TranscodeResult(
            video_local=video_local,
            audio_local=audio_local,
            audio_minio=audio_name,
            keyframes_local=keyframes_local,
            keyframes_minio=[
                f"{content_hash}/frames/{f.name}" for f in keyframes_local
            ],
            scene_changes_ms=scene_changes,
            duration_ms=probe.duration_ms,
            width=probe.width,
            height=probe.height,
            fps=probe.fps,
        )

    async def _detect_scene_changes(self, video_path: Path | str) -> list[int]:
        """场景变化检测（感知哈希差异）。

        这里简化实现：用 ffmpeg 输出 1fps 帧，逐帧计算 phash 差异。
        返回场景切换时间点列表（毫秒）。
        """
        video_path = Path(video_path)
        # 复用抽帧结果
        frames_dir = self._temp_dir / f"_scene_{video_path.stem}"
        frames_dir.mkdir(parents=True, exist_ok=True)
        await self._ffmpeg.extract_keyframes(
            video_path, frames_dir, fps=1.0
        )
        frames = sorted(frames_dir.glob("*.jpg"))
        if len(frames) < 2:
            return []

        try:
            from PIL import Image
            import imagehash
        except ImportError:
            # Pillow/imagehash 未装时退回均匀采样
            return [i * 30_000 for i in range(1, 10)]

        scene_changes: list[int] = []
        prev_hash = None
        for i, f in enumerate(frames):
            try:
                img = Image.open(f)
                cur_hash = imagehash.phash(img)
                if prev_hash is not None:
                    diff = cur_hash - prev_hash
                    if diff > self.SCENE_THRESHOLD * 64:  # 归一化到 0-64
                        ms = i * 1000
                        # 间隔约束
                        if not scene_changes or ms - scene_changes[-1] >= self.SCENE_MIN_INTERVAL * 1000:
                            scene_changes.append(ms)
                prev_hash = cur_hash
            except Exception:
                continue

        # 兜底：若检测点太少，按 MAX_INTERVAL 补采样
        if len(scene_changes) < probe.duration_ms / (self.SCENE_MAX_INTERVAL * 1000) * 0.5:
            scene_changes = [
                int(i * self.SCENE_MAX_INTERVAL * 1000)
                for i in range(1, max(2, int(probe.duration_ms / (self.SCENE_MAX_INTERVAL * 1000))))
            ]
        return scene_changes

    async def _upload_keyframes(
        self, keyframes: list[Path], content_hash: str
    ) -> None:
        """并行上传关键帧到 MinIO（忽略失败不阻塞主流程）。"""
        async def _one(kf: Path) -> None:
            try:
                await self._minio.upload_file(
                    f"{content_hash}/frames/{kf.name}", kf
                )
            except Exception:
                pass  # 抽帧 OCR 是增强，失败不阻塞管线

        import asyncio
        await asyncio.gather(*[_one(k) for k in keyframes])


_transcoder: Transcoder | None = None


def get_transcoder() -> Transcoder:
    global _transcoder
    if _transcoder is None:
        _transcoder = Transcoder()
    return _transcoder