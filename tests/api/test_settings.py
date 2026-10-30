"""M4 — settings: defaults, validation, scoping and audit."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import SystemRole
from bm.domain.settings.defaults import SPECS, SettingKey
from tests.fixtures import Tenant, UserFixture, create_user

pytestmark = pytest.mark.db


@pytest.fixture
def owner(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(
        migration_session, tenant, username="owner", role=SystemRole.OWNER, all_branches=True
    )


@pytest.fixture
def cashier(migration_session: Session, tenant: Tenant) -> UserFixture:
    return create_user(migration_session, tenant, username="ravi")


def _headers(client: TestClient, user: UserFixture) -> dict[str, str]:
    token = client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": user.password,
            "device": {"id": str(user.device_id), "label": "PC"},
        },
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Device-Id": str(user.device_id)}


def test_every_documented_setting_is_listed_with_its_default(
    api_client: TestClient, owner: UserFixture
) -> None:
    """Defaults are applied in code, not seeded as rows, so an unset key is never
    ambiguous (`DATA-MODEL.md §3.1`)."""
    response = api_client.get("/api/v1/admin/settings", headers=_headers(api_client, owner))
    assert response.status_code == 200, response.text
    items = {item["key"]: item for item in response.json()}

    assert set(items) == {str(key) for key in SPECS}
    for item in items.values():
        assert item["is_default"] is True
        assert item["description"]


def test_the_documented_defaults_are_the_values_in_force(
    api_client: TestClient, owner: UserFixture
) -> None:
    """A silent change to any of these changes the owner's business rules."""
    items = {
        item["key"]: item["value"]
        for item in api_client.get(
            "/api/v1/admin/settings", headers=_headers(api_client, owner)
        ).json()
    }
    assert items["tax.mode"] == "estimate_only"
    assert items["drafts.max_open_per_counter"] == 20
    assert items["sales.cancel_window_hours"] == 24
    assert items["sales.rounding_mode"] == "reject_fractional"
    assert items["sales.round_grand_total_to_rupee"] is False
    assert items["credit.default_terms_days"] == 30
    assert items["credit.enforce_limit"] is False
    assert items["discount.cashier_max_percent"] == "0"
    assert items["inventory.allow_oversell_override"] is True
    assert items["returns.window_days"] == 30
    assert items["print.date_format"] == "MM-DD-YYYY"
    assert items["print.paper_profile"] == "a5_portrait"


def test_updating_a_setting_persists_and_stops_being_a_default(
    api_client: TestClient, owner: UserFixture
) -> None:
    headers = _headers(api_client, owner)
    response = api_client.put(
        "/api/v1/admin/settings/credit.default_terms_days",
        headers=headers,
        json={"value": 45},
    )
    assert response.status_code == 200, response.text
    assert response.json()["value"] == 45
    assert response.json()["is_default"] is False

    items = {
        item["key"]: item
        for item in api_client.get("/api/v1/admin/settings", headers=headers).json()
    }
    assert items["credit.default_terms_days"]["value"] == 45
    assert items["credit.default_terms_days"]["is_default"] is False


def test_a_setting_change_is_audited_with_before_and_after(
    api_client: TestClient, migration_session: Session, owner: UserFixture
) -> None:
    """FR-AUDIT-003. A discount cap moves money, so who changed it has to be
    answerable."""
    api_client.put(
        "/api/v1/admin/settings/discount.cashier_max_percent",
        headers=_headers(api_client, owner),
        json={"value": "5"},
    )
    row = migration_session.execute(
        text(
            "SELECT before_value, after_value FROM audit_logs"
            " WHERE action = 'setting.changed' ORDER BY created_at DESC LIMIT 1"
        )
    ).first()
    assert row is not None
    before, after = row
    assert before["value"] == "0"
    assert after["value"] == "5"


def test_changing_tax_mode_uses_its_own_audit_action(
    api_client: TestClient, migration_session: Session, owner: UserFixture
) -> None:
    """Enabling GST is the production readiness gate (FR-TAX-003), so it must be
    greppable in the audit log on its own."""
    api_client.put(
        "/api/v1/admin/settings/tax.mode",
        headers=_headers(api_client, owner),
        json={"value": "estimate_only"},
    )
    actions = migration_session.execute(text("SELECT action FROM audit_logs")).scalars().all()
    assert "tax.mode_changed" in actions


