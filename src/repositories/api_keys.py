"""User-generated API key persistence.

Keys are stored only as ``sha256(raw_key)`` — the plaintext value is
returned exactly once at creation time and never persisted.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId

from src.repositories.mongo import get_mongo_client

VALID_TIERS = {"standard", "premium"}
RAW_KEY_PREFIX = "t1_"


def _collection():
    db_name = os.getenv("MONGODB_DATABASE", "T1API_DB")
    return get_mongo_client()[db_name]["api_keys"]


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _generate_raw_key() -> str:
    return f"{RAW_KEY_PREFIX}{secrets.token_urlsafe(24)}"


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    doc["owner_id"] = str(doc["owner_id"]) if doc.get("owner_id") else None
    doc.pop("key_hash", None)
    return doc


def create_api_key(owner_id: str, label: str, tier: str = "standard") -> Dict[str, Any]:
    if tier not in VALID_TIERS:
        raise ValueError(f"tier must be one of {sorted(VALID_TIERS)}")
    raw_key = _generate_raw_key()
    doc = {
        "owner_id": ObjectId(owner_id),
        "key_hash": hash_key(raw_key),
        "key_prefix": raw_key[:11],
        "tier": tier,
        "label": label.strip()[:80] or "key",
        "created_at": datetime.now(timezone.utc),
        "last_used_at": None,
        "revoked_at": None,
    }
    result = _collection().insert_one(doc)
    doc["_id"] = result.inserted_id
    serialized = _serialize(doc)
    serialized["raw_key"] = raw_key
    return serialized


def find_active_by_hash(key_hash: str) -> Optional[Dict[str, Any]]:
    doc = _collection().find_one({"key_hash": key_hash, "revoked_at": None})
    return doc


def touch_last_used(key_hash: str) -> None:
    _collection().update_one(
        {"key_hash": key_hash},
        {"$set": {"last_used_at": datetime.now(timezone.utc)}},
    )


def list_for_owner(owner_id: str) -> List[Dict[str, Any]]:
    try:
        oid = ObjectId(owner_id)
    except (InvalidId, TypeError):
        return []
    cursor = _collection().find({"owner_id": oid}).sort("created_at", -1)
    return [_serialize(d) for d in cursor]


def list_all(owner_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {}
    if owner_id:
        try:
            query["owner_id"] = ObjectId(owner_id)
        except (InvalidId, TypeError):
            return []
    cursor = _collection().find(query).sort("created_at", -1).limit(limit)
    return [_serialize(d) for d in cursor]


def revoke(key_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        oid = ObjectId(key_id)
    except (InvalidId, TypeError):
        return None
    query: Dict[str, Any] = {"_id": oid}
    if owner_id:
        try:
            query["owner_id"] = ObjectId(owner_id)
        except (InvalidId, TypeError):
            return None
    result = _collection().find_one_and_update(
        query,
        {"$set": {"revoked_at": datetime.now(timezone.utc)}},
    )
    return _serialize(result) if result else None


def find_by_id(key_id: str) -> Optional[Dict[str, Any]]:
    try:
        oid = ObjectId(key_id)
    except (InvalidId, TypeError):
        return None
    doc = _collection().find_one({"_id": oid})
    return doc
