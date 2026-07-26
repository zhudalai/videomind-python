"""RRF 融合算法测试。"""
import uuid

from src.videomind.core.rag.rrf import rrf_fuse

ID1 = uuid.UUID("00000000-0000-0000-0000-000000000001")
ID2 = uuid.UUID("00000000-0000-0000-0000-000000000002")
ID3 = uuid.UUID("00000000-0000-0000-0000-000000000003")


def test_single_channel_passthrough():
    """单个通道传入 → 返回相同的 id 顺序。"""
    channel = [(ID1, 0.95), (ID2, 0.87), (ID3, 0.42)]
    result = rrf_fuse([channel])
    assert [cid for cid, _ in result] == [ID1, ID2, ID3]


def test_two_channels_merge():
    """两个通道有重叠 chunk_id → RRF 分数合并后重新排序。"""
    channel_a = [(ID1, 0.9), (ID2, 0.5), (ID3, 0.3)]
    channel_b = [(ID3, 0.8), (ID1, 0.2)]
    result = rrf_fuse([channel_a, channel_b])
    ids = [cid for cid, _ in result]
    # ID1 = 1/(60+0+1)+1/(60+1+1) = 1/61+1/62 ≈ 0.0325
    # ID3 = 1/(60+2+1)+1/(60+0+1) = 1/63+1/61 ≈ 0.0323
    # ID2 = 1/(60+1+1)+0          = 1/62     ≈ 0.0161
    assert ids == [ID1, ID3, ID2]
    # 验证分数值
    scores = {cid: round(score, 6) for cid, score in result}
    assert scores[ID1] == round(1 / 61 + 1 / 62, 6)
    assert scores[ID3] == round(1 / 63 + 1 / 61, 6)
    assert scores[ID2] == round(1 / 62, 6)


def test_empty_channels():
    """空列表输入 → 返回空列表。"""
    assert rrf_fuse([]) == []


def test_k_parameter():
    """不同的 k 值产出不同的排序。"""
    channel = [(ID1, 1.0), (ID2, 0.9), (ID3, 0.8)]
    rankings = [channel]
    result_k60 = [cid for cid, _ in rrf_fuse(rankings, k=60)]
    result_k0 = [cid for cid, _ in rrf_fuse(rankings, k=0)]
    # 单通道时排序相同，但分数不同
    scores_k60 = {cid: round(s, 6) for cid, s in rrf_fuse(rankings, k=60)}
    scores_k0 = {cid: round(s, 6) for cid, s in rrf_fuse(rankings, k=0)}
    # 排序相同
    assert result_k60 == result_k0 == [ID1, ID2, ID3]
    # 分数不同
    assert scores_k60 != scores_k0
    # 验证具体分数
    assert scores_k60[ID1] == round(1 / (60 + 0 + 1), 6)
    assert scores_k0[ID1] == round(1 / (0 + 0 + 1), 6)