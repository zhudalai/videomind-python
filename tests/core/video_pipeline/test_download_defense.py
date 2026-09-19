"""下载阶段防御性测试 —— 错误分级翻译、磁盘预检、产物定位、失败清理。

守护契约（对应 core/video_pipeline/download.py + core/errors.py 方向 3.5）：
- yt-dlp DownloadError 按消息标记二分：404/私享/登录墙 → NonRetryableError
  （重试必然同结果，盲重浪费算力）；网络抖动/5xx → RetryableError（退避可恢复）。
- 磁盘预检低于下限 → RetryableError（释放后重试可恢复）；预检本身 OSError 不阻塞。
- 失败路径清理 subdir 半成品（.part/.ytdl 残留曾积数百 MB）；subdir=None 的
  共享根目录绝不清。
- yt-dlp opts 必须带 socket_timeout/retries/fragment_retries（防慢源挂死 worker）。
"""

from __future__ import annotations

import hashlib
import shutil
from types import SimpleNamespace

import pytest

from videomind.config import get_settings
from videomind.core.errors import NonRetryableError, RetryableError
from videomind.core.video_pipeline.download import (
    Downloader,
    DownloadResult,
    _check_disk_space,
    _resolve_downloaded_path,
    _sha256_file,
    _translate_download_error,
)


