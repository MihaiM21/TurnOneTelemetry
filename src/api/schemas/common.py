"""Shared response/error contracts for the public API.

Before this module the API emitted **six** mutually incompatible error shapes
(five from the handlers in ``src/api/app.py`` plus an ``ErrorResponse`` in
``health.py`` that matched none of them and was referenced nowhere). Clients
therefore could not write a single error parser.

These models mirror *exactly* what the handlers in ``app.py`` actually return —
they are documentation of real behaviour, not an aspiration. If you change a
handler, change the matching model here.

``COMMON_ERROR_RESPONSES`` is meant to be spread into a route's ``responses=``
so Swagger documents the failure modes instead of showing only ``200``/``422``:

    @router.get("/x", responses={**COMMON_ERROR_RESPONSES, **not_found()})
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ErrorEnvelope(BaseModel):
    """The default error body (generic handler, HTTPException, ValueError).

    Emitted by ``app.py`` for 400/401/403/404/409/422/500.
    """

    detail: str = Field(description="Human-readable description of the failure.")
    request_id: str = Field(description="Correlates this response with server logs.")
    timestamp: str = Field(description="UTC ISO-8601 timestamp of the failure.")

    model_config = {
        "json_schema_extra": {
            "example": {
                "detail": "Invalid API key",
                "request_id": "3f2a9c14",
                "timestamp": "2026-09-02T10:15:30.123456+00:00",
            }
        }
    }


class SessionNotFoundEnvelope(BaseModel):
    """404 raised when the schedule says the session does not exist."""

    error: str = Field(default="session_not_found")
    detail: str
    year: Optional[int] = None
    gp: Optional[Any] = None
    session: Optional[str] = None
    valid_rounds: Optional[List[int]] = Field(
        default=None, description="Rounds that do exist for this season."
    )
    suggestions: Optional[List[str]] = Field(
        default=None, description="Near-miss identifiers, when the input looks like a typo."
    )
    request_id: str
    timestamp: str


class DataNotAvailableEnvelope(BaseModel):
    """503 — the session exists but no source can supply its data yet.

    Retryable: the session may simply not have been published upstream.
    """

    error: str = Field(default="data_not_available")
    detail: str
    year: Optional[int] = None
    gp: Optional[Any] = None
    session: Optional[str] = None
    sources_tried: Optional[List[str]] = None
    retry_after_seconds: int = 300
    request_id: str
    timestamp: str


class UpstreamUnavailableEnvelope(BaseModel):
    """503 — an upstream dependency (livetiming, FastF1) is down."""

    error: str = Field(default="upstream_unavailable")
    detail: str
    source: Optional[str] = None
    retry_after_seconds: int = 60
    request_id: str
    timestamp: str


def _resp(model: type[BaseModel], description: str) -> Dict[str, Any]:
    return {"model": model, "description": description}


#: Failure modes essentially every authenticated endpoint can produce.
COMMON_ERROR_RESPONSES: Dict[int | str, Dict[str, Any]] = {
    401: _resp(ErrorEnvelope, "API key missing."),
    403: _resp(ErrorEnvelope, "API key invalid, or insufficient privileges."),
    429: _resp(ErrorEnvelope, "Rate limit exceeded for the caller's tier."),
    500: _resp(ErrorEnvelope, "Unhandled server error."),
}


def session_error_responses() -> Dict[int | str, Dict[str, Any]]:
    """Extra failure modes for endpoints addressed by (year, gp, session)."""
    return {
        400: _resp(ErrorEnvelope, "Malformed parameter."),
        404: _resp(SessionNotFoundEnvelope, "No such session in the schedule."),
        503: _resp(
            DataNotAvailableEnvelope,
            "Session data not yet available upstream, or an upstream source is down. "
            "Honour the `Retry-After` header.",
        ),
    }


#: Convenience: the full set for a session-addressed analysis endpoint.
ANALYSIS_ERROR_RESPONSES: Dict[int | str, Dict[str, Any]] = {
    **COMMON_ERROR_RESPONSES,
    **session_error_responses(),
}
