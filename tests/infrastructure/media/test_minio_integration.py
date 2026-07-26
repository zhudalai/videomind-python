"""L3 真实集成测试：MinIO 对象存储上传/下载。"""

import pytest
from io import BytesIO


@pytest.mark.infra
class TestMinioIntegration:
    """真实 MinIO 连接测试。"""

    def test_put_get_object(self, minio_client, temp_bucket):
        """上传小 bytes -> 下载对比一致。"""
        key = "test/object/123.txt"
        data = b"Hello MinIO integration test"
        stream = BytesIO(data)

        # 上传
        minio_client.put_object(
            bucket_name=temp_bucket,
            object_name=key,
            data=stream,
            length=len(data),
            content_type="text/plain",
        )

        # 下载
        resp = minio_client.get_object(temp_bucket, key)
        try:
            downloaded = resp.read()
        finally:
            resp.close()
            resp.release_conn()

        assert downloaded == data

    def test_stat_object(self, minio_client, temp_bucket):
        """上传后 stat_object 能拿到 metadata。"""
        key = "test/stat/456.bin"
        data = b"x" * 1024
        minio_client.put_object(temp_bucket, key, BytesIO(data), len(data))

        stat = minio_client.stat_object(temp_bucket, key)
        assert stat.size == len(data)
        assert stat.content_type == "application/octet-stream"

    def test_list_objects(self, minio_client, temp_bucket):
        """list_objects 列出上传的对象。"""
        for i in range(3):
            key = f"test/list/{i}.txt"
            minio_client.put_object(temp_bucket, key, BytesIO(b"x"), 1)

        objs = list(minio_client.list_objects(temp_bucket, prefix="test/list/", recursive=True))
        assert len(objs) == 3

    def test_remove_object(self, minio_client, temp_bucket):
        """删除对象。"""
        key = "test/remove/789.txt"
        minio_client.put_object(temp_bucket, key, BytesIO(b"del"), 3)

        minio_client.remove_object(temp_bucket, key)
        # 再次 stat 应抛异常
        from minio.error import S3Error

        with pytest.raises(S3Error):
            minio_client.stat_object(temp_bucket, key)