"""
Configuration management for F1 Telemetry API
Centralizes all environment variables and settings
"""

from pydantic_settings import BaseSettings
from typing import List, Optional
from functools import lru_cache

try:
    from src.__version__ import __version__
except ImportError:
    __version__ = "1.0.0"


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    # API Settings
    app_name: str = "F1 Telemetry API"
    app_version: str = __version__
    environment: str = "development"
    debug: bool = False

    # Server Settings
    host: str = "0.0.0.0"
    port: int = 8000
    host_port: int = 5000  # Docker host port for Coolify deployment
    docker_exposed_port: int = 5000
    workers: int = 4

    # MongoDB Settings
    mongodb_user: str = "root"
    mongodb_password: str
    mongodb_host: str = "localhost"
    mongodb_port: int = 27017
    mongodb_database: str = "T1API_DB"
    mongodb_max_pool_size: int = 50
    mongodb_min_pool_size: int = 10
    mongodb_timeout_ms: int = 30000

    # Security Settings
    api_secret_key: str = "your-secret-key-change-in-production"
    api_key_name: str = "X-API-Key"
    allowed_api_keys: str = ""  # Comma-separated
    docs_auth_always: bool = False
    docs_username: str = ""
    docs_password: str = ""

    # JWT & Admin Settings
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60 * 24
    bcrypt_rounds: int = 12
    api_key_cache_ttl_seconds: int = 60
    admin_secret_key: str = ""
    admin_email: str = ""
    admin_password: str = ""

    # ---- Admin operator credentials -------------------------------------
    # Distinct from ALLOWED_API_KEYS / PREMIUM_API_KEYS. Those are *consumer*
    # keys (the website's own key is one of them); treating them as admin meant
    # any front-end key could purge caches, revoke keys and restore backups.
    # Only keys listed here -- or a DB key whose owner has is_admin -- are
    # operators.
    admin_api_keys: str = ""  # Comma-separated

    # Transitional escape hatch. Set true only to restore the historical
    # behaviour where every env key was an admin key, while operator keys are
    # being provisioned. Logged loudly at startup.
    admin_allow_legacy_env_keys: bool = False

    # ---- Dev auth bypass -------------------------------------------------
    # verify_api_key used to skip authentication entirely whenever
    # environment == "development" and ALLOWED_API_KEYS was empty -- both of
    # which are the *defaults*, so a deploy that forgot ENVIRONMENT=production
    # silently served the whole API unauthenticated. The bypass now requires an
    # explicit opt-in as well.
    allow_insecure_dev_auth: bool = False

    # ---- Proxy trust -----------------------------------------------------
    # client_ip() gates the admin IP allowlist, the admin UI rate limit and the
    # login lockout. Honouring X-Forwarded-For unconditionally let anyone spoof
    # an allowlisted IP. Enable only when a reverse proxy you control rewrites
    # the header.
    trust_x_forwarded_for: bool = False
    trusted_proxy_ips: str = ""  # Comma-separated peer addresses

    # ---- Admin UI / login hardening (previously raw os.getenv) -----------
    # These were read via os.getenv in src/api/admin_security.py, bypassing
    # Settings even though config.py is documented as the single source of
    # truth. Same names, same defaults.
    admin_ip_allowlist: str = ""  # Comma-separated; empty = no IP gating
    admin_ui_rate_per_minute: int = 60
    admin_login_max_attempts: int = 5
    admin_login_lockout_seconds: int = 900
    admin_login_window_seconds: int = 600

    # Stripe Settings (Optional)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_pro: str = ""

    @property
    def allowed_api_keys_list(self) -> List[str]:
        """Parse API keys from comma-separated string"""
        if not self.allowed_api_keys:
            return []
        return [key.strip() for key in self.allowed_api_keys.split(',') if key.strip()]

    # CORS Settings
    cors_origins: str = "http://localhost:3000,http://localhost:8000"
    cors_allow_credentials: bool = True
    cors_allow_methods: str = "GET,POST"
    cors_allow_headers: str = "*"

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins from comma-separated string"""
        return [origin.strip() for origin in self.cors_origins.split(',') if origin.strip()]

    @property
    def cors_methods_list(self) -> List[str]:
        """Parse CORS methods from comma-separated string"""
        return [method.strip() for method in self.cors_allow_methods.split(',') if method.strip()]

    # Rate Limiting - Tiered System
    rate_limit_enabled: bool = True

    # Unauthenticated/Public endpoints (health, docs)
    rate_limit_public_per_minute: int = 30
    rate_limit_public_per_hour: int = 500

    # Authenticated API key users - Standard tier
    rate_limit_standard_per_minute: int = 100
    rate_limit_standard_per_hour: int = 2000

    # Authenticated API key users - Premium tier (for website)
    rate_limit_premium_per_minute: int = 300
    rate_limit_premium_per_hour: int = 10000

    # Data endpoints (more intensive)
    rate_limit_data_per_minute: int = 60
    rate_limit_data_per_hour: int = 1000

    # Monthly quotas per tier (reporting only, not enforced)
    quota_public_monthly: int = 5_000
    quota_standard_monthly: int = 100_000
    quota_premium_monthly: int = 1_000_000

    # Premium API keys (comma-separated list for higher rate limits)
    premium_api_keys: str = ""

    @property
    def premium_api_keys_list(self) -> List[str]:
        """Parse premium API keys from comma-separated string"""
        if not self.premium_api_keys:
            return []
        return [key.strip() for key in self.premium_api_keys.split(',') if key.strip()]

    @property
    def admin_api_keys_list(self) -> List[str]:
        """Operator keys. Empty means: only DB keys whose owner is_admin."""
        if not self.admin_api_keys:
            return []
        return [key.strip() for key in self.admin_api_keys.split(',') if key.strip()]

    @property
    def admin_ip_allowlist_list(self) -> List[str]:
        if not self.admin_ip_allowlist:
            return []
        return [ip.strip() for ip in self.admin_ip_allowlist.split(',') if ip.strip()]

    @property
    def trusted_proxy_ips_list(self) -> List[str]:
        if not self.trusted_proxy_ips:
            return []
        return [ip.strip() for ip in self.trusted_proxy_ips.split(',') if ip.strip()]

    @property
    def is_production(self) -> bool:
        """True for any production-ish environment.

        Deliberately a prefix test rather than ``== "production"``: values like
        ``production-eu`` previously fell through to the development branch and
        silently dropped the ``Secure`` cookie flag.
        """
        return self.environment.strip().lower().startswith("prod")

    @property
    def session_signing_secret(self) -> str:
        """Secret backing admin sessions, CSRF tokens and JWTs."""
        return self.jwt_secret_key or self.api_secret_key or ""

    # Cache Settings
    cache_dir: str = "./cache"
    output_dir: str = "./outputs"
    enable_response_cache: bool = True
    cache_ttl_seconds: int = 3600  # 1 hour

    # FastF1 Settings
    fastf1_cache_enabled: bool = True
    fastf1_cache_dir: str = "./cache"

    # Dataset export (admin-only). export_dir must live under a docker-mounted volume
    export_dir: str = "./outputs/exports"
    export_concurrency: int = 1
    export_max_concurrency: int = 2
    export_max_years: int = 10
    export_min_free_gb: float = 5.0

    # Social packs (admin-only): per-session folders of social-format charts + zip.
    social_dir: str = "./outputs/social"

    # Logging Settings
    log_level: str = "INFO"
    log_file: str = "logs/api.log"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_rotation: str = "100 MB"
    log_retention: str = "30 days"

    # Data Validation
    min_year: int = 2018
    max_year: int = 2030
    min_round: int = 1
    max_round: int = 24
    valid_sessions: str = (
        "FP1,FP2,FP3,Q,SQ,S,R,"
        "Practice 1,Practice 2,Practice 3,"
        "Qualifying,Sprint Qualifying,Sprint,Race"
    )

    @property
    def valid_sessions_list(self) -> List[str]:
        """Parse valid sessions from comma-separated string"""
        return [session.strip() for session in self.valid_sessions.split(',') if session.strip()]

    # Monitoring
    enable_metrics: bool = True
    sentry_dsn: Optional[str] = None
    sentry_environment: str = "development"
    sentry_traces_sample_rate: float = 0.1

    # Redis Configuration (Optional - for caching)
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # F1 Live Timing Proxy (optional — use a Cloudflare Worker URL to bypass VPS IP blocks)
    f1_proxy_base_url: Optional[str] = None

    # Background Processor
    enable_background_processor: bool = True
    processor_check_interval: int = 300  # Check every 5 minutes
    processor_auto_process_completed: bool = True  # Auto-process when sessions complete
    # Pre-warm every V2 raw stream (incl. the big CarData.z / Position.z telemetry)
    # into the durable GridFS cache when a session is processed, so later
    # per-driver / per-pair requests never re-download the multi-MB streams.
    enable_v2_stream_prewarm: bool = True

    # Circuits Sync (fallback only - our stored circuit data is the source of truth;
    # this only adds circuits/years we don't have yet and replaces telemetry-derived
    # layouts once multiviewer publishes the circuit; it never overwrites a
    # multiviewer layout)
    enable_circuits_sync: bool = True
    circuits_sync_interval_seconds: int = 2_592_000  # 30 days
    # When the background processor finishes a session whose livetiming
    # Circuit.Key has no stored layout (a brand-new circuit), trace one from the
    # session's fastest lap and store it (disk + MongoDB mirror).
    auto_derive_circuits: bool = True

    # Backup System (MongoDB + SQLite + optional volumes -> S3-compatible storage)
    backup_enabled: bool = False
    backup_s3_endpoint: str = ""           # blank => AWS default; set for B2/R2/MinIO
    backup_s3_region: str = "us-east-1"
    backup_s3_bucket: str = ""
    backup_s3_access_key: str = ""
    backup_s3_secret_key: str = ""
    backup_s3_prefix: str = "t1api"
    backup_s3_force_path_style: bool = True  # required by MinIO/B2/R2
    backup_encryption_key: str = ""         # base64-encoded 32 bytes (urlsafe ok)
    backup_schedule_hour_utc: int = 2       # daily run hour
    backup_schedule_minute_utc: int = 0
    backup_retention_daily: int = 7
    backup_retention_weekly: int = 4
    backup_retention_monthly: int = 6
    backup_include_volumes: bool = False    # tar.gz of outputs/ (skip cache/, logs/)
    backup_sqlite_path: str = "data/session_analytics.db"
    backup_tmp_dir: str = "/tmp/t1api-backup"
    backup_mongodump_bin: str = "mongodump"
    backup_mongorestore_bin: str = "mongorestore"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance

    Returns:
        Settings object with all configuration
    """
    return Settings()


