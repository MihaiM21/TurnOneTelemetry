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
    
    # Premium API keys (comma-separated list for higher rate limits)
    premium_api_keys: str = ""
    
    @property
    def premium_api_keys_list(self) -> List[str]:
        """Parse premium API keys from comma-separated string"""
        if not self.premium_api_keys:
            return []
        return [key.strip() for key in self.premium_api_keys.split(',') if key.strip()]
    
    # Cache Settings
    cache_dir: str = "./cache"
    output_dir: str = "./outputs"
    enable_response_cache: bool = True
    cache_ttl_seconds: int = 3600  # 1 hour
    
    # FastF1 Settings
    fastf1_cache_enabled: bool = True
    fastf1_cache_dir: str = "./cache"
    
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
    valid_sessions: str = "FP1,FP2,FP3,Q,SQ,S,R,Practice 1,Practice 2,Practice 3,Qualifying,Sprint Qualifying,Sprint,Race"
    
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
    
    # Background Processor
    enable_background_processor: bool = True
    processor_check_interval: int = 300  # Check every 5 minutes
    processor_auto_process_completed: bool = True  # Auto-process when sessions complete
    
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
