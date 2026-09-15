"""Private file storage for project documents. Files are addressed by keys the server generates, never by user input.

Only local disk storage exists for now (DOCUMENT_STORAGE_DIR); an S3-compatible store can implement the same interface.
"""

import os
import re
import secrets
import shutil
from pathlib import Path
from typing import Protocol

BACKEND_DIR = Path(__file__).resolve().parent
_KEY_PATTERN = re.compile(r"projects/\d+/[0-9a-f]{32}")


class StorageError(Exception):
    """A stored file can't be read or written. The message is safe to show users."""


class DocumentStorage(Protocol):
    def save(self, project_id: int, content: bytes) -> str: ...

    def read(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def delete_project(self, project_id: int) -> None: ...


class LocalStorage:
    """Files on local disk under one root directory, grouped by project so a project's files can be removed together."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        if not _KEY_PATTERN.fullmatch(key):
            raise StorageError("Invalid storage key")
        return self.root / key

    def save(self, project_id: int, content: bytes) -> str:
        key = f"projects/{project_id}/{secrets.token_hex(16)}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        partial = path.with_name(f"{path.name}.partial")
        partial.write_bytes(content)
        partial.replace(path)
        return key

    def read(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("The stored file is missing") from exc

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_project(self, project_id: int) -> None:
        shutil.rmtree(self.root / "projects" / str(int(project_id)), ignore_errors=True)


def document_storage() -> DocumentStorage:
    return LocalStorage(Path(os.getenv("DOCUMENT_STORAGE_DIR") or BACKEND_DIR / "storage" / "documents"))
