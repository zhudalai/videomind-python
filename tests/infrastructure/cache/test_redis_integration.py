"""L3 真实集成测试：Redis 连接 + 基本读写。"""

import pytest


@pytest.mark.infra
class TestRedisIntegration:
    """真实 Redis 连接测试。"""

    async def test_ping(self, redis_client):
        """PING -> PONG。"""
        pong = await redis_client.ping()
        assert pong is True

    async def test_set_get(self, redis_client):
        """SET / GET 往返。"""
        key = "test:key:123"
        value = "hello redis"

        await redis_client.set(key, value)
        got = await redis_client.get(key)
        assert got == value

    async def test_setex_expire(self, redis_client):
        """SETEX 带过期时间。"""
        key = "test:expire:456"
        await redis_client.setex(key, 1, "temp")  # 1 秒过期
        # 立即能读到
        assert await redis_client.get(key) == "temp"
        # 等待过期（不等待，直接验证 key 存在即可，TTL 测试需 sleep）
        ttl = await redis_client.ttl(key)
        assert 0 < ttl <= 1

    async def test_incr_decr(self, redis_client):
        """INCR / DECR 原子计数。"""
        key = "test:counter:789"
        await redis_client.set(key, "10")
        assert await redis_client.incr(key) == 11
        assert await redis_client.decr(key) == 10

    async def test_hset_hget(self, redis_client):
        """Hash 操作。"""
        key = "test:hash:abc"
        await redis_client.hset(key, mapping={"field1": "v1", "field2": "v2"})
        val = await redis_client.hgetall(key)
        assert val == {"field1": "v1", "field2": "v2"}