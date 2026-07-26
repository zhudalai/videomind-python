"""L3 真实集成测试：Qdrant 向量库 upsert + search。"""

import pytest
import uuid

from videomind.infrastructure.vector.qdrant import (
    QdrantStore,
    make_qdrant_point_id,
    build_chunk_payload,
)


@pytest.mark.infra
class TestQdrantIntegration:
    """真实 Qdrant 连接 + 基本读写。"""

    async def test_ensure_collection(self, qdrant_client):
        """collection 自动创建。"""
        store = QdrantStore()
        store._client = qdrant_client  # 用 fixture 的 client
        await store.ensure_collection()

        collections = await qdrant_client.get_collections()
        names = {c.name for c in collections.collections}
        assert store._collection in names

    async def test_upsert_and_search_point(self, qdrant_client, temp_collection):
        """upsert 一个向量，search 命中。"""
        media_id = uuid.uuid4()
        point_id = make_qdrant_point_id(media_id, 0)
        vector = [0.1] * 1024
        payload = build_chunk_payload(
            chunk_id=point_id,
            media_id=media_id,
            segment_id=None,
            chunk_index=0,
            content="测试文本内容",
            content_hash="test_hash",
            start_ms=0,
            end_ms=5000,
            source_type="asr",
        )

        await qdrant_client.upsert(
            collection_name=temp_collection,
            points=[
                {
                    "id": str(point_id),
                    "vector": vector,
                    "payload": payload,
                }
            ],
            wait=True,
        )

        # 搜索
        results = await qdrant_client.query_points(
            collection_name=temp_collection,
            query=vector,
            limit=1,
        )
        assert len(results.points) == 1
        assert str(results.points[0].id) == str(point_id)
        assert results.points[0].payload["content"] == "测试文本内容"
        assert results.points[0].payload["media_id"] == str(media_id)

    async def test_make_qdrant_point_id_deterministic(self):
        """同 (media_id, chunk_index) 总生成同一 UUID v5。"""
        media_id = uuid.uuid4()
        id1 = make_qdrant_point_id(media_id, 0)
        id2 = make_qdrant_point_id(media_id, 0)
        assert id1 == id2
        # 不同 chunk_index 生成不同 ID
        id3 = make_qdrant_point_id(media_id, 1)
        assert id1 != id3