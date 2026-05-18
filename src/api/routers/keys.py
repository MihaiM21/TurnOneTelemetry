"""User-owned API key management (JWT-protected)."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from src.auth.dependencies import get_current_user
from src.core.security.api_keys import invalidate_key_cache
from src.core.security.rate_limiting import apply_tiered_limit
from src.repositories.api_keys import (
    create_api_key,
    find_by_id,
    list_for_owner,
    revoke,
)

router = APIRouter(prefix="/api/keys", tags=["Auth"])


class CreateKeyRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)


@router.get("")
async def list_my_keys(user: dict = Depends(get_current_user)):
    keys = await run_in_threadpool(list_for_owner, user["id"])
    return {"keys": keys}


@router.post("", status_code=status.HTTP_201_CREATED)
@apply_tiered_limit("public")
async def create_my_key(
    request: Request,
    body: CreateKeyRequest,
    user: dict = Depends(get_current_user),
):
    """Mint a new API key for the current user.

    The ``raw_key`` is returned **once**; persist it client-side immediately
    because we only store its hash.
    """
    # Non-admin users always get standard tier — admins promote keys via
    # the admin endpoints.
    key = await run_in_threadpool(create_api_key, user["id"], body.label, "standard")
    return {
        "id": key["id"],
        "raw_key": key["raw_key"],
        "key_prefix": key["key_prefix"],
        "tier": key["tier"],
        "label": key["label"],
        "created_at": key["created_at"],
        "warning": "Store this key now — it will not be shown again.",
    }


@router.delete("/{key_id}", status_code=status.HTTP_200_OK)
async def revoke_my_key(key_id: str, user: dict = Depends(get_current_user)):
    existing = await run_in_threadpool(find_by_id, key_id)
    if not existing or str(existing.get("owner_id")) != user["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    revoked = await run_in_threadpool(revoke, key_id, user["id"])
    if revoked and existing.get("key_hash"):
        invalidate_key_cache(existing["key_hash"])
    return {"id": key_id, "revoked": True}
