"""The mechanic reference list (migration 0022).

A mechanic is **reference data**, not a financial record. That distinction decides
everything about this table:

* It is **mutable**. A misspelled name can be corrected, a duplicate deactivated, and
  nobody who left is stuck in the suggestion list for ever. The schema default grants
  (`scripts/init-roles.sql`) therefore apply unchanged — there is no `REVOKE` here, and
  adding one by analogy with `invoices` would make the correct-a-typo case impossible.
* It is **not referenced by any invoice**. `invoices.mechanic_name` stays a free-text
  snapshot of what the bill said when it was issued, exactly as
  `invoice_items.product_name_snapshot` does for a product (FR-SALE-012). Because there
  is no foreign key, correcting or deactivating a mechanic here **cannot** rewrite a
  finalised bill — which is the only reason it is safe for this table to be mutable at
  all.

`normalized_name` carries the uniqueness, so `  ramesh `, `Ramesh` and `RAMESH` are one
mechanic. `name` keeps the capitalisation as first entered, because that is what a human
reads back.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from bm.infra.db.base import NAME, NOTES, Base, created_at_column, updated_at_column


class Mechanic(Base):
    __tablename__ = "mechanics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    #: As typed. Display only — never the matching key.
    name: Mapped[str] = mapped_column(NAME, nullable=False)
    #: `normalize_name(name)`. The uniqueness key, so case and spacing cannot
    #: produce a second record for one person.
    normalized_name: Mapped[str] = mapped_column(NAME, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(NOTES, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    #: Drives "most recently used first", which beats alphabetical once the list is
    #: long enough to need scrolling and three names account for most bills.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="ck_mechanics__name_present"),
        CheckConstraint(
            "length(btrim(normalized_name)) > 0", name="ck_mechanics__normalized_present"
        ),
        CheckConstraint("use_count >= 0", name="ck_mechanics__use_count"),
        UniqueConstraint("business_id", "normalized_name", name="uq_mechanics__business_name"),
        # Mirrors the three indexes migration 0022 creates, so `alembic check` reports
        # no drift. The prefix and trigram ones use operator classes the ORM cannot
        # express, hence `postgresql_using`/`postgresql_ops`.
        Index(
            "ix_mechanics__name_prefix",
            "business_id",
            "normalized_name",
            postgresql_ops={"normalized_name": "text_pattern_ops"},
        ),
        Index(
            "ix_mechanics__name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
        Index(
            "ix_mechanics__recent",
            "business_id",
            text("last_used_at DESC NULLS LAST"),
            text("use_count DESC"),
        ),
    )
