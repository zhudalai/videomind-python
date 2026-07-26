"""EvidenceVerifier 证据校验器测试."""

from __future__ import annotations

import pytest

from videomind.core.agent_loop.types import Evidence


def test_verifier_all_passed_with_valid_evidence() -> None:
    """所有证据都在合法范围内时返回 passed=True，failed 为空。"""
    from videomind.core.agent_loop.verifier import EvidenceVerifier

    evidence = [
        Evidence(id="EID_01", timestamp_ms=10000, source="audio", content="有效内容", chunk_id="c1"),
        Evidence(id="EID_02", timestamp_ms=60000, source="text", content="另一段", chunk_id="c2"),
    ]
    verifier = EvidenceVerifier()
    passed, failed = verifier.verify_all(evidence, duration_ms=120000)

    assert passed is True
    assert len(failed) == 0


def test_verifier_detects_out_of_range() -> None:
    """timestamp_ms 越界和负值应被检测，content 为空也应被检测。"""
    from videomind.core.agent_loop.verifier import EvidenceVerifier

    evidence = [
        Evidence(id="EID_01", timestamp_ms=0, source="text", content="ok", chunk_id="c1"),
        Evidence(id="EID_02", timestamp_ms=-1, source="text", content="bad", chunk_id="c2"),
        Evidence(id="EID_03", timestamp_ms=999999, source="text", content="out", chunk_id="c3"),
        Evidence(id="EID_04", timestamp_ms=5000, source="text", content="", chunk_id="c4"),
    ]
    verifier = EvidenceVerifier()
    passed, failed = verifier.verify_all(evidence, duration_ms=120000)

    assert passed is False
    # 负时间戳、越界、空内容 —— 共 3 条失败
    assert len(failed) == 3