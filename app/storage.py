import os
import shutil
import boto3
from botocore.client import Config
from app.config import settings


class StorageManager:
    def __init__(self):
        self.backend = settings.storage_backend
        if self.backend == "s3":
            self.s3 = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                config=Config(signature_version="s3v4"),
                region_name="us-east-1",
            )
            self._ensure_bucket()
        else:
            os.makedirs(settings.local_storage_dir, exist_ok=True)

    def _ensure_bucket(self):
        existing = [b["Name"] for b in self.s3.list_buckets().get("Buckets", [])]
        if settings.s3_bucket not in existing:
            self.s3.create_bucket(Bucket=settings.s3_bucket)

    def check_health(self) -> bool:
        """Return True only when the configured storage backend is reachable."""
        try:
            if self.backend == "s3":
                self.s3.head_bucket(Bucket=settings.s3_bucket)
                return True
            os.makedirs(settings.local_storage_dir, exist_ok=True)
            return os.path.isdir(settings.local_storage_dir) and os.access(settings.local_storage_dir, os.W_OK)
        except Exception:
            return False

    def save_file(self, local_path: str, object_key: str) -> str:
        if self.backend == "s3":
            self.s3.upload_file(local_path, settings.s3_bucket, object_key)
            url = self.s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.s3_bucket, "Key": object_key},
                ExpiresIn=86400,
            )
            return url.replace(settings.s3_endpoint_url, settings.s3_public_url)
        else:
            dest = os.path.join(settings.local_storage_dir, object_key)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(local_path, dest)
            return f"/files/{object_key}"


storage = StorageManager()