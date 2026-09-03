"""Documentation-only response schemas for the ``/api/admin/*`` routers.

These models exist purely so Swagger can show a real shape for every admin
endpoint. Per the house rule (see ``src/api/schemas/analysis.py``) they are
attached via ``responses={200: {"model": ...}}`` — **never** via
``response_model=`` — so they cannot filter or otherwise alter the live
payload. Each model is derived from the actual ``return`` statements of the
handler or the service/repository function it calls; nothing here is guessed.

A few payloads are genuinely dynamic — a job record that is the union of two
independent serializations (``PlotGenJob.as_dict()`` and
``admin_jobs._serialize()``), a stored user/key document that mirrors whatever
is in Mongo, or a background-processor result whose ``results`` key is a
free-form ``{feature: count}`` map. Those use ``model_config = {"extra":
"allow"}`` and/or ``Dict[str, Any]`` rather than a guessed fixed schema — see
the docstring on each for why.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

class ExtraGroup(BaseModel):
    """One bucket of stored keys the singleton expectation set says nothing
    about (e.g. ``lap_all_data x1100``) — see ``plot_inventory._group_extras``.
    """

    prefix: str
    count: int
    sample: List[str] = Field(description="Up to 6 example keys from this bucket.")


class UsageWindowStats(BaseModel):
    """One aggregated window from ``api_key_usage._aggregate_window``."""

    requests: int
    errors: int
    avg_ms: float
    error_rate_percent: float


class UsageBucket(BaseModel):
    """One hourly bucket from ``api_key_usage.summary_for_key``/``summary_for_keys``."""

    bucket: Optional[str] = Field(default=None, description="``yyyymmddhh`` bucket key.")
    count: int
    errors: int
    avg_duration_ms: float


class KeyUsageSummary(BaseModel):
    """Windowed usage summary — ``api_key_usage.summary_for_key``/``summary_for_keys``."""

    window_hours: int
    total_requests: int
    total_errors: int
    error_rate_percent: float
    avg_duration_ms: float
    buckets: List[UsageBucket]


class PeakHourEntry(BaseModel):
    """One hour-of-day (UTC) bucket from ``api_key_usage.peak_hours_for_key*``."""

    hour: int
    count: int
    errors: Optional[int] = Field(
        default=None,
        description="Absent only in the no-data fallback of /peak-hours' peak_hour_utc.",
    )


class RequestTrackerSummary(BaseModel):
    """``RequestTracker.get_summary()`` — in-process request counters."""

    total_requests: int
    total_errors: int
    error_rate_percent: float
    tracked_requests_in_memory: int
    tracked_errors_in_memory: int
    unique_endpoints: int


# ---------------------------------------------------------------------------
# Processor / populate-sessions
# ---------------------------------------------------------------------------

class ProcessorStatusResponse(BaseModel):
    """``GET /api/admin/processor-status`` — ``BackgroundProcessor`` state."""

    running: bool
    check_interval: float
    processed_sessions_count: int
    processed_sessions: List[str] = Field(description="Last 20 processed session ids.")
    status: str


class ProcessLatestResponse(BaseModel):
    """``POST /api/admin/process-latest`` — ``BackgroundProcessor.force_process_latest()``.

    ``results`` is the free-form ``{feature_name: count}`` map returned by
    ``PlotDataGenerator.generate_all_session_data`` and is only present on a
    successful run; permissive because its keys track whatever plot features
    the generator currently supports.
    """

    status: str
    session: Optional[str] = None
    results: Optional[Dict[str, int]] = None
    error: Optional[str] = None
    reason: Optional[str] = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# MongoDB overview / coverage
# ---------------------------------------------------------------------------

class MongoOverviewYearStat(BaseModel):
    year: int
    collection: str
    gp_documents: int
    gp_with_session_data: int


class MongoOverviewResponse(BaseModel):
    """``GET /api/admin/mongodb/overview`` — ``_query_mongodb_overview``."""

    version: str
    database: str
    collections_matched: int
    total_gp_documents: int
    years: List[MongoOverviewYearStat]


class MongoPlotDetail(BaseModel):
    plot_type: str
    record_estimate: int = Field(description="List length / dict key count; a rough size proxy only.")


class MongoSessionSummary(BaseModel):
    session_type: str
    has_data: bool
    plot_count: int
    plot_types: List[str]
    plot_details: List[MongoPlotDetail]


class MongoGpResult(BaseModel):
    year: int
    round_nr: Optional[int] = None
    gp_id: Optional[str] = None
    event_name: Optional[str] = None
    session_count: int
    sessions: List[MongoSessionSummary]


class MongoSessionsCoverageResponse(BaseModel):
    """``GET /api/admin/mongodb/sessions-with-data`` — ``_query_mongodb_coverage``."""

    version: str
    database: str
    years_scanned: List[int]
    total_gp_documents_scanned: int
    total_gp_results: int
    available_session_types: List[str]
    available_plot_types: List[str]
    grand_prix: List[MongoGpResult]


# ---------------------------------------------------------------------------
# Plot inventory & backfill (V2)
# ---------------------------------------------------------------------------

class PlotInventorySessionEntry(BaseModel):
    session_type: str
    expected: List[str] = Field(description="Every singleton data_type expected for this session.")
    present: List[str]
    missing: List[str]
    extra_groups: List[ExtraGroup] = Field(
        description="Stored per-driver/pair/lap and legacy keys the expectation set says nothing about."
    )
    extra_count: int
    labels: Dict[str, str] = Field(description="data_type -> human label.")


class PlotInventoryGp(BaseModel):
    year: int
    round_nr: int
    event_name: str
    sessions: List[PlotInventorySessionEntry]


class PlotInventoryScope(BaseModel):
    year: Optional[int] = None
    gp: Optional[Any] = None
    session: Optional[str] = None


class PlotInventoryResponse(BaseModel):
    """``GET /api/admin/plots/missing`` — ``plot_inventory.compute_inventory``
    (aliased as ``compute_missing``). Schedule-driven, so entirely-absent
    sessions/GPs are reported too, not just Mongo gaps.
    """

    version: str
    scope: PlotInventoryScope
    years_scanned: List[int]
    total_sessions: int
    total_expected_plots: int
    total_missing_plots: int
    total_extra_plots: int
    grand_prix: List[PlotInventoryGp]


class FeatureCatalogEntry(BaseModel):
    """One selectable feature — ``registry.FeatureEntry.as_dict()``."""

    key: str
    label: str
    kind: str = Field(description="singleton | per_driver | per_pair | per_driver_lap | season | career")
    group: str
    applies_to: List[str] = Field(description="Session types this applies to; empty means every type.")
    cost: str = Field(description="light | heavy | extreme")
    session_scoped: bool


class PlotCatalogResponse(BaseModel):
    """``GET /api/admin/plots/catalog``."""

    count: int
    features: List[FeatureCatalogEntry]
    session: Optional[str] = None


class SeasonFeatureRow(BaseModel):
    feature: str
    label: str
    data_type: str
    present: bool


class SeasonInventoryResponse(BaseModel):
    """``GET /api/admin/plots/season`` — ``plot_inventory.season_inventory``."""

    year: int
    features: List[SeasonFeatureRow]
    extra_groups: List[ExtraGroup]
    is_current_season: bool


class LapRange(BaseModel):
    min: int
    max: int


class SessionDriversResponse(BaseModel):
    """``GET /api/admin/sessions/{year}/{gp}/{session}/drivers``."""

    year: int
    gp: str
    session: str
    drivers: List[str]
    laps: Optional[Dict[str, List[int]]] = Field(
        default=None, description="Only present when include_laps=true. Driver TLA -> lap numbers."
    )
    lap_range: Optional[LapRange] = Field(default=None, description="Only present when include_laps=true.")


class PlanFeatureCost(BaseModel):
    feature: str
    label: str
    units: int


class PlanEstimateResponse(BaseModel):
    """``POST /api/admin/plots/estimate`` — ``plot_inventory.estimate_plan``."""

    units: int
    by_feature: List[PlanFeatureCost]
    warnings: List[str]
    drivers_by_session: Dict[str, List[str]]
    truncated: bool = Field(description="True if the plan hit MAX_PLAN_UNITS and was cut short.")
    sessions: int = Field(description="Distinct (year, gp, session) touched, excluding season/career units.")


class PlotGenJobResponse(BaseModel):
    """``POST /api/admin/plots/generate`` — ``PlotGenJob.as_dict()`` at creation
    time (freshly queued, so counters are still zero).
    """

    job_id: str
    scope: Dict[str, Any]
    selection: Dict[str, Any]
    status: str = Field(description="queued | running | completed | failed | cancelled")
    total: int
    done: int
    success: int
    failed: int
    skipped: int
    current: Optional[str] = None
    per_feature: Dict[str, Dict[str, int]]
    errors: List[str] = Field(description="Most recent 25 errors.")
    warnings: List[str]
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class JobRecord(BaseModel):
    """One entry from ``GET /api/admin/plots/jobs`` or ``/jobs/{job_id}``.

    Genuinely a union of two independent serializations:
    ``admin_jobs._serialize()`` (the durable Mongo doc — contributes ``kind``,
    ``cancel_requested``, ``worker``, ``created_at``, ``heartbeat_at``) merged
    with ``PlotGenJob.as_dict()`` (the live in-process job, when this process
    owns it — contributes ``warnings``) via ``{**doc, **live}``. A job served
    from a process that never ran it has only the Mongo fields. Permissive
    because the merge means neither side's field set is guaranteed alone.
    """

    job_id: str
    kind: Optional[str] = None
    scope: Dict[str, Any]
    selection: Dict[str, Any]
    status: str
    total: int
    done: int
    success: int
    failed: int
    skipped: int
    current: Optional[str] = None
    per_feature: Dict[str, Any]
    errors: List[str]
    warnings: Optional[List[str]] = None
    cancel_requested: Optional[bool] = None
    worker: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    heartbeat_at: Optional[str] = None

    model_config = {"extra": "allow"}


class JobsListResponse(BaseModel):
    """``GET /api/admin/plots/jobs``."""

    count: int
    jobs: List[JobRecord]


class JobCancelResponse(BaseModel):
    """``POST /api/admin/plots/jobs/{job_id}/cancel``.

    ``cancel_requested=true`` only means the flag was set — the worker stops at
    its next progress flush (~2s / 25 units), not synchronously with this call.
    """

    job_id: str
    cancel_requested: bool


# ---------------------------------------------------------------------------
# User / API-key management
# ---------------------------------------------------------------------------

class UserRecord(BaseModel):
    """One ``users`` document — ``users.list_users`` (password_hash excluded).

    Permissive: this mirrors whatever is actually stored, and the collection
    predates a fixed schema.
    """

    id: str
    email: Optional[str] = None
    is_admin: Optional[bool] = None
    disabled: Optional[bool] = None
    created_at: Optional[Any] = None

    model_config = {"extra": "allow"}


class UsersListResponse(BaseModel):
    """``GET /api/admin/users``."""

    count: int
    users: List[UserRecord]


class ApiKeyRecord(BaseModel):
    """One ``api_keys`` document — ``api_keys.list_all`` (key_hash excluded,
    raw_key never stored/returned outside creation).
    """

    id: str
    owner_id: Optional[str] = None
    key_prefix: str
    tier: str
    label: str
    created_at: Optional[Any] = None
    last_used_at: Optional[Any] = None
    revoked_at: Optional[Any] = None


class KeysListResponse(BaseModel):
    """``GET /api/admin/keys``."""

    count: int
    keys: List[ApiKeyRecord]


class KeyVerifyResponse(BaseModel):
    """``GET /api/admin/keys/verify``.

    Three distinct shapes depending on what happened (lookup error / not
    found / found), so every field beyond ``found`` is optional.
    """

    found: bool
    error: Optional[str] = Field(default=None, description="Set only when the Mongo lookup itself raised.")
    key_prefix: Optional[str] = None
    tier: Optional[str] = None
    revoked: Optional[bool] = None
    owner_id: Optional[str] = None
    created_at: Optional[str] = None
    cached: Optional[Dict[str, Any]] = Field(
        default=None, description="Raw Redis auth-cache entry for this key hash, if any."
    )
    hint: Optional[str] = None


class KeyRevokeResponse(BaseModel):
    """``POST /api/admin/keys/{key_id}/revoke``."""

    id: str
    revoked: bool


# ---------------------------------------------------------------------------
# Usage / dashboard / analytics
# ---------------------------------------------------------------------------

class UsageTopRow(BaseModel):
    key_hash: str = Field(description="SHA-256 of the raw key — never the key itself.")
    count: int
    errors: int
    avg_duration_ms: float
    key_prefix: Optional[str] = None
    label: Optional[str] = None
    tier: Optional[str] = None
    owner_id: Optional[str] = None


class UsageTopResponse(BaseModel):
    """``GET /api/admin/usage/top``."""

    window_hours: int
    count: int
    rows: List[UsageTopRow]


class UsageForKeyResponse(BaseModel):
    """``GET /api/admin/usage/keys/{key_id}``."""

    key_id: str
    key_prefix: Optional[str] = None
    owner_id: Optional[str] = None
    tier: Optional[str] = None
    label: Optional[str] = None
    usage: KeyUsageSummary


class DashboardTotals(BaseModel):
    today: UsageWindowStats
    last_7_days: UsageWindowStats
    current_month: UsageWindowStats


class DashboardTopKeyRow(BaseModel):
    key_hash: str
    count: int
    errors: int
    avg_duration_ms: float
    key_prefix: Optional[str] = None
    label: Optional[str] = None
    tier: Optional[str] = None


class AdminDashboardResponse(BaseModel):
    """``GET /api/admin/dashboard`` — platform-wide usage summary."""

    totals: DashboardTotals
    month_resets_at: str
    top_keys_24h: List[DashboardTopKeyRow]
    request_tracker_summary: RequestTrackerSummary


class PeakHoursResponse(BaseModel):
    """``GET /api/admin/peak-hours``."""

    days: int
    peak_hour_utc: PeakHourEntry
    by_hour: List[PeakHourEntry] = Field(description="Always 24 entries, hour 0..23 UTC.")


class QuotaRow(BaseModel):
    key_id: str
    key_prefix: Optional[str] = None
    label: Optional[str] = None
    tier: str
    monthly_limit: int
    used_this_month: int
    remaining: int
    percent_used: float


class QuotaUsageResponse(BaseModel):
    """``GET /api/admin/quota-usage``."""

    count: int
    rows: List[QuotaRow] = Field(description="Sorted by percent_used, descending.")


class DashboardForKeysResult(BaseModel):
    """``api_key_usage.dashboard_for_keys`` — aggregated across a set of keys."""

    today: UsageWindowStats
    last_7_days: UsageWindowStats
    current_month: UsageWindowStats
    window: Optional[UsageWindowStats] = Field(default=None, description="Present only when hours was passed.")
    window_hours: Optional[int] = None
    buckets: List[UsageBucket]
    month_resets_at: str


class UserUsageKeyRow(BaseModel):
    id: str
    key_prefix: Optional[str] = None
    label: Optional[str] = None
    tier: Optional[str] = None
    revoked_at: Optional[Any] = None
    today: UsageWindowStats
    last_7_days: UsageWindowStats
    current_month: UsageWindowStats


class UserUsageResponse(BaseModel):
    """``GET /api/admin/users/{user_id}/usage``."""

    user: UserRecord
    window_hours: int
    aggregate: DashboardForKeysResult
    peak_hours: List[PeakHourEntry]
    keys: List[UserUsageKeyRow]


class DashboardForKeyResult(BaseModel):
    """``api_key_usage.dashboard_for_key`` — single-key today/7d/month + hourly series."""

    today: UsageWindowStats
    last_7_days: UsageWindowStats
    current_month: UsageWindowStats
    last_24h: List[UsageBucket]
    month_resets_at: str


class KeyAnalyticsKeyInfo(BaseModel):
    id: str
    key_prefix: Optional[str] = None
    label: Optional[str] = None
    tier: str
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None
    revoked_at: Optional[str] = None
    owner_id: Optional[str] = None


class KeyAnalyticsQuota(BaseModel):
    monthly_limit: int
    used_this_month: int
    remaining: int
    percent_used: float


class KeyAnalyticsResponse(BaseModel):
    """``GET /api/admin/keys/{key_id}/analytics``."""

    key: KeyAnalyticsKeyInfo
    owner: Optional[UserRecord] = None
    window_hours: int
    dashboard: DashboardForKeyResult
    window_summary: KeyUsageSummary
    peak_hours: List[PeakHourEntry]
    quota: KeyAnalyticsQuota


class EndpointStatsEntry(BaseModel):
    """Per-endpoint slice of ``RequestTracker.get_endpoint_stats()``."""

    total_requests: int
    total_errors: int
    error_rate: float
    avg_duration_ms: float
    min_duration_ms: float
    max_duration_ms: float


class PerformanceResponse(BaseModel):
    """``GET /api/admin/performance``.

    ``endpoints`` is keyed by endpoint identifier (dynamic — one entry per
    endpoint actually hit since process start), so it is a plain mapping
    rather than a fixed set of fields.
    """

    summary: RequestTrackerSummary
    endpoints: Dict[str, EndpointStatsEntry]


# ---------------------------------------------------------------------------
# V2 cache admin (admin_cache.py)
# ---------------------------------------------------------------------------

class RawCacheSessionEntry(BaseModel):
    """One session's raw-stream summary — ``raw_stream_cache.list_raw_stream_sessions``."""

    session_key: str
    year: Optional[int] = None
    round_nr: Optional[int] = None
    session: Optional[str] = None
    streams: List[str] = Field(description="Distinct stream names cached for this session (e.g. CarData.z).")
    bytes: int
    schema_drift: int = Field(description="Count of blobs written under an older SCHEMA_VERSION.")
    newest: Optional[str] = None
    stream_count: int


