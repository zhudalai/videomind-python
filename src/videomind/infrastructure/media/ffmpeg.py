"""FFmpeg subprocess 封装 —— 转码 / 抽音 / 抽帧 / 探测元信息。

对应 docs/VIDEO-PIPELINE.md 转码与音频提取阶段。
设计要点：
1. `ffmpeg` / `ffprobe` 子进程调用，用 anyio 放到线程跑（非阻塞）。
2. `ffprobe` 拿 duration_ms / width / height / fps，填充 `media_file` 元数据。
3. 抽音用 OGG/Vorbis（Whisper 友好），抽帧 1fps 关键帧（OCR 用）。
4. 防御性边界（3.6）：
   - 构造时 fail-fast 检查二进制存在性：缺 ffmpeg 时 subprocess 抛的
     FileNotFoundError（空 stderr）极难排查，直接给 NonRetryableError + 安装指引。
   - 所有子进程带 timeout=：损坏文件可让 ffmpeg 永不返回 → 挂死 worker。
   - 超时 → RetryableError（分不清系统过载与坏文件，宁可退避重试一次再落终态）；
     非零退出 → NonRetryableError（转码失败几乎总是输入文件问题，重试必然同结果）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

from videomind.config import get_settings
from videomind.core.errors import NonRetryableError, RetryableError


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
        # PATH 命中优先；FFMPEG_PATH 可配绝对路径兜底（Windows 下常见装在非 PATH 位置）
        self._ffmpeg = shutil.which("ffmpeg") or shutil.which(s.ffmpeg_path) or s.ffmpeg_path
        self._ffprobe = shutil.which("ffprobe") or "ffprobe"
        self._timeout_transcode = float(s.ffmpeg_timeout_s)
        self._timeout_probe = float(s.ffprobe_timeout_s)

        # fail-fast：二进制缺失时后续 subprocess 只会给空 stderr 的
        # FileNotFoundError（exit 2），排查成本极高 → 构造期给清晰指引
        missing = [
            name
            for name, binpath in (("ffmpeg", self._ffmpeg), ("ffprobe", self._ffprobe))
            if shutil.which(binpath) is None
        ]
        if missing:
            raise NonRetryableError(
                f"FFmpeg 可执行文件缺失: {missing}。"
                "安装后重试：Windows `choco install ffmpeg` / `scoop install ffmpeg`，"
                "Linux `apt-get install ffmpeg`，macOS `brew install ffmpeg`"
            )

    # ── 探测 ──
    async def probe(self, video_path: str | Path) -> ProbeResult:
        """用 ffprobe 拿视频元信息。

        损坏文件两种典型表现：非零退出（stderr 带 moov atom 缺失等线索）或
        挂死不返回——前者带 stderr 细节抛 NonRetryableError，后者由超时兜底。
        """
        p = Path(video_path)

        cmd = [
            self._ffprobe, "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(p),
        ]

        def _do() -> str:
            r = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                timeout=self._timeout_probe,
            )
            # 手动检查 returncode（不用 check=True）：把 stderr 带进异常，
            # 排查"为什么探测失败"不用重跑一遍命令
            if r.returncode != 0:
                raise NonRetryableError(
                    f"ffprobe failed (exit {r.returncode})，疑似文件损坏/格式不受支持: {p}\n"
                    f"stderr: {r.stderr[:1000]}"
                )
            return r.stdout

        try:
            out = await anyio.to_thread.run_sync(_do)
        except subprocess.TimeoutExpired as e:
            raise RetryableError(
                f"ffprobe 超时（{self._timeout_probe}s），疑似损坏文件挂死或系统过载: {p}"
            ) from e
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
            "-c:a", "copy", str(pattern),
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
        """跑 ffmpeg 子进程：超时兜底 + 非零退出异常分级。"""

        def _do() -> None:
            r = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                timeout=self._timeout_transcode,
            )
            if r.returncode != 0:
                raise NonRetryableError(
                    f"FFmpeg failed (exit {r.returncode})，疑似源文件损坏/编码不受支持:\n"
                    f"cmd: {' '.join(cmd)}\n"
                    f"stderr: {r.stderr[:1000]}"
                )

        try:
            await anyio.to_thread.run_sync(_do)
        except subprocess.TimeoutExpired as e:
            raise RetryableError(
                f"FFmpeg 超时（{self._timeout_transcode}s），"
                f"疑似损坏文件导致挂死或系统过载:\ncmd: {' '.join(cmd)}"
            ) from e


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
