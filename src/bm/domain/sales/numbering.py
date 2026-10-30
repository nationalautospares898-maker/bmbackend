"""Internal sale-reference allocation (M9).

⚠️ **This produces an INTERNAL reference only.** [OD-3] is re-answered — *"No need of
invoice numbering."* The value must never be rendered to a customer and must **not**
appear in `print_payload`. It is retained for audit, reconciliation, stable ordering,
idempotency recovery and the OD-25 contingency (if law later requires a visible serial,
this mechanism already satisfies it).

Mechanism: a single `invoice_series` row locked `FOR UPDATE` **inside** the finalisation
transaction (ADR-009). Not a sequence — a sequence increments outside transactions, so a
rolled-back sale would consume a number.

Financial-year rollover is handled by **lazy creation inside the same transaction**
(`P2-D-09`): `INSERT … ON CONFLICT DO NOTHING`, then `SELECT … FOR UPDATE`. The first
sale after 1 April therefore cannot fail for want of a row, and two concurrent
first-sales cannot create conflicting rows — the loser of the insert race simply reads
the winner's row.

Gaplessness follows from in-transaction allocation and is the implemented behaviour, but
it is **not** an owner-approved guarantee and **not** a release criterion while no
customer-visible serial exists.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from bm.domain.shared.clock import financial_year
from bm.infra.db.enums import DocumentKind
from bm.infra.db.models.sales import InvoiceSeries

#: Placeholder series code. `NAS` is **not** an owner-supplied business identity — OD-4
#: remains open, and this string is never printed.
DEFAULT_SERIES_CODE = "NAS"

#: `{fy}` and `{seq}` are substituted; the width keeps references sortable as text.
DEFAULT_PREFIX_TEMPLATE = "NAS/{fy}/{seq:05d}"


@dataclass(frozen=True, slots=True)
class AllocatedReference:
    series_id: uuid.UUID
    sequence_number: int
    #: The formatted internal reference, e.g. `NAS/2026-27/00412`.
    reference: str
    financial_year: str
    document_kind: str


class NumberingService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def allocate(
        self,
        *,
        business_id: uuid.UUID,
        branch_id: uuid.UUID,
        now: datetime,
        timezone: str,
        financial_year_start_month: int,
        series_code: str = DEFAULT_SERIES_CODE,
        document_kind: DocumentKind = DocumentKind.ESTIMATE,
    ) -> AllocatedReference:
        """Allocate the next reference, holding the series row for the minimum time.

        Called **after** stock has been verified (`Blueprint §5.1` property 5), so a
        stock failure consumes no number and the hot row is held only for a single-row
        read and update.
        """
        fy = financial_year(now, timezone, financial_year_start_month)

        # Lazy creation. `ON CONFLICT DO NOTHING` makes two concurrent first-sales safe:
        # exactly one row ends up existing and both proceed to lock it.
        self._session.execute(
            text(
                "INSERT INTO invoice_series"
                " (id, business_id, branch_id, series_code, financial_year,"
                "  prefix_template, next_number, document_kind, is_active)"
                " VALUES (gen_random_uuid(), :business_id, :branch_id, :series_code, :fy,"
                "         :prefix_template, 1, CAST(:document_kind AS document_kind), true)"
                " ON CONFLICT (business_id, branch_id, series_code, financial_year)"
                " DO NOTHING"
            ),
            {
                "business_id": business_id,
                "branch_id": branch_id,
                "series_code": series_code,
                "fy": fy,
                "prefix_template": DEFAULT_PREFIX_TEMPLATE,
                "document_kind": str(document_kind),
            },
        )

        row = self._session.execute(
            select(
                InvoiceSeries.id,
                InvoiceSeries.next_number,
                InvoiceSeries.prefix_template,
                InvoiceSeries.document_kind,
            )
            .where(
                InvoiceSeries.business_id == business_id,
                InvoiceSeries.branch_id == branch_id,
                InvoiceSeries.series_code == series_code,
                InvoiceSeries.financial_year == fy,
            )
            .with_for_update()
        ).first()

        if row is None:  # pragma: no cover - the insert above guarantees a row
            raise RuntimeError("invoice series row missing after lazy creation")

        series_id, sequence_number, template, kind = row

        self._session.execute(
            text(
                "UPDATE invoice_series SET next_number = next_number + 1,"
                " updated_at = now() WHERE id = :id"
            ),
            {"id": series_id},
        )

        return AllocatedReference(
            series_id=series_id,
            sequence_number=sequence_number,
            reference=self.format_reference(template, fy=fy, sequence=sequence_number),
            financial_year=fy,
            document_kind=str(kind),
        )

    @staticmethod
    def format_reference(template: str, *, fy: str, sequence: int) -> str:
        """Render the internal reference.

        A malformed template must not take down a sale, so an unusable template falls
        back to a readable form rather than raising inside the finalisation transaction.
        """
        try:
            return template.format(fy=fy, seq=sequence)
        except (KeyError, IndexError, ValueError):
            return f"{DEFAULT_SERIES_CODE}/{fy}/{sequence:05d}"

    def peek_next(
        self, *, business_id: uuid.UUID, branch_id: uuid.UUID, financial_year_label: str
    ) -> int | None:
        """Read `next_number` **without** locking. Diagnostics and reconciliation only —
        never used to predict a reference for a client (ADR-009)."""
        return self._session.execute(
            select(InvoiceSeries.next_number).where(
                InvoiceSeries.business_id == business_id,
                InvoiceSeries.branch_id == branch_id,
                InvoiceSeries.financial_year == financial_year_label,
            )
        ).scalar_one_or_none()
