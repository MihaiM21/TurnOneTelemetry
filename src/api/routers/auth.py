"""Public user-authentication endpoints (signup / login / me)."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from src.auth.dependencies import get_current_user
from src.auth.jwt import create_access_token
from src.auth.passwords import hash_password, verify_password
from src.core.security.rate_limiting import apply_tiered_limit
from src.repositories.users import (
    create_user,
    find_user_by_email,
    find_user_by_id,
)
from src.repositories.api_keys import list_for_owner

router = APIRouter(prefix="/api/auth", tags=["Auth"])


_EMAIL_RE = __import__("re").compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email")
        return v.lower()


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email_lower(cls, v: str) -> str:
        return v.strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@apply_tiered_limit("public")
async def signup(request: Request, body: SignupRequest):
    pwd_hash = await run_in_threadpool(hash_password, body.password)
    user = await run_in_threadpool(create_user, body.email, pwd_hash, False)
    if not user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    token = create_access_token(subject=user["id"], extra={"email": user["email"]})
    return TokenResponse(access_token=token, user_id=user["id"])


@router.post("/login", response_model=TokenResponse)
@apply_tiered_limit("public")
async def login(request: Request, body: LoginRequest):
    user = await run_in_threadpool(find_user_by_email, body.email)
    if not user or not verify_password(body.password, user.get("password_hash", "")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if user.get("disabled"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")
    user_id = str(user["_id"])
    token = create_access_token(subject=user_id, extra={"email": user["email"]})
    return TokenResponse(access_token=token, user_id=user_id)


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    keys = await run_in_threadpool(list_for_owner, user["id"])
    active = sum(1 for k in keys if not k.get("revoked_at"))
    return {
        "id": user["id"],
        "email": user.get("email"),
        "is_admin": bool(user.get("is_admin")),
        "created_at": user.get("created_at"),
        "key_count": len(keys),
        "active_key_count": active,
    }
