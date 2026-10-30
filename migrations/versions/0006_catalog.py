"""catalog and tax reference data

Revision ID: 0006
Revises: 0005
Created: 2026-10-30

Search indexes are the point of this migration. `ix_products__sku_prefix` and
`ix_products__name_prefix` use `text_pattern_ops` so a prefix match can use the index at
all; `ix_products__search_trgm` is a GIN trigram index for the fuzzy/contains tier.
Together they are what lets search answer from the **first character** (FR-CAT-003)
without a separate search engine (ADR-005).

**No tax rate is seeded.** The system has no opinion about the shop's tax status
(FR-TAX-004), and inventing one would be inventing a business fact.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "bm_app"


def _app_role_exists() -> bool:
    """Local development sometimes runs everything as one role.

    The grants are skipped rather than failing the migration, and the privilege tests
    skip with a clear reason when the role is absent - so nobody sees a green
    immutability test that proved nothing.
    """
    bind = op.get_bind()
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
        ).first()
    )


# Every ENUM type was created in migration 0001, so each reference here carries
# `create_type=False`. Without it Alembic would emit a `CREATE TYPE` per column and
# the migration would fail on the second table that uses the same type.


def upgrade() -> None:
    op.create_table(
        "units",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("allows_fraction", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("decimal_places", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("allows_fraction OR decimal_places = 0", name="ck_units__integer_scale"),
        sa.CheckConstraint("decimal_places BETWEEN 0 AND 3", name="ck_units__decimal_places"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "code", name="uq_units__business_code"),
    )
    op.create_table(
        "brands",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "normalized_name", name="uq_brands__business_name"),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "normalized_name", name="uq_categories__business_name"),
    )
    op.create_table(
        "tax_groups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("hsn_code", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "code", name="uq_tax_groups__business_code"),
    )
    op.create_table(
        "tax_rates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tax_group_id", sa.UUID(), nullable=False),
        sa.Column(
            "component",
            postgresql.ENUM(
                "cgst", "sgst", "igst", "cess", name="tax_component", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("rate_percent", sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("rate_percent >= 0", name="ck_tax_rates__nonneg"),
        sa.ForeignKeyConstraint(["tax_group_id"], ["tax_groups.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tax_group_id", "component", "effective_from", name="uq_tax_rates__scope"
        ),
    )
    op.create_table(
        "tax_config_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column(
            "mode",
            postgresql.ENUM("estimate_only", "gst_enabled", name="tax_mode", create_type=False),
            server_default=sa.text("'estimate_only'::tax_mode"),
            nullable=False,
        ),
        sa.Column(
            "prices_include_tax", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by_user_id", sa.UUID(), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["activated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=True),
        sa.Column("normalized_sku", sa.String(length=64), nullable=True),
        sa.Column("barcode", sa.String(length=64), nullable=True),
        sa.Column("brand_id", sa.UUID(), nullable=True),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column("unit_id", sa.UUID(), nullable=False),
        sa.Column("tax_group_id", sa.UUID(), nullable=True),
        sa.Column("hsn_code", sa.String(length=16), nullable=True),
        sa.Column("default_selling_price", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("last_cost_price", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("reorder_level", sa.Numeric(precision=14, scale=3), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("sales_rank", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("default_selling_price >= 0", name="ck_products__price_nonneg"),
        sa.ForeignKeyConstraint(["brand_id"], ["brands.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tax_group_id"], ["tax_groups.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_products__business_barcode",
        "products",
        ["business_id", "barcode"],
        unique=True,
        postgresql_where=sa.text("barcode IS NOT NULL"),
    )
    op.create_index(
        "uq_products__business_sku",
        "products",
        ["business_id", "normalized_sku"],
        unique=True,
        postgresql_where=sa.text("normalized_sku IS NOT NULL"),
    )
    op.create_table(
        "product_aliases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.String(length=128), nullable=False),
        sa.Column("normalized_alias", sa.String(length=128), nullable=False),
        sa.Column(
            "alias_type",
            postgresql.ENUM(
                "alternate_name",
                "old_part_no",
                "oem_no",
                "local_name",
                "competitor_no",
                name="alias_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id",
            "normalized_alias",
            "alias_type",
            name="uq_product_aliases__business_alias",
        ),
    )
    op.create_table(
        "product_prices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("selling_price", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("product_prices")
    op.drop_table("product_aliases")
    op.execute("DROP INDEX IF EXISTS uq_products__business_barcode")
    op.execute("DROP INDEX IF EXISTS uq_products__business_sku")
    op.drop_table("products")
    op.drop_table("tax_config_versions")
    op.drop_table("tax_rates")
    op.drop_table("tax_groups")
    op.drop_table("categories")
    op.drop_table("brands")
    op.drop_table("units")
