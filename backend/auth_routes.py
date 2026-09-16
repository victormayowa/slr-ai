import hashlib
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import emailer
import entitlements
import models
import notifications
from database import get_db
from rate_limiting import rate_limit

load_dotenv()
logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
if len(SECRET_KEY) < 32 or SECRET_KEY == "super_secret_omnireview_key":
    raise RuntimeError(
        "JWT_SECRET_KEY must be a random value of at least 32 characters. "
        'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
    )
ALGORITHM = "HS256"
TOKEN_LIFETIME = timedelta(hours=12)
BCRYPT_MAX_PASSWORD_BYTES = 72
# Lower only in tests; each step halves hashing time.
BCRYPT_ROUNDS = int(os.getenv("BCRYPT_ROUNDS", "12"))
LOGIN_RATE_LIMIT_PER_MINUTE = 20
REGISTER_RATE_LIMIT_PER_HOUR = 20
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
# Personal access tokens for the public API start with this; anything else in an Authorization header is a login token.
TOKEN_PREFIX = "omr_"
# Tokens can't manage tokens or reach the platform admin routes, so a leaked token can't widen its own access.
TOKEN_FORBIDDEN_PATHS = ("/api/me/tokens", "/api/admin")
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# The version of the Terms of Service and Privacy Policy new accounts accept (docs/legal).
TERMS_VERSION = os.getenv("TERMS_VERSION", "2026-09-16")
TOKEN_LIFETIMES = {
    "password_reset": timedelta(hours=1),
    "email_verification": timedelta(days=3),
    "mfa_login": timedelta(minutes=5),
}
VERIFICATION_RESEND_INTERVAL = timedelta(minutes=5)

# Checked when no account matches, so a failed login takes as long whether or not the account exists.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(BCRYPT_ROUNDS))

router = APIRouter(prefix="/api/auth")
bearer_scheme = HTTPBearer(auto_error=False)


def check_password(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(value.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes")
    return value


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("utf-8")


def password_matches(user: models.User, password: str) -> bool:
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_PASSWORD_BYTES:
        return False
    return bcrypt.checkpw(encoded, user.hashed_password.encode("utf-8"))


class UserCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=8)
    institutional_email: str | None = Field(None, max_length=254, pattern=EMAIL_PATTERN)
    orcid_id: str | None = Field(None, max_length=50)
    position_role: str = Field(min_length=1, max_length=100)
    reason_for_joining: str = Field(min_length=1, max_length=1000)
    institution: str = Field(min_length=1, max_length=200)
    # The Terms of Service and Privacy Policy must be accepted to create an account.
    accept_terms: bool = False

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        return check_password(value)

    @field_validator("accept_terms")
    @classmethod
    def terms_accepted(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Accept the Terms of Service and Privacy Policy to create an account")
        return value


class UserLogin(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1000)


def password_version(user: models.User) -> int:
    """Changes whenever the password does, so sessions issued before a change stop working."""
    return int(user.password_changed_at.timestamp() * 1_000_000) if user.password_changed_at else 0


def create_access_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.now(UTC) + TOKEN_LIFETIME})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def session_response(user: models.User) -> dict:
    token = create_access_token(data={"sub": user.email, "name": user.full_name, "pwv": password_version(user)})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user.id, "email": user.email, "name": user.full_name},
    }


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def issue_auth_token(db: Session, user: models.User, purpose: str) -> str:
    """A single-use token for a link or a sign-in step. Earlier unused tokens for the same purpose stop working."""
    now = models.utcnow()
    for earlier in db.scalars(
        select(models.AuthToken).where(
            models.AuthToken.user_id == user.id,
            models.AuthToken.purpose == purpose,
            models.AuthToken.used_at.is_(None),
        )
    ):
        earlier.used_at = now
    raw = secrets.token_urlsafe(32)
    db.add(
        models.AuthToken(
            user_id=user.id, purpose=purpose, token_hash=hash_secret(raw), expires_at=now + TOKEN_LIFETIMES[purpose]
        )
    )
    return raw


def consume_auth_token(db: Session, raw: str, purpose: str) -> models.User:
    """The user a valid token belongs to, marking the token used. Raises 400 for unknown, used, or expired tokens."""
    token = db.scalar(
        select(models.AuthToken).where(
            models.AuthToken.token_hash == hash_secret(raw.strip()), models.AuthToken.purpose == purpose
        )
    )
    if token is None or token.used_at is not None:
        raise HTTPException(status_code=400, detail="That link isn't valid any more. Request a new one.")
    if token.expires_at <= models.utcnow():
        raise HTTPException(status_code=400, detail="That link has expired. Request a new one.")
    if not token.user.is_active or token.user.deleted_at is not None:
        raise HTTPException(status_code=400, detail="That account can't be used")
    token.used_at = models.utcnow()
    return token.user


def send_verification_email(db: Session, user: models.User) -> None:
    """Email a link that confirms the address. Sending failures are logged, never shown as a failed request."""
    raw = issue_auth_token(db, user, "email_verification")
    db.commit()
    link = notifications.app_url(f"/verify-email?token={raw}")
    try:
        emailer.send(
            user.email,
            "Confirm your email address for OmniReview",
            f"Hello {user.first_name},\n\nConfirm your email address by opening this link (valid for 3 days):\n\n"
            f"{link}\n\nIf you didn't create an OmniReview account, you can ignore this email.",
        )
    except emailer.EmailError:
        logger.warning("The verification email for user %s couldn't be sent", user.id)


