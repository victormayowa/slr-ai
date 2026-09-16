"""Account security and privacy: password resets, email verification, password changes, two-factor sign-in, a copy of
your data, account deletion, and the legal documents.

Account deletion is scheduled with a grace period and carried out by the worker (anonymize_user). The account row is
anonymized rather than removed: the project audit trail's hash chain records who did what, and removing the row would
break it. Contributions to reviews shared with other people (decisions, extracted values) stay with the review,
attributed to "Deleted user"; projects that only you belonged to are deleted with their files.
"""

import json
import logging
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import billing
import crypto
import emailer
import mfa
import models
import notifications
from auth_routes import (
    EMAIL_PATTERN,
    LOGIN_RATE_LIMIT_PER_MINUTE,
    TERMS_VERSION,
    check_password,
    consume_auth_token,
    get_current_user,
    hash_password,
    issue_auth_token,
    password_matches,
    send_verification_email,
    session_response,
)
from database import get_db
from entitlements import Account, subscription_for
from llm.providers import PROVIDERS
from permissions import ProjectRole
from rate_limiting import rate_limit
from storage import document_storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["account"])

LEGAL_DIR = Path(os.getenv("LEGAL_DOCS_DIR", str(Path(__file__).resolve().parent.parent / "docs" / "legal")))
LEGAL_SLUG = re.compile(r"^[a-z][a-z-]{1,40}$")
DELETION_CONFIRMATION = "delete my account"


def deletion_grace() -> timedelta:
    return timedelta(days=int(os.getenv("ACCOUNT_DELETION_GRACE_DAYS", "14")))


# --- Password resets and email verification ---


class ResetRequest(BaseModel):
    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)


class ResetConfirm(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=8, max_length=200)

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: str) -> str:
        return check_password(value)


class TokenBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)


@router.post(
    "/api/auth/password-reset/request",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("password-reset", limit=5, window_seconds=3600))],
)
def request_password_reset(body: ResetRequest, db: Session = Depends(get_db)):
    """Email a reset link. The answer is the same whether or not an account uses the address, so the form can't be
    used to find out who has an account."""
    user = db.scalar(select(models.User).where(func.lower(models.User.email) == body.email.strip().lower()))
    if user is not None and user.is_active and user.deleted_at is None:
        raw = issue_auth_token(db, user, "password_reset")
        db.commit()
        link = notifications.app_url(f"/reset-password?token={raw}")
        try:
            emailer.send(
                user.email,
                "Reset your OmniReview password",
                f"Hello {user.first_name},\n\nSomeone asked to reset the password for your OmniReview account. Open "
                f"this link within an hour to choose a new one:\n\n{link}\n\nIf you didn't ask, ignore this email; "
                "your password stays the same.",
            )
        except emailer.EmailError:
            logger.warning("The password reset email for user %s couldn't be sent", user.id)
    return {"message": "If an account uses that address, we've sent it a link to reset the password."}


@router.post(
    "/api/auth/password-reset/confirm",
    dependencies=[Depends(rate_limit("password-reset-confirm", limit=10, window_seconds=3600))],
)
def confirm_password_reset(body: ResetConfirm, db: Session = Depends(get_db)):
    """Set a new password. Every existing session ends; two-factor sign-in stays on."""
    user = consume_auth_token(db, body.token, "password_reset")
    user.hashed_password = hash_password(body.password)
    user.password_changed_at = models.utcnow()
    # The link proves control of the mailbox, so the address counts as confirmed.
    user.email_verified_at = user.email_verified_at or models.utcnow()
    db.commit()
    return {"message": "Your password has been changed. Sign in with the new password."}


@router.post("/api/auth/verify-email/confirm")
def confirm_email(body: TokenBody, db: Session = Depends(get_db)):
    user = consume_auth_token(db, body.token, "email_verification")
    user.email_verified_at = user.email_verified_at or models.utcnow()
    db.commit()
    return {"email_verified": True, "email": user.email}


@router.post("/api/auth/verify-email/request", status_code=status.HTTP_202_ACCEPTED)
def resend_verification(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.email_verified_at is not None:
        return {"message": "Your email address is already confirmed."}
    send_verification_email(db, user)
    return {"message": f"We've sent a new link to {user.email}."}


class MfaLogin(BaseModel):
    mfa_token: str = Field(min_length=10, max_length=200)
    code: str = Field(min_length=6, max_length=20)


@router.post(
    "/api/auth/mfa/verify",
    dependencies=[Depends(rate_limit("login", limit=LOGIN_RATE_LIMIT_PER_MINUTE, window_seconds=60))],
)
def verify_mfa_login(body: MfaLogin, db: Session = Depends(get_db)):
    """The second step of signing in: an authenticator code, or a recovery code."""
    user = consume_auth_token(db, body.mfa_token, "mfa_login")
    if not mfa.verify_second_factor(user, body.code):
        # The sign-in token is spent, so a wrong code means starting again: codes can't be guessed in a loop.
        db.commit()
        raise HTTPException(status_code=401, detail="That code isn't right. Sign in again to try another code.")
    db.commit()
    return session_response(user)


# --- Security settings ---


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)

    @field_validator("new_password")
    @classmethod
    def valid_password(cls, value: str) -> str:
        return check_password(value)


