"""seed default units

Revision ID: 0016
Revises: 0015
Created: 2026-10-30

Seeds **units only**, and only for businesses that already exist. Units are reference
data a spare-parts shop cannot operate without (`DATA-MODEL.md §6` rule 7 permits
permissions, system roles and units; nothing else).

`allows_fraction` is the field that matters: it is what makes E-18 enforceable. A `SET`
of brake shoes cannot be sold in halves; oil by the litre can. Getting this wrong means
either refusing a legitimate sale or accepting a quantity the shop cannot dispense.

Still **never** seeded: a business, a tax rate, a product, a customer, a demo account or
a default password.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Stable ids so re-running the seed cannot create duplicates.
_UNIT_NAMESPACE = uuid.UUID("7a2d3e5f-0000-4000-8000-000000000000")

#: `(code, name, allows_fraction, decimal_places)`.
DEFAULT_UNITS: tuple[tuple[str, str, bool, int], ...] = (
    ("NOS", "Numbers", False, 0),
    ("PCS", "Pieces", False, 0),
    ("SET", "Set", False, 0),
    ("PAIR", "Pair", False, 0),
    ("BOX", "Box", False, 0),
    ("LTR", "Litre", True, 3),
    ("ML", "Millilitre", True, 0),
    ("KG", "Kilogram", True, 3),
    ("GM", "Gram", True, 0),
    ("MTR", "Metre", True, 2),
    ("FT", "Foot", True, 2),
)


def _unit_id(business_id: uuid.UUID, code: str) -> uuid.UUID:
    return uuid.uuid5(_UNIT_NAMESPACE, f"{business_id}:{code}")


def upgrade() -> None:
    bind = op.get_bind()
    businesses = [row[0] for row in bind.execute(sa.text("SELECT id FROM businesses")).all()]

    # A fresh database has no business yet, which is the normal case: the owner's
    # business is created at deployment, and `ensure_default_units` in
    # `domain/catalog/service.py` seeds units for it at that point. This migration
    # covers businesses that already exist.
    for business_id in businesses:
        for code, name, allows_fraction, decimal_places in DEFAULT_UNITS:
            bind.execute(
                sa.text(
                    "INSERT INTO units (id, business_id, code, name, allows_fraction,"
                    "                   decimal_places)"
                    " VALUES (:id, :business_id, :code, :name, :allows_fraction, :places)"
                    " ON CONFLICT (business_id, code) DO UPDATE"
                    "   SET name = EXCLUDED.name,"
                    "       allows_fraction = EXCLUDED.allows_fraction,"
                    "       decimal_places = EXCLUDED.decimal_places"
                ),
                {
                    "id": _unit_id(business_id, code),
                    "business_id": business_id,
                    "code": code,
                    "name": name,
                    "allows_fraction": allows_fraction,
                    "places": decimal_places,
                },
            )


def downgrade() -> None:
    bind = op.get_bind()
    # Only the seeded ids are removed, and a unit still referenced by a product will
    # block this through the RESTRICT foreign key — which is correct: silently
    # detaching a product from its unit would make its quantities meaningless.
    businesses = [row[0] for row in bind.execute(sa.text("SELECT id FROM businesses")).all()]
    ids = [
        _unit_id(business_id, code) for business_id in businesses for code, _, _, _ in DEFAULT_UNITS
    ]
    if ids:
        bind.execute(sa.text("DELETE FROM units WHERE id = ANY(:ids)"), {"ids": ids})
