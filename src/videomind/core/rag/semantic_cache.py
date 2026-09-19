"""RAG 语义缓存 —— 高频问答两级缓存，避免重复烧 LLM API。

对应方向 1.1（docs/ARCHITECTURE.md Redis DB0 应用缓存）。
设计要点：
1. **两级命中**：
   - L1 精确匹配：规范化(query + sorted media_ids) SHA256 直接 GET，零嵌入成本
     （空白/大小写变体同键："  What  IS it " ≡ "what is it"）。
   - L2 语义匹配：L1 miss 后 embed query，对同媒体集桶内全部条目做进程内余弦扫描，
     ≥ semantic_cache_threshold 判命中。标点变体（"讲了什么？"）与近义问法
     （"视频主要内容" ≈ "讲了什么"）都由这一层兜底。
2. **media_ids 一致是硬约束**：桶键 = SHA256(sorted media_ids)。同一问题在不同
   视频集上答案完全不同，语义匹配只在同桶内做，绝不跨媒体集。
3. **LRU + TTL 双逐出**：每桶 ZSET(last_used 时间戳) 做 LRU trim 到 max_entries；
   条目/桶/反向索引全部 EXPIRE ttl_s，命中刷新（活跃条目活得久）。
4. **fail-open**：Redis 断连/嵌入失败/JSON 损坏一律 log warning 后跳过缓存，
   绝不阻断问答主流程——缓存是加速器不是依赖。
5. **主动失效**：索引重建/删除媒体时按 media_id 反向索引精准清除相关条目
   （见 invalidate_semantic_cache_for_media），不等 TTL。

键空间约定（Redis DB0）：
    ragcache:bucket:{bucket_sha}        ZSET  {entry_id: last_used_ts}（桶 = 媒体集）
    ragcache:entry:{bucket_sha}:{eid}   JSON  {query, query_vec, answer, evidence, media_ids, created_at}
    ragcache:media:{media_id}           SET   引用了该 media 的 entry key（反向索引，失效用）

配置锚点（config.py）:
    SEMANTIC_CACHE_ENABLED=true
    SEMANTIC_CACHE_TTL_S=21600          # 6h
    SEMANTIC_CACHE_THRESHOLD=0.92
    SEMANTIC_CACHE_MAX_ENTRIES=256      # 单桶上限
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

from videomind.config import get_settings
from videomind.observability.metrics import RAG_CACHE_HITS, RAG_CACHE_MISSES

log = logging.getLogger(__name__)


# ──────────────────────────── 键构造 / 规范化（纯函数，单测友好）────────────────────────────


def normalize_query(query: str) -> str:
    """规范化查询：压缩连续空白 + 去首尾空白 + 小写。

    让 "  What  IS it " 与 "what is it" 命中同一缓存键（L1 精确层）。
    不做 NFKC/去标点——过度规范化会吞掉语义差异（"3.5版本" vs "35版本"）；
    标点变体（"讲了什么？"）由 L2 语义层的余弦匹配兜底，两级各司其职。
    """
    return " ".join(query.split()).strip().lower()


def _bucket_sha(media_ids: list[uuid.UUID] | list[str]) -> str:
    """媒体集桶键：sorted media_ids 的 SHA256 前 16 位。

    排序保证 [A, B] 与 [B, A] 同桶（集合语义而非序列语义）。
    """
    ids = sorted(str(m).lower() for m in media_ids)
    return hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()[:16]


def _entry_key(bucket: str, norm_q: str) -> str:
    """条目键 = 桶 + 规范化查询的 SHA256 前 16 位。"""
    eid = hashlib.sha256(norm_q.encode("utf-8")).hexdigest()[:16]
    return f"ragcache:entry:{bucket}:{eid}"


def _bucket_key(bucket: str) -> str:
    return f"ragcache:bucket:{bucket}"


def _media_index_key(media_id: uuid.UUID | str) -> str:
    return f"ragcache:media:{str(media_id).lower()}"


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """进程内余弦相似度（BGE-M3 归一化向量下等价点积，但 API 路径不保证归一）。

    长度不一致（模型换了维度）或零向量 → 0.0（防御：跳过该条目不崩）。
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = norm_a = norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a ** 0.5 * norm_b ** 0.5)


# ──────────────────────────── 返回类型 ────────────────────────────


@dataclass
class SemanticCacheHit:
    """缓存命中结果（answer + evidence 与 RagChatResponse 同 shape）。"""

    answer: str
    evidence: list[dict] = field(default_factory=list)
    hit_type: str = "exact"  # exact | semantic


# ──────────────────────────── 查询（lookup）────────────────────────────


