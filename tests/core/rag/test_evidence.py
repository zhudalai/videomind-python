"""evidence_id 锚定模块的单元测试

验证 evidence_id 的生成、解析、正则引用提取等纯函数行为。
"""

import re
import uuid

import pytest

from videomind.core.rag.evidence import (
    EID_CITATION_RE,
    extract_evidence_ids,
    make_evidence_id,
    parse_evidence_id,
)

CHUNK_ID = uuid.uuid4()
CID_HEX = str(CHUNK_ID).replace("-", "")


class TestMakeEvidenceId:
    """测试 evidence_id 生成。"""

    def test_make_evidence_id_basic(self):
        """idx=0 生成 _01（1-based，两位数填充）。"""
        eid = make_evidence_id(CHUNK_ID, 0)
        expected = f"EID_{CID_HEX[:8]}_01"
        assert eid.startswith("EID_")
        assert eid == expected

    def test_make_evidence_id_second(self):
        """idx=1 生成 _02。"""
        eid = make_evidence_id(CHUNK_ID, 1)
        assert eid == f"EID_{CID_HEX[:8]}_02"

    def test_make_evidence_id_large_index(self):
        """idx=99 生成 _100（超过两位也保留原样）。"""
        eid = make_evidence_id(CHUNK_ID, 99)
        assert eid == f"EID_{CID_HEX[:8]}_100"


class TestParseEvidenceId:
    """测试 parse_evidence_id 解析。

    注意: make_evidence_id 只编码 UUID 前 8 位 hex，因此 parse 出的 UUID
    只能保证前 8 位 hex 一致，其余位为占位符（非原始 UUID）。
    """

    def test_round_trip(self):
        """生成→解析应恢复索引，且 UUID 前 8 hex 匹配原始值。"""
        eid = make_evidence_id(CHUNK_ID, 5)
        parsed_id, index = parse_evidence_id(eid)
        # 前 8 hex 应一致
        parsed_hex8 = str(parsed_id).replace("-", "")[:8]
        assert parsed_hex8 == CID_HEX[:8]
        assert index == 5
        # 应是合法 UUID
        assert isinstance(parsed_id, uuid.UUID)

    def test_round_trip_zero_index(self):
        """idx=0 的往返（1-based overflow 回归）。"""
        eid = make_evidence_id(CHUNK_ID, 0)
        parsed_id, index = parse_evidence_id(eid)
        parsed_hex8 = str(parsed_id).replace("-", "")[:8]
        assert parsed_hex8 == CID_HEX[:8]
        assert index == 0
        assert isinstance(parsed_id, uuid.UUID)

    def test_invalid_bare_string(self):
        """不含 EID_ 前缀的字符串抛出 ValueError。"""
        with pytest.raises(ValueError):
            parse_evidence_id("bad_format_nil")

    def test_invalid_too_short(self):
        """EID_ 前缀但后缀过短抛出 ValueError。"""
        with pytest.raises(ValueError):
            parse_evidence_id("EID_too_short")


class TestCitationRegex:
    """测试引用正则与文本提取。"""

    def test_extract_two_ids(self):
        """中英文混排文本中提取所有引用。"""
        text = "总结 [EID_a1b2c3d4_01] 基于证据 [EID_12345678_02] 分析"
        ids = extract_evidence_ids(text)
        assert ids == ["EID_a1b2c3d4_01", "EID_12345678_02"]

    def test_no_match_partial_prefix(self):
        """不完整前缀不应被提取。"""
        assert extract_evidence_ids("EID_a1") == []

    def test_regex_compiled(self):
        """EID_CITATION_RE 是 re.Pattern 实例。"""
        assert isinstance(EID_CITATION_RE, re.Pattern)

    def test_regex_matches_valid(self):
        """正则正确匹配合法引用（带方括号）。"""
        match = EID_CITATION_RE.fullmatch("[EID_abcdef12_05]")
        assert match is not None
        assert match.group(1) == "abcdef12"
        assert match.group(2) == "05"

    def test_regex_rejects_short_hex(self):
        """hex8 部分不足时不应 fullmatch。"""
        assert EID_CITATION_RE.fullmatch("[EID_abcde_01]") is None

    def test_regex_rejects_one_digit_index(self):
        """索引只有一位数字时不应 fullmatch。"""
        assert EID_CITATION_RE.fullmatch("[EID_abcdef12_5]") is None