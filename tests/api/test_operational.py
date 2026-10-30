"""M1 — operational endpoints and the error envelope.

`/readyz` carrying a migration-head mismatch is the one that earns its keep: ADR-016
says migrations never run on start, so a replica whose code expects a newer schema must
refuse traffic rather than serve requests against a schema it does not understand.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bm.config import Settings
from bm.infra.db import migration_state as migration_state_module
from bm.infra.db.migration_state import MigrationState
from bm.main import create_app


def _settings(**overrides: object) -> Settings:
    """Every field the tests depend on is set explicitly.

    Leaving one to the environment would make a developer's local `.env` decide whether
    an assertion holds — which is exactly how a security test quietly stops testing.
    """
    values: dict[str, object] = {
        "env": "test",
        "database_url": "postgresql+psycopg://bm_app:pw@localhost:5432/bm",
        "jwt_signing_key": "a" * 40,
        "password_pepper": "b" * 40,
        "ip_hash_pepper": "c" * 40,
        "cors_allowed_origins": "http://localhost:5173",
        "enable_api_docs": False,
        "max_body_bytes": 1_048_576,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture
def app() -> FastAPI:
    return create_app(_settings())


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions=False so the 500 handler's envelope is observable rather
    # than the exception escaping into the test.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_healthz_does_not_touch_the_database(client: TestClient) -> None:
    """Liveness must not fail because of a database blip — that would have the
    orchestrator kill a healthy process during a brief outage."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_not_ready_when_the_database_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bm.main.database_reachable", lambda _session: False)
    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "unreachable"


def test_readyz_fails_on_a_migration_head_mismatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-016. The code expects 0009; the database is at 0004. Serving traffic here
    would mean answering requests against a schema the code does not match."""
    monkeypatch.setattr("bm.main.database_reachable", lambda _session: True)
    monkeypatch.setattr(
        "bm.main.migration_state",
        lambda _session: MigrationState(expected_head="0009", current_revision="0004"),
    )
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["migrations"]["status"] == "mismatch"


def test_readyz_is_ready_when_heads_match(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bm.main.database_reachable", lambda _session: True)
    monkeypatch.setattr(
        "bm.main.migration_state",
        lambda _session: MigrationState(expected_head="0004", current_revision="0004"),
    )
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_multiple_alembic_heads_are_treated_as_not_a_known_head() -> None:
    """An unmerged migration branch must not have readiness pick one arbitrarily."""
    state = MigrationState(expected_head=None, current_revision="0004")
    assert state.matches is False


def test_expected_head_is_read_from_the_migration_directory() -> None:
    """The code's expected head is real, not a hardcoded string."""
    migration_state_module.expected_head.cache_clear()
    head = migration_state_module.expected_head()
    assert head is not None and head.isdigit()


def test_unknown_path_returns_the_project_error_envelope(client: TestClient) -> None:
    """`API-SPECIFICATION.md §4`: every non-2xx carries the envelope — including
    Starlette's own 404, which would otherwise be `{"detail": "Not Found"}`."""
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details", "request_id", "retryable"}
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["request_id"]


def test_request_id_is_echoed_when_well_formed(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-Id": "abc-123"})
    assert response.headers["X-Request-Id"] == "abc-123"


def test_malformed_request_id_is_replaced_not_echoed(client: TestClient) -> None:
    """The id reaches logs and error bodies, so an unvalidated one is a log-injection
    vector."""
    response = client.get("/healthz", headers={"X-Request-Id": "evil\nlog line"})
    assert response.headers["X-Request-Id"] != "evil\nlog line"
    assert len(response.headers["X-Request-Id"]) == 36  # a generated UUID


def test_security_headers_are_present(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "no-store" in response.headers["Cache-Control"]
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]


def test_oversized_body_is_refused_before_parsing(client: TestClient) -> None:
    response = client.post(
        "/api/v1/meta",
        content=b"x",
        headers={"Content-Length": "99999999", "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_api_docs_are_disabled_unless_explicitly_enabled() -> None:
    """`SECURITY.md §6`: `/docs` is disabled or authenticated in production."""
    app = create_app(_settings(enable_api_docs=False))
    with TestClient(app) as client:
        assert client.get("/api/v1/docs").status_code == 404


def test_api_docs_can_be_enabled_for_local_development() -> None:
    app = create_app(_settings(enable_api_docs=True))
    with TestClient(app) as client:
        assert client.get("/api/v1/docs").status_code == 200


def test_openapi_document_is_served_for_frontend_type_generation(client: TestClient) -> None:
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["version"]


def test_cors_allow_list_is_explicit_never_a_wildcard(client: TestClient) -> None:
    response = client.options(
        "/api/v1/meta",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    allow_origin = response.headers.get("access-control-allow-origin")
    assert allow_origin == "http://localhost:5173"
    assert allow_origin != "*"


def test_disallowed_origin_is_not_granted_cors(client: TestClient) -> None:
    response = client.options(
        "/api/v1/meta",
        headers={
            "Origin": "https://attacker.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") != "https://attacker.example.com"