async def semantic_cache_lookup(
    query: str,
    media_ids: list[uuid.UUID] | list[str],
) -> tuple[SemanticCacheHit | None, list[float] | None]:
    """查缓存：L1 精确匹配 → L2 余弦近邻。

    Returns:
        (hit, query_vec)：
        - 命中 → (SemanticCacheHit, None 或 vec)；调用方直接返回缓存答案
        - 未命中 → (None, query_vec)；query_vec 是 L2 probe 已算的嵌入，
          传给 semantic_cache_store 复用，省一次重复 embed
        - 禁用/故障 fail-open → (None, None)，直走原流程
    """
    s = get_settings()
    if not s.semantic_cache_enabled:
        return None, None

    bucket = _bucket_sha(media_ids)
    norm_q = normalize_query(query)
    ekey = _entry_key(bucket, norm_q)
    bkey = _bucket_key(bucket)
    now = time.time()

    try:
        from videomind.infrastructure.cache.redis import get_redis

        r = get_redis()

        # ── L1：规范化精确匹配（零嵌入成本，热路径）──
        raw = await r.get(ekey)
        if raw is not None:
            entry = _decode_entry(raw)
            if entry is not None:
                # 命中刷新：LRU score + TTL（活跃条目活得久）
                await r.zadd(bkey, {ekey: now})
                await r.expire(ekey, s.semantic_cache_ttl_s)
                await r.expire(bkey, s.semantic_cache_ttl_s)
                RAG_CACHE_HITS.labels(hit_type="exact").inc()
                log.debug("语义缓存 L1 命中: %s", norm_q[:50])
                return _hit(entry, "exact"), None

        # ── L2：语义近邻（桶内余弦扫描，media_ids 一致是硬约束）──
        query_vec = await _embed_probe(norm_q)
        if query_vec is None:
            # 嵌入失败 fail-open；也记 miss（这次请求确实回源了）
            RAG_CACHE_MISSES.inc()
            return None, None

        member_scores = await r.zrange(bkey, 0, -1, withscores=False)
        if not member_scores:
            RAG_CACHE_MISSES.inc()
            return None, query_vec

        raws = await r.mget(member_scores)
        best: dict | None = None
        best_score = 0.0
        for m, raw2 in zip(member_scores, raws):
            if raw2 is None:
                continue
            entry = _decode_entry(raw2)
            if entry is None or not entry.get("query_vec"):
                continue
            sim = cosine_similarity(query_vec, entry["query_vec"])
            if sim > best_score:
                best_score, best = sim, entry
        if best is not None and best_score >= s.semantic_cache_threshold:
            ekey_hit = _entry_key(bucket, normalize_query(best["query"]))
            await r.zadd(bkey, {ekey_hit: now})
            await r.expire(ekey_hit, s.semantic_cache_ttl_s)
            await r.expire(bkey, s.semantic_cache_ttl_s)
            RAG_CACHE_HITS.labels(hit_type="semantic").inc()
            log.debug("语义缓存 L2 命中 (cos=%.4f): %s ≈ %s", best_score, norm_q[:50], best["query"][:50])
            return _hit(best, "semantic"), query_vec

        RAG_CACHE_MISSES.inc()
        return None, query_vec
    except Exception as e:  # noqa: BLE001 —— fail-open：缓存故障绝不阻断问答
        log.warning("语义缓存 lookup 失败（fail-open 直走原流程）: %r", e)
        RAG_CACHE_MISSES.inc()
        return None, None


# ──────────────────────────── 写入（store）────────────────────────────


async def semantic_cache_store(
    query: str,
    media_ids: list[uuid.UUID] | list[str],
    query_vec: list[float] | None,
    answer: str,
    evidence: list[dict],
) -> None:
    """回源生成后写缓存。失败静默跳过（下次问同一问题重新生成，仅损失一次加速）。

    query_vec 复用 lookup 的 probe 结果（无 vec 的条目无法参与 L2 匹配，跳过不写）。
    """
    s = get_settings()
    if not s.semantic_cache_enabled or not query_vec:
        return

    bucket = _bucket_sha(media_ids)
    norm_q = normalize_query(query)
    ekey = _entry_key(bucket, norm_q)
    bkey = _bucket_key(bucket)
    ttl = s.semantic_cache_ttl_s

    entry = {
        "query": norm_q,
        "query_vec": query_vec,
        "answer": answer,
        "evidence": evidence,
        "media_ids": [str(m).lower() for m in media_ids],
        "created_at": time.time(),
    }
    try:
        from videomind.infrastructure.cache.redis import get_redis

        r = get_redis()
        now = time.time()
        # 条目 + 桶 + 反向索引三写一体；pipeline 减少往返
        pipe = r.pipeline(transaction=False)
        pipe.set(ekey, json.dumps(entry, ensure_ascii=False), ex=ttl)
        pipe.zadd(bkey, {ekey: now})
        pipe.expire(bkey, ttl)
        for mid in media_ids:
            pipe.sadd(_media_index_key(mid), ekey)
            pipe.expire(_media_index_key(mid), ttl)
        await pipe.execute()

        await _trim_bucket(r, bucket, bkey, s.semantic_cache_max_entries, ttl)
    except Exception as e:  # noqa: BLE001 —— fail-open
        log.warning("语义缓存 store 失败（跳过，不影响已生成的答案）: %r", e)


