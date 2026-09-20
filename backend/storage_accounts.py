"""Where a billing account's files are kept: OmniReview's storage, or the account's own S3-compatible bucket.

An account (a person, or an organization for its projects) connects at most one bucket. The connection is checked by
writing, reading, and deleting a test object before it is saved. `use_for_new_files` picks where new files go;
existing files stay where they are until the account asks to move them, which the worker does in batches
(move_pending_files). Every stored file is referenced by exactly one column, listed in file_references, so moves and
checks see all of them.
"""

import logging
import secrets
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from typing import Any

from sqlalchemy import cast, func, select
from sqlalchemy.orm import Session
from sqlalchemy.types import Text

import crypto
import entitlements
import models
from database import SessionLocal
from entitlements import Account
from storage import S3Storage, StorageError, bucket_key, local_storage, parse_bucket_key

logger = logging.getLogger(__name__)

FEATURE = "bring_your_own_storage"
PROVIDERS = ("aws", "b2", "r2", "wasabi", "minio", "other")
MOVE_BATCH = 25
# Tables with a storage_key column. Analysis plots keep their keys inside AnalysisRun.plots instead.
KEYED_MODELS: tuple[Any, ...] = (models.Document, models.IPDDataset, models.SubmissionPackage, models.RepositoryDeposit)


def _context(connection: models.StorageConnection, field: str) -> str:
    return f"storage_connection:{account_of(connection).key}:{field}"


def account_of(connection: models.StorageConnection) -> Account:
    if connection.organization_id is not None:
        return Account("organization", connection.organization_id)
    return Account("user", connection.user_id or 0)


def connection_for(db: Session, account: Account) -> models.StorageConnection | None:
    column = models.StorageConnection.user_id if account.kind == "user" else models.StorageConnection.organization_id
    return db.scalar(select(models.StorageConnection).where(column == account.id))


def set_credentials(connection: models.StorageConnection, access_key_id: str, secret_access_key: str) -> None:
    connection.access_key_id_encrypted = crypto.encrypt(access_key_id, _context(connection, "access_key_id"))
    connection.secret_access_key_encrypted = crypto.encrypt(secret_access_key, _context(connection, "secret"))
    connection.access_key_last_four = access_key_id[-4:]


def open_bucket(connection: models.StorageConnection) -> S3Storage:
    return S3Storage(
        bucket=connection.bucket,
        access_key_id=crypto.decrypt(connection.access_key_id_encrypted, _context(connection, "access_key_id")),
        secret_access_key=crypto.decrypt(connection.secret_access_key_encrypted, _context(connection, "secret")),
        endpoint_url=connection.endpoint_url,
        region=connection.region,
        prefix=connection.key_prefix,
    )


def verify(bucket: S3Storage) -> None:
    """Raise StorageError unless the bucket accepts writing, reading back, and deleting an object."""
    key = f"projects/0/{secrets.token_hex(16)}"
    probe = b"OmniReview storage check"
    bucket.put(key, probe)
    try:
        if bucket.read(key) != probe:
            raise StorageError("Your storage bucket returned different content than was written")
    finally:
        bucket.delete(key)


# Called by storage.RoutingStorage, which has no database session of its own.


def bucket_for_new_files(project_id: int) -> tuple[int, S3Storage] | None:
    with SessionLocal() as db:
        project = db.get(models.Project, project_id)
        if project is None:
            return None
        account = entitlements.account_for_project(project)
        connection = connection_for(db, account)
        if (
            connection is None
            or not connection.use_for_new_files
            or connection.pending_move == "to_platform"
            or not entitlements.feature_allowed(db, account, FEATURE)
        ):
            return None
        return connection.id, open_bucket(connection)


def bucket_by_id(connection_id: int) -> S3Storage | None:
    with SessionLocal() as db:
        connection = db.get(models.StorageConnection, connection_id)
        return open_bucket(connection) if connection else None


def connections_for_project(project_id: int) -> set[int]:
    """Buckets that may hold the project's files: those its file references point to, and its account's bucket."""
    with SessionLocal() as db:
        ids = {
            located[0]
            for reference in file_references(db, [project_id])
            if (located := parse_bucket_key(reference.key)) is not None
        }
        project = db.get(models.Project, project_id)
        if project is not None:
            connection = connection_for(db, entitlements.account_for_project(project))
            if connection is not None:
                ids.add(connection.id)
        return ids


