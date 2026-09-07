"""Documentation-only response schemas for the V2 discovery endpoints.

Attached via ``responses={200: {"model": ...}}`` in ``discovery_v2.py`` --
**never** via ``response_model=`` -- so they document the shape without
filtering the live payload. See ``src/services/analysis/v2/registry.py``
(``FeatureEntry.as_dict()``) and ``src/workers/plot_inventory.py``
(``compute_inventory``) for the data these mirror.
"""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class FeatureCatalogEntry(BaseModel):
    """One selectable V2 feature, exactly as produced by ``FeatureEntry.as_dict()``."""

    key: str = Field(description="Stable selection slug (e.g. a data_type, or a parameterized feature name).")
    label: str = Field(description="Human-readable name.")
    kind: str = Field(
        description="One of: singleton, per_driver, per_pair, per_driver_lap, season, career."
    )
    group: str = Field(description="UI section heading, e.g. 'Field-wide' or 'Per driver'.")
    applies_to: List[str] = Field(
        description="Session abbreviations this feature applies to. Empty means every session type."
    )
    cost: str = Field(description="Relative generation cost: light | heavy | extreme.")
    session_scoped: bool = Field(
        description="False for season/career-scope features, which are not tied to one session."
    )


class FeatureGroupSummary(BaseModel):
    """Count of catalog entries in one ``group``, for a quick overview."""

    group: str
    count: int


class FeatureCatalogResponse(BaseModel):
    """Response for ``GET /api/v2/features``."""

    features: List[FeatureCatalogEntry]
    count: int = Field(description="Number of entries in `features` after any filtering.")
    groups: List[FeatureGroupSummary] = Field(
        description="Per-group counts of the (possibly filtered) `features` list, sorted by group name."
    )


class SessionFeatureAvailability(BaseModel):
    """Whether one singleton feature's data is already stored for a session."""

    data_type: str = Field(description="The MongoDB data_type key this feature is stored under.")
    label: str
    available: bool


class SessionAvailabilityResponse(BaseModel):
    """Response for ``GET /api/v2/sessions/{year}/{gp}/{session}/availability``."""

    year: int
    gp: Any = Field(description="The resolved event name.")
    round_nr: Optional[int] = None
    session: str = Field(description="Normalized session abbreviation, e.g. Q, R, FP1.")
    available: List[str] = Field(description="Singleton data_type keys already stored for this session.")
    missing: List[str] = Field(description="Singleton data_type keys not yet generated for this session.")
    features: List[SessionFeatureAvailability] = Field(
        description="Every expected singleton feature for this session type, with its availability."
    )
    drivers: Optional[List[str]] = Field(
        default=None,
        description=(
            "Participating driver TLAs, only when `include_drivers=true` was requested. "
            "`null` otherwise."
        ),
    )
