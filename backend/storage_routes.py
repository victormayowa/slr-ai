"""An account's choice of where its files are stored: OmniReview's storage, or its own S3-compatible bucket.

The personal account is managed by its owner; an organization's by its owners and admins (the same people who manage
its billing). Credentials are verified before saving, encrypted, and never returned.
"""

import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import crypto
import entitlements
import models
import storage_accounts
from auth_routes import get_current_user
from billing_routes import _account
from database import get_db
from entitlements import Account
from net_safety import UnsafeURL, ensure_public_url, private_urls_allowed
from storage import S3Storage, StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/storage", tags=["storage"])

_BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]")
_PREFIX = re.compile(r"[A-Za-z0-9!\-_.*'()/]*")


class ConnectionUpdate(BaseModel):
    provider: Literal["aws", "b2", "r2", "wasabi", "minio", "other"]
    endpoint_url: str = Field(default="", max_length=500)
    region: str = Field(default="", max_length=60)
    bucket: str = Field(min_length=3, max_length=63)
    key_prefix: str = Field(default="", max_length=200)
    # Both may be left out when changing other settings of a saved connection, to keep its credentials.
    access_key_id: str | None = Field(default=None, min_length=4, max_length=200)
    secret_access_key: str | None = Field(default=None, min_length=8, max_length=500)
    use_for_new_files: bool = True


class UseUpdate(BaseModel):
    use_for_new_files: bool


class MoveRequest(BaseModel):
    direction: Literal["to_bucket", "to_platform"]


def _connection_out(db: Session, account: Account, connection: models.StorageConnection | None) -> dict:
    return {
        "feature_allowed": entitlements.feature_allowed(db, account, storage_accounts.FEATURE),
        "files": storage_accounts.count_files(db, account, connection.id if connection else None),
        "connection": (
            {
                "provider": connection.provider,
                "endpoint_url": connection.endpoint_url,
                "region": connection.region,
                "bucket": connection.bucket,
                "key_prefix": connection.key_prefix,
                "access_key_last_four": connection.access_key_last_four,
                "use_for_new_files": connection.use_for_new_files,
                "pending_move": connection.pending_move,
                "move_error": connection.move_error,
                "last_verified_at": connection.last_verified_at,
                "updated_at": connection.updated_at,
            }
            if connection
            else None
        ),
    }


def _check_endpoint(endpoint_url: str) -> None:
    if not endpoint_url:
        return
    if not endpoint_url.startswith("https://") and not private_urls_allowed():
        raise HTTPException(status_code=422, detail="The endpoint must be an https:// address")
    try:
        ensure_public_url(endpoint_url)
    except UnsafeURL as exc:
        raise HTTPException(status_code=422, detail=f"Endpoint: {exc}") from exc


def _verify(bucket: S3Storage) -> None:
    try:
        storage_accounts.verify(bucket)
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=f"The bucket couldn't be used: {exc}") from exc


