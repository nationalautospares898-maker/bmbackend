"""Customer tables (migration 0009).

`customers`, `customer_phones`, `customer_vehicles`.

Design points that are requirements, not preferences:

* **Only `name` is required** (FR-CUST-010). A name-only customer can receive a credit
  invoice. Any implicit UI requirement promoting an optional field to required is a
  defect, and a test asserts name-only works.
* `customers.id` is **client-generatable**, so creating a customer offline is idempotent
  and the client's id becomes the server's id with no remapping (FR-CUST-003).
* `uq_customer_phones__business_phone` is the duplicate **detector** (FR-CUST-004). It
  never merges anything — duplicates are surfaced for a person to choose.
* `customer_vehicles.vehicle_number` keeps the **entered** form because it is printed
  verbatim; the normalised form exists only for search.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from bm.infra.db.base import AMOUNT, NAME, NOTES, Base, created_at_column, updated_at_column


class Customer(Base):
    __tablename__ = "customers"

    #: Client-generatable so offline creation is idempotent.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    #: The only required field. A rename changes this label and nothing else —
    #: grouping and invoice links key on `id` (FR-CUST-011, FR-RPT-029).
    name: Mapped[str] = mapped_column(NAME, nullable=False)
    normalized_name: Mapped[str] = mapped_column(NAME, nullable=False)
    business_name: Mapped[str | None] = mapped_column(NAME, nullable=True)
    address_lines: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    default_mechanic_name: Mapped[str | None] = mapped_column(NAME, nullable=True)
    credit_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credit_limit: Mapped[Decimal | None] = mapped_column(AMOUNT, nullable=True)
    #: Soft deactivate only. A referenced customer is never hard-deleted.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    notes: Mapped[str | None] = mapped_column(NOTES, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="ck_customers__name_present"),
        CheckConstraint(
            "credit_limit IS NULL OR credit_limit >= 0", name="ck_customers__credit_limit"
        ),
        CheckConstraint(
            "credit_terms_days IS NULL OR credit_terms_days >= 0",
            name="ck_customers__credit_terms",
        ),
    )


class CustomerPhone(Base):
    __tablename__ = "customer_phones"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (
        UniqueConstraint(
            "business_id", "normalized_phone", name="uq_customer_phones__business_phone"
        ),
    )


class CustomerVehicle(Base):
    __tablename__ = "customer_vehicles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    #: As entered, and printed verbatim. Never overwritten with the normalised form.
    vehicle_number: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Uppercase, separators stripped. For search only.
    normalized_vehicle_number: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(NAME, nullable=True)


class CustomerCreditBalance(Base):
    """Derived cache (P1). **Never authoritative** — rebuildable from source at any time.

    Outstanding is always derived from invoices and valid allocations (FR-CREDIT-002);
    this table exists only to make the Credit Bills summary cheap.
    """

    __tablename__ = "customer_credit_balances"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    outstanding_total: Mapped[Decimal] = mapped_column(
        AMOUNT, nullable=False, server_default=text("0")
    )
    on_account_credit: Mapped[Decimal] = mapped_column(
        AMOUNT, nullable=False, server_default=text("0")
    )
    oldest_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    recomputed_at: Mapped[datetime] = updated_at_column()


Index(
    "ix_customer_vehicles__normalized",
    CustomerVehicle.business_id,
    CustomerVehicle.normalized_vehicle_number,
)
Index("ix_customer_phones__customer", CustomerPhone.customer_id)
