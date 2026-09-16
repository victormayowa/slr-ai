"""Account security and privacy: terms acceptance, email verification, password resets and changes, two-factor
sign-in, a copy of your data, account deletion, and the legal documents."""

import json
import re
from datetime import timedelta

from factories import PASSWORD, registration
from sqlalchemy import select
from workflow_helpers import add_member, create_project

import emailer
import mfa
import models
from account_routes import run_due_deletions
from database import SessionLocal
from llm.providers import PROVIDERS


def link_token(to: str, path: str) -> str:
    for message in reversed(emailer.OUTBOX):
        if message["to"] == to and path in message["text"]:
            match = re.search(r"token=([A-Za-z0-9_\-]+)", message["text"])
            assert match is not None
            return match.group(1)
    raise AssertionError(f"No email to {to} with {path}")


def update_user(email: str, **fields) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(models.User).where(models.User.email == email))
        assert user is not None
        for name, value in fields.items():
            setattr(user, name, value)
        db.commit()


def register(client, **overrides) -> dict:
    user = registration(**overrides)
    response = client.post("/api/auth/register", json=user)
    assert response.status_code == 200, response.text
    return user


def sign_in(client, identifier: str, password: str = PASSWORD):
    return client.post("/api/auth/login", json={"identifier": identifier, "password": password})


def bearer(response) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_registration_needs_the_terms_accepted(client):
    response = client.post("/api/auth/register", json=registration(accept_terms=False))

    assert response.status_code == 422
    assert "Terms of Service" in response.text


def test_registration_sends_a_link_that_confirms_the_address(client, login):
    user = register(client)
    token = link_token(user["email"], "/verify-email")

    confirmed = client.post("/api/auth/verify-email/confirm", json={"token": token})

    assert confirmed.status_code == 200 and confirmed.json()["email_verified"] is True
    me = client.get("/api/auth/me", headers=login(user["email"])).json()
    assert me["email_verified"] is True and me["terms_version"] == me["current_terms_version"]
    # A link works once.
    assert client.post("/api/auth/verify-email/confirm", json={"token": token}).status_code == 400


def test_unconfirmed_accounts_cannot_sign_in_when_verification_is_required(client, monkeypatch):
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    user = register(client)

    blocked = sign_in(client, user["email"])
    assert blocked.status_code == 403
    assert "Confirm your email address" in blocked.json()["detail"]

    client.post("/api/auth/verify-email/confirm", json={"token": link_token(user["email"], "/verify-email")})
    assert sign_in(client, user["email"]).status_code == 200


def test_password_reset_ends_old_sessions(client, make_user):
    email, old_headers = make_user()

    requested = client.post("/api/auth/password-reset/request", json={"email": email})
    assert requested.status_code == 202
    token = link_token(email, "/reset-password")

    reset = client.post("/api/auth/password-reset/confirm", json={"token": token, "password": "a brand new password"})
    assert reset.status_code == 200, reset.text

    assert client.get("/api/auth/me", headers=old_headers).status_code == 401
    assert sign_in(client, email).status_code == 401
    assert sign_in(client, email, "a brand new password").status_code == 200
    # The link can't be used again.
    again = client.post("/api/auth/password-reset/confirm", json={"token": token, "password": "yet another password"})
    assert again.status_code == 400


def test_password_reset_does_not_reveal_whether_an_account_exists(client, make_user):
    email, _ = make_user()
    unknown = "nobody-here@example.org"
    sent_before = len(emailer.OUTBOX)

    known = client.post("/api/auth/password-reset/request", json={"email": email})
    missing = client.post("/api/auth/password-reset/request", json={"email": unknown})

    assert known.status_code == missing.status_code == 202
    assert known.json() == missing.json()
    assert not any(message["to"] == unknown for message in list(emailer.OUTBOX)[sent_before:])


def test_an_expired_reset_link_is_refused(client, make_user):
    email, _ = make_user()
    client.post("/api/auth/password-reset/request", json={"email": email})
    token = link_token(email, "/reset-password")
    with SessionLocal() as db:
        row = db.scalars(select(models.AuthToken).order_by(models.AuthToken.id.desc())).first()
        assert row is not None and row.purpose == "password_reset"
        row.expires_at = models.utcnow() - timedelta(minutes=1)
        db.commit()

    response = client.post(
        "/api/auth/password-reset/confirm", json={"token": token, "password": "a brand new password"}
    )

    assert response.status_code == 400
    assert "expired" in response.json()["detail"]


def test_changing_the_password_keeps_only_this_session(client, make_user):
    _, headers = make_user()

    wrong = client.post(
        "/api/me/password", json={"current_password": "not it", "new_password": "a brand new password"}, headers=headers
    )
    assert wrong.status_code == 400

    changed = client.post(
        "/api/me/password", json={"current_password": PASSWORD, "new_password": "a brand new password"}, headers=headers
    )
    assert changed.status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(changed)).status_code == 200


