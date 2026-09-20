"""Private file storage for project documents. Files are addressed by keys the server generates, never by user input.

Files live either in OmniReview's own storage (local disk under DOCUMENT_STORAGE_DIR) or in the billing account's own
S3-compatible bucket (models.StorageConnection, managed in storage_accounts.py). The key records where a file is:

- "projects/<project id>/<32 hex>" is on local disk;
- "s3:<connection id>:projects/<project id>/<32 hex>" is in that connection's bucket, under its prefix.

document_storage() returns a store that writes new files wherever the project's account chose, and always reads,
deletes, and removes projects from where the files actually are, so callers never need to know.
"""

import logging
import os
import re
import secrets
import shutil
from collections.abc import Collection
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent
_KEY_PATTERN = re.compile(r"projects/\d+/[0-9a-f]{32}")
_BUCKET_KEY_PATTERN = re.compile(r"s3:(\d+):(projects/\d+/[0-9a-f]{32})")
BUCKET_KEY_PREFIX = "s3:"


class StorageError(Exception):
    """A stored file can't be read or written. The message is safe to show users."""


class DocumentStorage(Protocol):
    def save(self, project_id: int, content: bytes) -> str: ...

    def read(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def delete_project(self, project_id: int, buckets: Collection[int] | None = None) -> None: ...

    def buckets_for_project(self, project_id: int) -> set[int]: ...


def new_key(project_id: int) -> str:
    return f"projects/{int(project_id)}/{secrets.token_hex(16)}"


def bucket_key(connection_id: int, key: str) -> str:
    return f"{BUCKET_KEY_PREFIX}{connection_id}:{key}"


def parse_bucket_key(key: str) -> tuple[int, str] | None:
    """(connection id, key inside the bucket) for a file in an account's bucket; None for OmniReview's storage."""
    match = _BUCKET_KEY_PATTERN.fullmatch(key)
    return (int(match.group(1)), match.group(2)) if match else None


class LocalStorage:
    """Files on local disk under one root directory, grouped by project so a project's files can be removed together."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        if not _KEY_PATTERN.fullmatch(key):
            raise StorageError("Invalid storage key")
        return self.root / key

    def save(self, project_id: int, content: bytes) -> str:
        key = new_key(project_id)
        self.put(key, content)
        return key

    def put(self, key: str, content: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        partial = path.with_name(f"{path.name}.partial")
        partial.write_bytes(content)
        partial.replace(path)

    def read(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("The stored file is missing") from exc

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_project(self, project_id: int, buckets: Collection[int] | None = None) -> None:
        shutil.rmtree(self.root / "projects" / str(int(project_id)), ignore_errors=True)

    def buckets_for_project(self, project_id: int) -> set[int]:
        return set()


class S3Storage:
    """Files in an S3-compatible bucket (AWS S3, Backblaze B2, Cloudflare R2, Wasabi, MinIO), under an optional prefix.

    Keys passed in and returned are the plain "projects/..." keys; the "s3:<id>:" marker is added by RoutingStorage.
    """

    def __init__(
        self,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        endpoint_url: str = "",
        region: str = "",
        prefix: str = "",
    ) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region or None,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 3, "mode": "standard"},
                # Path-style addressing works with every S3-compatible service, including MinIO.
                s3={"addressing_style": "path"},
            ),
        )

    def _object(self, key: str) -> str:
        if not _KEY_PATTERN.fullmatch(key):
            raise StorageError("Invalid storage key")
        return f"{self.prefix}/{key}" if self.prefix else key

    def _call(self, action: str, **kwargs: Any) -> Any:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return getattr(self.client, action)(Bucket=self.bucket, **kwargs)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404"):
                raise StorageError("The stored file is missing from your storage bucket") from exc
            if code in ("AccessDenied", "403", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
                raise StorageError(
                    "Your storage bucket refused access: check the access key and its permissions"
                ) from exc
            if code == "NoSuchBucket":
                raise StorageError(f'The bucket "{self.bucket}" doesn\'t exist') from exc
            raise StorageError(f"Your storage bucket returned an error ({code or 'unknown'})") from exc
        except BotoCoreError as exc:
            raise StorageError("Your storage bucket couldn't be reached: check the endpoint and region") from exc

    def save(self, project_id: int, content: bytes) -> str:
        key = new_key(project_id)
        self.put(key, content)
        return key

    def put(self, key: str, content: bytes) -> None:
        self._call("put_object", Key=self._object(key), Body=content)

    def read(self, key: str) -> bytes:
        response = self._call("get_object", Key=self._object(key))
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self._call("delete_object", Key=self._object(key))

    def delete_project(self, project_id: int) -> None:
        folder = self._object(f"projects/{int(project_id)}/{'0' * 32}").rsplit("/", 1)[0] + "/"
        token: str | None = None
        while True:
            page = self._call("list_objects_v2", Prefix=folder, **({"ContinuationToken": token} if token else {}))
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                self._call("delete_objects", Delete={"Objects": objects, "Quiet": True})
            if not page.get("IsTruncated"):
                return
            token = page.get("NextContinuationToken")


def local_storage() -> LocalStorage:
    return LocalStorage(Path(os.getenv("DOCUMENT_STORAGE_DIR") or BACKEND_DIR / "storage" / "documents"))


class RoutingStorage:
    """Writes to the storage the project's account chose; reads each file from where its key says it is."""

    def __init__(self) -> None:
        self.local = local_storage()

    def save(self, project_id: int, content: bytes) -> str:
        import storage_accounts

        target = storage_accounts.bucket_for_new_files(project_id)
        if target is None:
            return self.local.save(project_id, content)
        connection_id, bucket = target
        return bucket_key(connection_id, bucket.save(project_id, content))

    def read(self, key: str) -> bytes:
        located = parse_bucket_key(key)
        if located is None:
            return self.local.read(key)
        return self._bucket(located[0]).read(located[1])

    def delete(self, key: str) -> None:
        located = parse_bucket_key(key)
        if located is None:
            self.local.delete(key)
        else:
            self._bucket(located[0]).delete(located[1])

    def delete_project(self, project_id: int, buckets: Collection[int] | None = None) -> None:
        """Remove every file of the project. Pass `buckets` (from buckets_for_project) when the project's rows are
        already deleted; otherwise they are looked up."""
        self.local.delete_project(project_id)
        for connection_id in self.buckets_for_project(project_id) if buckets is None else buckets:
            try:
                self._bucket(connection_id).delete_project(project_id)
            except StorageError as exc:
                logger.warning("Files of deleted project %s remain in bucket %s: %s", project_id, connection_id, exc)

    def buckets_for_project(self, project_id: int) -> set[int]:
        import storage_accounts

        return storage_accounts.connections_for_project(project_id)

    def _bucket(self, connection_id: int) -> S3Storage:
        import storage_accounts

        bucket = storage_accounts.bucket_by_id(connection_id)
        if bucket is None:
            raise StorageError("The storage bucket this file was saved in is no longer connected")
        return bucket


def document_storage() -> DocumentStorage:
    return RoutingStorage()
