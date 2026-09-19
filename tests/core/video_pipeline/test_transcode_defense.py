"""转码阶段防御性测试 —— execute 编排、场景检测三分支、工件清理、上传容错。

守护契约（对应 core/video_pipeline/transcode.py）：
- execute()：probe → 抽音 → 抽帧 → 场景检测 → 并行上传 MinIO → 清理本地工件；
  产物全进 MinIO 后本地副本即删（旧实现从不清理，磁盘缓慢失血）。
- 场景检测三分支：帧 <2 → []；Pillow/imagehash 缺失 → 均匀采样兜底；
  检测点过少 → 按 MAX_INTERVAL 补采样（保底不空）。
- 关键帧上传单帧失败被吞（OCR 增强不阻塞管线）；清理失败同样不炸已成功任务。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from videomind.config import get_settings
from videomind.core.video_pipeline.transcode import Transcoder, TranscodeResult
from videomind.infrastructure.media.ffmpeg import ProbeResult


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ──────────────────────────── 假基础设施 ────────────────────────────


class FakeFFmpeg:
    """probe 返回固定元信息；extract_keyframes 按剧本在目标目录造帧文件。"""

    def __init__(self, frame_names: list[str] | None = None):
        self.probe_result = ProbeResult(
            duration_ms=5000, width=640, height=360, fps=25.0, mime_type="mp4"
        )
        self.frame_names = frame_names or []
        self.calls: list[tuple] = []

    async def probe(self, path):
        self.calls.append(("probe", str(path)))
        return self.probe_result

    async def extract_audio(self, video, audio_out):
        self.calls.append(("extract_audio", str(audio_out)))
        p = Path(audio_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"audio-bytes")
        return p

    async def extract_keyframes(self, video, out_dir, fps=1.0):
        self.calls.append(("extract_keyframes", str(out_dir), fps))
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        made = []
        for name in self.frame_names:
            p = d / name
            p.write_bytes(b"jpg")
            made.append(p)
        return made


class FakeMinio:
    """记录上传；fail_keys 里的对象键抛错（测单帧失败容错）。"""

    def __init__(self, fail_keys: set[str] | None = None):
        self.uploads: list[tuple[str, str]] = []
        self.bucket_ensured = False
        self.fail_keys = fail_keys or set()

    async def ensure_bucket(self):
        self.bucket_ensured = True

    async def upload_file(self, object_key, local_path):
        if object_key in self.fail_keys:
            raise RuntimeError(f"minio boom: {object_key}")
        self.uploads.append((str(object_key), str(local_path)))


def _make_transcoder(
    monkeypatch, tmp_path, ffmpeg: FakeFFmpeg, minio: FakeMinio
) -> Transcoder:
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "videomind.core.video_pipeline.transcode.get_ffmpeg", lambda: ffmpeg
    )
    monkeypatch.setattr(
        "videomind.core.video_pipeline.transcode.get_minio_client", lambda: minio
    )
    return Transcoder()


# ──────────────────────────── execute 编排 ────────────────────────────


async def test_execute_full_flow(monkeypatch, tmp_path):
    """完整编排：元信息透传、MinIO 双路径键、本地工件清干净。"""
    # 场景检测强制走"imagehash 缺失 → 均匀采样"分支（确定性，不依赖宿主装没装）
    monkeypatch.setitem(sys.modules, "imagehash", None)
    ffmpeg = FakeFFmpeg(frame_names=["frame_00000001.jpg", "frame_00000002.jpg"])
    minio = FakeMinio()
    t = _make_transcoder(monkeypatch, tmp_path, ffmpeg, minio)

    video = tmp_path / "video.mp4"
    video.write_bytes(b"v")
    result = await t.execute(video, "hash123")

    # 结果字段
    assert isinstance(result, TranscodeResult)
    assert result.audio_minio == "hash123/audio.ogg"
    assert result.keyframes_minio == [
        "hash123/frames/frame_00000001.jpg",
        "hash123/frames/frame_00000002.jpg",
    ]
    assert (result.duration_ms, result.width, result.height, result.fps) == (
        5000, 640, 360, 25.0,
    )
    # imagehash 缺失 → 均匀采样 9 个点（30s 间隔）
    assert result.scene_changes_ms == [i * 30_000 for i in range(1, 10)]

    # MinIO 交互：bucket 确保 + 音频 + 两帧
    assert minio.bucket_ensured
    keys = {k for k, _ in minio.uploads}
    assert keys == {
        "hash123/audio.ogg",
        "hash123/frames/frame_00000001.jpg",
        "hash123/frames/frame_00000002.jpg",
    }

    # 本地工件已清理（音频文件 / 关键帧目录 / 场景检测目录）
    temp = tmp_path / "transcode_temp"
    assert not (temp / "hash123_audio.ogg").exists()
    assert not (temp / "hash123_frames").exists()
    assert not (temp / "_scene_video").exists()


async def test_execute_scene_few_frames_returns_empty(monkeypatch, tmp_path):
    """抽帧 <2 → 场景检测直接 []（不够帧做 phash 对比）。"""
    ffmpeg = FakeFFmpeg(frame_names=[])  # 一帧都抽不出来
    minio = FakeMinio()
    t = _make_transcoder(monkeypatch, tmp_path, ffmpeg, minio)

    result = await t.execute(tmp_path / "v.mp4", "h")

    assert result.scene_changes_ms == []
    assert result.keyframes_minio == []


# ──────────────────────────── 场景检测分支 ────────────────────────────


async def test_scene_detection_without_imagehash_uniform_sampling(monkeypatch, tmp_path):
    """imagehash 缺失 → ImportError 兜底：均匀采样 9 点（30s 间隔），不炸。"""
    monkeypatch.setitem(sys.modules, "imagehash", None)
    ffmpeg = FakeFFmpeg(frame_names=["a.jpg", "b.jpg", "c.jpg"])
    t = _make_transcoder(monkeypatch, tmp_path, ffmpeg, FakeMinio())

    points = await t._detect_scene_changes(tmp_path / "v.mp4", duration_ms=300_000)

    assert points == [i * 30_000 for i in range(1, 10)]


async def test_scene_detection_fallback_sampling_when_too_few(monkeypatch, tmp_path):
    """phash 路径检测点过少 → MAX_INTERVAL 补采样（保底密度，不让下游没锚点）。

    用无法被 PIL 打开的伪 jpg：per-frame try/except 吞掉 → 检测点 0 → 触发兜底。
    需要 Pillow 在环境里（没有则该测试走不到 phash 路径，importorskip 跳过）。
    """
    pytest.importorskip("PIL.Image")
    pytest.importorskip("imagehash")
    ffmpeg = FakeFFmpeg(frame_names=["x.jpg", "y.jpg", "z.jpg"])
    t = _make_transcoder(monkeypatch, tmp_path, ffmpeg, FakeMinio())

    points = await t._detect_scene_changes(tmp_path / "v.mp4", duration_ms=90_000)

    # 90s / 30s = 3 → range(1, 3) → 2 个兜底点
    assert points == [30_000, 60_000]


# ──────────────────────────── 清理 / 上传容错 ────────────────────────────


def test_cleanup_artifacts_removes_all_and_tolerates_missing(monkeypatch, tmp_path):
    """清理三类工件；路径不存在/权限异常也不炸（已成功任务不因清理回滚失败）。"""
    t = _make_transcoder(monkeypatch, tmp_path, FakeFFmpeg(), FakeMinio())
    temp = tmp_path / "transcode_temp"
    (temp / "h_audio.ogg").parent.mkdir(parents=True, exist_ok=True)
    (temp / "h_audio.ogg").write_bytes(b"a")
    (temp / "h_frames").mkdir()
    (temp / "_scene_v").mkdir()

    t._cleanup_artifacts("h", tmp_path / "v.mp4")

    assert not (temp / "h_audio.ogg").exists()
    assert not (temp / "h_frames").exists()
    assert not (temp / "_scene_v").exists()
    # 再来一遍（全不存在）也不抛
    t._cleanup_artifacts("h", tmp_path / "v.mp4")


async def test_upload_keyframes_swallows_single_failure(monkeypatch, tmp_path):
    """单帧上传失败被吞（OCR 增强不阻塞管线），其余帧照常上传。"""
    minio = FakeMinio(fail_keys={"h/frames/bad.jpg"})
    t = _make_transcoder(monkeypatch, tmp_path, FakeFFmpeg(), minio)

    ok = tmp_path / "ok.jpg"
    bad = tmp_path / "bad.jpg"
    ok.write_bytes(b"1")
    bad.write_bytes(b"2")

    await t._upload_keyframes([ok, bad], "h")  # 不抛即通过

    assert [k for k, _ in minio.uploads] == ["h/frames/ok.jpg"]


def test_transcode_result_as_media_meta():
    r = TranscodeResult(
        video_local=Path("v.mp4"), audio_local=Path("a.ogg"), audio_minio="k",
        keyframes_local=[], duration_ms=1000, width=2, height=3, fps=4.0,
    )
    assert r.as_media_meta() == {
        "duration_ms": 1000, "width": 2, "height": 3, "fps": 4.0,
    }
