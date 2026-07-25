from minio import Minio
client = Minio('localhost:9000', access_key='minioadmin', secret_key='minioadmin', secure=False)
buckets = client.list_buckets()
for b in buckets:
    print(f'Bucket: {b.name}')
print('Bucket videomind exists:', client.bucket_exists('videomind'))