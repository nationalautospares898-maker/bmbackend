"""Declarative base and the shared column types.

Money and quantity precision is fixed here rather than repeated per column, because
getting one of them wrong is a silent financial bug:

| Concept     | Type            | Why |
|-------------|-----------------|-----|
| Amount      | `NUMERIC(14,2)` | Rupees and paise; `ck_invoices__total` reconciles in this scale |
| Unit price  | `NUMERIC(14,4)` | Per-ml / per-gram pricing needs four places |
| Quantity    | `NUMERIC(14,3)` | Integer-only units are enforced in the domain, not the column |
| Percentage  | `NUMERIC(6,3)`  | |

**Never** `float`, `real`, `double precision` or `money` (NFR-DATA-009). A test inspects
`information_schema` to prove no float column exists.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from sqlalchemy import DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column, registry


class Base(DeclarativeBase):
    """All ORM models inherit from this.

    ORM models live inside `infra` and never leave a repository as an API response type
    (`ARCHITECTURE.md §3` rule 4).
    """

    registry = registry(
        type_annotation_map={
            datetime: DateTime(timezone=True),
            str: Text(),
            uuid.UUID: PgUUID(as_uuid=True),
        }
    )


# --- reusable column annotations ---------------------------------------------

#: Primary key. UUIDs are client-generatable for drafts, draft items and customers so
#: offline creation is idempotent (`DATA-MODEL.md §1`).
uuid_pk = Annotated[
    uuid.UUID,
    mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
]

#: Monetary amount — rupees and paise. `ck_invoices__total` reconciles in this scale.
AMOUNT = Numeric(14, 2)
#: Unit price — four places, because per-ml and per-gram pricing needs them.
UNIT_PRICE = Numeric(14, 4)
#: Quantity. Integer-only units are enforced in the domain, not by the column.
QUANTITY = Numeric(14, 3)
#: Percentage.
PERCENT = Numeric(6, 3)

#: Annotated forms for use as `Mapped[Decimal]` column types.
AmountCol = Annotated[Decimal, mapped_column(AMOUNT)]
UnitPriceCol = Annotated[Decimal, mapped_column(UNIT_PRICE)]
QuantityCol = Annotated[Decimal, mapped_column(QUANTITY)]
PercentCol = Annotated[Decimal, mapped_column(PERCENT)]

#: Bounded free text, so a hostile or careless client cannot grow a row unboundedly
#: (`SECURITY.md §4`).
NAME = String(200)
SHORT_TEXT = String(500)
NOTES = String(2000)


def created_at_column() -> MappedColumn[datetime]:
    """Server time is authoritative; client-supplied times live in separate
    `client_*` columns (`DATA-MODEL.md §1`)."""
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def updated_at_column() -> MappedColumn[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