class RawCacheInfo(BaseModel):
    sessions: List[RawCacheSessionEntry]
    session_count: int
    files: int
    bytes: int
    schema_version: int
    schema_drift: int


class BundleCacheEntry(BaseModel):
    """One derived-bundle summary — ``session_cache.list_bundles``."""

    doc_id: str
    year: Optional[int] = None
    round_nr: Optional[int] = None
    session: Optional[str] = None
    event_name: Optional[str] = None
    keys: List[str] = Field(description="Top-level keys stored in this session's derived bundle.")
    schema_version: Optional[int] = None
    schema_drift: bool
    created_at: Optional[str] = None


class BundleCacheInfo(BaseModel):
    sessions: List[BundleCacheEntry]
    session_count: int
    total: int
    schema_version: int
    schema_drift: int


class CacheInventoryResponse(BaseModel):
    """``GET /api/admin/cache/inventory`` — ``admin_views.cache_inventory``."""

    year: Optional[int] = None
    raw: RawCacheInfo
    bundles: BundleCacheInfo


class CachePurgeRawResponse(BaseModel):
    """``POST /api/admin/cache/purge/raw/{session_key}`` (destructive)."""

    session_key: str
    removed: int = Field(description="Number of stream blobs deleted.")


class CachePurgeBundleResponse(BaseModel):
    """``POST /api/admin/cache/purge/bundle/{doc_id}`` (destructive)."""

    doc_id: str
    removed: bool


