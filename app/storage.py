from minio import Minio
from minio.error import S3Error

from app.settings import StorageSettings


def setup_storage(config: StorageSettings) -> Minio:
    client = Minio(
        endpoint=config.endpoint,
        access_key=config.access_key.get_secret_value(),
        secret_key=config.secret_key.get_secret_value(),
        secure=config.secure,
    )
    if not client.bucket_exists(config.bucket):
        try:
            client.make_bucket(config.bucket)
        except S3Error as error:
            if error.code != "BucketAlreadyOwnedByYou":
                raise
    return client