def verification_required() -> bool:
    return os.getenv("REQUIRE_EMAIL_VERIFICATION", "false").strip().lower() == "true"


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _user_from_api_token(request: Request, raw_token: str, db: Session) -> models.User:
    """Authenticate a public-API token, checking its scopes and the projects it's limited to."""
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="That API token isn't valid",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = db.scalar(select(models.ApiToken).where(models.ApiToken.token_hash == hash_api_token(raw_token)))
    if token is None or token.revoked_at is not None:
        raise invalid
    if token.expires_at is not None and token.expires_at <= models.utcnow():
        raise HTTPException(status_code=401, detail="That API token has expired")
    if not token.user.is_active or token.user.deleted_at is not None:
        raise invalid
    path = request.url.path
    if any(path.startswith(blocked) for blocked in TOKEN_FORBIDDEN_PATHS):
        raise HTTPException(status_code=403, detail="API tokens can't be used on this route; sign in instead")
    needed = "read" if request.method in READ_METHODS else "write"
    if needed not in token.scopes:
        raise HTTPException(status_code=403, detail=f"This token doesn't have the {needed} scope")
    project_id = request.path_params.get("project_id")
    if token.project_ids and project_id is not None and int(project_id) not in token.project_ids:
        raise HTTPException(status_code=403, detail="This token isn't allowed to use that project")
    entitlements.require_user_feature(db, token.user, "api_access")
    token.last_used_at = models.utcnow()
    db.commit()
    return token.user


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Please sign in again",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    if credentials.credentials.startswith(TOKEN_PREFIX):
        return _user_from_api_token(request, credentials.credentials, db)
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise unauthorized from None
    user = db.scalar(select(models.User).where(models.User.email == payload.get("sub")))
    if user is None or not user.is_active or user.deleted_at is not None:
        raise unauthorized
    if payload.get("pwv", 0) != password_version(user):
        raise unauthorized
    return user


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    """Platform administrators manage the model catalog, plans, and benchmarks. Grant with scripts/make_admin.py."""
    if not user.is_platform_admin:
        raise HTTPException(status_code=403, detail="This is only available to platform administrators")
    return user


@router.post(
    "/register",
    dependencies=[Depends(rate_limit("register", limit=REGISTER_RATE_LIMIT_PER_HOUR, window_seconds=3600))],
)
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    already_registered = HTTPException(status_code=400, detail="An account with this email or ORCID iD already exists")

    identifier_matches = [models.User.email == user.email]
    if user.institutional_email:
        identifier_matches.append(models.User.institutional_email == user.institutional_email)
    if user.orcid_id:
        identifier_matches.append(models.User.orcid_id == user.orcid_id)
    if db.scalar(select(models.User).where(or_(*identifier_matches))):
        raise already_registered

    new_user = models.User(
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        institutional_email=user.institutional_email,
        orcid_id=user.orcid_id,
        hashed_password=hash_password(user.password),
        position_role=user.position_role,
        reason_for_joining=user.reason_for_joining,
        institution=user.institution,
        terms_accepted_at=models.utcnow(),
        terms_version=TERMS_VERSION,
    )
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise already_registered from None

    send_verification_email(db, new_user)
    return {"message": "User created successfully", "verification_required": verification_required()}


@router.post(
    "/login",
    dependencies=[Depends(rate_limit("login", limit=LOGIN_RATE_LIMIT_PER_MINUTE, window_seconds=60))],
)
def login_user(user: UserLogin, db: Session = Depends(get_db)):
    invalid_credentials = HTTPException(status_code=401, detail="Invalid credentials")
    password = user.password.encode("utf-8")
    if len(password) > BCRYPT_MAX_PASSWORD_BYTES:
        raise invalid_credentials

    db_user = db.scalar(
        select(models.User).where(
            or_(
                models.User.email == user.identifier,
                models.User.institutional_email == user.identifier,
                models.User.orcid_id == user.identifier,
            )
        )
    )

    if db_user is None:
        bcrypt.checkpw(password, _DUMMY_PASSWORD_HASH)
        raise invalid_credentials
    if (
        not bcrypt.checkpw(password, db_user.hashed_password.encode("utf-8"))
        or not db_user.is_active
        or db_user.deleted_at is not None
    ):
        raise invalid_credentials

    if verification_required() and db_user.email_verified_at is None:
        latest = db.scalar(
            select(models.AuthToken.created_at)
            .where(models.AuthToken.user_id == db_user.id, models.AuthToken.purpose == "email_verification")
            .order_by(models.AuthToken.id.desc())
            .limit(1)
        )
        if latest is None or latest < models.utcnow() - VERIFICATION_RESEND_INTERVAL:
            send_verification_email(db, db_user)
        raise HTTPException(
            status_code=403,
            detail=f"Confirm your email address first. We've sent a link to {db_user.email}.",
        )

    if db_user.mfa_enabled:
        mfa_token = issue_auth_token(db, db_user, "mfa_login")
        db.commit()
        return {"mfa_required": True, "mfa_token": mfa_token}

    return session_response(db_user)


@router.get("/me")
def read_current_user(user: models.User = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.full_name,
        "is_platform_admin": user.is_platform_admin,
        "email_verified": user.email_verified_at is not None,
        "mfa_enabled": user.mfa_enabled,
        "terms_version": user.terms_version,
        "current_terms_version": TERMS_VERSION,
        "organizations": [
            {"id": membership.organization.id, "name": membership.organization.name, "role": membership.role}
            for membership in user.organization_memberships
        ],
    }
