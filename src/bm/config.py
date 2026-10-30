"""Typed configuration from the environment.

Two rules from `CLAUDE.md §11` shape this module:

1. **Infrastructure only.** Business rules (tax mode, draft limits, credit terms,
   discount caps, cancellation windows) are database `settings` rows so the owner can
   change them without a deploy. Nothing business-shaped belongs here.
2. **Startup fails fast** on a missing or malformed required variable. A process that
   boots with half a configuration is worse than one that refuses to boot.
"""

from __future__ import annotations

import functools
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
StorageBackend = Literal["local", "s3"]
LogFormat = Literal["json", "console"]
AlertSeverity = Literal["info", "warning", "critical"]


class ConfigError(RuntimeError):
    """Raised when the environment cannot produce a usable configuration."""


class Settings(BaseSettings):
    """Infrastructure configuration. See `docs/DEPLOYMENT.md §3`."""

    model_config = SettingsConfigDict(
        env_prefix="BM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    env: Environment = "production"
    release: str = "unknown"

    # --- database -------------------------------------------------------------
    database_url: str
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_statement_timeout_ms: int = Field(default=15_000, ge=100)
    db_lock_timeout_ms: int = Field(default=3_000, ge=100)

    # --- auth -----------------------------------------------------------------
    jwt_signing_key: SecretStr
    jwt_previous_key: SecretStr | None = None
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=365)
    password_pepper: SecretStr
    ip_hash_pepper: SecretStr

    # --- http -----------------------------------------------------------------
    cors_allowed_origins: str = ""
    enable_api_docs: bool = False
    rate_limit_enabled: bool = True
    max_body_bytes: int = Field(default=1_048_576, ge=1024)

    # --- storage --------------------------------------------------------------
    storage_backend: StorageBackend = "local"
    storage_path: str = "./storage"
    storage_bucket: str | None = None
    max_upload_mb: int = Field(default=10, ge=1, le=100)

    # --- observability --------------------------------------------------------
    log_level: str = "INFO"
    log_format: LogFormat = "json"
    sentry_dsn: str | None = None

    # --- worker ---------------------------------------------------------------
    worker_concurrency: int = Field(default=2, ge=1, le=32)

    # --- alerting (OD-19) -----------------------------------------------------
    #
    # The **recipient is configuration, never a default**. OD-19 is still open and says
    # outright that "an alert that reaches nobody is not monitoring", so this code will
    # not invent an address: with alerting enabled and no recipient, `/readyz` reports
    # `alerting: unconfigured` rather than silently dropping alerts. Inventing a
    # plausible-looking address would be worse than leaving it blank, because the gap
    # would then be invisible.
    alerting_enabled: bool = False
    #: Where a critical alert is delivered. An HTTPS webhook URL (Slack, Teams, a pager
    #: provider) or `mailto:` — the channel is inferred from the scheme so adding one
    #: does not need a new variable.
    alert_webhook_url: SecretStr | None = None
    #: Free-text name of the human who is accountable for alerts. OD-19 requires a
    #: *named* person, not a shared inbox, so this is recorded and surfaced in `/readyz`
    #: — the operator can see at a glance whether anyone owns the pager.
    alert_recipient_name: str | None = None
    alert_min_severity: AlertSeverity = "warning"
    alert_timeout_seconds: float = Field(default=5.0, gt=0, le=30)

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        # SQLite is prohibited (ADR-017): it cannot express row locks, NUMERIC,
        # partial indexes, enums or column privileges, which is most of what this
        # system relies on. Catching it here turns a confusing runtime failure into a
        # clear startup failure.
        if value.startswith("sqlite"):
            raise ValueError(
                "SQLite is prohibited (ADR-017). PostgreSQL 16 is required because the "
                "system depends on row locks, NUMERIC, partial indexes and column privileges."
            )
        if not value.startswith("postgresql"):
            raise ValueError("BM_DATABASE_URL must be a postgresql:// or postgresql+psycopg:// URL")
        return value

    @field_validator("jwt_signing_key", "password_pepper", "ip_hash_pepper")
    @classmethod
    def _validate_secret_length(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("secret must be at least 32 characters")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"BM_LOG_LEVEL must be one of {sorted(allowed)}")
        return upper

    @property
    def cors_origins(self) -> list[str]:
        """Explicit allow-list. A wildcard is never produced (`SECURITY.md §6`)."""
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def load_settings() -> Settings:
    """Build settings, converting pydantic's error into a readable startup failure."""
    try:
        # Every value comes from the environment, so no arguments are passed.
        return Settings()
    except Exception as exc:
        raise ConfigError(
            "Configuration is invalid or incomplete. The API will not start.\n"
            f"{exc}\n"
            "See docs/DEPLOYMENT.md §3 for the required variables."
        ) from exc


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached so the environment is read exactly once."""
    return load_settings()
