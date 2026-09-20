"""Choosing where files are stored: OmniReview's storage, or the account's own S3-compatible bucket."""

import time

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import select
from test_billing import billing_on, set_limits  # noqa: F401  (billing_on is used as a fixture)
from test_governance import make_admin
from workflow_helpers import create_project, decide, open_screening, url

import models
import storage_accounts
from database import SessionLocal
from entitlements import Account
from storage import document_storage, local_storage

BUCKET = "omnireview-test-bucket"
CREDENTIALS = {"access_key_id": "AKIAEXAMPLE1234", "secret_access_key": "secret-example-key-value"}
CONNECTION = {"provider": "aws", "region": "us-east-1", "bucket": BUCKET, **CREDENTIALS}


@pytest.fixture
def s3():
    """A stand-in S3 service (moto) with the test bucket ready."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def objects(client, prefix: str = "") -> list[str]:
    listing = client.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return sorted(item["Key"] for item in listing.get("Contents", []))


def storage_url(account_id: int, kind: str = "user", suffix: str = "") -> str:
    return f"/api/storage/{kind}/{account_id}{suffix}"


def account_id(client, headers) -> int:
    return client.get("/api/auth/me", headers=headers).json()["id"]


def connect(client, headers, user_id: int, **overrides):
    return client.put(storage_url(user_id), json={**CONNECTION, **overrides}, headers=headers)


def upload_document(client, project_id, headers, record_id, name="notes.txt", content=b"Methods. Randomized trial."):
    return client.post(
        url(project_id, f"records/{record_id}/documents"), files={"file": (name, content)}, headers=headers
    )


@pytest.fixture
def full_text_project(client, auth_headers, fake_provider):
    """A project with screening open and one included record, ready for a full-text upload."""
    project_id = create_project(client, auth_headers)
    first, _ = open_screening(client, project_id, auth_headers, fake_provider)
    decide(client, project_id, auth_headers, first["id"], "include")
    return project_id, first["id"]


def test_bucket_details_are_checked_and_never_returned(client, auth_headers, s3):
    user_id = account_id(client, auth_headers)

    missing = connect(client, auth_headers, user_id, bucket="omnireview-not-a-bucket")

    assert missing.status_code == 400
    assert "doesn't exist" in missing.json()["detail"]
    with SessionLocal() as db:
        assert db.scalar(select(models.StorageConnection)) is None, "nothing is saved when the check fails"

    saved = connect(client, auth_headers, user_id)

    assert saved.status_code == 200, saved.text
    body = saved.json()["connection"]
    assert (body["bucket"], body["access_key_last_four"], body["use_for_new_files"]) == (BUCKET, "1234", True)
    assert CREDENTIALS["secret_access_key"] not in saved.text
    assert body["last_verified_at"] and objects(s3) == [], "the test object is removed again"
    with SessionLocal() as db:
        row = db.scalar(select(models.StorageConnection))
        assert CREDENTIALS["secret_access_key"].encode() not in row.secret_access_key_encrypted
    assert client.post(storage_url(user_id, suffix="/test"), headers=auth_headers).status_code == 200


def test_endpoints_on_private_networks_are_refused(client, auth_headers, s3):
    user_id = account_id(client, auth_headers)

    response = connect(client, auth_headers, user_id, provider="minio", endpoint_url="http://127.0.0.1:9000")

    assert response.status_code == 422
    assert "https" in response.json()["detail"]


def test_only_the_account_holder_manages_its_storage(client, auth_headers, make_user, s3):
    user_id = account_id(client, auth_headers)
    _, other = make_user()

    assert client.get(storage_url(user_id), headers=other).status_code == 404
    assert connect(client, other, user_id).status_code == 404
    assert client.get(storage_url(user_id), headers=auth_headers).json()["connection"] is None


def test_organization_admins_connect_the_organizations_bucket(client, make_user, s3):
    admin_email, admin_headers = make_user()
    make_admin(admin_email)
    member_email, member_headers = make_user()
    organization = client.post(
        "/api/admin/organizations", json={"name": f"Institute {time.time_ns()}"}, headers=admin_headers
    ).json()
    client.put(
        f"/api/admin/organizations/{organization['id']}/members",
        json={"email": member_email, "role": "admin"},
        headers=admin_headers,
    )

    response = client.put(storage_url(organization["id"], "organization"), json=CONNECTION, headers=member_headers)

    assert response.status_code == 200, response.text
    assert response.json()["connection"]["bucket"] == BUCKET


def test_files_go_to_the_chosen_storage_and_are_read_back_from_it(client, auth_headers, s3, full_text_project):
    project_id, record_id = full_text_project
    user_id = account_id(client, auth_headers)
    assert connect(client, auth_headers, user_id, key_prefix="reviews").status_code == 200

    uploaded = upload_document(client, project_id, auth_headers, record_id)

    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]
    with SessionLocal() as db:
        key = db.get(models.Document, document_id).storage_key
    assert key.startswith("s3:")
    assert objects(s3) == [f"reviews/{key.split(':', 2)[2]}"]
    download = client.get(url(project_id, f"documents/{document_id}/file"), headers=auth_headers)
    assert download.status_code == 200
    assert download.content == b"Methods. Randomized trial."

    usage = client.get(f"/api/billing/accounts/user/{user_id}", headers=auth_headers).json()["usage"]
    assert usage["storage_mb"] == 0, "files in the account's own bucket don't count against the plan"
    assert client.get(storage_url(user_id), headers=auth_headers).json()["files"] == {"platform": 0, "bucket": 1}

    assert client.delete(url(project_id, f"documents/{document_id}"), headers=auth_headers).status_code == 204
    assert objects(s3) == []


def test_new_files_follow_the_choice_and_older_files_stay_readable(client, auth_headers, s3, full_text_project):
    project_id, record_id = full_text_project
    user_id = account_id(client, auth_headers)
    connect(client, auth_headers, user_id)
    in_bucket = upload_document(client, project_id, auth_headers, record_id, "first.txt", b"First file").json()

    switched = client.put(storage_url(user_id, suffix="/use"), json={"use_for_new_files": False}, headers=auth_headers)

    assert switched.status_code == 200, switched.text
    on_platform = upload_document(client, project_id, auth_headers, record_id, "second.txt", b"Second file").json()
    with SessionLocal() as db:
        keys = {row.id: row.storage_key for row in db.scalars(select(models.Document))}
    assert keys[in_bucket["id"]].startswith("s3:") and not keys[on_platform["id"]].startswith("s3:")
    for document, content in ((in_bucket, b"First file"), (on_platform, b"Second file")):
        response = client.get(url(project_id, f"documents/{document['id']}/file"), headers=auth_headers)
        assert response.content == content, "every file is read from where it was written"
    assert client.get(storage_url(user_id), headers=auth_headers).json()["files"] == {"platform": 1, "bucket": 1}


def test_files_are_moved_between_storages_on_request(client, auth_headers, s3, full_text_project):
    project_id, record_id = full_text_project
    user_id = account_id(client, auth_headers)
    on_platform = upload_document(client, project_id, auth_headers, record_id, "before.txt", b"Before").json()
    connect(client, auth_headers, user_id)

    asked = client.post(storage_url(user_id, suffix="/move"), json={"direction": "to_bucket"}, headers=auth_headers)

    assert asked.status_code == 200, asked.text
    with SessionLocal() as db:
        assert storage_accounts.move_all_pending(db) == 1
    assert len(objects(s3)) == 1
    with SessionLocal() as db:
        moved_key = db.get(models.Document, on_platform["id"]).storage_key
        assert moved_key.startswith("s3:")
        assert db.scalar(select(models.StorageConnection)).pending_move is None
    assert client.get(url(project_id, f"documents/{on_platform['id']}/file"), headers=auth_headers).content == b"Before"

    blocked = client.delete(storage_url(user_id), headers=auth_headers)
    assert blocked.status_code == 409
    assert "Move them" in blocked.json()["detail"]

    back = client.post(storage_url(user_id, suffix="/move"), json={"direction": "to_platform"}, headers=auth_headers)
    assert back.status_code == 200, back.text
    with SessionLocal() as db:
        assert storage_accounts.move_all_pending(db) == 1
    assert objects(s3) == []
    with SessionLocal() as db:
        document = db.get(models.Document, on_platform["id"])
        assert not document.storage_key.startswith("s3:")
        assert local_storage().read(document.storage_key) == b"Before"
    assert client.delete(storage_url(user_id), headers=auth_headers).status_code == 204


def test_deleting_a_project_removes_its_files_from_the_bucket(client, auth_headers, s3, full_text_project):
    project_id, record_id = full_text_project
    user_id = account_id(client, auth_headers)
    connect(client, auth_headers, user_id)
    upload_document(client, project_id, auth_headers, record_id)
    assert len(objects(s3)) == 1

    assert client.delete(f"/api/projects/{project_id}", headers=auth_headers).status_code == 204

    assert objects(s3) == []


def test_plans_can_withhold_bringing_your_own_storage(client, auth_headers, s3, billing_on):  # noqa: F811
    user_id = account_id(client, auth_headers)
    set_limits("free", bring_your_own_storage=False)
    try:
        refused = connect(client, auth_headers, user_id)

        assert refused.status_code == 402
        assert "doesn't include" in refused.json()["detail"]
        assert client.get(storage_url(user_id), headers=auth_headers).json()["feature_allowed"] is False
    finally:
        set_limits("free", bring_your_own_storage=True)


def test_a_missing_bucket_connection_gives_a_clear_error(client, auth_headers, s3, full_text_project):
    project_id, record_id = full_text_project
    user_id = account_id(client, auth_headers)
    assert connect(client, auth_headers, user_id).status_code == 200
    document = upload_document(client, project_id, auth_headers, record_id).json()
    with SessionLocal() as db:
        assert db.get(models.Document, document["id"]).storage_key.startswith("s3:")
        db.delete(storage_accounts.connection_for(db, Account("user", user_id)))
        db.commit()

    response = client.get(url(project_id, f"documents/{document['id']}/file"), headers=auth_headers)

    assert response.status_code == 404, response.text
    assert "no longer connected" in response.json()["detail"]


def test_storage_routing_is_off_without_a_connection(client, auth_headers, full_text_project):
    project_id, _ = full_text_project

    key = document_storage().save(project_id, b"local only")

    assert not key.startswith("s3:")
    assert document_storage().read(key) == b"local only"
