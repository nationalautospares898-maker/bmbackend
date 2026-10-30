"""identity: users, roles, permissions, memberships, devices, tokens, login attempts

Revision ID: 0003
Revises: 0002
Created: 2026-10-30

No user, role or permission row is seeded here. Seeding is migration 0016, and it covers
**permissions, system roles and units only** — never a business, tax rate, product or
demo account.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NIL_UUID = "00000000-0000-0000-0000-000000000000"
DEVICE_STATUS = postgresql.ENUM(name="device_status", create_type=False)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        # citext: `Ravi` and `ravi` are one login, enforced by the type rather than by
        # every call site remembering to lowercase.
        sa.Column("username", postgresql.CITEXT(), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("pin_hash", sa.Text(), nullable=True),
        sa.Column(
            "must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], name="fk_users__business", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("business_id", "username", name="uq_users__business_username"),
    )

    op.create_table(
        "permissions",
        sa.Column("code", sa.String(64), primary_key=True),
        sa.Column("description", sa.String(500), nullable=False),
    )

    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], name="fk_roles__business", ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "(is_system AND business_id IS NULL) OR (NOT is_system AND business_id IS NOT NULL)",
            name="ck_roles__system_scope",
        ),
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_roles__scope_code
            ON roles (coalesce(business_id, '{NIL_UUID}'::uuid), code)
        """
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("permission_code", sa.String(64), primary_key=True),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_role_permissions__role", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["permission_code"],
            ["permissions.code"],
            name="fk_role_permissions__permission",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships__user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], name="fk_memberships__business", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"], ["branches.id"], name="fk_memberships__branch", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_memberships__role", ondelete="RESTRICT"
        ),
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_memberships__user_branch_role
            ON memberships (user_id, coalesce(branch_id, '{NIL_UUID}'::uuid), role_id)
        """
    )
    op.create_index(
        "ix_memberships__user_active",
        "memberships",
        ["user_id"],
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "devices",
        # Client-generated and stable per browser profile, so a reload does not look
        # like a new device.
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("counter_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column(
            "status",
            DEVICE_STATUS,
            nullable=False,
            server_default=sa.text("'active'::device_status"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoke_reason", sa.String(500), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], name="fk_devices__business", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"], ["branches.id"], name="fk_devices__branch", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["counter_id"], ["counters.id"], name="fk_devices__counter", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"], ["users.id"], name="fk_devices__revoker", ondelete="SET NULL"
        ),
        # A revoked device must carry the moment it was revoked; a half-revoked row
        # would make the audit trail unreadable.
        sa.CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)", name="ck_devices__revoke_fields"
        ),
    )
    op.create_index("ix_devices__business_status", "devices", ["business_id", "status"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Reuse detection revokes the whole family: a consumed token presented again
        # means the cookie was copied (FR-AUTH-002).
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        # SHA-256 hex of the opaque token. The token itself only ever exists in the
        # browser's HttpOnly cookie.
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_refresh_tokens__user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"], name="fk_refresh_tokens__device", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["refresh_tokens.id"],
            name="fk_refresh_tokens__replaced_by",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens__hash"),
    )
    op.create_index("ix_refresh_tokens__family", "refresh_tokens", ["family_id"])
    op.create_index("ix_refresh_tokens__user_device", "refresh_tokens", ["user_id", "device_id"])

    op.create_table(
        "login_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("username_attempted", postgresql.CITEXT(), nullable=True),
        # HMAC with a server pepper, never a raw address (`SECURITY.md §2.4`).
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_login_attempts__business",
            ondelete="SET NULL",
        ),
    )
    op.execute(
        "CREATE INDEX ix_login_attempts__username_time"
        " ON login_attempts (username_attempted, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_login_attempts__ip_time ON login_attempts (ip_hash, created_at DESC)"
    )


def downgrade() -> None:
    op.drop_table("login_attempts")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_devices__business_status", table_name="devices")
    op.drop_table("devices")
    op.drop_index("ix_memberships__user_active", table_name="memberships")
    op.execute("DROP INDEX IF EXISTS uq_memberships__user_branch_role")
    op.drop_table("memberships")
    op.drop_table("role_permissions")
    op.execute("DROP INDEX IF EXISTS uq_roles__scope_code")
    op.drop_table("roles")
    op.drop_table("permissions")
    op.drop_table("users")