# ──────────────────────────── 主动失效 ────────────────────────────


async def invalidate_semantic_cache_for_media(media_id: uuid.UUID | str) -> int:
    """按 media_id 反向索引精准清除相关条目（索引重建/删除媒体时调用）。

    媒体的 chunks 变化后旧答案不再可信；不等 TTL，立即失效。
    交叉维护：条目被 N 个 media 引用时从每个反向索引中移除自身。
    返回删除的条目数（0 = 无相关条目，测试友好）。
    """
    s = get_settings()
    if not s.semantic_cache_enabled:
        return 0
    try:
        from videomind.infrastructure.cache.redis import get_redis

        r = get_redis()
        mkey = _media_index_key(media_id)
        ekeys = await r.smembers(mkey)
        removed = 0
        for ekey in ekeys:
            raw = await r.get(ekey)
            entry = _decode_entry(raw) if raw else None
            if entry is not None:
                # 从条目引用的所有 media 反向索引中移除
                for mid in entry.get("media_ids", []):
                    await r.srem(_media_index_key(mid), ekey)
            # ZREM 桶 + DEL 条目（ekey 自带 bucket 前缀，直接拆）
            parts = ekey.split(":")
            if len(parts) >= 4:
                await r.zrem(_bucket_key(parts[2]), ekey)
            await r.delete(ekey)
            removed += 1
        await r.delete(mkey)
        if removed:
            log.info("语义缓存失效: media %s 清除 %d 条", media_id, removed)
        return removed
    except Exception as e:  # noqa: BLE001 —— 失效失败仅损失新鲜度保证，不阻断业务
        log.warning("语义缓存失效失败（%r）——条目将由 TTL 兜底过期", e)
        return 0


# ──────────────────────────── 内部辅助 ────────────────────────────


def _decode_entry(raw: str) -> dict | None:
    """JSON 解码条目，损坏数据返回 None 跳过（防止单条脏数据打断整桶扫描）。"""
    try:
        entry = json.loads(raw)
        if not isinstance(entry, dict) or "answer" not in entry:
            return None
        return entry
    except (json.JSONDecodeError, TypeError):
        return None


def _hit(entry: dict, hit_type: str) -> SemanticCacheHit:
    """条目 dict → SemanticCacheHit（evidence 缺失兜底空列表）。"""
    return SemanticCacheHit(
        answer=entry.get("answer") or "",
        evidence=list(entry.get("evidence") or []),
        hit_type=hit_type,
    )


async def _embed_probe(query: str) -> list[float] | None:
    """L2 probe：embed 单条查询。失败返回 None（fail-open）。"""
    try:
        from videomind.core.video_pipeline.embed import get_embedding_backend

        result = await get_embedding_backend().embed([query])
        return result.vectors[0] if result.vectors else None
    except Exception as e:  # noqa: BLE001
        log.warning("语义缓存 embedding probe 失败（跳过 L2）: %r", e)
        return None


async def _trim_bucket(
    r, bucket: str, bkey: str, max_entries: int, ttl: int
) -> None:
    """桶内 LRU trim：超过 max_entries 逐出最旧条目（连同反向索引清理）。

    ZSET 按 last_used 升序，ZRANGE 头部 = 最久未用。
    """
    count = await r.zcard(bkey)
    if count <= max_entries:
        return
    overflow = count - max_entries
    # 先取要逐出的条目（拿 media_ids 清反向索引），再删
    evict = await r.zrange(bkey, 0, overflow - 1)
    for ekey in evict:
        raw = await r.get(ekey)
        entry = _decode_entry(raw) if raw else None
        if entry is not None:
            for mid in entry.get("media_ids", []):
                await r.srem(_media_index_key(mid), ekey)
        await r.delete(ekey)
    await r.zremrangebyrank(bkey, 0, overflow - 1)
    await r.expire(bkey, ttl)


__all__ = [
    "SemanticCacheHit",
    "semantic_cache_lookup",
    "semantic_cache_store",
    "invalidate_semantic_cache_for_media",
    "cosine_similarity",
    "normalize_query",
]
