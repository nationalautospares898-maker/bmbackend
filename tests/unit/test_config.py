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


def test_refresh_cookie_is_samesite_lax_unless_a_deployment_opts_out() -> None:
    """The default is the CSRF control described in `api/v1/auth.py`, so it is asserted
    rather than left to whichever value happens to be in the class body."""
    assert _settings().refresh_cookie_samesite == "lax"


def test_samesite_none_is_refused_on_local_where_the_cookie_is_not_secure() -> None:
    """`auth.py` sets `secure = env != "local"`, and a browser discards a
    `SameSite=None` cookie that is not `Secure`. Refused at startup rather than produced:
    the symptom is a working login followed by an inexplicable sign-out fifteen minutes
    later, which is close to undiagnosable from the counter."""
    with pytest.raises(Exception) as caught:
        _settings(BM_ENV="local", BM_REFRESH_COOKIE_SAMESITE="none")
    assert "Secure" in str(caught.value)


def test_samesite_none_is_allowed_in_production() -> None:
    """The front end and the API on two *provider* domains (`*.vercel.app` talking to
    `*.up.railway.app`) are cross-site, so a `lax` cookie is never sent at all and the
    session cannot be refreshed. `none` is how that deployment works."""
    settings = _settings(BM_ENV="production", BM_REFRESH_COOKIE_SAMESITE="none")
    assert settings.refresh_cookie_samesite == "none"


def test_an_unknown_samesite_value_is_refused() -> None:
    with pytest.raises(ValidationError):
        _settings(BM_REFRESH_COOKIE_SAMESITE="cross-site")


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


# --- the 13-minute logout -----------------------------------------------------------
#
# The owner reported *"it's logging out after every 13 min and the work I was doing was
# gone"*. The cause was configuration, not code: a `lax` refresh cookie between two
# provider domains is never sent, so the renewal that runs two minutes ahead of the
# fifteen-minute access token fails and the session ends. Nothing in `/healthz`, the
# database checks or a short smoke test shows it, which is why it is asserted here.


def test_a_lax_cookie_between_two_provider_domains_is_reported() -> None:
    """Topology B — the deployment that produced the owner's report."""
    settings = _settings(
        BM_ENV="production",
        BM_REFRESH_COOKIE_SAMESITE="lax",
        BM_CORS_ALLOWED_ORIGINS="https://bmfrontend-seven.vercel.app",
    )
    assert settings.cross_site_refresh_cookie_origins == ["https://bmfrontend-seven.vercel.app"]


def test_samesite_none_clears_the_report() -> None:
    """The documented fix for topology B, and the one the deployment needs."""
    settings = _settings(
        BM_ENV="production",
        BM_REFRESH_COOKIE_SAMESITE="none",
        BM_CORS_ALLOWED_ORIGINS="https://bmfrontend-seven.vercel.app",
    )
    assert settings.cross_site_refresh_cookie_origins == []


def test_one_owned_domain_on_two_subdomains_is_not_reported() -> None:
    """Topology A. `lax` is correct here, and a warning would be crying wolf."""
    settings = _settings(
        BM_ENV="production",
        BM_REFRESH_COOKIE_SAMESITE="lax",
        BM_CORS_ALLOWED_ORIGINS="https://app.national-auto-spares.in",
    )
    assert settings.cross_site_refresh_cookie_origins == []


def test_a_proxied_front_end_is_not_reported() -> None:
    """Topology C. The browser sees one origin, so there is no allow-list to offend."""
    settings = _settings(
        BM_ENV="production",
        BM_REFRESH_COOKIE_SAMESITE="lax",
        BM_CORS_ALLOWED_ORIGINS="",
    )
    assert settings.cross_site_refresh_cookie_origins == []


def test_development_is_never_reported() -> None:
    """`local` drops `Secure`, so `none` is refused there and `lax` is the only option."""
    settings = _settings(
        BM_ENV="local",
        BM_REFRESH_COOKIE_SAMESITE="lax",
        BM_CORS_ALLOWED_ORIGINS="https://bmfrontend-seven.vercel.app",
    )
    assert settings.cross_site_refresh_cookie_origins == []


@pytest.mark.parametrize(
    "origin",
    [
        "https://nas.vercel.app",
        "https://nas-api.up.railway.app",
        "https://nas.netlify.app",
        "https://nas.pages.dev",
        "https://nas.onrender.com",
    ],
)
def test_every_known_provider_suffix_is_recognised(origin: str) -> None:
    settings = _settings(
        BM_ENV="production",
        BM_REFRESH_COOKIE_SAMESITE="lax",
        BM_CORS_ALLOWED_ORIGINS=origin,
    )
    assert settings.cross_site_refresh_cookie_origins == [origin]
