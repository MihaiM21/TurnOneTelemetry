"""Documentation-only response models for the account/auth surface.

These models back ``responses=`` entries for Swagger only — they are never
passed as a bare ``response_model=`` (see ``src/api/schemas/common.py`` for
why). The one exception is ``TokenResponse`` in ``auth.py`` itself, which
already existed as a real ``response_model=`` before this pass and is left
untouched.

Shapes here are traced from what the handlers actually build:
``src/repositories/api_keys.py`` (``_serialize``, ``create_api_key``),
``src/repositories/api_key_usage.py`` (``summary_for_key``,
``dashboard_for_key``, ``peak_hours_for_key``), and the inline dicts in
``src/api/routers/auth.py`` / ``src/api/routers/keys.py``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MeResponse(BaseModel):
    """Current-user profile, built inline in ``GET /api/auth/me``."""

    id: str
    email: Optional[str] = None
    is_admin: bool
    created_at: Optional[Any] = Field(default=None, description="Stored as a BSON datetime; serialized by FastAPI.")
    key_count: int = Field(description="Total API keys ever created by this user, including revoked ones.")
    active_key_count: int = Field(description="API keys that are not revoked.")


class ApiKeySummary(BaseModel):
    """One key as returned by ``src.repositories.api_keys._serialize`` — never includes the raw key or hash."""

    id: str
    owner_id: Optional[str] = None
    key_prefix: str = Field(description="First 11 characters of the raw key, e.g. `t1_abcdEFGh`.")
    tier: str = Field(description="`standard` or `premium`.")
    label: str
    created_at: Optional[Any] = None
    last_used_at: Optional[Any] = None
    revoked_at: Optional[Any] = Field(default=None, description="Null while the key is active.")


class ListMyKeysResponse(BaseModel):
    """``GET /api/keys`` — every key (active and revoked) owned by the caller."""

    keys: List[ApiKeySummary]


class CreateKeyResponse(BaseModel):
    """``POST /api/keys``.

    ``raw_key`` is returned exactly once, at creation time — only its SHA-256
    hash (``key_hash``) is persisted (see ``src/repositories/api_keys.py``),
    so a key that isn't captured from this response cannot be recovered and
    must be revoked and re-issued. This endpoint always mints a
    **standard**-tier key; premium tier can only be granted through the
    admin endpoints, never by a user (admin or not) through self-service.
    """

    id: str
    raw_key: str = Field(description="The plaintext API key. Shown only in this response — store it now.")
    key_prefix: str
    tier: str
    label: str
    created_at: Optional[Any] = None
    warning: str


class UsageBucket(BaseModel):
    """One hourly bucket from ``api_key_usage`` (bucket key is `yyyymmddhh`)."""

    bucket: Optional[str] = None
    count: int = 0
    errors: int = 0
    avg_duration_ms: float = 0.0


class KeyUsageResponse(BaseModel):
    """``GET /api/keys/{key_id}/usage`` — hourly stats over a rolling window."""

    key_id: str
    key_prefix: Optional[str] = None
    label: Optional[str] = None
    window_hours: int
    total_requests: int
    total_errors: int
    error_rate_percent: float
    avg_duration_ms: float
    buckets: List[UsageBucket]


class WindowStats(BaseModel):
    """Aggregate request/error counts for a fixed calendar window."""

    requests: int = 0
    errors: int = 0
    avg_ms: float = 0.0
    error_rate_percent: float = 0.0


class KeyInfo(BaseModel):
    id: str
    prefix: Optional[str] = None
    label: Optional[str] = None
    tier: str


class QuotaInfo(BaseModel):
    """Built by ``keys.py:_build_quota`` from ``limits_for_tier``."""

    monthly_limit: int
    used_this_month: int
    remaining: int
    percent_used: float
    resets_at: str


class RateLimitInfo(BaseModel):
    per_minute: int
    per_hour: int


class HourCount(BaseModel):
    hour: int = Field(ge=0, le=23)
    count: int = 0
    errors: int = 0


class KeyDashboardUsage(BaseModel):
    today: WindowStats
    last_7_days: WindowStats
    current_month: WindowStats


class KeyDashboardResponse(BaseModel):
    """``GET /api/keys/{key_id}/dashboard`` — full per-key usage dashboard."""

    key: KeyInfo
    quota: QuotaInfo
    rate_limit: RateLimitInfo
    usage: KeyDashboardUsage
    peak_hour_utc: HourCount
    last_24h: List[UsageBucket]


class KeyPeakHoursResponse(BaseModel):
    """``GET /api/keys/{key_id}/peak-hours`` — 24-entry hour-of-day (UTC) distribution."""

    key_id: str
    days: int
    peak_hour_utc: HourCount
    by_hour: List[HourCount]


class PerKeyUsageSummary(BaseModel):
    """One row of the ``keys`` list in ``GET /api/me/usage``."""

    id: str
    prefix: Optional[str] = None
    label: Optional[str] = None
    tier: Optional[str] = None
    today: int
    current_month: int


class MyUsageTotals(BaseModel):
    today: Dict[str, int]
    last_7_days: Dict[str, int]
    current_month: Dict[str, int]


class MyUsageResponse(BaseModel):
    """``GET /api/me/usage`` — aggregate dashboard across all of the caller's active keys."""

    user_id: str
    key_count: int
    totals: MyUsageTotals
    quota: QuotaInfo
    keys: List[PerKeyUsageSummary]


class RevokeKeyResponse(BaseModel):
    """``DELETE /api/keys/{key_id}``."""

    id: str
    revoked: bool
