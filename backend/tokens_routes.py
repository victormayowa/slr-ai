"""Personal access tokens for the public API. A token is shown once; only its SHA-256 hash is stored."""

import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from auth_routes import TOKEN_PREFIX, get_current_user, hash_api_token
from database import get_db

router = APIRouter(prefix="/api/me/tokens", tags=["api tokens"])
MAX_TOKENS = 20


def token_out(token: models.ApiToken) -> dict:
    return {
        "id": token.id,
        "name": token.name,
        "prefix": token.prefix,
        "scopes": token.scopes,
        "project_ids": token.project_ids,
        "expires_at": token.expires_at,
        "last_used_at": token.last_used_at,
        "revoked_at": token.revoked_at,
        "created_at": token.created_at,
    }


@router.get("")
def list_tokens(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(models.ApiToken).where(models.ApiToken.user_id == user.id).order_by(models.ApiToken.id.desc())
    )
    return [token_out(t) for t in rows]


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Literal["read", "write"]] = Field(min_length=1, max_length=2)
    project_ids: list[int] = Field(default_factory=list, max_length=100)
    expires_in_days: int | None = Field(None, ge=1, le=365)


@router.post("", status_code=201)
def create_token(body: TokenCreate, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    active = db.scalars(
        select(models.ApiToken).where(models.ApiToken.user_id == user.id, models.ApiToken.revoked_at.is_(None))
    ).all()
    if len(active) >= MAX_TOKENS:
        raise HTTPException(
            status_code=409, detail=f"Revoke an unused token first (at most {MAX_TOKENS} active tokens)"
        )
    memberships = set(
        db.scalars(select(models.ProjectMember.project_id).where(models.ProjectMember.user_id == user.id))
    )
    unknown = [pid for pid in body.project_ids if pid not in memberships]
    if unknown:
        raise HTTPException(status_code=422, detail="You can only limit a token to projects you belong to")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = models.ApiToken(
        user_id=user.id,
        name=body.name.strip(),
        prefix=raw[:12],
        token_hash=hash_api_token(raw),
        scopes=sorted(set(body.scopes)),
        project_ids=sorted(set(body.project_ids)),
        expires_at=models.utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    db.add(token)
    db.commit()
    return {**token_out(token), "token": raw}


@router.delete("/{token_id}", status_code=204)
def revoke_token(token_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    token = db.get(models.ApiToken, token_id)
    if token is None or token.user_id != user.id:
        raise HTTPException(status_code=404, detail="Token not found")
    if token.revoked_at is None:
        token.revoked_at = models.utcnow()
        db.commit()
    return Response(status_code=204)