class CachePrewarmResponse(BaseModel):
    """``POST /api/admin/cache/prewarm``."""

    year: int
    gp: str
    session: str
    prewarmed: bool


# ---------------------------------------------------------------------------
# Stored-data browse/delete (admin_data.py)
# ---------------------------------------------------------------------------

class DataBrowseRow(BaseModel):
    """One session's stored keys — ``MongoDBManager.summarize_stored_data``."""

    year: int
    gp_id: Optional[str] = None
    event_name: Optional[str] = None
    round_nr: Optional[int] = None
    session_type: Optional[str] = None
    data_types: List[str] = Field(description="Every stored data_type for this session, legacy keys included.")
    count: int


class DataBrowseResponse(BaseModel):
    """``GET /api/admin/data/browse`` — ``admin_views.browse_stored_data``."""

    year: int
    rows: List[DataBrowseRow]
    total_sessions: int
    total_data_types: int


class DataDeleteResponse(BaseModel):
    """``DELETE /api/admin/data/{year}/{gp_id}/{session_type}/{data_type}`` (destructive).

    Removes exactly one stored ``data_type`` entry; the rest of the session
    document is left untouched. Not recoverable — the payload must be
    regenerated (e.g. via ``/api/admin/plots/generate?force=true``).
    """

    year: int
    gp_id: str
    session_type: str
    data_type: str
    deleted: bool


