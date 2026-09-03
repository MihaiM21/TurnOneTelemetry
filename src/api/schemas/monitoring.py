"""Documentation-only response models for ``src/api/routers/monitoring.py``.

Never attached as a bare ``response_model=`` (see ``src/api/schemas/common.py``
for why) — these exist purely so Swagger can show a schema in ``responses=``.

Shapes are traced from ``src/core/observability/monitoring.py``
(``RequestTracker`` / ``SystemMonitor``) and the inline dicts each handler in
``monitoring.py`` builds around them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CpuMetrics(BaseModel):
    process_percent: float
    system_percent: float
    count: Optional[int] = None


class MemoryMetrics(BaseModel):
    process_mb: float
    process_percent: float
    system_total_mb: float
    system_available_mb: float
    system_percent: float


class DiskMetrics(BaseModel):
    total_gb: float
    used_gb: float
    free_gb: float
    percent: float


class NetworkMetrics(BaseModel):
    bytes_sent: int
    bytes_received: int
    packets_sent: int
    packets_received: int


class SystemMetricsResponse(BaseModel):
    """``SystemMonitor.get_metrics()``.

    All fields are optional because the method swallows its own exceptions
    and returns ``{"error": "..."}`` instead of raising — that shape is
    genuinely dynamic, so ``extra`` is left open rather than guessed at.
    """

    timestamp: Optional[str] = None
    uptime_seconds: Optional[int] = None
    uptime_human: Optional[str] = None
    cpu: Optional[CpuMetrics] = None
    memory: Optional[MemoryMetrics] = None
    disk: Optional[DiskMetrics] = None
    network: Optional[NetworkMetrics] = Field(default=None, description="Null when psutil network counters fail.")
    error: Optional[str] = Field(default=None, description="Present instead of the metrics above on failure.")

    model_config = {"extra": "allow"}


class RequestRecord(BaseModel):
    """One entry from ``RequestTracker.get_recent_requests`` (built in ``RequestTracingMiddleware``)."""

    request_id: str
    timestamp: str
    method: str
    endpoint: str
    full_url: str
    client_ip: str
    user_agent: str
    tier: str
    status_code: int
    duration: float = Field(description="Seconds, float precision.")
    duration_ms: float
    error: Optional[Dict[str, Any]] = Field(
        default=None, description="`{type, message, traceback}` when the request raised; null otherwise."
    )


class RecentRequestsResponse(BaseModel):
    count: int
    limit: int
    requests: List[RequestRecord]


class ErrorRecord(RequestRecord):
    """A ``RequestRecord`` for a failed/erroring request, plus its `error_details` copy."""

    error_details: Optional[Dict[str, Any]] = None


class RecentErrorsResponse(BaseModel):
    count: int
    limit: int
    errors: List[ErrorRecord]


class EndpointStat(BaseModel):
    total_requests: int
    total_errors: int
    error_rate: float = Field(description="Percent, e.g. 2.5 for 2.5%.")
    avg_duration_ms: float
    min_duration_ms: float
    max_duration_ms: float


class TrackerSummary(BaseModel):
    total_requests: int
    total_errors: int
    error_rate_percent: float
    tracked_requests_in_memory: int
    tracked_errors_in_memory: int
    unique_endpoints: int


class EndpointStatsResponse(BaseModel):
    summary: TrackerSummary
    endpoints: Dict[str, EndpointStat] = Field(description="Keyed by request path.")


class LoadSystemInfo(BaseModel):
    cpu_percent: float
    memory_percent: float
    memory_mb: float


class LoadRequestsInfo(BaseModel):
    total: int
    total_errors: int
    error_rate_percent: float


class CurrentLoadResponse(BaseModel):
    timestamp: str
    active_requests: float = Field(description="Current value of the `api_active_requests` Prometheus gauge.")
    system: LoadSystemInfo
    requests: LoadRequestsInfo
    uptime: str


class ServicesStatus(BaseModel):
    api: str
    session_tracker: str
    background_processor: str
    monitoring: str
    request_tracking: str


class BackgroundProcessorStatus(BaseModel):
    running: bool
    processed_sessions: int


class DetailedHealthRequestsInfo(BaseModel):
    active: float
    total: int
    total_errors: int
    error_rate_percent: float
    tracked_in_memory: int
    unique_endpoints: int


class DetailedHealthResponse(BaseModel):
    """``GET /api/monitoring/health-detailed``."""

    status: str
    timestamp: str
    version: str
    environment: str
    services: ServicesStatus
    system: SystemMetricsResponse
    requests: DetailedHealthRequestsInfo
    background_processor: BackgroundProcessorStatus


class TestPingResponse(BaseModel):
    status: str
    request_id: str
    timestamp: str
    message: str


class TopEndpointEntry(BaseModel):
    endpoint: str
    requests: int


class ErrorProneEndpointEntry(BaseModel):
    endpoint: str
    error_rate_percent: float


class RecentErrorSample(BaseModel):
    request_id: Optional[str] = None
    endpoint: Optional[str] = None
    status_code: Optional[int] = None
    timestamp: Optional[str] = None
    error_type: Optional[str] = None


class DiagnosticsSystemInfo(BaseModel):
    uptime: str
    cpu_percent: float
    memory_mb: float
    memory_percent: float


class DiagnosticsRequestsInfo(BaseModel):
    active: float
    total: int
    total_errors: int
    error_rate_percent: float
    unique_endpoints: int
    tracked_in_memory: int


class DiagnosticsResponse(BaseModel):
    """``GET /api/diagnostics`` — everything from the other monitoring endpoints in one call."""

    request_id: str
    timestamp: str
    status: str
    version: str
    environment: str
    system: DiagnosticsSystemInfo
    requests: DiagnosticsRequestsInfo
    top_endpoints: List[TopEndpointEntry]
    error_prone_endpoints: List[ErrorProneEndpointEntry]
    recent_errors_sample: List[RecentErrorSample]
    background_processor: BackgroundProcessorStatus
    services: ServicesStatus