class CodeBody(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class DisableMfa(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=6, max_length=20)


def security_out(user: models.User) -> dict[str, Any]:
    due = user.deletion_requested_at + deletion_grace() if user.deletion_requested_at else None
    return {
        "email": user.email,
        "email_verified": user.email_verified_at is not None,
        "mfa_enabled": user.mfa_enabled,
        "recovery_codes_left": len(user.mfa_recovery_hashes or []) if user.mfa_enabled else 0,
        "password_changed_at": user.password_changed_at,
        "terms_version": user.terms_version,
        "terms_accepted_at": user.terms_accepted_at,
        "current_terms_version": TERMS_VERSION,
        "deletion_requested_at": user.deletion_requested_at,
        "deletion_due_at": due,
    }


@router.get("/api/me/security")
def get_security(user: models.User = Depends(get_current_user)):
    return security_out(user)


@router.post("/api/me/password")
def change_password(body: PasswordChange, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Change the password. Other sessions end; this one continues with the new session token returned."""
    if not password_matches(user, body.current_password):
        raise HTTPException(status_code=400, detail="Your current password isn't right")
    user.hashed_password = hash_password(body.new_password)
    user.password_changed_at = models.utcnow()
    db.commit()
    return session_response(user)


@router.post("/api/me/terms/accept")
def accept_terms(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.terms_accepted_at, user.terms_version = models.utcnow(), TERMS_VERSION
    db.commit()
    return security_out(user)


@router.post("/api/me/mfa/setup")
def setup_mfa(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Start two-factor setup: a new secret for an authenticator app. It takes effect once a code is confirmed."""
    if user.mfa_enabled:
        raise HTTPException(status_code=409, detail="Two-factor sign-in is already on. Turn it off to set it up again.")
    secret = mfa.new_secret()
    try:
        user.mfa_secret_encrypted = crypto.encrypt(secret, mfa.secret_context(user.id))
    except crypto.EncryptionNotConfigured as exc:
        raise HTTPException(
            status_code=503, detail="Two-factor sign-in needs the server's encryption configured"
        ) from exc
    user.mfa_last_step = None
    db.commit()
    return {"secret": secret, "otpauth_uri": mfa.provisioning_uri(secret, user.email)}


@router.post("/api/me/mfa/enable")
def enable_mfa(body: CodeBody, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.mfa_enabled:
        raise HTTPException(status_code=409, detail="Two-factor sign-in is already on")
    if user.mfa_secret_encrypted is None:
        raise HTTPException(status_code=409, detail="Start two-factor setup first")
    if not mfa.verify_totp(user, body.code):
        raise HTTPException(
            status_code=400, detail="That code isn't right. Check the time on your device and try again."
        )
    codes = mfa.new_recovery_codes()
    user.mfa_enabled = True
    user.mfa_recovery_hashes = [mfa.hash_recovery_code(code) for code in codes]
    db.commit()
    return {"recovery_codes": codes}


@router.post("/api/me/mfa/recovery-codes")
def regenerate_recovery_codes(
    body: CodeBody, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if not user.mfa_enabled:
        raise HTTPException(status_code=409, detail="Two-factor sign-in is off")
    if not mfa.verify_totp(user, body.code):
        raise HTTPException(status_code=400, detail="That code isn't right")
    codes = mfa.new_recovery_codes()
    user.mfa_recovery_hashes = [mfa.hash_recovery_code(code) for code in codes]
    db.commit()
    return {"recovery_codes": codes}


@router.post("/api/me/mfa/disable")
def disable_mfa(body: DisableMfa, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.mfa_enabled:
        raise HTTPException(status_code=409, detail="Two-factor sign-in is already off")
    if not password_matches(user, body.password) or not mfa.verify_second_factor(user, body.code):
        raise HTTPException(status_code=400, detail="The password or code isn't right")
    user.mfa_enabled, user.mfa_secret_encrypted, user.mfa_last_step, user.mfa_recovery_hashes = False, None, None, []
    db.commit()
    return security_out(user)


# --- A copy of your data ---


def export_data(db: Session, user: models.User) -> dict[str, Any]:
    memberships = db.scalars(select(models.ProjectMember).where(models.ProjectMember.user_id == user.id)).all()
    decisions = db.execute(
        select(models.ScreeningDecision, models.Record.title, models.Record.project_id)
        .join(models.Record, models.Record.id == models.ScreeningDecision.record_id)
        .where(models.ScreeningDecision.reviewer_id == user.id)
        .order_by(models.ScreeningDecision.id)
    ).all()
    comments = db.scalars(
        select(models.Comment).where(models.Comment.author_id == user.id).order_by(models.Comment.id)
    ).all()
    tasks = db.scalars(
        select(models.Task)
        .where((models.Task.assignee_id == user.id) | (models.Task.created_by_id == user.id))
        .order_by(models.Task.id)
    ).all()
    audit = db.scalars(
        select(models.AuditEvent)
        .where(models.AuditEvent.actor_id == user.id)
        .order_by(models.AuditEvent.id.desc())
        .limit(5000)
    ).all()
    subscription = subscription_for(db, Account("user", user.id))
    return {
        "exported_at": models.utcnow(),
        "profile": {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "institutional_email": user.institutional_email,
            "orcid_id": user.orcid_id,
            "institution": user.institution,
            "position_role": user.position_role,
            "reason_for_joining": user.reason_for_joining,
            "created_at": user.created_at,
            "email_verified_at": user.email_verified_at,
            "terms_version": user.terms_version,
            "terms_accepted_at": user.terms_accepted_at,
            "email_notifications": user.email_notifications,
            "mfa_enabled": user.mfa_enabled,
        },
        "organizations": [
            {"name": membership.organization.name, "role": membership.role}
            for membership in user.organization_memberships
        ],
        "projects": [
            {"project_id": member.project_id, "title": member.project.title, "role": member.role}
            for member in memberships
        ],
        "declarations": [
            {
                "project_id": row.project_id,
                "has_competing_interests": row.has_competing_interests,
                "statement": row.statement,
                "funding": row.funding,
            }
            for row in db.scalars(select(models.MemberDeclaration).where(models.MemberDeclaration.user_id == user.id))
        ],
        "screening_decisions": [
            {
                "project_id": project_id,
                "record": title,
                "stage": decision.stage,
                "decision": decision.decision,
                "reason_code": decision.reason_code,
                "note": decision.note,
                "created_at": decision.created_at,
            }
            for decision, title, project_id in decisions
        ],
        "comments": [
            {
                "project_id": comment.project_id,
                "about": comment.anchor_label or comment.anchor_key,
                "body": comment.body,
                "created_at": comment.created_at,
            }
            for comment in comments
            if not comment.deleted
        ],
        "tasks": [
            {"project_id": task.project_id, "title": task.title, "status": task.status, "due_on": task.due_on}
            for task in tasks
        ],
        "notifications": [
            {"title": row.title, "body": row.body, "created_at": row.created_at, "read_at": row.read_at}
            for row in db.scalars(select(models.Notification).where(models.Notification.user_id == user.id))
        ],
        "api_tokens": [
            {"name": token.name, "prefix": token.prefix, "scopes": token.scopes, "created_at": token.created_at}
            for token in db.scalars(select(models.ApiToken).where(models.ApiToken.user_id == user.id))
        ],
        "saved_ai_provider_keys": [
            row.provider for row in db.scalars(select(models.UserAPIKey).where(models.UserAPIKey.user_id == user.id))
        ],
        "subscription": (
            {"plan": subscription.plan.name, "status": subscription.status, "created_at": subscription.created_at}
            if subscription
            else None
        ),
        "activity": [
            {"project_id": event.project_id, "action": event.action, "created_at": event.created_at} for event in audit
        ],
    }


@router.get("/api/me/export")
def download_my_data(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Everything OmniReview holds about you, as JSON. Secrets (passwords, keys, token values) are never included."""
    content = json.dumps(export_data(db, user), indent=2, default=str).encode()
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="omnireview-my-data.json"'},
    )


# --- Account deletion ---


class DeletionRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    confirmation: str = Field(min_length=1, max_length=100)


def blocking_projects(db: Session, user: models.User) -> list[str]:
    """Shared projects where the user is the only owner: ownership must be handed over before the account can go."""
    titles = []
    for member in db.scalars(select(models.ProjectMember).where(models.ProjectMember.user_id == user.id)):
        project = member.project
        others = [m for m in project.members if m.user_id != user.id]
        owners = [m for m in project.members if m.role == ProjectRole.OWNER]
        if others and member.role == ProjectRole.OWNER and len(owners) == 1:
            titles.append(project.title)
    return titles


@router.post("/api/me/deletion")
def request_deletion(
    body: DeletionRequest, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Schedule the account for deletion after the grace period. Until then it can be cancelled."""
    if body.confirmation.strip().lower() != DELETION_CONFIRMATION:
        raise HTTPException(status_code=422, detail=f'Type "{DELETION_CONFIRMATION}" to confirm')
    if not password_matches(user, body.password):
        raise HTTPException(status_code=400, detail="Your password isn't right")
    blocked = blocking_projects(db, user)
    if blocked:
        raise HTTPException(
            status_code=409,
            detail=f"Hand over ownership of these projects first: {', '.join(blocked[:10])}",
        )
    user.deletion_requested_at = models.utcnow()
    db.commit()
    due = user.deletion_requested_at + deletion_grace()
    try:
        emailer.send(
            user.email,
            "Your OmniReview account will be deleted",
            f"Hello {user.first_name},\n\nYour account is scheduled for deletion on {due:%d %B %Y}. Until then you "
            "can cancel under Settings. Download a copy of your data there first if you want one.",
        )
    except emailer.EmailError:
        logger.warning("The deletion confirmation for user %s couldn't be sent", user.id)
    return security_out(user)


@router.delete("/api/me/deletion")
def cancel_deletion(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.deletion_requested_at = None
    db.commit()
    return security_out(user)


def anonymize_user(db: Session, user: models.User) -> bool:
    """Carry out a deletion. Returns False (and leaves the account) if the user is still the only owner of a shared
    project."""
    if blocking_projects(db, user):
        return False
    storage = document_storage()
    for member in list(db.scalars(select(models.ProjectMember).where(models.ProjectMember.user_id == user.id))):
        project = member.project
        if all(m.user_id == user.id for m in project.members):
            storage.delete_project(project.id)
            db.delete(project)
        else:
            db.delete(member)
    subscription = subscription_for(db, Account("user", user.id))
    if subscription is not None and subscription.status != "canceled":
        chosen = billing.PROVIDERS.get(subscription.provider)
        try:
            if chosen is not None and chosen.configured():
                chosen.cancel(subscription, at_period_end=False)
        except billing.BillingError:
            logger.warning(
                "Cancelling the subscription of deleted user %s with %s failed", user.id, subscription.provider
            )
        subscription.status = "canceled"
    for model in (models.UserAPIKey, models.ApiToken, models.Notification, models.AuthToken, models.OrganizationMember):
        for row in db.scalars(select(model).where(model.user_id == user.id)):
            db.delete(row)
    user.first_name, user.last_name = "Deleted", "user"
    user.email = f"deleted-{user.id}@deleted.invalid"
    user.institutional_email = user.orcid_id = user.position_role = user.reason_for_joining = user.institution = None
    user.hashed_password = hash_password(os.urandom(16).hex())
    user.is_active, user.is_platform_admin, user.email_notifications = False, False, False
    user.mfa_enabled, user.mfa_secret_encrypted, user.mfa_recovery_hashes = False, None, []
    user.deleted_at = models.utcnow()
    return True


def run_due_deletions(db: Session) -> int:
    """Carry out deletions whose grace period has passed. Called hourly by the worker."""
    cutoff = models.utcnow() - deletion_grace()
    due = db.scalars(
        select(models.User).where(
            models.User.deletion_requested_at.is_not(None),
            models.User.deletion_requested_at <= cutoff,
            models.User.deleted_at.is_(None),
        )
    ).all()
    done = 0
    for user in due:
        if anonymize_user(db, user):
            done += 1
        else:
            logger.warning("Deletion of user %s is waiting: they still own a shared project", user.id)
        db.commit()
    return done


# --- Legal documents ---


def _legal_meta(path: Path) -> dict[str, str]:
    text = path.read_text()
    title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
    version = next((line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("Version:")), "")
    return {"slug": path.stem, "title": title, "version": version}


@router.get("/api/legal")
def list_legal_documents():
    if not LEGAL_DIR.is_dir():
        return []
    return [_legal_meta(path) for path in sorted(LEGAL_DIR.glob("*.md"))]


@router.get("/api/legal/{slug}")
def get_legal_document(slug: str):
    path = LEGAL_DIR / f"{slug}.md"
    if not LEGAL_SLUG.fullmatch(slug) or not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    content = path.read_text()
    if slug == "subprocessors":
        rows = "\n".join(
            f"| {spec.label} | AI processing of the text you send to the model a project uses | {spec.headquarters} |"
            for spec in PROVIDERS.values()
        )
        content += (
            "\n\n## AI model providers\n\nOnly the provider of the model a project pins receives that project's text, "
            "and only when an AI task runs. Calls made with your own API key are governed by your agreement with "
            "that provider.\n\n| Provider | Purpose | Location |\n|---|---|---|\n" + rows + "\n"
        )
    return {**_legal_meta(path), "content": content}
