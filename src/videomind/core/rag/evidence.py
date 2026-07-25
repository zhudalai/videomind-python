"""evidence_id 锚定模块 —— 证据引用的生成、解析与提取（纯函数，无外部依赖）。

引用格式:
    内部 ID:  EID_{chunk_id[:8]}_{idx:02d}
        - chunk_id[:8] 为 Chunk.id UUID 的前 8 位 hex（小写）
        - idx 为 1-based 两位数填充索引
    文本引用: [EID_{chunk_id[:8]}_{idx:02d}]  （含方括号，便于在回答中内联引用）
"""

import re
import uuid

# ----------------------------------------------------------------
# 常量
# ----------------------------------------------------------------

_CID_HEX_LEN = 8  # chunk_id 前 8 位 hex

# 内部解析用: 匹配不带方括号的 EID 字符串
_EID_INNER_PATTERN = re.compile(
    r'^EID_([a-z0-9]{8})_(\d{2})$'
)

# 文本引用提取: 匹配带方括号的 [EID_...] 形式
EID_CITATION_RE = re.compile(r'\[EID_([a-z0-9]{8})_(\d{2})\]')

# ------------------------------------------------------------
# API
# ------------------------------------------------------------


def make_evidence_id(chunk_id: uuid.UUID, index: int) -> str:
    """生成证据引用锚点。

    Args:
        chunk_id: Chunk 的 UUID 主键。
        index: 0-based 索引，内部转为 1-based 两位数填充。

    Returns:
        "EID_{hex8}_{idx+1:02d}" 格式字符串。
    """
    short = str(chunk_id).replace('-', '')[: _CID_HEX_LEN]
    return f"EID_{short}_{index + 1:02d}"


def parse_evidence_id(eid: str) -> tuple[uuid.UUID, int]:
    """从锚点字符串解析回 (chunk_id_uuid, 0-based index)。

    Args:
        eid: 不带方括号的 evidence_id 字符串，如 "EID_a1b2c3d4_05"。

    Returns:
        (chunk_id, index) 元组，index 为 0-based。

    Raises:
        ValueError: 格式不匹配时抛出。
    """
    match = _EID_INNER_PATTERN.match(eid)
    if not match:
        raise ValueError(f"无效的 evidence_id 格式: {eid!r}")
    sid = match.group(1)  # 8 位 hex
    # 将 8 hex 还原为 UUID 格式: 8-4-4-4-12
    full_id = f"{sid}-0000-4000-a000-000000000000"
    idx = int(match.group(2)) - 1  # 1-based → 0-based
    return uuid.UUID(full_id), idx


def extract_evidence_ids(text: str) -> list[str]:
    """从文本中提取所有 evidence_id 锚点（不含方括号）。

    Args:
        text: 包含 [EID_...] 引用的文本。

    Returns:
        按出现顺序排列的 evidence_id 字符串列表。
    """
    matches = EID_CITATION_RE.finditer(text)
    # group(0) 为 "[EID_...]", 裁掉首尾方括号得 "EID_..."
    return [m.group(0)[1:-1] for m in matches]


__all__ = [
    "EID_CITATION_RE",
    "extract_evidence_ids",
    "make_evidence_id",
    "parse_evidence_id",
]