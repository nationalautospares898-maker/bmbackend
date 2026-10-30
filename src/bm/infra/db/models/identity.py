"""Identity and access tables (migration 0003).

`users`, `roles`, `permissions`, `role_permissions`, `memberships`, `devices`,
`refresh_tokens`, `login_attempts`.

Notes that matter:

* `refresh_tokens.family_id` is what makes reuse detection possible: presenting a
  consumed token revokes the **whole family**, because a consumed token in the wild means
  the cookie was copied (FR-AUTH-002).
* Only the SHA-256 **hash** of a refresh token is stored. The token itself exists only in
  the browser's `HttpOnly` cookie.
* `login_attempts.ip_hash` is an HMAC, never a raw address (`SECURITY.md §2.4`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bm.infra.db.base import NAME, SHORT_TEXT, Base, created_at_column, updated_at_column
from bm.infra.db.enums import DEVICE_STATUS
from bm.infra.db.models.tenancy import NIL_BRANCH_SCOPE_SQL, NIL_BUSINESS_SCOPE_SQL


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    #: citext so `Ravi` and `ravi` are the same login without the application having to
    #: remember to lowercase at every call site.
    username: Mapped[str] = mapped_column(CITEXT, nullable=False)
    full_name: Mapped[str] = mapped_column(NAME, nullable=False)
    #: Argon2id. The hash carries its own parameters, so a future upgrade re-hashes
    #: transparently on next login (`SECURITY.md §2.1`).
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    pin_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Seeded or admin-created accounts start true; the API refuses every non-auth
    #: endpoint until the password is changed.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint("business_id", "username", name="uq_users__business_username"),
    )


class Permission(Base):
    """Seeded from `bm.domain.auth.permissions.Permission` by migration 0016."""

    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(SHORT_TEXT, nullable=False)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    #: Null means a system role, shared by every business.
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(NAME, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        Index(
            "uq_roles__scope_code",
            text(NIL_BUSINESS_SCOPE_SQL),
            "code",
            unique=True,
        ),
        CheckConstraint(
            "(is_system AND business_id IS NULL) OR (NOT is_system AND business_id IS NOT NULL)",
            name="ck_roles__system_scope",
        ),
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("permissions.code", ondelete="RESTRICT"), primary_key=True
    )


class Membership(Base):
    """A user's role within a business, optionally narrowed to one branch."""

    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    #: Null means every branch of the business.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        Index(
            "uq_memberships__user_branch_role",
            "user_id",
            text(NIL_BRANCH_SCOPE_SQL),
            "role_id",
            unique=True,
        ),
        Index("ix_memberships__user_active", "user_id", postgresql_where=text("is_active")),
    )


class Device(Base):
    """Client-generated id, stable per browser profile.

    Revocation is the control for a stolen counter machine (T-4). A revoked device is
    refused on **every** endpoint including sync (FR-OFFLINE-010), and local data is
    never deleted in response — the client shows a blocking screen instead.
    """

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=True
    )
    counter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("counters.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped[str] = mapped_column(NAME, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(SHORT_TEXT, nullable=True)
    status: Mapped[str] = mapped_column(
        DEVICE_STATUS, nullable=False, server_default=text("'active'::device_status")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoke_reason: Mapped[str | None] = mapped_column(SHORT_TEXT, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)",
            name="ck_devices__revoke_fields",
        ),
        Index("ix_devices__business_status", "business_id", "status"),
    )


class RefreshToken(Base):
    """Opaque, rotating, device-bound. Only the hash is stored."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    #: Reuse detection revokes the whole family, not just the presented token.
    family_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = created_at_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens__hash"),
        Index("ix_refresh_tokens__family", "family_id"),
        Index("ix_refresh_tokens__user_device", "user_id", "device_id"),
    )


class LoginAttempt(Base):
    """Rate-limiting and lockout evidence. IPs are hashed, never stored raw."""

    __tablename__ = "login_attempts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True
    )
    username_attempted: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        Index(
            "ix_login_attempts__username_time",
            "username_attempted",
            text("created_at DESC"),
        ),
        Index("ix_login_attempts__ip_time", "ip_hash", text("created_at DESC")),
    )


class AuditLog(Base):
    """Append-only (migration 0004).

    `UPDATE` and `DELETE` are revoked from the application role in the **same migration
    that creates the table**, so there is never a window in which immutability is
    untested (NFR-DATA-006, T-14). Written in the same transaction as the change it
    describes, so an audited action cannot commit without its audit row.
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=True
    )
    counter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("counters.id", ondelete="RESTRICT"), nullable=True
    )
    #: e.g. `sale.finalized`, `inventory.oversell_override`, `invoice.cancelled`.
    #: ⚠️ The audit-action spelling differs from the permission and settings spellings
    #: of the same concept on purpose — `DATA-MODEL.md §3.14` is authoritative.
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reason: Mapped[str | None] = mapped_column(SHORT_TEXT, nullable=True)
    before_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    after_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        Index("ix_audit_logs__entity", "entity_type", "entity_id", text("created_at DESC")),
        Index("ix_audit_logs__actor", "actor_user_id", text("created_at DESC")),
        Index("ix_audit_logs__action", "business_id", "action", text("created_at DESC")),
    )