# Convenience accessor
settings = get_settings()


# ---------------------------------------------------------------------------
# Startup safety
# ---------------------------------------------------------------------------

#: Placeholder shipped as the default for ``api_secret_key``.
DEFAULT_API_SECRET = "your-secret-key-change-in-production"


class InsecureConfigurationError(RuntimeError):
    """Raised when a production process is configured to fail open."""


def check_production_safety(s: "Settings" = None) -> List[str]:
    """Return configuration problems; raise on fatal ones in production.

    Several defaults combine into a fail-open deployment: ``environment``
    defaults to ``development`` and ``allowed_api_keys`` to ``""``, which used
    to disable authentication entirely, while ``api_secret_key`` ships a public
    placeholder that makes admin session cookies derivable. Catching that at
    boot is far better than discovering it from traffic.

    Warnings are returned for the caller to log; fatal problems raise.
    """
    s = s or settings
    fatal: List[str] = []
    warnings: List[str] = []

    if s.is_production:
        if not s.session_signing_secret:
            fatal.append(
                "JWT_SECRET_KEY (or API_SECRET_KEY) must be set in production; "
                "admin sessions and JWTs are signed with it."
            )
        if s.api_secret_key == DEFAULT_API_SECRET and not s.jwt_secret_key:
            fatal.append(
                "API_SECRET_KEY is still the shipped placeholder. Admin session "
                "cookies would be derivable by anyone reading the repository."
            )
        if s.allow_insecure_dev_auth:
            fatal.append(
                "ALLOW_INSECURE_DEV_AUTH must not be enabled in production; "
                "it disables API key verification."
            )
        if not s.allowed_api_keys_list and not s.premium_api_keys_list:
            warnings.append(
                "No env API keys configured; only database-issued keys will work."
            )
        if not s.admin_api_keys_list and not s.admin_allow_legacy_env_keys:
            warnings.append(
                "ADMIN_API_KEYS is empty: admin endpoints are reachable only by "
                "database keys whose owner has is_admin."
            )
        if s.admin_allow_legacy_env_keys:
            warnings.append(
                "ADMIN_ALLOW_LEGACY_ENV_KEYS is on: every consumer API key is "
                "treated as an admin key. Provision ADMIN_API_KEYS and turn it off."
            )
        if "*" in s.cors_origins_list and s.cors_allow_credentials:
            fatal.append(
                "CORS_ORIGINS='*' with CORS_ALLOW_CREDENTIALS=true reflects any "
                "origin with credentials. Set explicit origins."
            )

    if fatal:
        raise InsecureConfigurationError("; ".join(fatal))
    return warnings
