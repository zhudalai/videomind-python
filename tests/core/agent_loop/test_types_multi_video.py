"""Multi-video agent_loop 数据结构扩展测试."""

from __future__ import annotations

from videomind.core.agent_loop import types as t  # noqa: F401  触发导入


def test_video_meta_defaults() -> None:
    vm = t.VideoMeta(media_id="m1", filename="a.mp4", duration_ms=1000)
    assert vm.media_id == "m1" and vm.filename == "a.mp4" and vm.duration_ms == 1000
    vm2 = t.VideoMeta(media_id="m2", filename="b.mp4")
    assert vm2.duration_ms is None


def test_agent_state_multi_video_fields_default_empty() -> None:
    state = t.AgentState(goal="g")
    assert state.media_ids == []
    assert state.video_meta == []
    assert state.retrieved_evidence_ids == set()
    assert state.trace_id  # 仍有默认 uuid4().hex


def test_subtask_search_query_default_empty() -> None:
    st = t.SubTask(id="t1", description="d", required_evidence_type="text")
    assert st.search_query == ""


def test_evidence_new_fields_default_and_legacy_backcompat() -> None:
    ev = t.Evidence(id="EID_1")
    assert ev.chunk_id == "" and ev.content == ""
    assert ev.source_type == "" and ev.score == 0.0
    assert ev.start_ms is None and ev.end_ms is None
    assert ev.media_id == "" and ev.media_title == ""
    # 旧字段保留默认，兼容 verifier 旧测试
    assert ev.timestamp_ms == 0 and ev.source == "text"
    # 旧 keyword 构造仍工作（test_verifier.py 依赖）
    ev2 = t.Evidence(id="EID_01", timestamp_ms=10000, source="audio",
                    content="x", chunk_id="c1")
    assert ev2.timestamp_ms == 10000 and ev2.source == "audio" and ev2.content == "x" and ev2.chunk_id == "c1"