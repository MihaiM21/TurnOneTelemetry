"""Public user-authentication endpoints (signup / login / me)."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from src.api.schemas.accounts import MeResponse
from src.api.schemas.common import COMMON_ERROR_RESPONSES, ErrorEnvelope
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


@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    operation_id="auth_signup",
    description=(
        "Create a new user account and return a JWT bearer token. "
        "Unlike the rest of this API, authenticated endpoints under `/api/auth` and `/api/keys` / "
        "`/api/me` use an `Authorization: Bearer <token>` header, not the `X-API-Key` header — "
        "send the returned `access_token` that way on subsequent requests."
    ),
    responses={
        409: {"model": ErrorEnvelope, "description": "Email already registered."},
        429: COMMON_ERROR_RESPONSES[429],
        500: COMMON_ERROR_RESPONSES[500],
    },
)
@apply_tiered_limit("public")
async def signup(request: Request, body: SignupRequest):
    pwd_hash = await run_in_threadpool(hash_password, body.password)
    user = await run_in_threadpool(create_user, body.email, pwd_hash, False)
    if not user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    token = create_access_token(subject=user["id"], extra={"email": user["email"]})
    return TokenResponse(access_token=token, user_id=user["id"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in and obtain a bearer token",
    operation_id="auth_login",
    description=(
        "Exchange email/password for a JWT bearer token. This and every other `/api/auth` "
        "endpoint use `Authorization: Bearer <token>` — not the `X-API-Key` header used "
        "elsewhere in this API."
    ),
    responses={
        401: {"model": ErrorEnvelope, "description": "Invalid email or password."},
        403: {"model": ErrorEnvelope, "description": "Account is disabled."},
        429: COMMON_ERROR_RESPONSES[429],
        500: COMMON_ERROR_RESPONSES[500],
    },
)
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


@router.get(
    "/me",
    summary="Get the current user's profile",
    operation_id="auth_get_me",
    description=(
        "Return the authenticated user's profile and API key counts. Requires "
        "`Authorization: Bearer <token>` — not the `X-API-Key` header used elsewhere in this API."
    ),
    responses={
        200: {"model": MeResponse, "description": "Current user profile."},
        401: {
            "model": ErrorEnvelope,
            "description": "Bearer token missing, invalid, or expired; or the user is disabled.",
        },
    },
)
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
