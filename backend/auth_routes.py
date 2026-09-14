import os
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from database import get_db
from rate_limiting import rate_limit

load_dotenv()

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

# Checked when no account matches, so a failed login takes as long whether or not the account exists.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(BCRYPT_ROUNDS))

router = APIRouter(prefix="/api/auth")
bearer_scheme = HTTPBearer(auto_error=False)


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

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
            raise ValueError(f"Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes")
        return value


class UserLogin(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1000)


def create_access_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.now(UTC) + TOKEN_LIFETIME})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
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
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise unauthorized from None
    user = db.scalar(select(models.User).where(models.User.email == payload.get("sub")))
    if user is None or not user.is_active:
        raise unauthorized
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

    hashed_pw = bcrypt.hashpw(user.password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("utf-8")
    new_user = models.User(
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        institutional_email=user.institutional_email,
        orcid_id=user.orcid_id,
        hashed_password=hashed_pw,
        position_role=user.position_role,
        reason_for_joining=user.reason_for_joining,
        institution=user.institution,
    )
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise already_registered from None

    return {"message": "User created successfully"}


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
    if not bcrypt.checkpw(password, db_user.hashed_password.encode("utf-8")) or not db_user.is_active:
        raise invalid_credentials

    access_token = create_access_token(data={"sub": db_user.email, "name": db_user.full_name})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {"id": db_user.id, "email": db_user.email, "name": db_user.full_name},
    }


@router.get("/me")
def read_current_user(user: models.User = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.full_name,
        "organizations": [
            {"id": membership.organization.id, "name": membership.organization.name, "role": membership.role}
            for membership in user.organization_memberships
        ],
    }
