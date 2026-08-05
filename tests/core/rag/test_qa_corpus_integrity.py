"""QA 评估集数据完整性 + self-retrieval 防线单元测试（CI gate）。

保护 scripts/eval/qa_corpus.json 与 scripts/eval/gold_chunks.json 两条人工标注
资产，防止后续手改引入：
  - 结构破坏（字段缺失 / 类型错 / id 不唯一）
  - gold 失配（gold_chunks 内容缺失、语言/视频对不上）
  - self-retrieval 退化：query 与 gold chunk 连续字面重叠（LCS）超过阈值，
    即 query 直接拷贝 chunk 独特锚点 → grep 即满分、测不出检索算法价值。

LCS 阈值取 5：中日文功能词 / 通用动词短重叠无损测试有效性（"氏がなぜ""肩入れし"
属句式功能词或通用动词），超过即视为检索锚点字面拷贝，红。
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from pathlib import Path

import pytest

# tests/core/rag/ -> ../../.. 项目根 -> scripts/eval/
_EVAL_DIR = Path(__file__).resolve().parents[3] / "scripts" / "eval"
_QA_PATH = _EVAL_DIR / "qa_corpus.json"
_GOLD_PATH = _EVAL_DIR / "gold_chunks.json"

# self-retrieval 防线阈值：中日文连续字面子串最长允许长度。
# 5 容忍功能句式 / 通用动词；实测最长 LCS=5（"が肩入れし""都に呼び戻"通用动作非专名）。
_LCS_THRESHOLD = 5


def _load_json(path: Path) -> list | dict:
    assert path.exists(), f"评估集缺失: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _lcs_len(s: str, t: str) -> int:
    """最长公共连续子串长度（字符级，O(n*m) DP）。"""
    n, m = len(s), len(t)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    best = 0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if s[i - 1] == t[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
                if dp[i][j] > best:
                    best = dp[i][j]
    return best


# ───────────────────────── 结构与引用完整性 ─────────────────────────


class TestQACorpusStructure:
    """qa_corpus.json 结构 + 字段 + 唯一性。"""

    def setup_method(self):
        self.qa = _load_json(_QA_PATH)

    def test_is_list_with_expected_size(self):
        assert isinstance(self.qa, list)
        # 当前种子集 9 条（3 中 + 6 日）。扩展时更新下界。
        assert len(self.qa) >= 9

    def test_each_item_has_required_fields(self):
        required = {"qid", "language", "difficulty", "media_id", "query", "gold_chunk_id", "rationale"}
        for item in self.qa:
            missing = required - set(item.keys())
            assert not missing, f"{item.get('qid')} 缺字段 {missing}"

    def test_qids_unique(self):
        qids = [i["qid"] for i in self.qa]
        assert len(qids) == len(set(qids)), "qid 重复"

    def test_language_values_valid(self):
        for item in self.qa:
            assert item["language"] in {"zh", "ja"}, f"{item['qid']} 语言非法: {item['language']}"

    def test_difficulty_values_valid(self):
        for item in self.qa:
            assert item["difficulty"] in {"normal", "hard"}, (
                f"{item['qid']} 难度非法: {item['difficulty']}"
            )

    def test_media_id_is_uuid(self):
        for item in self.qa:
            uuid.UUID(item["media_id"])  # 解析失败即抛错

    def test_gold_chunk_id_is_uuid(self):
        for item in self.qa:
            uuid.UUID(item["gold_chunk_id"])

    def test_query_nonempty(self):
        for item in self.qa:
            assert len(item["query"].strip()) >= 5, f"{item['qid']} query 过短"

    def test_rationale_nonempty(self):
        for item in self.qa:
            assert len(item["rationale"].strip()) >= 10, f"{item['qid']} rationale 过短（标注依据缺失）"

    def test_language_distribution(self):
        """确保中/日双语均有覆盖（关键字段化）。"""
        langs = Counter(i["language"] for i in self.qa)
        assert langs["zh"] >= 3, "中文样本不足"
        assert langs["ja"] >= 6, "日文样本不足（catches P0-1 BM25 CJK 回归）"

    def test_difficulty_distribution(self):
        """normal 与 hard 均有覆盖（hard 测 cross-encoder 抽象提问判别力）。"""
        diffs = Counter(i["difficulty"] for i in self.qa)
        assert diffs["hard"] >= 3, "hard 样本不足"
        assert diffs["normal"] >= 3, "normal 样本不足"


# ───────────────────────── gold_chunks 引用对齐 ─────────────────────────


class TestGoldChunksAlignment:
    """gold_chunks.json 必须覆盖所有 gold_chunk_id，且语言/视频对齐。"""

    def setup_method(self):
        self.qa = _load_json(_QA_PATH)
        self.gold = _load_json(_GOLD_PATH)

    def test_gold_covers_all_gold_ids(self):
        qa_ids = {i["gold_chunk_id"] for i in self.qa}
        gold_ids = set(self.gold.keys())
        missing = qa_ids - gold_ids
        assert not missing, f"gold_chunks 缺: {missing}"

    def test_gold_content_nonempty(self):
        for cid, g in self.gold.items():
            assert len(g["content"].strip()) >= 20, f"{cid} gold content 过短"

    def test_gold_language_matches_qa(self):
        for item in self.qa:
            g = self.gold[item["gold_chunk_id"]]
            g_lang = "zh" if g["language"] == "中文" else "ja" if g["language"] == "日文" else None
            assert g_lang == item["language"], (
                f"{item['qid']} 语言不齐: qa={item['language']} gold={g['language']}"
            )

    def test_gold_media_id_matches_qa(self):
        for item in self.qa:
            g = self.gold[item["gold_chunk_id"]]
            assert g["media_id"] == item["media_id"], f"{item['qid']} media_id 不齐"

    def test_gold_chunk_id_format_uuid(self):
        for cid in self.gold:
            uuid.UUID(cid)


# ───────────────────────── self-retrieval 防线 ─────────────────────────


class TestSelfRetrievalGuard:
    """query 与 gold chunk 连续字面重叠 LCS <= 阈值（防 grep 式自检索）。

    超阈值即说明 query 直接拷贝了 chunk 的独立锚点短语，BM25 字面命中即可满分，
    recall@k 虚高，评测失效。阈值 5 容忍功能句式 / 通用动词重叠。
    """

    def setup_method(self):
        self.qa = _load_json(_QA_PATH)
        self.gold = _load_json(_GOLD_PATH)

    def test_no_query_chunk_lcs_exceeds_threshold(self):
        worst = []
        for item in self.qa:
            g = self.gold[item["gold_chunk_id"]]
            n = _lcs_len(item["query"], g["content"])
            worst.append((item["qid"], n))
            assert n <= _LCS_THRESHOLD, (
                f"{item['qid']} LCS={n} > {_LCS_THRESHOLD}: query 与 gold 连续字面重叠过长，"
                f"疑 self-retrieval 退化，请改写 query 规避独特实体锚点"
            )
        # 顺手校验最坏值有迹可循（回归防护）
        worst.sort(key=lambda x: -x[1])


