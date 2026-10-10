"""Application configuration management via Pydantic Settings."""

from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application runtime settings."""

    # Project metadata
    PROJECT_NAME: str = "Pulseboard API"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/v1"
    ENVIRONMENT: str = "production"

    # Database settings
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pulseboard"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10

    # Redis settings
    REDIS_URL: Optional[str] = "redis://localhost:6379/0"
    REDIS_STREAM_KEY: str = "pulseboard:events"
    REDIS_CONSUMER_GROUP: str = "pulseboard_workers"
    REDIS_CONSUMER_NAME: str = "worker-1"

    # Worker settings
    WORKER_BATCH_SIZE: int = 500
    WORKER_POLL_INTERVAL_MS: int = 1000
    WORKER_COMPACTION_INTERVAL_SECONDS: int = 60
    WORKER_RETENTION_INTERVAL_SECONDS: int = 3600

    # JWT Authentication
    JWT_SECRET_KEY: str = "pulseboard-super-secret-key-change-in-production-1234567890"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours

    # Ingestion Rate Limiting (per API key)
    RATE_LIMIT_PER_MINUTE: int = 1000

    # Data Retention
    RETENTION_DAYS: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