@dataclass
class FileReference:
    key: str
    update: Callable[[str], None]


def file_references(db: Session, project_ids) -> Iterator[FileReference]:
    """Every stored file of the given projects (a list of ids or a subquery), with a way to repoint its key."""
    for model in KEYED_MODELS:
        rows = db.scalars(select(model).where(model.project_id.in_(project_ids), model.storage_key != ""))
        for row in rows:
            yield FileReference(row.storage_key, partial(setattr, row, "storage_key"))
    runs = db.scalars(
        select(models.AnalysisRun).where(
            models.AnalysisRun.project_id.in_(project_ids), func.jsonb_array_length(models.AnalysisRun.plots) > 0
        )
    )
    for run in runs:
        for index, plot in enumerate(run.plots):
            if plot.get("storage_key"):

                def update(key: str, run=run, index=index) -> None:
                    plots = [dict(p) for p in run.plots]
                    plots[index]["storage_key"] = key
                    run.plots = plots

                yield FileReference(plot["storage_key"], update)


def count_files(db: Session, account: Account, connection_id: int | None) -> dict[str, int]:
    """How many of the account's files are in OmniReview's storage, and in the given bucket."""
    counts = {"platform": 0, "bucket": 0}
    marker = f"s3:{connection_id}:" if connection_id is not None else None
    for reference in file_references(db, entitlements.project_ids(account)):
        if parse_bucket_key(reference.key) is None:
            counts["platform"] += 1
        elif marker and reference.key.startswith(marker):
            counts["bucket"] += 1
    return counts


def references_connection(db: Session, connection_id: int) -> bool:
    """Whether any file anywhere is still kept in this bucket (including projects that changed account)."""
    marker = f"s3:{connection_id}:%"
    for model in KEYED_MODELS:
        if db.scalar(select(model.id).where(model.storage_key.like(marker)).limit(1)) is not None:
            return True
    plots = cast(models.AnalysisRun.plots, Text)
    return db.scalar(select(models.AnalysisRun.id).where(plots.like(f'%"{marker}')).limit(1)) is not None


def move_pending_files(db: Session, connection: models.StorageConnection, limit: int = MOVE_BATCH) -> int:
    """Move up to `limit` files in the direction the account asked for, committing after each so a failure loses
    nothing. Clears pending_move once no files are left to move. Returns the number moved."""
    direction = connection.pending_move
    if direction not in ("to_bucket", "to_platform"):
        return 0
    local = local_storage()
    bucket = open_bucket(connection)
    marker = f"s3:{connection.id}:"
    moved = 0
    remaining = False
    for reference in list(file_references(db, entitlements.project_ids(account_of(connection)))):
        in_bucket = reference.key.startswith(marker)
        if (direction == "to_bucket" and parse_bucket_key(reference.key) is not None) or (
            direction == "to_platform" and not in_bucket
        ):
            continue
        if moved >= limit:
            remaining = True
            break
        try:
            if direction == "to_bucket":
                content = local.read(reference.key)
                bucket.put(reference.key, content)
                reference.update(bucket_key(connection.id, reference.key))
                db.commit()
                local.delete(reference.key)
            else:
                plain = reference.key[len(marker) :]
                local.put(plain, bucket.read(plain))
                reference.update(plain)
                db.commit()
                bucket.delete(plain)
        except StorageError as exc:
            db.rollback()
            connection.move_error = str(exc)
            db.commit()
            logger.warning("Moving files for storage connection %s stopped: %s", connection.id, exc)
            return moved
        moved += 1
    if not remaining:
        connection.pending_move = None
        connection.move_error = ""
        db.commit()
    return moved


def move_all_pending(db: Session) -> int:
    """Worker entry point: one batch for every account that asked to move its files."""
    moved = 0
    for connection in list(
        db.scalars(select(models.StorageConnection).where(models.StorageConnection.pending_move.is_not(None)))
    ):
        moved += move_pending_files(db, connection)
    return moved