# ---------------------------------------------------------------------------
# Backup subsystem (backup_admin.py)
# ---------------------------------------------------------------------------

class BackupStatusConfig(BaseModel):
    schedule_utc: str
    retention_daily: int
    retention_weekly: int
    retention_monthly: int
    include_volumes: bool
    bucket: Optional[str] = None
    prefix: Optional[str] = None


class BackupStatusResponse(BaseModel):
    """``GET /api/admin/backup/status``."""

    enabled: bool
    scheduler_running: bool
    manual_in_progress: bool
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    last_error: Optional[str] = None
    last_backup_id: Optional[str] = None
    last_backup_tier: Optional[str] = None
    last_backup_success: Optional[bool] = None
    s3_reachable: Optional[bool] = Field(
        default=None, description="null when the backup subsystem is disabled (S3 was never pinged)."
    )
    config: BackupStatusConfig


class BackupListResponse(BaseModel):
    """``GET /api/admin/backup/list``."""

    tier: str
    count: int
    backup_ids: List[str]


class BackupArtifact(BaseModel):
    """One artifact in a backup manifest — ``services.backup.manifest.Artifact``."""

    name: str = Field(description='Logical component name, e.g. "mongo".')
    s3_key: str
    sha256_plaintext: str
    sha256_ciphertext: str
    plaintext_bytes: int
    ciphertext_bytes: int
    encrypted: bool


