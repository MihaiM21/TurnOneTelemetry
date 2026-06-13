"""S3-compatible storage adapter (AWS S3, Backblaze B2, Cloudflare R2, MinIO)."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, List

import boto3
from botocore.client import Config

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


class S3Storage:
    """Thin wrapper around boto3 S3 client tuned for backup workloads."""

    def __init__(self) -> None:
        if not settings.backup_s3_bucket:
            raise ValueError("backup_s3_bucket is not configured")
        if not settings.backup_s3_access_key or not settings.backup_s3_secret_key:
            raise ValueError("backup S3 credentials are not configured")

        client_kwargs: dict = {
            "aws_access_key_id": settings.backup_s3_access_key,
            "aws_secret_access_key": settings.backup_s3_secret_key,
            "region_name": settings.backup_s3_region,
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.backup_s3_force_path_style else "auto"},
                retries={"max_attempts": 5, "mode": "standard"},
            ),
        }
        if settings.backup_s3_endpoint:
            client_kwargs["endpoint_url"] = settings.backup_s3_endpoint
        self._client = boto3.client("s3", **client_kwargs)
        self._bucket = settings.backup_s3_bucket

    # ── core ops ──────────────────────────────────────────────────────────
    def upload(self, local_path: Path, key: str, content_type: str = "application/octet-stream") -> None:
        logger.info(f"S3 upload: {local_path} -> s3://{self._bucket}/{key} ({local_path.stat().st_size} bytes)")
        self._client.upload_file(
            Filename=str(local_path),
            Bucket=self._bucket,
            Key=key,
            ExtraArgs={"ContentType": content_type},
        )

    def download(self, key: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"S3 download: s3://{self._bucket}/{key} -> {local_path}")
        self._client.download_file(Bucket=self._bucket, Key=key, Filename=str(local_path))

    def get_bytes(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self._bucket, Key=key)
        return obj["Body"].read()

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/json") -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def copy(self, src_key: str, dst_key: str) -> None:
        self._client.copy_object(
            Bucket=self._bucket,
            Key=dst_key,
            CopySource={"Bucket": self._bucket, "Key": src_key},
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._client.exceptions.ClientError:
            return False

    def list_prefix(self, prefix: str) -> Iterator[dict]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                yield obj

    def ping(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return True
        except Exception as exc:
            logger.warning(f"S3 head_bucket failed: {exc}")
            return False

    # ── higher-level helpers ──────────────────────────────────────────────
    def list_backups(self, tier: str) -> List[str]:
        """Return sorted list of backup IDs under a tier (newest first)."""
        prefix = f"{settings.backup_s3_prefix}/{tier}/"
        ids: set[str] = set()
        for obj in self.list_prefix(prefix):
            rest = obj["Key"][len(prefix):]
            if "/" in rest:
                ids.add(rest.split("/", 1)[0])
        return sorted(ids, reverse=True)

    def list_artifacts(self, tier: str, backup_id: str) -> List[str]:
        prefix = f"{settings.backup_s3_prefix}/{tier}/{backup_id}/"
        return [obj["Key"] for obj in self.list_prefix(prefix)]

    def delete_backup(self, tier: str, backup_id: str) -> int:
        keys = self.list_artifacts(tier, backup_id)
        for k in keys:
            self.delete(k)
        return len(keys)