@router.get("/{kind}/{account_id}")
def get_storage(
    kind: str, account_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    account = _account(db, user, kind, account_id)
    return _connection_out(db, account, storage_accounts.connection_for(db, account))


@router.put("/{kind}/{account_id}")
def save_connection(
    kind: str,
    account_id: int,
    body: ConnectionUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Connect a bucket, or change the connected one. Nothing is saved unless a test object can be written, read back,
    and deleted with the given details."""
    account = _account(db, user, kind, account_id)
    entitlements.require_feature(db, account, storage_accounts.FEATURE)
    bucket_name = body.bucket.strip()
    prefix = body.key_prefix.strip().strip("/")
    endpoint = body.endpoint_url.strip().rstrip("/")
    if not _BUCKET_NAME.fullmatch(bucket_name):
        raise HTTPException(status_code=422, detail="Bucket names use lowercase letters, numbers, dots, and hyphens")
    if not _PREFIX.fullmatch(prefix):
        raise HTTPException(status_code=422, detail="The folder may use letters, numbers, / and - _ . * ' ( ) !")
    if body.provider != "aws" and not endpoint:
        raise HTTPException(status_code=422, detail="Give the endpoint address your storage provider lists")
    _check_endpoint(endpoint)

    connection = storage_accounts.connection_for(db, account)
    if connection is None:
        if not body.access_key_id or not body.secret_access_key:
            raise HTTPException(status_code=422, detail="Give the access key ID and secret access key")
        connection = models.StorageConnection(
            user_id=account.id if account.kind == "user" else None,
            organization_id=account.id if account.kind == "organization" else None,
            created_by_id=user.id,
        )
    else:
        location_changed = (endpoint, body.region.strip(), bucket_name, prefix) != (
            connection.endpoint_url,
            connection.region,
            connection.bucket,
            connection.key_prefix,
        )
        if location_changed and (connection.pending_move or storage_accounts.references_connection(db, connection.id)):
            raise HTTPException(
                status_code=409,
                detail="Files are still stored in the current bucket. Move them to OmniReview's storage before "
                "connecting a different bucket or folder.",
            )
        if (body.access_key_id is None) != (body.secret_access_key is None):
            raise HTTPException(status_code=422, detail="Give both the access key ID and the secret access key")

    connection.provider = body.provider
    connection.endpoint_url = endpoint
    connection.region = body.region.strip()
    connection.bucket = bucket_name
    connection.key_prefix = prefix
    connection.use_for_new_files = body.use_for_new_files
    if body.access_key_id and body.secret_access_key:
        try:
            storage_accounts.set_credentials(connection, body.access_key_id.strip(), body.secret_access_key.strip())
        except crypto.EncryptionNotConfigured as exc:
            raise HTTPException(
                status_code=503, detail="Storage credentials can't be saved because server encryption isn't set up"
            ) from exc
    _verify(storage_accounts.open_bucket(connection))
    connection.last_verified_at = models.utcnow()
    connection.updated_at = models.utcnow()
    db.add(connection)
    db.commit()
    logger.info("Storage connection for %s saved by user %s (bucket %s)", account.key, user.id, bucket_name)
    return _connection_out(db, account, connection)


def _saved(db: Session, account: Account) -> models.StorageConnection:
    connection = storage_accounts.connection_for(db, account)
    if connection is None:
        raise HTTPException(status_code=404, detail="No storage bucket is connected")
    return connection


@router.post("/{kind}/{account_id}/test")
def test_connection(
    kind: str, account_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    account = _account(db, user, kind, account_id)
    connection = _saved(db, account)
    _verify(storage_accounts.open_bucket(connection))
    connection.last_verified_at = models.utcnow()
    db.commit()
    return _connection_out(db, account, connection)


@router.put("/{kind}/{account_id}/use")
def choose_storage(
    kind: str,
    account_id: int,
    body: UseUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Choose where new files go. Files already stored stay where they are."""
    account = _account(db, user, kind, account_id)
    connection = _saved(db, account)
    if body.use_for_new_files:
        entitlements.require_feature(db, account, storage_accounts.FEATURE)
    connection.use_for_new_files = body.use_for_new_files
    connection.updated_at = models.utcnow()
    db.commit()
    return _connection_out(db, account, connection)


@router.post("/{kind}/{account_id}/move")
def move_files(
    kind: str,
    account_id: int,
    body: MoveRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ask for the account's existing files to be moved. The worker moves them in batches; progress shows in `files`."""
    account = _account(db, user, kind, account_id)
    connection = _saved(db, account)
    if body.direction == "to_bucket":
        entitlements.require_feature(db, account, storage_accounts.FEATURE)
    if connection.pending_move and connection.pending_move != body.direction:
        raise HTTPException(status_code=409, detail="Files are already being moved the other way. Wait until it ends.")
    connection.pending_move = body.direction
    connection.move_error = ""
    if body.direction == "to_platform":
        connection.use_for_new_files = False
    db.commit()
    return _connection_out(db, account, connection)


@router.delete("/{kind}/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(
    kind: str, account_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Remove the bucket's details. Refused while any file is still stored in it."""
    account = _account(db, user, kind, account_id)
    connection = _saved(db, account)
    if connection.pending_move or storage_accounts.references_connection(db, connection.id):
        raise HTTPException(
            status_code=409,
            detail="Files are still stored in this bucket. Move them to OmniReview's storage first.",
        )
    db.delete(connection)
    db.commit()
    logger.info("Storage connection for %s removed by user %s", account.key, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