class BackupManifestResponse(BaseModel):
    """``GET /api/admin/backup/manifest/{backup_id}`` — ``services.backup.manifest.Manifest``."""

    backup_id: str
    tier: str
    started_at: str
    finished_at: str
    app_version: str
    mongo_database: str
    sqlite_path: str
    artifacts: List[BackupArtifact]
    success: bool
    error: Optional[str] = None
    encryption: str


class BackupRunResponse(BaseModel):
    """``POST /api/admin/backup/run``."""

    status: str
    message: str


class BackupVerifyResponse(BaseModel):
    """``POST /api/admin/backup/verify/{backup_id}``."""

    backup_id: str
    tier: str
    verified: List[str] = Field(description="Component names whose ciphertext checksum matched the manifest.")


class BackupRestoreResponse(BaseModel):
    """``POST /api/admin/backup/restore/{backup_id}`` (destructive).

    Restores components from the manifest into MongoDB/SQLite. Without both
    ``?confirm=true`` and body field ``i_understand=true`` (or a throwaway
    ``mongo_target_db`` drill), the handler refuses with 400 before touching
    anything.
    """

    backup_id: str
    tier: str
    restored: List[str] = Field(description="Component names actually restored.")
    skipped: List[str] = Field(description="Component names present in the manifest but not restored.")
    mongo_target_db: Optional[str] = Field(
        default=None, description="Set only for a drill restore into a throwaway database."
    )
