"""语义缓存单元测试 —— L1/L2 两级命中、桶隔离、LRU trim、失效与 fail-open。

零外部依赖：FakeRedis（内存 dict 实现 Redis 子集）+ FakeEmbedder（固定向量）。
守护契约（对应 src/videomind/core/rag/semantic_cache.py 设计要点）：
- 两级命中：L1 规范化精确匹配（零嵌入成本，命中时 embedder 不得被调用）；
  L2 桶内余弦 ≥ 阈值（标点/近义变体兜底）。
- media_ids 一致硬约束：同一问题在不同媒体集上绝不互相命中（集合语义：排序后同桶）。
- fail-open：Redis 断连 / 嵌入失败 / JSON 损坏一律跳过缓存，绝不阻断问答。
- store 三写一体（entry + bucket ZSET + media 反向索引 SET）+ LRU trim 到 max_entries。
- invalidate 反向索引交叉清理（条目被 N 个 media 引用时逐一摘除）。
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from videomind.config import get_settings
from videomind.core.rag import semantic_cache as sc

# ──────────────────────────── 内存 FakeRedis ────────────────────────────


class FakeRedis:
    """内存版 Redis 子集：仅实现 semantic_cache 用到的命令。

    ZSET 按 (score, 插入序) 稳定排序——sorted 稳定 + dict 保序，
    同分时先插入者排前（视为最旧，LRU 逐出语义确定可测）。
    calls 记录调用名，用于断言"零嵌入成本"/"fail-open 不触碰 Redis"等契约。
    """

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.sets: dict[str, set[str]] = {}
        self.expires: list[tuple[str, int]] = []
        self.calls: list[str] = []

    async def get(self, key: str):
        self.calls.append("get")
        return self.kv.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.calls.append("set")
        self.kv[key] = value

    async def mget(self, keys):
        self.calls.append("mget")
        return [self.kv.get(k) for k in keys]

    async def zadd(self, key: str, mapping: dict[str, float]):
        self.calls.append("zadd")
        self.zsets.setdefault(key, {}).update(mapping)

    def _zsorted_members(self, key: str) -> list[str]:
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])
        return [m for m, _ in items]

    async def zrange(self, key: str, start: int, end: int, withscores: bool = False):
        self.calls.append("zrange")
        members = self._zsorted_members(key)
        n = len(members)
        if end < 0:
            end = n + end
        sel = members[start : end + 1] if n else []
        if withscores:
            sm = dict(self.zsets.get(key, {}))
            return [(m, sm[m]) for m in sel]
        return sel

    async def zcard(self, key: str):
        self.calls.append("zcard")
        return len(self.zsets.get(key, {}))

    async def zrem(self, key: str, *members: str):
        self.calls.append("zrem")
        z = self.zsets.get(key, {})
        return sum(1 for m in members if z.pop(m, None) is not None)

    async def zremrangebyrank(self, key: str, start: int, end: int):
        self.calls.append("zremrangebyrank")
        members = self._zsorted_members(key)
        n = len(members)
        if end < 0:
            end = n + end
        victims = members[start : end + 1] if n else []
        z = self.zsets.get(key, {})
        for m in victims:
            z.pop(m, None)
        return len(victims)

    async def expire(self, key: str, ttl: int):
        self.calls.append("expire")
        self.expires.append((key, ttl))
        return True

    async def sadd(self, key: str, *members: str):
        self.calls.append("sadd")
        self.sets.setdefault(key, set()).update(members)

    async def smembers(self, key: str):
        self.calls.append("smembers")
        return set(self.sets.get(key, set()))

    async def srem(self, key: str, *members: str):
        self.calls.append("srem")
        s = self.sets.get(key, set())
        return sum(1 for m in members if m in s and not s.discard(m))

    async def delete(self, *keys: str):
        self.calls.append("delete")
        removed = 0
        for k in keys:
            removed += int(self.kv.pop(k, None) is not None)
            removed += int(self.zsets.pop(k, None) is not None)
            removed += int(self.sets.pop(k, None) is not None)
        return removed

    def pipeline(self, transaction: bool = False):
        self.calls.append("pipeline")
        return FakePipeline(self)


class FakePipeline:
    """语义缓存 store 的 pipe 调用：同步写宿主（redis-py 的 pipe.set(...) 不 await，
    只在 execute() 时批量发——这里立即写，execute 为兼容空操作）。"""

    def __init__(self, host: FakeRedis) -> None:
        self._host = host

    def set(self, key: str, value: str, ex: int | None = None):
        self._host.kv[key] = value
        if ex is not None:
            self._host.expires.append((key, ex))

    def zadd(self, key: str, mapping: dict[str, float]):
        self._host.zsets.setdefault(key, {}).update(mapping)

    def expire(self, key: str, ttl: int):
        self._host.expires.append((key, ttl))

    def sadd(self, key: str, *members: str):
        self._host.sets.setdefault(key, set()).update(members)

    async def execute(self):
        return []


class FakeEmbedder:
    """L2 probe 的假嵌入后端：固定向量；error 非 None 时抛错（fail-open 测试）。"""

    def __init__(self, vec: list[float] | None = None, error: Exception | None = None):
        self.vec = vec if vec is not None else [1.0, 0.0]
        self.error = error
        self.calls: list[list[str]] = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(vectors=[list(self.vec) for _ in texts])


# ──────────────────────────── fixtures ────────────────────────────


@pytest.fixture(autouse=True)
def _cache_settings(monkeypatch):
    """每个测试显式设语义缓存配置 + 全新 Settings（防宿主 .env 干扰）。"""
    monkeypatch.setenv("SEMANTIC_CACHE_ENABLED", "true")
    monkeypatch.setenv("SEMANTIC_CACHE_TTL_S", "21600")
    monkeypatch.setenv("SEMANTIC_CACHE_THRESHOLD", "0.92")
    monkeypatch.setenv("SEMANTIC_CACHE_MAX_ENTRIES", "256")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_redis(monkeypatch) -> FakeRedis:
    r = FakeRedis()
    monkeypatch.setattr("videomind.infrastructure.cache.redis.get_redis", lambda: r)
    return r


@pytest.fixture
def fake_embed(monkeypatch) -> FakeEmbedder:
    e = FakeEmbedder()
    monkeypatch.setattr(
        "videomind.core.video_pipeline.embed.get_embedding_backend", lambda: e
    )
    return e


# 固定媒体 ID（确定性，便于断言失败时阅读）
M1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
M2 = uuid.UUID("22222222-2222-2222-2222-222222222222")


# ──────────────────────────── 纯函数契约 ────────────────────────────


def test_normalize_query_collapses_whitespace_and_lowercases():
    """空白压缩 + 首尾裁剪 + 小写：'  What  IS it ' ≡ 'what is it'。"""
    assert sc.normalize_query("  What  IS it ") == "what is it"
    assert sc.normalize_query("what is it") == sc.normalize_query("WHAT  IS  IT\n")


def test_normalize_query_preserves_punctuation():
    """不做去标点：'3.5版本' 与 '35版本' 必须不同键（防吞语义差异）。"""
    assert sc.normalize_query("3.5版本") != sc.normalize_query("35版本")


def test_cosine_similarity_contracts():
    """同向→1.0、正交→0.0、长度不一致/零向量/空→0.0（防御不崩）。"""
    assert abs(sc.cosine_similarity([1.0, 2.0], [2.0, 4.0]) - 1.0) < 1e-9
    assert abs(sc.cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9
    assert sc.cosine_similarity([1.0], [1.0, 0.0]) == 0.0
    assert sc.cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert sc.cosine_similarity([], []) == 0.0


def test_bucket_sha_is_set_semantics():
    """桶键是集合语义：[A,B] 与 [B,A] 同桶；str/UUID 大小写差异同桶。"""
    assert sc._bucket_sha([M1, M2]) == sc._bucket_sha([M2, M1])
    assert sc._bucket_sha([str(M1)]) == sc._bucket_sha([str(M1).upper()])
    assert sc._bucket_sha([M1]) != sc._bucket_sha([M2])


def test_entry_key_deterministic_and_unique():
    """同桶同查询 → 同键；同桶不同查询 → 不同键。"""
    b = sc._bucket_sha([M1])
    assert sc._entry_key(b, "abc") == sc._entry_key(b, "abc")
    assert sc._entry_key(b, "abc") != sc._entry_key(b, "abd")


def test_decode_entry_defensive():
    """损坏 JSON / 非 dict / 缺 answer 字段 → None（跳过脏条目不炸桶）。"""
    assert sc._decode_entry("{broken json") is None
    assert sc._decode_entry('["a list"]') is None
    assert sc._decode_entry('{"no_answer": true}') is None
    ok = sc._decode_entry('{"answer": "A", "evidence": []}')
    assert ok is not None and ok["answer"] == "A"


# ──────────────────────────── L1 精确命中 ────────────────────────────


async def test_l1_exact_hit_skips_embedder(fake_redis, fake_embed):
    """L1 命中零嵌入成本：embedder 一次都不得被调用（核心性能契约）。"""
    await sc.semantic_cache_store("视频讲了什么", [M1], [1.0, 0.0], "答案A", [{"text": "ev"}])

    hit, vec = await sc.semantic_cache_lookup("视频讲了什么", [M1])

    assert hit is not None and hit.hit_type == "exact"
    assert hit.answer == "答案A"
    assert hit.evidence == [{"text": "ev"}]
    assert vec is None  # L1 命中不需要 probe 向量
    assert fake_embed.calls == []  # 零嵌入成本契约


async def test_l1_hit_on_whitespace_case_variant(fake_redis, fake_embed):
    """空白/大小写变体同键命中（normalize_query 契约）。"""
    await sc.semantic_cache_store("What is it about", [M1], [1.0, 0.0], "A", [])

    hit, _ = await sc.semantic_cache_lookup("  what   IS it about ", [M1])

    assert hit is not None and hit.hit_type == "exact"
    assert fake_embed.calls == []


async def test_l1_hit_refreshes_lru_and_ttl(fake_redis, fake_embed):
    """命中刷新：zadd 更新 LRU score + expire 续 TTL（活跃条目活得久）。"""
    await sc.semantic_cache_store("Q", [M1], [1.0, 0.0], "A", [])
    fake_redis.calls.clear()
    fake_redis.expires.clear()

    hit, _ = await sc.semantic_cache_lookup("Q", [M1])

    assert hit is not None
    assert "zadd" in fake_redis.calls
    assert "expire" in fake_redis.calls


# ──────────────────────────── L2 语义命中 ────────────────────────────


async def test_l2_semantic_hit_on_paraphrase(fake_redis, fake_embed):
    """近义问法命中：cos ≥ 0.92 → semantic 命中，且返回 probe 向量供 store 复用。"""
    await sc.semantic_cache_store("视频的主要内容是什么", [M1], [1.0, 0.0], "答案B", [])

    # 新问题嵌入 [0.99, 0.141]，与 [1, 0] 的 cos ≈ 0.99 ≥ 0.92
    fake_embed.vec = [0.99, 0.141]
    hit, vec = await sc.semantic_cache_lookup("这个视频讲了什么", [M1])

    assert hit is not None and hit.hit_type == "semantic"
    assert hit.answer == "答案B"
    assert vec == [0.99, 0.141]  # 复用 probe，省一次 embed


async def test_l2_punctuation_variant_falls_to_semantic(fake_redis, fake_embed):
    """标点变体不走 L1（不做去标点），由 L2 语义层兜底命中。"""
    await sc.semantic_cache_store("讲了什么", [M1], [1.0, 0.0], "A", [])
    fake_embed.vec = [1.0, 0.0]  # 与存储向量 cos = 1.0

    hit, _ = await sc.semantic_cache_lookup("讲了什么？", [M1])

    assert hit is not None and hit.hit_type == "semantic"
    assert fake_embed.calls  # L2 确实做了 probe


async def test_l2_below_threshold_miss_returns_vec(fake_redis, fake_embed):
    """低于阈值 miss；返回 probe 向量供 store 复用（不浪费已算嵌入）。"""
    await sc.semantic_cache_store("Q1", [M1], [1.0, 0.0], "A", [])

    fake_embed.vec = [0.0, 1.0]  # 正交，cos = 0
    hit, vec = await sc.semantic_cache_lookup("Q2", [M1])

    assert hit is None
    assert vec == [0.0, 1.0]


async def test_bucket_isolation_across_media_sets(fake_redis, fake_embed):
    """media_ids 一致是硬约束：同问题换媒体集 → 不同桶 → miss。"""
    await sc.semantic_cache_store("讲了什么", [M1], [1.0, 0.0], "A", [])

    hit, vec = await sc.semantic_cache_lookup("讲了什么", [M2])

    assert hit is None
    assert vec == [1.0, 0.0]  # 空桶 L2：probe 向量返回供 store


async def test_l2_skips_corrupted_entries(fake_redis, fake_embed):
    """桶内脏数据（坏 JSON / 缺 answer / 缺 vec）跳过，不打断扫描。"""
    bucket = sc._bucket_sha([M1])
    bkey = sc._bucket_key(bucket)
    bad1 = sc._entry_key(bucket, "bad1")
    bad2 = sc._entry_key(bucket, "bad2")
    bad3 = sc._entry_key(bucket, "bad3")
    fake_redis.zsets[bkey] = {bad1: 1.0, bad2: 2.0, bad3: 3.0}
    fake_redis.kv[bad1] = "{broken"
    fake_redis.kv[bad2] = '{"no_answer": 1}'
    fake_redis.kv[bad3] = json.dumps({"answer": "A", "query_vec": []})  # 空 vec 也跳过

    fake_embed.vec = [1.0, 0.0]
    hit, vec = await sc.semantic_cache_lookup("new query", [M1])

    assert hit is None
    assert vec == [1.0, 0.0]


# ──────────────────────────── fail-open ────────────────────────────


async def test_lookup_disabled_touches_nothing(fake_redis, monkeypatch):
    """SEMANTIC_CACHE_ENABLED=false → (None, None)，Redis 一次都不碰。"""
    monkeypatch.setenv("SEMANTIC_CACHE_ENABLED", "false")
    get_settings.cache_clear()

    hit, vec = await sc.semantic_cache_lookup("Q", [M1])

    assert (hit, vec) == (None, None)
    assert fake_redis.calls == []


async def test_lookup_redis_down_fail_open(monkeypatch, fake_embed):
    """Redis 断连 → (None, None) 直走原流程，绝不抛异常阻断问答。"""

    def _down():
        raise ConnectionError("redis down")

    monkeypatch.setattr("videomind.infrastructure.cache.redis.get_redis", _down)
    hit, vec = await sc.semantic_cache_lookup("Q", [M1])
    assert (hit, vec) == (None, None)


async def test_lookup_embed_failure_fail_open(fake_redis, monkeypatch):
    """嵌入失败 → (None, None)；且 L1 miss 后 L2 直接放弃（不重试 probe）。"""
    e = FakeEmbedder(error=RuntimeError("embed boom"))
    monkeypatch.setattr(
        "videomind.core.video_pipeline.embed.get_embedding_backend", lambda: e
    )

    hit, vec = await sc.semantic_cache_lookup("Q", [M1])

    assert (hit, vec) == (None, None)
    assert len(e.calls) == 1  # 只 probe 一次


async def test_store_disabled_writes_nothing(fake_redis, monkeypatch):
    monkeypatch.setenv("SEMANTIC_CACHE_ENABLED", "false")
    get_settings.cache_clear()

    await sc.semantic_cache_store("Q", [M1], [1.0], "A", [])

    assert fake_redis.kv == {} and fake_redis.zsets == {}


async def test_store_without_vec_writes_nothing(fake_redis):
    """无 query_vec 的条目无法参与 L2 → 跳过不写（不占 LRU 位）。"""
    await sc.semantic_cache_store("Q", [M1], None, "A", [])
    assert fake_redis.kv == {}


async def test_store_redis_down_swallowed(fake_redis, monkeypatch):
    """store 时 Redis 断连：静默跳过，不抛（仅损失一次加速）。"""

    def _down():
        raise ConnectionError("redis down")

    monkeypatch.setattr("videomind.infrastructure.cache.redis.get_redis", _down)
    await sc.semantic_cache_store("Q", [M1], [1.0], "A", [])  # 不抛即通过


# ──────────────────────────── store 结构与 trim ────────────────────────────


async def test_store_writes_entry_bucket_and_media_index(fake_redis):
    """三写一体：entry JSON + bucket ZSET + 每 media 反向索引 SET，均带 TTL。"""
    await sc.semantic_cache_store("Q", [M1, M2], [0.5, 0.5], "A", [{"t": 1}])

    bucket = sc._bucket_sha([M1, M2])
    ekey = sc._entry_key(bucket, sc.normalize_query("Q"))
    entry = json.loads(fake_redis.kv[ekey])
    assert entry["answer"] == "A" and entry["evidence"] == [{"t": 1}]
    assert entry["query_vec"] == [0.5, 0.5]
    assert ekey in fake_redis.zsets[sc._bucket_key(bucket)]
    assert ekey in fake_redis.sets[sc._media_index_key(M1)]
    assert ekey in fake_redis.sets[sc._media_index_key(M2)]
    # entry / bucket / 两个反向索引 都带 TTL
    ttl_keys = {k for k, _ in fake_redis.expires}
    assert ttl_keys >= {ekey, sc._bucket_key(bucket),
                        sc._media_index_key(M1), sc._media_index_key(M2)}


async def test_store_trim_evicts_oldest_lru(fake_redis, monkeypatch):
    """超 max_entries 逐出最旧条目：entry 删除 + 反向索引摘除，桶收敛到上限。"""
    monkeypatch.setenv("SEMANTIC_CACHE_MAX_ENTRIES", "2")
    get_settings.cache_clear()

    await sc.semantic_cache_store("q1", [M1], [1.0, 0.0], "A1", [])
    await sc.semantic_cache_store("q2", [M1], [0.0, 1.0], "A2", [])
    await sc.semantic_cache_store("q3", [M1], [1.0, 1.0], "A3", [])

    bucket = sc._bucket_sha([M1])
    bkey = sc._bucket_key(bucket)
    assert await fake_redis.zcard(bkey) == 2
    # 最旧的 q1 被逐出（同分按插入序，q1 最旧）
    e1 = sc._entry_key(bucket, sc.normalize_query("q1"))
    e2 = sc._entry_key(bucket, sc.normalize_query("q2"))
    e3 = sc._entry_key(bucket, sc.normalize_query("q3"))
    assert e1 not in fake_redis.kv
    assert e1 not in fake_redis.sets.get(sc._media_index_key(M1), set())
    assert e2 in fake_redis.kv and e3 in fake_redis.kv


# ──────────────────────────── 主动失效 ────────────────────────────


async def test_invalidate_removes_across_media_indices(fake_redis, fake_embed):
    """失效 M1：其引用的两条全删；与 M2 共享的条目也从 M2 反向索引摘除。"""
    await sc.semantic_cache_store("q1", [M1], [1.0, 0.0], "A1", [])
    await sc.semantic_cache_store("q2", [M1, M2], [0.0, 1.0], "A2", [])

    removed = await sc.invalidate_semantic_cache_for_media(M1)

    assert removed == 2
    assert fake_redis.kv == {}
    m2_idx = sc._media_index_key(M2)
    assert fake_redis.sets.get(m2_idx, set()) == set()  # 交叉清理
    # 失效后再问 = 全 miss（回到回源路径）
    hit, _ = await sc.semantic_cache_lookup("q1", [M1])
    assert hit is None


async def test_invalidate_unknown_media_returns_zero(fake_redis):
    assert await sc.invalidate_semantic_cache_for_media(uuid.uuid4()) == 0


async def test_invalidate_disabled_returns_zero(monkeypatch):
    monkeypatch.setenv("SEMANTIC_CACHE_ENABLED", "false")
    get_settings.cache_clear()
    assert await sc.invalidate_semantic_cache_for_media(M1) == 0


async def test_invalidate_redis_down_returns_zero(monkeypatch):
    """失效失败不阻断业务（条目由 TTL 兜底过期）。"""

    def _down():
        raise ConnectionError("redis down")

    monkeypatch.setattr("videomind.infrastructure.cache.redis.get_redis", _down)
    assert await sc.invalidate_semantic_cache_for_media(M1) == 0


async def test_invalidate_corrupted_entry_still_removed(fake_redis):
    """条目 JSON 损坏：反向索引仍按 ekey 摘除 + 桶/条目删除（不读 media_ids 兜底）。"""
    bucket = sc._bucket_sha([M1])
    ekey = sc._entry_key(bucket, "broken")
    fake_redis.sets[sc._media_index_key(M1)] = {ekey}
    fake_redis.kv[ekey] = "{broken"
    fake_redis.zsets[sc._bucket_key(bucket)] = {ekey: 1.0}

    removed = await sc.invalidate_semantic_cache_for_media(M1)

    assert removed == 1
    assert ekey not in fake_redis.kv
    assert ekey not in fake_redis.zsets.get(sc._bucket_key(bucket), {})
