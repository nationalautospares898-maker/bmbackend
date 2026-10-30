"""M1 — configuration fails fast.

A process that boots with half a configuration is worse than one that refuses to boot,
so every required variable is proven to be required.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bm.config import ConfigError, Settings, load_settings

VALID = {
    "BM_DATABASE_URL": "postgresql+psycopg://bm_app:pw@localhost:5432/bm",
    "BM_JWT_SIGNING_KEY": "a" * 40,
    "BM_PASSWORD_PEPPER": "b" * 40,
    "BM_IP_HASH_PEPPER": "c" * 40,
}


def _settings(**overrides: str) -> Settings:
    return Settings(
        _env_file=None, **{k.removeprefix("BM_").lower(): v for k, v in (VALID | overrides).items()}
    )  # type: ignore[arg-type]


@pytest.mark.parametrize("missing", sorted(VALID))
def test_missing_required_variable_fails_startup(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """Each required variable is individually required, not merely documented."""
    for key, value in VALID.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing)
    # A developer's local .env must not decide whether this test passes.
    monkeypatch.chdir("/")

    with pytest.raises(ConfigError) as caught:
        load_settings()

    assert missing.removeprefix("BM_").lower() in str(caught.value).lower()


def test_load_settings_wraps_the_failure_in_a_readable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BM_DATABASE_URL", raising=False)
    monkeypatch.setenv("BM_JWT_SIGNING_KEY", "a" * 40)
    monkeypatch.setenv("BM_PASSWORD_PEPPER", "b" * 40)
    monkeypatch.setenv("BM_IP_HASH_PEPPER", "c" * 40)
    monkeypatch.chdir("/")

    with pytest.raises(ConfigError) as caught:
        load_settings()

    message = str(caught.value)
    assert "will not start" in message
    assert "DEPLOYMENT.md" in message


def test_sqlite_is_rejected_outright() -> None:
    """ADR-017. SQLite cannot test row locks, NUMERIC, partial indexes, enums or
    column privileges, so it must not even be configurable."""
    with pytest.raises(Exception) as caught:
        _settings(BM_DATABASE_URL="sqlite:///./bm.db")
    assert "ADR-017" in str(caught.value)


def test_non_postgres_url_is_rejected() -> None:
    with pytest.raises(Exception) as caught:
        _settings(BM_DATABASE_URL="mysql://user:pw@localhost/bm")
    assert "postgresql" in str(caught.value)


@pytest.mark.parametrize(
    "secret", ["BM_JWT_SIGNING_KEY", "BM_PASSWORD_PEPPER", "BM_IP_HASH_PEPPER"]
)
def test_short_secrets_are_rejected(secret: str) -> None:
    with pytest.raises(Exception) as caught:
        _settings(**{secret: "too-short"})
    assert "32 characters" in str(caught.value)


def test_bad_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(BM_LOG_LEVEL="CHATTY")


def test_log_level_is_normalised_to_upper_case() -> None:
    assert _settings(BM_LOG_LEVEL="debug").log_level == "DEBUG"


def test_cors_origins_never_produce_a_wildcard() -> None:
    settings = _settings(BM_CORS_ALLOWED_ORIGINS="https://a.example.com, https://b.example.com ,")
    assert settings.cors_origins == ["https://a.example.com", "https://b.example.com"]
    assert "*" not in settings.cors_origins


def test_empty_cors_list_is_empty_not_wildcard() -> None:
    assert _settings(BM_CORS_ALLOWED_ORIGINS="").cors_origins == []


def test_settings_are_frozen() -> None:
    """Configuration is read once at startup; a request must not be able to change it."""
    settings = _settings()
    with pytest.raises(ValidationError):
        settings.env = "local"  # type: ignore[misc]


def test_no_business_rule_is_configurable_through_the_environment() -> None:
    """`CLAUDE.md §11`: business rules are database `settings` rows so the owner can
    change them without a deploy. A field named after one here would be a design
    regression, so the field list is asserted directly."""
    forbidden_fragments = (
        "tax_mode",
        "draft_limit",
        "max_open_draft",
        "credit_terms",
        "discount",
        "rounding",
        "cancel_window",
        "invoice_prefix",
        "oversell",
        "returns_window",
    )
    fields = set(Settings.model_fields)
    offenders = [field for field in fields for fragment in forbidden_fragments if fragment in field]
    assert offenders == [], (
        f"{offenders} look like business rules. They belong in the database `settings` "
        "table, not in environment configuration."
    )
