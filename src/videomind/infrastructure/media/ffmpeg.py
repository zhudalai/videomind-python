"""FFmpeg subprocess 封装 —— 转码 / 抽音 / 抽帧 / 探测元信息。

对应 docs/VIDEO-PIPELINE.md 转码与音频提取阶段。
设计要点：
1. `ffmpeg` / `ffprobe` 子进程调用，用 anyio 放到线程跑（非阻塞）。
2. `ffprobe` 拿 duration_ms / width / height / fps，填充 `media_file` 元数据。
3. 抽音用 OGG/Vorbis（Whisper 友好），抽帧 1fps 关键帧（OCR 用）。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

from videomind.config import get_settings


@dataclass
class ProbeResult:
    """视频探测结果，填充 media_file 元数据用。"""

    duration_ms: int
    width: int
    height: int
    fps: float
    mime_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "duration_ms": self.duration_ms,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "mime_type": self.mime_type,
        }


class FFmpegWrapper:
    """FFmpeg / ffprobe 命令行封装。"""

    def __init__(self) -> None:
        s = get_settings()
        self._ffmpeg = shutil.which("ffmpeg") or s.ffmpeg_path
        self._ffprobe = shutil.which("ffprobe") or "ffprobe"

    # ── 探测 ──
    async def probe(self, video_path: str | Path) -> ProbeResult:
        """用 ffprobe 拿视频元信息。"""
        p = Path(video_path)

        cmd = [
            self._ffprobe, "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(p),
        ]

        def _do() -> str:
            import subprocess
            r = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return r.stdout

        out = await anyio.to_thread.run_sync(_do)
        data = json.loads(out)

        # 找视频流
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            {},
        )
        duration_sec = float(data.get("format", {}).get("duration", 0.0))
        return ProbeResult(
            duration_ms=int(duration_sec * 1000),
            width=int(video_stream.get("width", 0)),
            height=int(video_stream.get("height", 0)),
            fps=_parse_fps(video_stream.get("r_frame_rate", "0/1")),
            mime_type=data.get("format", {}).get("format_long_name", "unknown"),
        )

    # ── 抽音 ──
    async def extract_audio(
        self, video_path: str | Path, audio_path: str | Path
    ) -> Path:
        """抽取音频为 16kHz 单声道 OGG（Whisper 友好）。"""
        out = Path(audio_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self._ffmpeg, "-y", "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libvorbis", str(out),
        ]
        await self._run(cmd)
        return out

    # ── 音频分片（断点续传用）──
    async def split_audio(
        self, audio_path: str | Path, out_dir: str | Path,
        *, segment_ms: int = 60000
    ) -> list[Path]:
        """按固定时长切分音频（配套 ASR 断点续传）。"""
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        seg_sec = segment_ms / 1000.0
        pattern = d / "audio_chunk_%04d.ogg"
        cmd = [
            self._ffmpeg, "-y", "-i", str(audio_path),
            "-f", "segment", "-segment_time", str(seg_sec),
            "-c", "copy", str(pattern),
        ]
        await self._run(cmd)
        return sorted(d.glob("audio_chunk_*.ogg"))

    # ── 抽帧 ──
    async def extract_keyframes(
        self, video_path: str | Path, out_dir: str | Path,
        *, fps: float = 1.0
    ) -> list[Path]:
        """按 fps 抽关键帧（OCR 用）。默认 1fps = 每秒一帧。"""
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        pattern = d / "frame_%08d.jpg"
        cmd = [
            self._ffmpeg, "-y", "-i", str(video_path),
            "-vf", f"fps={fps}", "-q:v", "2", str(pattern),
        ]
        await self._run(cmd)
        frames = sorted(d.glob("frame_*.jpg"))
        return frames

    # ── 转码 ──
    async def transcode(
        self, src: str | Path, dst: str | Path,
        *, codec: str = "libx264", crf: int = 23, scale: str | None = None,
    ) -> Path:
        """通用转码：默认 H.264 + CRF 23，可选缩放（scale 如 '1280:-2'）。"""
        out = Path(dst)
        out.parent.mkdir(parents=True, exist_ok=True)
        vf = f"scale={scale}" if scale else None
        cmd = [self._ffmpeg, "-y", "-i", str(src), "-c:v", codec, "-crf", str(crf)]
        if codec == "libx264":
            cmd += ["-preset", "medium", "-pix_fmt", "yuv420p"]
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-c:a", "aac", "-b:a", "128k", str(out)]
        await self._run(cmd)
        return out

    # ── 内部 ──
    async def _run(self, cmd: list[str]) -> None:
        def _do() -> None:
            import subprocess
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(
                    f"FFmpeg failed (exit {r.returncode}):\n"
                    f"cmd: {' '.join(cmd)}\n"
                    f"stderr: {r.stderr[:1000]}"
                )

        await anyio.to_thread.run_sync(_do)


def _parse_fps(rate: str) -> float:
    """解析 ffprobe 的 r_frame_rate（'30/1' 或 '30000/1001'）。"""
    try:
        num, den = rate.split("/")
        den_f = float(den) or 1.0
        return float(num) / den_f
    except Exception:
        return 0.0


_ffmpeg_singleton: FFmpegWrapper | None = None


def get_ffmpeg() -> FFmpegWrapper:
    global _ffmpeg_singleton
    if _ffmpeg_singleton is None:
        _ffmpeg_singleton = FFmpegWrapper()
    return _ffmpeg_singleton
