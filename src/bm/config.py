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
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
StorageBackend = Literal["local", "s3"]
LogFormat = Literal["json", "console"]
AlertSeverity = Literal["info", "warning", "critical"]
RefreshCookieSameSite = Literal["lax", "strict", "none"]


class ConfigError(RuntimeError):
    """Raised when the environment cannot produce a usable configuration."""


#: Public suffixes on which every customer gets their own subdomain, so two services
#: hosted on the same one are still **cross-site** to `SameSite`. Used only to warn about
#: `cross_site_refresh_cookie_origins`; it never changes behaviour, so an absent entry
#: costs a warning rather than correctness.
_PROVIDER_SUFFIXES = frozenset(
    {
        "vercel.app",
        "up.railway.app",
        "railway.app",
        "netlify.app",
        "pages.dev",
        "onrender.com",
        "fly.dev",
        "herokuapp.com",
        "azurewebsites.net",
    }
)


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

    # --- refresh cookie --------------------------------------------------------
    #
    # `lax` is the default and **is** the CSRF control described in `api/v1/auth.py`:
    # every other endpoint authenticates with a bearer header, so a cross-site form post
    # cannot act as the user, and the one cookie endpoint is additionally bound to
    # `X-Device-Id`. Nothing about that reasoning changes here, and nothing changes for
    # any deployment that leaves this alone.
    #
    # It exists because `SameSite` compares the **registrable domain**, not the origin,
    # and that decides whether the refresh cookie is sent at all:
    #
    #   * `app.example.com` → `api.example.com`  — same site. `lax` works. Use `lax`.
    #   * `nas.vercel.app`  → `nas.up.railway.app` — **cross**-site, because `vercel.app`
    #     and `up.railway.app` are both public suffixes. A `lax` cookie is never sent, so
    #     `POST /auth/refresh` arrives with no cookie, every cold start lands on the sign-in
    #     form, and the session dies 15 minutes after each login (the access-token TTL).
    #
    # So a split front end and API on two *provider* domains needs `none`, which browsers
    # only accept alongside `Secure` — hence the validator below refusing it on `local`,
    # where `Secure` is deliberately dropped for plain-HTTP development.
    #
    # Prefer two subdomains of one owned domain and leave this at `lax`. Reach for `none`
    # only when that is not available; see `docs/PRODUCTION-DEPLOYMENT-GUIDE.md §10`.
    refresh_cookie_samesite: RefreshCookieSameSite = "lax"

    # --- unattended access -----------------------------------------------------
    #
    # Lets a browser obtain a session **without anybody typing a password**, so the shop
    # PC opens straight into billing. Nothing else about authentication changes: the
    # session it issues is an ordinary one, every endpoint still demands a valid token,
    # every permission is still checked, the tenant scope still comes from the token, and
    # a revoked device is still refused.
    #
    # **Off by default, and that default is the security control.** With this on, anyone
    # who can reach the server gets a working session — there is no second factor and no
    # prompt to fail. It is therefore safe only where network access is already
    # controlled: a machine bound to `127.0.0.1`, or a LAN the shop owns. Exposing a
    # server with this enabled to the open internet publishes the shop's customers,
    # prices, takings and the ability to issue bills.
    #
    # `main.py` logs a warning on every startup while it is on, so a deployment cannot
    # drift into it unnoticed.
    unattended_access: bool = False
    #: Which account an unattended session belongs to. Required when the flag is on —
    #: the session is a real user's, so the audit trail still names who acted.
    unattended_username: str | None = None

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

    @model_validator(mode="after")
    def _unattended_access_needs_an_account(self) -> Settings:
        """Enabling unattended access without naming an account is a misconfiguration.

        Refused at startup rather than at the first request: a server that boots and then
        fails every attempt to open the application is far harder to diagnose than one
        that says why it will not boot. The session an unattended request issues belongs
        to a real user, so the audit trail still names who acted — which is only possible
        if the account is configured.
        """
        if self.unattended_access and not (self.unattended_username or "").strip():
            raise ValueError(
                "BM_UNATTENDED_ACCESS is on, so BM_UNATTENDED_USERNAME must name the "
                "account those sessions belong to."
            )
        return self

    @model_validator(mode="after")
    def _samesite_none_requires_a_secure_cookie(self) -> Settings:
        """`SameSite=None` without `Secure` is rejected by every current browser.

        `api/v1/auth.py` sets `secure=env != "local"`, so the two settings can only
        disagree in one direction, and it is refused at startup rather than producing a
        cookie the browser silently drops — a failure that looks exactly like a working
        login followed by an inexplicable sign-out fifteen minutes later.
        """
        if self.refresh_cookie_samesite == "none" and self.env == "local":
            raise ValueError(
                "BM_REFRESH_COOKIE_SAMESITE=none requires a Secure cookie, which is not "
                "set when BM_ENV=local. Use `lax` locally."
            )
        return self

    @property
    def cors_origins(self) -> list[str]:
        """Explicit allow-list. A wildcard is never produced (`SECURITY.md §6`)."""
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def cross_site_refresh_cookie_origins(self) -> list[str]:
        """Front-end origins that will **never** send the refresh cookie as configured.

        This is the misconfiguration that produced the owner's "it logs me out after
        every 13 minutes": `SameSite` compares the **registrable domain**, and
        `vercel.app`, `up.railway.app` and friends are public suffixes, so a front end on
        one provider domain and an API on another are **cross-site**. A `lax` cookie is
        then never sent to `POST /auth/refresh`, the access token dies at
        `access_token_ttl_seconds`, and the cashier is signed out mid-bill.

        13 minutes, specifically, because the client renews two minutes ahead of a
        fifteen-minute token — so the failure lands on the renewal, not on the expiry.

        Detection is deliberately narrow, so it cannot cry wolf on a correct deployment:

        * **Topology A** (`app.example.com` + `api.example.com`) — the origin is not on a
          provider domain, so nothing is reported and `lax` is right.
        * **Topology B** (`*.vercel.app` + `*.up.railway.app`) — reported, which is the
          bug. The fix is `BM_REFRESH_COOKIE_SAMESITE=none`.
        * **Topology C** (Vercel proxies `/api/*`) — the browser sees one origin, so the
          allow-list is empty and nothing is reported. `lax` is right there too.

        Returns the offending origins rather than a bool so the warning can name them.
        """
        if not self.is_production or self.refresh_cookie_samesite != "lax":
            return []
        offenders = []
        for origin in self.cors_origins:
            host = urlsplit(origin).hostname or ""
            if any(host == suffix or host.endswith(f".{suffix}") for suffix in _PROVIDER_SUFFIXES):
                offenders.append(origin)
        return offenders

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
