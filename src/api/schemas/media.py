"""Response schemas for the static media endpoints (driver images, team logos)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class DriverImageItem(BaseModel):
    code: str = Field(description="Three-letter driver code, e.g. VER")
    name: Optional[str] = Field(default=None, description="Driver full name")
    team: Optional[str] = None
    number: Optional[int] = None
    image_url: str = Field(description="Path to the PNG under the /assets mount")
    available: bool = Field(description="Whether the image file is present on disk")


class TeamLogoItem(BaseModel):
    name: str
    short_name: str = Field(description="Short team code the logo file is named after, e.g. RBR")
    color: Optional[str] = None
    logo_url: str = Field(description="Path to the PNG under the /assets mount")
    available: bool = Field(description="Whether the logo file is present on disk")


class DriverImagesResponse(BaseModel):
    year: int
    images: List[DriverImageItem]


class TeamLogosResponse(BaseModel):
    year: int
    logos: List[TeamLogoItem]
