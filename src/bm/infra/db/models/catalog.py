"""Catalog and tax tables (migrations 0006–0007).

`units`, `brands`, `categories`, `tax_groups`, `tax_rates`, `tax_config_versions`,
`products`, `product_aliases`, `product_prices`.

Search design (`ARCHITECTURE.md §8`): `products.search_text` concatenates the normalised
name, SKU, brand and aliases, and is **maintained by the application** rather than by a
trigger, so the normalisation rule lives in one testable place. Fix **S-8** is the
consequence: inserting an alias must refresh the parent's `search_text`, or alias
searches silently stop working as the catalog grows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from bm.infra.db.base import (
    NAME,
    PERCENT,
    QUANTITY,
    SHORT_TEXT,
    UNIT_PRICE,
    Base,
    created_at_column,
    updated_at_column,
)
from bm.infra.db.enums import ALIAS_TYPE, TAX_COMPONENT, TAX_MODE


class Unit(Base):
    """`allows_fraction` is what makes E-18 enforceable: a `SET` cannot be sold in
    halves, engine oil by the litre can."""

    __tablename__ = "units"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(NAME, nullable=False)
    allows_fraction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    decimal_places: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint("business_id", "code", name="uq_units__business_code"),
        CheckConstraint("decimal_places BETWEEN 0 AND 3", name="ck_units__decimal_places"),
        CheckConstraint("allows_fraction OR decimal_places = 0", name="ck_units__integer_scale"),
    )


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(NAME, nullable=False)
    normalized_name: Mapped[str] = mapped_column(NAME, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint("business_id", "normalized_name", name="uq_brands__business_name"),
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(NAME, nullable=False)
    normalized_name: Mapped[str] = mapped_column(NAME, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint("business_id", "normalized_name", name="uq_categories__business_name"),
    )


class TaxConfigVersion(Base):
    """Created but unused while `tax.mode = estimate_only` (`DATA-MODEL.md §3.15`).

    It exists now so enabling GST later is configuration and data entry, not a schema
    migration on a live financial database.
    """

    __tablename__ = "tax_config_versions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    mode: Mapped[str] = mapped_column(
        TAX_MODE, nullable=False, server_default=text("'estimate_only'::tax_mode")
    )
    prices_include_tax: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    effective_from: Mapped[datetime] = created_at_column()
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(SHORT_TEXT, nullable=True)


class TaxGroup(Base):
    __tablename__ = "tax_groups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(NAME, nullable=False)
    #: Never invented. Null until the owner's accountant supplies it.
    hsn_code: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (UniqueConstraint("business_id", "code", name="uq_tax_groups__business_code"),)


class TaxRate(Base):
    """**No rates are seeded.** The system has no opinion about the shop's tax status
    (FR-TAX-004)."""

    __tablename__ = "tax_rates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tax_group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tax_groups.id", ondelete="RESTRICT"), nullable=False
    )
    component: Mapped[str] = mapped_column(TAX_COMPONENT, nullable=False)
    rate_percent: Mapped[Decimal] = mapped_column(PERCENT, nullable=False)
    effective_from: Mapped[datetime] = created_at_column()
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tax_group_id", "component", "effective_from", name="uq_tax_rates__scope"),
        CheckConstraint("rate_percent >= 0", name="ck_tax_rates__nonneg"),
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    #: As printed on the bill.
    name: Mapped[str] = mapped_column(NAME, nullable=False)
    #: lower + unaccent + collapsed whitespace.
    normalized_name: Mapped[str] = mapped_column(NAME, nullable=False)
    #: Part code as entered.
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: lower, separators (`-`, `/`, `.`, space) stripped — so `JK-123`, `jk 123`,
    #: `jk123` and `JK.123` all match the same product (FR-CAT-006).
    normalized_sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)

    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="RESTRICT"), nullable=True
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True
    )
    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("units.id", ondelete="RESTRICT"), nullable=False
    )
    tax_group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tax_groups.id", ondelete="RESTRICT"), nullable=True
    )
    hsn_code: Mapped[str | None] = mapped_column(String(16), nullable=True)

    default_selling_price: Mapped[Decimal] = mapped_column(UNIT_PRICE, nullable=False)
    #: Visible only with `catalog.cost.read`, and **omitted** from the response rather
    #: than nulled, so a careless client render cannot leak it (FR-CAT-004).
    last_cost_price: Mapped[Decimal | None] = mapped_column(UNIT_PRICE, nullable=True)
    reorder_level: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    #: name + sku + brand + aliases, normalised; maintained by the application.
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Periodically refreshed; a stable tie-break for search ranking.
    sales_rank: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        CheckConstraint("default_selling_price >= 0", name="ck_products__price_nonneg"),
        Index(
            "uq_products__business_sku",
            "business_id",
            "normalized_sku",
            unique=True,
            postgresql_where=text("normalized_sku IS NOT NULL"),
        ),
        Index(
            "uq_products__business_barcode",
            "business_id",
            "barcode",
            unique=True,
            postgresql_where=text("barcode IS NOT NULL"),
        ),
    )


class ProductAlias(Base):
    """Alias rows are joined, not duplicated into the product row, so editing an alias
    does not rewrite products (`ARCHITECTURE.md §8`)."""

    __tablename__ = "product_aliases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(128), nullable=False)
    alias_type: Mapped[str] = mapped_column(ALIAS_TYPE, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "normalized_alias",
            "alias_type",
            name="uq_product_aliases__business_alias",
        ),
    )


class ProductPrice(Base):
    """Price history (P2). **Invoices never read this table** — they carry their own
    immutable snapshot, so a later price change cannot rewrite a past bill."""

    __tablename__ = "product_prices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    selling_price: Mapped[Decimal] = mapped_column(UNIT_PRICE, nullable=False)
    effective_from: Mapped[datetime] = created_at_column()
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(SHORT_TEXT, nullable=True)
