"""NFR-DATA-006 — append-only tables are enforced by database privilege.

These tests connect as the **application role** and attempt the mutation the design
forbids. A passing test means the database refused it, not that the code chose not to
try. That distinction is the whole point: T-1 (a cashier editing history) and T-14 (audit
tampering) must be structural, not a matter of code discipline.

If `bm_app` does not exist as a separate role, every test here **skips with a reason**.
A green run that proved nothing would be worse than a red one.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.db


@pytest.fixture
def seeded_business(migration_session: Session) -> uuid.UUID:
    """Reference data created with a raw insert, which the fixture policy allows."""
    business_id = uuid.uuid4()
    migration_session.execute(
        text("INSERT INTO businesses (id, display_name) VALUES (:id, 'TEST BUSINESS')"),
        {"id": business_id},
    )
    migration_session.commit()
    yield business_id
    migration_session.execute(
        text("DELETE FROM audit_logs WHERE business_id = :id"), {"id": business_id}
    )
    migration_session.execute(text("DELETE FROM businesses WHERE id = :id"), {"id": business_id})
    migration_session.commit()


def _insert_audit_row(session: Session, business_id: uuid.UUID) -> uuid.UUID:
    row_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO audit_logs (id, business_id, action, entity_type)"
            " VALUES (:id, :business_id, 'test.action', 'test')"
        ),
        {"id": row_id, "business_id": business_id},
    )
    session.commit()
    return row_id


def test_app_role_can_insert_an_audit_row(
    app_session: Session, app_role_present: bool, seeded_business: uuid.UUID
) -> None:
    """The grant has to permit INSERT — an audit row is written in the same transaction
    as the change it describes, so refusing INSERT would break every write path."""
    if not app_role_present:
        pytest.skip("bm_app role is absent; privilege enforcement cannot be verified")
    row_id = _insert_audit_row(app_session, seeded_business)
    assert row_id is not None


def test_app_role_cannot_update_an_audit_row(
    app_session: Session,
    migration_session: Session,
    app_role_present: bool,
    seeded_business: uuid.UUID,
) -> None:
    if not app_role_present:
        pytest.skip("bm_app role is absent; privilege enforcement cannot be verified")
    row_id = _insert_audit_row(migration_session, seeded_business)

    with pytest.raises(ProgrammingError) as caught:
        app_session.execute(
            text("UPDATE audit_logs SET reason = 'tampered' WHERE id = :id"), {"id": row_id}
        )
    app_session.rollback()
    assert "permission denied" in str(caught.value).lower()


def test_app_role_cannot_delete_an_audit_row(
    app_session: Session,
    migration_session: Session,
    app_role_present: bool,
    seeded_business: uuid.UUID,
) -> None:
    if not app_role_present:
        pytest.skip("bm_app role is absent; privilege enforcement cannot be verified")
    row_id = _insert_audit_row(migration_session, seeded_business)

    with pytest.raises(ProgrammingError) as caught:
        app_session.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": row_id})
    app_session.rollback()
    assert "permission denied" in str(caught.value).lower()


def test_app_role_cannot_truncate_audit_logs(app_session: Session, app_role_present: bool) -> None:
    """TRUNCATE bypasses row-level DELETE, so it is revoked separately."""
    if not app_role_present:
        pytest.skip("bm_app role is absent; privilege enforcement cannot be verified")
    with pytest.raises(ProgrammingError):
        app_session.execute(text("TRUNCATE audit_logs"))
    app_session.rollback()


def test_app_role_cannot_create_a_table(app_session: Session, app_role_present: bool) -> None:
    """The application role owns no DDL privileges (`SECURITY.md §7`)."""
    if not app_role_present:
        pytest.skip("bm_app role is absent; privilege enforcement cannot be verified")
    with pytest.raises(ProgrammingError):
        app_session.execute(text("CREATE TABLE should_not_exist (id int)"))
    app_session.rollback()
