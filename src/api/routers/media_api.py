"""Static media endpoints: driver images and team logos.

Like ``drivers_api``/``teams_api`` this router is **unauthenticated** — it serves
curated repo assets, not live session data. The single-item endpoints 302-redirect
to the file under the ``/assets`` mount (see ``src/api/app.py``) so a CDN can cache
the bytes; the list endpoints return JSON metadata with the same URLs plus an
``available`` flag telling clients which files have actually been uploaded.

Asset layout (files to add live here):
  * driver images -> ``assets/drivers/{CODE}.png``      (e.g. assets/drivers/VER.png)
  * team logos    -> ``assets/logos/{SHORT_NAME}.png``  (e.g. assets/logos/RBR.png)
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Path as PathParam, Query, Request
from fastapi.responses import RedirectResponse

from src.api.schemas.common import COMMON_ERROR_RESPONSES, ErrorEnvelope
from src.api.schemas.media import (
    DriverImageItem,
    DriverImagesResponse,
    TeamLogoItem,
    TeamLogosResponse,
)
from src.core.logging import get_logger
from src.core.security.rate_limiting import apply_tiered_limit
from src.ingestion.reference import get_season_drivers, get_season_teams

logger = get_logger(__name__)

router = APIRouter(prefix="/api/static/media")

#: No ``verify_api_key`` dependency here, so 401/403 cannot occur.
_UNAUTH_COMMON_RESPONSES = {429: COMMON_ERROR_RESPONSES[429], 500: COMMON_ERROR_RESPONSES[500]}

#: Repo-root ``assets/`` directory (this file is src/api/routers/media_api.py).
_ASSETS_DIR = Path(__file__).resolve().parents[3] / "assets"
_DRIVERS_DIR = _ASSETS_DIR / "drivers"
_LOGOS_DIR = _ASSETS_DIR / "logos"

DRIVER_IMAGE_URL = "/assets/drivers/{code}.png"
TEAM_LOGO_URL = "/assets/logos/{short_name}.png"


def _driver_image_available(code: str) -> bool:
    return (_DRIVERS_DIR / f"{code}.png").is_file()


def _team_logo_available(short_name: str) -> bool:
    return (_LOGOS_DIR / f"{short_name}.png").is_file()


@router.get(
    "/drivers",
    tags=["Static"],
    summary="List driver image URLs for a season",
    operation_id="static_media_driver_images",
    responses={
        **_UNAUTH_COMMON_RESPONSES,
        200: {"model": DriverImagesResponse, "description": "One entry per driver on the grid."},
        400: {"model": ErrorEnvelope, "description": "Season year not covered by the static reference data."},
    },
)
@apply_tiered_limit("data")
async def list_driver_images(
    request: Request,
    year: int = Query(2026, ge=2025, le=2026, description="Season year (2025-2026)"),
):
    """List every driver for ``year`` with the URL of their portrait.

    Unauthenticated. ``available`` is ``false`` when no PNG has been uploaded for that
    driver yet; the ``image_url`` is still returned so clients can retry later.
    """
    try:
        drivers = get_season_drivers(year)
    except ValueError as e:
        logger.error("Error listing driver images for %s: %s", year, e)
        raise HTTPException(status_code=400, detail=str(e))

    items = []
    for d in drivers:
        code = (d.get("code") or "").upper()
        items.append(DriverImageItem(
            code=code,
            name=d.get("full_name") or d.get("name"),
            team=d.get("team"),
            number=d.get("number"),
            image_url=DRIVER_IMAGE_URL.format(code=code),
            available=_driver_image_available(code),
        ))
    return {"year": year, "images": items}


@router.get(
    "/drivers/{code}",
    tags=["Static"],
    summary="Redirect to a driver image",
    operation_id="static_media_driver_image",
    responses={
        **_UNAUTH_COMMON_RESPONSES,
        307: {"description": "Redirect to the PNG under /assets/drivers/."},
        404: {"model": ErrorEnvelope, "description": "No image uploaded for that driver code."},
    },
)
@apply_tiered_limit("data")
async def get_driver_image(
    request: Request,
    code: str = PathParam(..., description="Three-letter driver code, e.g. VER"),
):
    """302-redirect to ``/assets/drivers/{CODE}.png``. Unauthenticated."""
    code = code.upper()
    if not _driver_image_available(code):
        raise HTTPException(status_code=404, detail=f"No image for driver code {code!r}")
    return RedirectResponse(url=DRIVER_IMAGE_URL.format(code=code), status_code=302)


@router.get(
    "/teams",
    tags=["Static"],
    summary="List team logo URLs for a season",
    operation_id="static_media_team_logos",
    responses={
        **_UNAUTH_COMMON_RESPONSES,
        200: {"model": TeamLogosResponse, "description": "One entry per constructor."},
        400: {"model": ErrorEnvelope, "description": "Season year not covered by the static reference data."},
    },
)
@apply_tiered_limit("data")
async def list_team_logos(
    request: Request,
    year: int = Query(2026, ge=2025, le=2026, description="Season year (2025-2026)"),
):
    """List every constructor for ``year`` with the URL of its logo. Unauthenticated."""
    try:
        teams = get_season_teams(year)
    except ValueError as e:
        logger.error("Error listing team logos for %s: %s", year, e)
        raise HTTPException(status_code=400, detail=str(e))

    items = []
    for t in teams:
        short_name = (t.get("short_name") or "").upper()
        items.append(TeamLogoItem(
            name=t.get("name") or "",
            short_name=short_name,
            color=t.get("color"),
            logo_url=TEAM_LOGO_URL.format(short_name=short_name),
            available=_team_logo_available(short_name),
        ))
    return {"year": year, "logos": items}


@router.get(
    "/teams/{short_name}",
    tags=["Static"],
    summary="Redirect to a team logo",
    operation_id="static_media_team_logo",
    responses={
        **_UNAUTH_COMMON_RESPONSES,
        307: {"description": "Redirect to the PNG under /assets/logos/."},
        404: {"model": ErrorEnvelope, "description": "No logo uploaded for that team code."},
    },
)
@apply_tiered_limit("data")
async def get_team_logo(
    request: Request,
    short_name: str = PathParam(..., description="Short team code, e.g. RBR"),
):
    """302-redirect to ``/assets/logos/{SHORT_NAME}.png``. Unauthenticated."""
    short_name = short_name.upper()
    if not _team_logo_available(short_name):
        raise HTTPException(status_code=404, detail=f"No logo for team code {short_name!r}")
    return RedirectResponse(url=TEAM_LOGO_URL.format(short_name=short_name), status_code=302)
