"""Tenancy and administration tables (migration 0002).

`businesses`, `branches`, `counters`, `settings`.

Two things here are easy to get wrong and are therefore constrained in the database:

* `businesses.legal_name` is **nullable**. The owner has not supplied it, and inventing
  a legal name is explicitly forbidden (OD-4). A blank value blocks go-live, not startup.
* `settings` uniqueness has to treat a NULL `branch_id` as a real scope value, because
  "business-wide" and "branch-specific" are different rows for the same key. A plain
  `UNIQUE (business_id, branch_id, key)` would let two business-wide rows coexist, since
  NULLs do not compare equal — so the index is on `coalesce(branch_id, <nil uuid>)`
  (fix S-3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bm.infra.db.base import NAME, Base, created_at_column, updated_at_column

#: Stand-in for "no branch" inside the settings scope index. A sentinel is needed
#: because SQL NULLs do not compare equal to each other.
NIL_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")

#: Scope expressions for the unique indexes above, written as literal SQL rather than
#: interpolated. `tests/structural/test_module_boundaries.py` forbids any f-string
#: inside `text()`, with no exception for "but this value is trusted" — a rule with an
#: exception is a rule that gets argued about at the wrong moment.
NIL_BRANCH_SCOPE_SQL = "coalesce(branch_id, '00000000-0000-0000-0000-000000000000'::uuid)"
NIL_BUSINESS_SCOPE_SQL = "coalesce(business_id, '00000000-0000-0000-0000-000000000000'::uuid)"


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    #: Nullable until the owner supplies it (OD-4). Never defaulted, never invented.
    legal_name: Mapped[str | None] = mapped_column(NAME, nullable=True)
    #: Invoice heading, e.g. `NATIONAL AUTO SPARES`. Owner-supplied.
    display_name: Mapped[str] = mapped_column(NAME, nullable=False)
    address_lines: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    phone_numbers: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    email: Mapped[str | None] = mapped_column(NAME, nullable=True)

    #: GSTIN when confirmed. Null while `tax.mode = estimate_only`. Never invented.
    tax_identifier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tax_identifier_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(8), nullable=True)

    #: Drives invoice-series financial-year rollover. April start for India.
    financial_year_start_month: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("4")
    )
    #: T3-02. The financial-year boundary and report day boundaries are evaluated in
    #: business-local time, not UTC: a sale at 23:59 on 31 March and one at 00:01 on
    #: 1 April must land in different financial years.
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'Asia/Kolkata'")
    )

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        CheckConstraint(
            "financial_year_start_month BETWEEN 1 AND 12",
            name="ck_businesses__fy_start_month",
        ),
    )


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(NAME, nullable=False)
    address_lines: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (UniqueConstraint("business_id", "code", name="uq_branches__business_code"),)


class Counter(Base):
    __tablename__ = "counters"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(NAME, nullable=False)

    #: C-5 is a floor, not a default: at least 20 active draft bills per counter. The
    #: CHECK stops a well-meaning configuration change from breaking the requirement.
    max_open_drafts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("20"))
    #: Optional per-counter invoice series — the documented escalation path if the
    #: single series row ever becomes a contention point (`ARCHITECTURE.md §5.4`).
    #: Must not be used without re-checking OD-25 first.
    #:
    #: The foreign key is named explicitly because it is **added later**, by migration
    #: `0010`: `counters` is created in `0002`, before `invoice_series` exists, so the
    #: constraint cannot be declared at table-creation time. It was missing from this
    #: model until the 30 October 2026 audit, which is why `alembic check` proposed
    #: dropping it — autogenerate compares against metadata, and metadata did not know
    #: the constraint existed.
    invoice_series_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "invoice_series.id",
            ondelete="SET NULL",
            name="fk_counters__invoice_series",
            use_alter=True,
        ),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint("branch_id", "code", name="uq_counters__branch_code"),
        CheckConstraint("max_open_drafts >= 20", name="ck_counters__min_drafts"),
    )


class Setting(Base):
    """Business rules live here, not in environment variables.

    The owner must be able to change tax mode, draft limits, credit terms, discount
    caps, the cancellation window and invoice prefixes without a deploy
    (`CLAUDE.md §11`). Defaults are applied in code rather than seeded as rows, so an
    unset key is never ambiguous — there is exactly one place that knows the default.
    """

    __tablename__ = "settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    #: Null means business-wide.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=True
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    value_type: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        # S-3: expression unique index so a NULL branch_id is a comparable scope value.
        Index(
            "uq_settings__scope_key",
            "business_id",
            text(NIL_BRANCH_SCOPE_SQL),
            "key",
            unique=True,
        ),
    )