def test_two_factor_sign_in(client, make_user):
    email, headers = make_user()
    setup = client.post("/api/me/mfa/setup", headers=headers).json()
    secret = setup["secret"]
    assert setup["otpauth_uri"].startswith("otpauth://totp/")

    assert client.post("/api/me/mfa/enable", json={"code": "000000"}, headers=headers).status_code == 400
    enabled = client.post("/api/me/mfa/enable", json={"code": mfa.code_at(secret, mfa.current_step())}, headers=headers)
    assert enabled.status_code == 200, enabled.text
    recovery = enabled.json()["recovery_codes"]
    assert len(recovery) == mfa.RECOVERY_CODES

    first = sign_in(client, email).json()
    assert first["mfa_required"] is True and "access_token" not in first
    # The code already used to turn two-factor on can't be replayed.
    replay = client.post(
        "/api/auth/mfa/verify", json={"mfa_token": first["mfa_token"], "code": mfa.code_at(secret, mfa.current_step())}
    )
    assert replay.status_code == 401

    second = sign_in(client, email).json()
    verified = client.post(
        "/api/auth/mfa/verify",
        json={"mfa_token": second["mfa_token"], "code": mfa.code_at(secret, mfa.current_step() + 1)},
    )
    assert verified.status_code == 200 and verified.json()["access_token"]

    # A recovery code works once.
    third = sign_in(client, email).json()
    assert (
        client.post("/api/auth/mfa/verify", json={"mfa_token": third["mfa_token"], "code": recovery[0]}).status_code
        == 200
    )
    fourth = sign_in(client, email).json()
    assert (
        client.post("/api/auth/mfa/verify", json={"mfa_token": fourth["mfa_token"], "code": recovery[0]}).status_code
        == 401
    )

    session = bearer(verified)
    disabled = client.post("/api/me/mfa/disable", json={"password": PASSWORD, "code": recovery[1]}, headers=session)
    assert disabled.status_code == 200 and disabled.json()["mfa_enabled"] is False
    assert "access_token" in sign_in(client, email).json()


def test_a_copy_of_my_data_has_no_secrets(client, make_user):
    email, headers = make_user()
    create_project(client, headers, title="My private review")

    response = client.get("/api/me/export", headers=headers)

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    data = json.loads(response.content)
    assert data["profile"]["email"] == email
    assert [project["title"] for project in data["projects"]] == ["My private review"]
    assert "hashed_password" not in response.text and "$2b$" not in response.text


def test_deletion_waits_for_ownership_of_shared_projects(client, make_user):
    _, headers = make_user()
    project_id = create_project(client, headers, title="Shared review")
    add_member(client, project_id, headers, make_user, "screener")

    wrong_words = client.post("/api/me/deletion", json={"password": PASSWORD, "confirmation": "yes"}, headers=headers)
    assert wrong_words.status_code == 422
    wrong_password = client.post(
        "/api/me/deletion", json={"password": "nope", "confirmation": "delete my account"}, headers=headers
    )
    assert wrong_password.status_code == 400

    blocked = client.post(
        "/api/me/deletion", json={"password": PASSWORD, "confirmation": "delete my account"}, headers=headers
    )
    assert blocked.status_code == 409
    assert "Shared review" in blocked.json()["detail"]


def test_account_deletion_can_be_cancelled_and_is_carried_out_after_the_grace_period(client, make_user):
    email, headers = make_user()
    account_id = client.get("/api/auth/me", headers=headers).json()["id"]
    project_id = create_project(client, headers, title="Only mine")
    body = {"password": PASSWORD, "confirmation": "delete my account"}

    scheduled = client.post("/api/me/deletion", json=body, headers=headers)
    assert scheduled.status_code == 200 and scheduled.json()["deletion_due_at"]
    assert client.delete("/api/me/deletion", headers=headers).json()["deletion_requested_at"] is None

    client.post("/api/me/deletion", json=body, headers=headers)
    update_user(email, deletion_requested_at=models.utcnow() - timedelta(days=15))
    with SessionLocal() as db:
        assert run_due_deletions(db) >= 1
        user = db.get(models.User, account_id)
        assert user is not None
        assert user.email == f"deleted-{account_id}@deleted.invalid"
        assert user.is_active is False and user.first_name == "Deleted" and user.deleted_at is not None
        # The project only this user belonged to is gone.
        assert db.get(models.Project, project_id) is None

    assert sign_in(client, email).status_code == 401
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_legal_documents_are_published(client):
    listed = client.get("/api/legal")
    assert listed.status_code == 200
    assert {"terms", "privacy", "cookies", "subprocessors", "dpa"} <= {doc["slug"] for doc in listed.json()}

    subprocessors = client.get("/api/legal/subprocessors").json()
    first_provider = next(iter(PROVIDERS.values())).label
    assert first_provider in subprocessors["content"]
    assert subprocessors["version"]

    assert client.get("/api/legal/not-a-document").status_code == 404
    assert client.get("/api/legal/..%2Fsecrets").status_code == 404


def test_security_settings_summary(client, make_user):
    _, headers = make_user()

    summary = client.get("/api/me/security", headers=headers).json()

    assert summary["mfa_enabled"] is False
    assert summary["deletion_requested_at"] is None
    assert summary["terms_version"] == summary["current_terms_version"]


def test_resend_verification(client, make_user):
    email, headers = make_user()

    response = client.post("/api/auth/verify-email/request", headers=headers)

    assert response.status_code == 202
    assert link_token(email, "/verify-email")
