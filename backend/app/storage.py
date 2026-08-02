"""Image storage abstraction: local disk (default) or S3-compatible object storage."""

import asyncio
import os
import uuid
from abc import ABC, abstractmethod

from .config import get_settings


class Storage(ABC):
    @abstractmethod
    async def save_image(self, data: bytes, *, story_id: str, position: int, mime: str) -> str:
        """Persist image bytes; return a URL the browser can load."""

    @abstractmethod
    async def delete_story_media(self, story_id: str) -> None:
        """Best-effort removal of all images belonging to a story."""


def _ext_for(mime: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")


class LocalStorage(Storage):
    """Writes under MEDIA_ROOT; files are served by the API at MEDIA_URL_PREFIX."""

    def __init__(self, root: str, url_prefix: str):
        self.root = root
        self.url_prefix = url_prefix.rstrip("/")

    def _write_sync(self, abs_dir: str, name: str, data: bytes) -> None:
        os.makedirs(abs_dir, exist_ok=True)
        with open(os.path.join(abs_dir, name), "wb") as f:
            f.write(data)

    async def save_image(self, data: bytes, *, story_id: str, position: int, mime: str) -> str:
        name = f"{position:02d}-{uuid.uuid4().hex[:8]}.{_ext_for(mime)}"
        abs_dir = os.path.join(self.root, "stories", story_id)
        # Disk writes are blocking; off the event loop so concurrent illustrations
        # and every other in-flight request keep making progress.
        await asyncio.to_thread(self._write_sync, abs_dir, name, data)
        return f"{self.url_prefix}/stories/{story_id}/{name}"

    async def delete_story_media(self, story_id: str) -> None:
        import shutil

        target = os.path.join(self.root, "stories", story_id)
        if os.path.isdir(target):
            await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)


class S3Storage(Storage):
    """S3-compatible storage (AWS S3, Cloudflare R2, GCS interop). Lazy boto3 import."""

    def __init__(self):
        import boto3  # imported here so boto3 is only required when s3 is configured

        s = get_settings()
        self.bucket = s.s3_bucket
        self.public_base = s.s3_public_base_url.rstrip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url or None,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key_id,
            aws_secret_access_key=s.s3_secret_access_key,
        )

    async def save_image(self, data: bytes, *, story_id: str, position: int, mime: str) -> str:
        import asyncio

        key = f"stories/{story_id}/{position:02d}-{uuid.uuid4().hex[:8]}.{_ext_for(mime)}"
        await asyncio.to_thread(
            self.client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=mime
        )
        return f"{self.public_base}/{key}"

    async def delete_story_media(self, story_id: str) -> None:
        import asyncio

        def _delete():
            prefix = f"stories/{story_id}/"
            resp = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
            keys = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})

        await asyncio.to_thread(_delete)


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "s3":
            _storage = S3Storage()
        else:
            _storage = LocalStorage(s.media_root, s.media_url_prefix)
    return _storage


def reset_storage() -> None:
    """Test helper."""
    global _storage
    _storage = None
