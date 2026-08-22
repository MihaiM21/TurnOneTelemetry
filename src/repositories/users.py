"""User CRUD against the ``users`` MongoDB collection."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from src.repositories.mongo import get_mongo_client


def _collection():
    db_name = os.getenv("MONGODB_DATABASE", "T1API_DB")
    return get_mongo_client()[db_name]["users"]


def _serialize(user: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not user:
        return None
    user = dict(user)
    user["id"] = str(user.pop("_id"))
    user.pop("password_hash", None)
    return user


def create_user(email: str, password_hash: str, is_admin: bool = False) -> Optional[Dict[str, Any]]:
    doc = {
        "email": email.lower().strip(),
        "password_hash": password_hash,
        "is_admin": bool(is_admin),
        "disabled": False,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        result = _collection().insert_one(doc)
    except DuplicateKeyError:
        return None
    doc["_id"] = result.inserted_id
    return _serialize(doc)


def find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    return _collection().find_one({"email": email.lower().strip()})


def find_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    try:
        oid = ObjectId(user_id)
    except (InvalidId, TypeError):
        return None
    return _serialize(_collection().find_one({"_id": oid}))


def set_admin(user_id: str, is_admin: bool = True) -> bool:
    """Grant or revoke admin rights. Returns True when a user was updated.

    The admin UI used to reach past this module straight into the collection
    with an inline ObjectId import and a bare ``except: pass``, so a failed
    promotion looked identical to a successful one.
    """
    try:
        oid = ObjectId(user_id)
    except (InvalidId, TypeError):
        return False
    result = _collection().update_one({"_id": oid}, {"$set": {"is_admin": bool(is_admin)}})
    return result.matched_count > 0


def list_users(limit: int = 100, skip: int = 0) -> List[Dict[str, Any]]:
    cursor = _collection().find({}, {"password_hash": 0}).sort("created_at", -1).skip(skip).limit(limit)
    out: List[Dict[str, Any]] = []
    for u in cursor:
        u["id"] = str(u.pop("_id"))
        out.append(u)
    return out