def test_tax_mode_cannot_be_switched_to_gst_through_this_endpoint(
    api_client: TestClient, owner: UserFixture
) -> None:
    """`gst_enabled` is never set implicitly. It requires the gated
    `POST /admin/tax/enable-gst` with its configuration checklist."""
    response = api_client.put(
        "/api/v1/admin/settings/tax.mode",
        headers=_headers(api_client, owner),
        json={"value": "gst_enabled"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_setting_key_is_rejected(api_client: TestClient, owner: UserFixture) -> None:
    response = api_client.put(
        "/api/v1/admin/settings/not.a.real.setting",
        headers=_headers(api_client, owner),
        json={"value": 1},
    )
    assert response.status_code == 422
    assert "not a known setting" in response.json()["error"]["details"]["fields"]["key"]


def test_a_value_below_the_documented_floor_is_refused_not_clamped(
    api_client: TestClient, owner: UserFixture
) -> None:
    """C-5 makes 20 open drafts a floor. A silently clamped value would let a
    configuration change break a confirmed requirement without anyone noticing."""
    response = api_client.put(
        "/api/v1/admin/settings/drafts.max_open_per_counter",
        headers=_headers(api_client, owner),
        json={"value": 5},
    )
    assert response.status_code == 422
    assert "at least 20" in response.json()["error"]["details"]["fields"]["value"]


def test_wrong_type_is_refused(api_client: TestClient, owner: UserFixture) -> None:
    response = api_client.put(
        "/api/v1/admin/settings/credit.enforce_limit",
        headers=_headers(api_client, owner),
        json={"value": "yes"},
    )
    assert response.status_code == 422


def test_unknown_enum_value_is_refused(api_client: TestClient, owner: UserFixture) -> None:
    response = api_client.put(
        "/api/v1/admin/settings/print.paper_profile",
        headers=_headers(api_client, owner),
        json={"value": "thermal_80mm"},
    )
    assert response.status_code == 422
    # Thermal and pre-printed stock require a separately approved template (OD-6).
    assert "a5_portrait" in response.json()["error"]["details"]["fields"]["value"]


def test_a_decimal_setting_round_trips_as_a_string(
    api_client: TestClient, owner: UserFixture
) -> None:
    """A JSON number would be parsed as a float somewhere along the way (ADR-013)."""
    headers = _headers(api_client, owner)
    response = api_client.put(
        "/api/v1/admin/settings/discount.cashier_max_percent",
        headers=headers,
        json={"value": "2.500"},
    )
    assert response.status_code == 200
    assert response.json()["value"] == "2.500"


def test_a_float_decimal_setting_is_refused(api_client: TestClient, owner: UserFixture) -> None:
    response = api_client.put(
        "/api/v1/admin/settings/discount.cashier_max_percent",
        headers=_headers(api_client, owner),
        json={"value": 2.5},
    )
    assert response.status_code == 422


def test_negative_discount_cap_is_refused(api_client: TestClient, owner: UserFixture) -> None:
    response = api_client.put(
        "/api/v1/admin/settings/discount.cashier_max_percent",
        headers=_headers(api_client, owner),
        json={"value": "-5"},
    )
    assert response.status_code == 422


def test_cashier_cannot_read_or_change_settings(
    api_client: TestClient, cashier: UserFixture
) -> None:
    headers = _headers(api_client, cashier)
    assert api_client.get("/api/v1/admin/settings", headers=headers).status_code == 403
    assert (
        api_client.put(
            "/api/v1/admin/settings/credit.enforce_limit",
            headers=headers,
            json={"value": True},
        ).status_code
        == 403
    )


def test_settings_never_cross_a_business_boundary(
    api_client: TestClient, migration_session: Session, owner: UserFixture
) -> None:
    from tests.fixtures import create_tenant

    other = create_tenant(migration_session, display_name="OTHER", branch_code="OTH")
    other_owner = create_user(
        migration_session, other, username="owner-b", role=SystemRole.OWNER, all_branches=True
    )

    api_client.put(
        "/api/v1/admin/settings/credit.default_terms_days",
        headers=_headers(api_client, owner),
        json={"value": 45},
    )

    items = {
        item["key"]: item
        for item in api_client.get(
            "/api/v1/admin/settings", headers=_headers(api_client, other_owner)
        ).json()
    }
    # The other business still sees the documented default, not business A's value.
    assert items["credit.default_terms_days"]["value"] == 30
    assert items["credit.default_terms_days"]["is_default"] is True


# --- money policy derived from settings ---------------------------------------


def test_money_policy_reflects_the_v1_default(app_session: Session, tenant: Tenant) -> None:
    from bm.domain.settings.service import SettingsService
    from bm.domain.shared.money import RoundingMode

    policy = SettingsService(app_session).money_policy(tenant.business_id, tenant.branch_id)
    assert policy.version == 1
    assert policy.rounding_mode is RoundingMode.REJECT_FRACTIONAL
    assert policy.require_integral_amounts is True
    assert policy.round_grand_total_to_rupee is False
    assert policy.display_decimal_places == 0


def test_changing_the_rounding_mode_changes_the_policy_version(
    api_client: TestClient, app_session: Session, owner: UserFixture, tenant: Tenant
) -> None:
    """T3-03: the version is persisted on every invoice so a disputed historical bill
    can be explained under the rule that produced it. It must therefore change when the
    rule does."""
    from bm.domain.settings.service import SettingsService

    api_client.put(
        "/api/v1/admin/settings/sales.rounding_mode",
        headers=_headers(api_client, owner),
        json={"value": "line_half_up"},
    )
    policy = SettingsService(app_session).money_policy(tenant.business_id, tenant.branch_id)
    assert policy.version == 2
    assert policy.require_integral_amounts is False


def test_settings_keys_are_spelled_distinctly_from_permissions(
    api_client: TestClient, owner: UserFixture
) -> None:
    """`DATA-MODEL.md §3.14`: the oversell family has five deliberately different
    spellings. Using one vocabulary's spelling where another is expected is a defect."""
    keys = {
        item["key"]
        for item in api_client.get(
            "/api/v1/admin/settings", headers=_headers(api_client, owner)
        ).json()
    }
    assert "inventory.allow_oversell_override" in keys  # settings vocabulary
    assert "inventory.oversell.override" not in keys  # permission vocabulary
    assert "inventory.oversell_override" not in keys  # audit-action vocabulary


def test_every_key_in_the_enum_has_a_spec() -> None:
    """A key without a spec has no default and no validation — it would accept
    anything."""
    assert set(SPECS) == set(SettingKey)
