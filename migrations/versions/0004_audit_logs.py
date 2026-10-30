"""audit_logs, append-only at the privilege level

Revision ID: 0004
Revises: 0003
Created: 2026-10-30

The `REVOKE` ships **in the same migration as the table**. M2's risk note is explicit
about why: applying grants in a later migration would leave a window in which
immutability is untested, and a window is all a bug needs.

This is the control that makes T-1 (a cashier editing history) and T-14 (audit
tampering) structural rather than a matter of code discipline. `tests/invariants/`
proves it by attempting an `UPDATE` as the application role and asserting it fails.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "bm_app"


def _app_role_exists() -> bool:
    """Local development sometimes runs everything as one role.

    The grants are skipped rather than failing the migration, and the invariant test
    skips with a clear message when the role is absent — so a developer never sees a
    green immutability test that proved nothing.
    """
    bind = op.get_bind()
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
        ).first()
    )


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("counter_id", postgresql.UUID(as_uuid=True), nullable=True),
        # The audit-action vocabulary (`sale.finalized`, `inventory.oversell_override`)
        # is deliberately spelled differently from the permission and settings
        # vocabularies for the same concept — `DATA-MODEL.md §3.14` is authoritative.
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("before_value", postgresql.JSONB(), nullable=True),
        sa.Column("after_value", postgresql.JSONB(), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], name="fk_audit_logs__business", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"], ["branches.id"], name="fk_audit_logs__branch", ondelete="RESTRICT"
        ),
        # RESTRICT, not SET NULL: deactivating a user must never be able to orphan the
        # record of what they did.
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name="fk_audit_logs__actor", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"], name="fk_audit_logs__device", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["counter_id"], ["counters.id"], name="fk_audit_logs__counter", ondelete="RESTRICT"
        ),
    )

    op.execute(
        "CREATE INDEX ix_audit_logs__entity ON audit_logs (entity_type, entity_id, created_at DESC)"
    )
    op.execute("CREATE INDEX ix_audit_logs__actor ON audit_logs (actor_user_id, created_at DESC)")
    op.execute(
        "CREATE INDEX ix_audit_logs__action ON audit_logs (business_id, action, created_at DESC)"
    )

    if _app_role_exists():
        op.execute(f"GRANT SELECT, INSERT ON audit_logs TO {APP_ROLE}")
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("audit_logs")
