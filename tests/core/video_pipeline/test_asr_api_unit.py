"""ASR API 路径单元测试 —— Groq OpenAI 兼容 transcription 端点。

守护 _transcribe_api 的行为契约（对应路线 B：local 主力 + API 后备双路径）。
设计把"构建请求 / 解析响应"抽成纯函数，单测无 mock 无网络；真调 Groq 留集成测。

守护契约：
- URL = {base_url}/audio/transcriptions（OpenAI 兼容，Groq 实测支持）
- Authorization 由 AsyncClient 默认 headers 带（multipart body 不手设 Content-Type，
  避免缺 boundary 致 400）
- data["response_format"]="verbose_json"：必须，json 只有 text、不带 segments → 拿不到 chunks
- data["model"]=asr_api_model：透传配置（默认 whisper-large-v3-turbo）
- 多语言中立：data 不含 "language" 字段 → Groq/whisper 自动检测（与 local 路径一致）
- 热词：prompt 空则不传（多语言中立），配了则原样透传（Groq prompt = whisper initial_prompt）
- 响应解析：verbose_json → ASRResult，segments 的 start/end（秒）→ chunks 的 start_ms/end_ms
"""

from __future__ import annotations

import pytest

from videomind.core.video_pipeline.asr import (
    ASRResult,
    _build_groq_request,
    _parse_groq_response,
)

BASE = "https://api.groq.com/openai/v1"
AUDIO = b"RIFF....fake wav bytes...."


# ────────────── _build_groq_request ──────────────


def test_build_url_appends_audio_transcriptions():
    url, _data, _files = _build_groq_request(BASE, AUDIO, "a.wav", "whisper-large-v3-turbo", None)
    assert url == "https://api.groq.com/openai/v1/audio/transcriptions"


def test_build_url_strips_trailing_slash_from_base():
    url, _d, _f = _build_groq_request(BASE + "/", AUDIO, "a.wav", "m", None)
    assert url == "https://api.groq.com/openai/v1/audio/transcriptions"


def test_build_carries_model_name():
    _u, data, _f = _build_groq_request(BASE, AUDIO, "a.wav", "whisper-large-v3-turbo", None)
    assert data["model"] == "whisper-large-v3-turbo"


def test_build_requests_verbose_json_for_segments():
    """response_format 必须 verbose_json —— 否则拿不到 segments 时间戳。"""
    _u, data, _f = _build_groq_request(BASE, AUDIO, "a.wav", "m", None)
    assert data["response_format"] == "verbose_json"


def test_build_omits_language_for_multilingual_autodetect():
    """多语言契约：data 不含 language —— 让 Groq/whisper 自动检测。"""
    _u, data, _f = _build_groq_request(BASE, AUDIO, "a.wav", "m", None)
    assert "language" not in data


def test_build_omits_prompt_when_none():
    """空热词 → 不传 prompt（多语言中立，与 local 路径 initial_prompt=None 对齐）。"""
    _u, data, _f = _build_groq_request(BASE, AUDIO, "a.wav", "m", None)
    assert "prompt" not in data


def test_build_forwards_prompt_when_set():
    """配了热词 → data["prompt"] 原样透传（Groq prompt = whisper initial_prompt 偏置）。"""
    _u, data, _f = _build_groq_request(BASE, AUDIO, "a.wav", "m", "VideoMind, RAG, 语音识别")
    assert data["prompt"] == "VideoMind, RAG, 语音识别"


def test_build_files_carries_audio_bytes_and_filename():
    _u, _d, files = _build_groq_request(BASE, AUDIO, "clip.wav", "m", None)
    # httpx files 三元组：(filename, bytes, content_type) 或 (filename, bytes)
    assert "file" in files
    entry = files["file"]
    assert entry[1] == AUDIO  # 音频字节透传
    assert entry[0] == "clip.wav"  # 文件名透传（Groq 按扩展名判格式）


# ────────────── _parse_groq_response ──────────────


def test_parse_basic_fields_from_verbose_json():
    data = {
        "text": "你好世界",
        "language": "chinese",
        "duration": 3.5,
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.5, "text": "你好"},
            {"id": 1, "start": 1.5, "end": 3.5, "text": "世界"},
        ],
    }
    r = _parse_groq_response(data, model_name="whisper-large-v3-turbo")
    assert isinstance(r, ASRResult)
    assert r.full_text == "你好世界"
    assert r.language == "chinese"
    assert r.model_name == "whisper-large-v3-turbo"
    assert r.duration_sec == pytest.approx(3.5)


def test_parse_segments_to_chunks_with_ms():
    """segments 的 start/end（秒）→ chunks 的 start_ms/end_ms（毫秒）。"""
    data = {
        "text": "a b",
        "language": "english",
        "duration": 2.0,
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "a"},
            {"start": 1.0, "end": 2.0, "text": "b"},
        ],
    }
    r = _parse_groq_response(data, model_name="m")
    assert len(r.chunks) == 2
    assert r.chunks[0] == {"index": 0, "start_ms": 0, "end_ms": 1000, "text": "a"}
    assert r.chunks[1] == {"index": 1, "start_ms": 1000, "end_ms": 2000, "text": "b"}


def test_parse_strips_segment_text():
    """对齐 local 路径 seg.text.strip() —— Groq 段文本透传时去首尾空白。"""
    data = {"text": "x", "language": "en", "duration": 0.1,
            "segments": [{"start": 0.0, "end": 0.1, "text": "  hello  "}]}
    r = _parse_groq_response(data, model_name="m")
    assert r.chunks[0]["text"] == "hello"


def test_parse_empty_segments_yields_empty_chunks():
    """无 segments → chunks=[]，duration 兜底 0。"""
    data = {"text": "", "language": "", "segments": []}
    r = _parse_groq_response(data, model_name="m")
    assert r.chunks == []
    assert r.full_text == ""
    assert r.duration_sec == 0.0


def test_parse_missing_text_field_defaults_empty():
    """响应缺 text 字段 → full_text=""，不 KeyError。"""
    data = {"language": "en", "segments": []}
    r = _parse_groq_response(data, model_name="m")
    assert r.full_text == ""


def test_parse_duration_fallback_to_last_chunk_end():
    """顶层无 duration → 用最后一段 end 秒兜底。"""
    data = {"text": "x", "language": "en",
            "segments": [{"start": 0.0, "end": 4.2, "text": "x"}]}  # 无 duration 字段
    r = _parse_groq_response(data, model_name="m")
    assert r.duration_sec == pytest.approx(4.2)
