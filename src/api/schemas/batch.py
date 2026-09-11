"""Request/response schemas for the V2 batch endpoint (documentation only).

Per the project's OpenAPI conventions (see CLAUDE.md), these are attached via
``responses={200: {"model": ...}}`` for Swagger only -- never via
``response_model=``, which would filter the hand-built dict actually returned
by the endpoint and silently drop fields from production traffic.
"""
from __future__ import annotations

from typing import Any, List, Optional, Union

from pydantic import BaseModel, Field


class BatchFeatureRequest(BaseModel):
    """One requested feature within a batch.

    Only the fields the feature's ``kind`` needs are read (see
    ``registry.KIND_*``): a singleton feature needs none of these, a
    per-driver feature needs ``driver``, a per-pair feature needs ``driver1``
    / ``driver2``, and a per-driver-lap feature needs ``driver`` + ``lap``.
    """

    key: str = Field(
        ...,
        description="Feature key from the V2 registry catalog (registry.FEATURE_CATALOG / "
                    "GET /api/admin/plots/catalog).",
    )
    driver: Optional[str] = Field(
        None, description="Driver TLA, for per-driver features (e.g. VER)."
    )
    driver1: Optional[str] = Field(
        None, description="First driver TLA, for per-pair features."
    )
    driver2: Optional[str] = Field(
        None, description="Second driver TLA, for per-pair features."
    )
    lap: Optional[int] = Field(
        None, ge=1, description="Lap number, for per-driver-lap features."
    )


class BatchRequestBody(BaseModel):
    """Request body for ``POST /api/v2/batch``."""

    year: int = Field(2025, ge=2018, le=2030, description="Season year.")
    gp: Union[int, str] = Field(1, description="Round number, Event Key, or Official Name.")
    session: str = Field("Q", description="Session identifier, e.g. FP1, Q, SQ, S, R.")
    features: List[BatchFeatureRequest] = Field(
        default_factory=list,
        description="Features to fetch for this session. The endpoint caps how many "
                    "may be requested at once and returns 400 past that cap.",
    )


class BatchFeatureResult(BaseModel):
    """One feature's outcome. ``data`` is set on success, ``error`` on failure --
    a single failing feature does not fail the whole batch."""

    key: str = Field(..., description="Echoes the requested feature key.")
    status: str = Field(..., description="'ok' or 'error'.")
    data: Optional[Any] = Field(None, description="The feature's JSON payload, when status is 'ok'.")
    error: Optional[str] = Field(None, description="Failure detail, when status is 'error'.")


class BatchResponse(BaseModel):
    """Response body for ``POST /api/v2/batch``."""

    year: int
    gp: Union[int, str]
    session: str = Field(..., description="Normalized session abbreviation (e.g. 'Q', 'R').")
    results: List[BatchFeatureResult]