@pytest.fixture(autouse=True)
def _fresh_settings():
    """每个测试全新 Settings（DOWNLOAD_DIR 等 env 改动须重新读）。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ──────────────────────────── 错误分级翻译 ────────────────────────────


def test_translate_download_error_deterministic_markers():
    """命中确定性标记（404/私享/登录/地区封锁/版权）→ NonRetryableError。"""
    pytest.importorskip("yt_dlp")
    from yt_dlp.utils import DownloadError

    deterministic = [
        "ERROR: [youtube] abc: Video unavailable. This video is not available",
        "ERROR: [youtube] abc: Private video. Sign in",
        "ERROR: 404 Not Found",
        "ERROR: The video has been removed by the uploader",
        "ERROR: This video is not available in your country",  # 地区封锁变体
        "ERROR: This video is age-restricted",
        "ERROR: Unsupported URL: https://example.com/page",
        "ERROR: This video is no longer available due to a copyright claim",
    ]
    for msg in deterministic:
        e = _translate_download_error(DownloadError(msg))
        assert isinstance(e, NonRetryableError), f"应判不可重试: {msg}"


def test_translate_download_error_transient_goes_retryable():
    """网络抖动 / 源站 5xx / 超时 → RetryableError（退避后重试可恢复）。"""
    pytest.importorskip("yt_dlp")
    from yt_dlp.utils import DownloadError

    for msg in [
        "ERROR: unable to download video data: Connection reset by peer",
        "ERROR: HTTP Error 500: Internal Server Error",
        "ERROR: HTTP Error 503: Service Unavailable",
        "ERROR: timed out",
    ]:
        e = _translate_download_error(DownloadError(msg))
        assert isinstance(e, RetryableError), f"应判可重试: {msg}"


def test_translate_passthrough_non_download_error():
    """非 DownloadError 原样返回（socket/OSError 交 BaseVideoTask autoretry 兜底）。"""
    v = ValueError("contract violation")
    assert _translate_download_error(v) is v


# ──────────────────────────── 磁盘预检 ────────────────────────────


def test_disk_space_below_min_raises_retryable(monkeypatch, tmp_path):
    """剩余空间低于下限 → RetryableError（释放后重试可恢复，非终态）。"""
    monkeypatch.setattr(
        shutil, "disk_usage",
        lambda p: SimpleNamespace(total=1 << 32, used=0, free=int(0.5 * (1 << 30))),
    )
    with pytest.raises(RetryableError, match="磁盘剩余空间不足"):
        _check_disk_space(tmp_path, 2.0)


def test_disk_space_enough_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        shutil, "disk_usage",
        lambda p: SimpleNamespace(total=1 << 33, used=0, free=10 * (1 << 30)),
    )
    _check_disk_space(tmp_path, 2.0)  # 不抛即通过


def test_disk_space_probe_oserror_not_blocking(monkeypatch, tmp_path):
    """预检本身失败（跨平台盘符/权限差异）不阻塞下载——真盘满由写入阶段兜底。"""

    def _boom(p):
        raise OSError("weird fs")

    monkeypatch.setattr(shutil, "disk_usage", _boom)
    _check_disk_space(tmp_path, 999.0)  # 不抛即通过


# ──────────────────────────── 产物定位 / 哈希 ────────────────────────────


def test_resolve_prefers_requested_downloads_filepath(tmp_path):
    """merge 后最终路径优先（requested_downloads[0].filepath）。"""
    f = tmp_path / "merged.mp4"
    f.write_bytes(b"x")
    info = {"id": "abc", "ext": "mkv", "requested_downloads": [{"filepath": str(f)}]}
    assert _resolve_downloaded_path(tmp_path, info) == f


def test_resolve_falls_back_to_id_ext(tmp_path):
    """无 requested_downloads → out_dir/{id}.{ext} 回退探测。"""
    (tmp_path / "vid1.webm").write_bytes(b"x")
    assert _resolve_downloaded_path(tmp_path, {"id": "vid1", "ext": "webm"}) == tmp_path / "vid1.webm"


def test_resolve_missing_artifact_is_nonretryable(tmp_path):
    """下载"成功"但产物不存在 = 契约违规（yt-dlp 行为变更）→ NonRetryableError。"""
    with pytest.raises(NonRetryableError, match="找不到产物"):
        _resolve_downloaded_path(tmp_path, {"id": "ghost", "ext": "mp4"})


async def test_sha256_file_chunked(tmp_path):
    """分块 SHA256 与一次性计算一致（content_hash 去重键的正确性根基）。"""
    content = b"hello world" * 100_000  # >1MB，跨分块边界
    p = tmp_path / "big.bin"
    p.write_bytes(content)
    assert await _sha256_file(p) == hashlib.sha256(content).hexdigest()


def test_download_result_as_media_meta(tmp_path):
    r = DownloadResult(
        url="u", local_path=tmp_path / "a.mp4", filename="a.mp4",
        duration_ms=1000, width=1, height=2, fps=3.0, title="t", content_hash="h",
    )
    meta = r.as_media_meta()
    assert meta == {
        "duration_ms": 1000, "width": 1, "height": 2,
        "fps": 3.0, "title": "t", "filename": "a.mp4", "content_hash": "h",
    }


# ──────────────────────────── Downloader 主流程（mock yt-dlp）────────────────────────────


def _install_fake_ytdlp(monkeypatch, info: dict | None = None, error: Exception | None = None):
    """装假 YoutubeDL：返回固定 info 或抛错；captured 记录构造 opts 与调用参数。"""

    captured: dict = {}

    class _FakeYDL:
        def __init__(self, opts):
            captured["opts"] = dict(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            captured["url"] = url
            captured["download"] = download
            if error is not None:
                raise error
            return dict(info or {})

    monkeypatch.setattr("yt_dlp.YoutubeDL", _FakeYDL)
    return captured


def _make_downloader(monkeypatch, tmp_path) -> Downloader:
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    return Downloader()


async def test_download_success_full_flow(monkeypatch, tmp_path):
    """完整下载流程：opts 透传 + 回退定位产物 + 分块 SHA256 + 元信息映射。"""
    content = b"fake video bytes" * 1000
    out_dir = tmp_path / "bucket1"
    out_dir.mkdir(parents=True)
    (out_dir / "abc123.mp4").write_bytes(content)

    captured = _install_fake_ytdlp(monkeypatch, info={
        "id": "abc123", "ext": "mp4",
        "duration": 3.5, "width": 1280, "height": 720,
        "fps": 30.0, "title": "测试视频",
    })
    d = _make_downloader(monkeypatch, tmp_path)

    result = await d.download("https://example.com/v/abc123", subdir="bucket1")

    assert result.local_path == out_dir / "abc123.mp4"
    assert result.filename == "abc123.mp4"
    assert result.duration_ms == 3500
    assert (result.width, result.height, result.fps) == (1280, 720, 30.0)
    assert result.title == "测试视频"
    assert result.content_hash == hashlib.sha256(content).hexdigest()
    # yt-dlp 兜底 opts 必须带：慢源挂死 worker 的防线
    opts = captured["opts"]
    assert opts["noprogress"] is True
    assert "socket_timeout" in opts and "retries" in opts and "fragment_retries" in opts
    assert captured["download"] is True


async def test_download_opts_follow_settings(monkeypatch, tmp_path):
    """DOWNLOAD_SOCKET_TIMEOUT_S / DOWNLOAD_RETRIES / YTDLP_PROXY 透传到 ydl opts。"""
    monkeypatch.setenv("DOWNLOAD_SOCKET_TIMEOUT_S", "7.5")
    monkeypatch.setenv("DOWNLOAD_RETRIES", "9")
    monkeypatch.setenv("YTDLP_PROXY", "http://127.0.0.1:7890")
    info = {"id": "v", "ext": "mp4", "requested_downloads": []}
    (tmp_path / "v.mp4").write_bytes(b"x")
    captured = _install_fake_ytdlp(monkeypatch, info=info)
    d = _make_downloader(monkeypatch, tmp_path)

    await d.download("https://example.com/v")

    assert captured["opts"]["socket_timeout"] == 7.5
    assert captured["opts"]["retries"] == 9
    assert captured["opts"]["fragment_retries"] == 9
    assert captured["opts"]["proxy"] == "http://127.0.0.1:7890"


async def test_download_failure_cleans_subdir_and_translates(monkeypatch, tmp_path):
    """失败时：subdir 半成品目录整体清理 + 错误翻译成 NonRetryableError。"""
    pytest.importorskip("yt_dlp")
    from yt_dlp.utils import DownloadError

    out_dir = tmp_path / "bucket2"
    out_dir.mkdir(parents=True)
    (out_dir / "abc.part").write_bytes(b"partial")  # 半成品残留

    _install_fake_ytdlp(
        monkeypatch, error=DownloadError("ERROR: 404 Not Found: this video is private")
    )
    d = _make_downloader(monkeypatch, tmp_path)

    with pytest.raises(NonRetryableError):
        await d.download("https://example.com/v/private", subdir="bucket2")

    assert not out_dir.exists(), "失败后半成品 subdir 必须清理"


async def test_download_failure_without_subdir_keeps_shared_root(monkeypatch, tmp_path):
    """subdir=None 的失败绝不清共享下载根目录（否则并发任务互相拆台）。"""
    pytest.importorskip("yt_dlp")
    from yt_dlp.utils import DownloadError

    (tmp_path / "other_media.mp4").write_bytes(b"untouchable")
    _install_fake_ytdlp(
        monkeypatch, error=DownloadError("ERROR: HTTP Error 500: Internal Server Error")
    )
    d = _make_downloader(monkeypatch, tmp_path)

    with pytest.raises(RetryableError):
        await d.download("https://example.com/v/ok")

    assert (tmp_path / "other_media.mp4").exists()


async def test_extract_info_only_sets_skip_download(monkeypatch, tmp_path):
    """纯探测（预校验 URL/估时长）必须 skip_download=True，不真下文件。"""
    captured = _install_fake_ytdlp(
        monkeypatch, info={"id": "v", "duration": 12.0, "title": "probe"}
    )
    d = _make_downloader(monkeypatch, tmp_path)

    info = await d.extract_info_only("https://example.com/v")

    assert captured["opts"]["skip_download"] is True
    assert captured["download"] is False
    assert info["duration"] == 12.0
