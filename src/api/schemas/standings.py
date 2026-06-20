"""Response schemas for the V2 championship standings endpoints."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class DriverStandingItem(BaseModel):
    position: int
    points: float
    wins: int
    driver_code: str
    driver_name: str
    team: str
    nationality: Optional[str] = None


class ConstructorStandingItem(BaseModel):
    position: int
    points: float
    wins: int
    team: str
    nationality: Optional[str] = None


class DriversStandingsResponse(BaseModel):
    year: int
    round: Optional[int] = Field(default=None, description="Round number; null for full season")
    source: str = Field(description="Upstream that supplied the data (livetiming or ergast)")
    standings: List[DriverStandingItem]


class ConstructorsStandingsResponse(BaseModel):
    year: int
    round: Optional[int] = Field(default=None, description="Round number; null for full season")
    source: str = Field(description="Upstream that supplied the data (livetiming or ergast)")
    standings: List[ConstructorStandingItem]
