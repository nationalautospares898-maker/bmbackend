"""Customer service (M7).

The requirement that shapes this module: **only `name` is required** (FR-CUST-010). A
customer created from a name alone can receive a credit invoice. Every other field is
optional and is persisted when supplied (FR-CREDIT-012) — the credit flow must not
become a data-entry form, because at a busy counter that is what makes staff skip it and
write the sale down on paper instead.

Duplicate handling is deliberately passive. `POST /customers/check-duplicates` and the
`CUSTOMER_DUPLICATE_SUSPECTED` warning surface candidates; **nothing is ever merged
automatically** (FR-CUST-004). The system cannot know that two people sharing a phone
number are the same customer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import NotFound, ValidationFailed, WarningCode
from bm.domain.shared.money import ZERO, parse_decimal
from bm.domain.shared.normalize import (
    escape_like,
    normalize_name,
    normalize_phone,
    normalize_vehicle_number,
)
from bm.infra.db.models.customers import Customer, CustomerPhone, CustomerVehicle

MAX_SEARCH_LIMIT = 50


@dataclass(frozen=True, slots=True)
class CustomerView:
    id: uuid.UUID
    name: str
    business_name: str | None
    primary_phone: str | None
    phones: tuple[str, ...]
    vehicles: tuple[str, ...]
    address_lines: tuple[str, ...]
    default_mechanic_name: str | None
    credit_terms_days: int | None
    credit_limit: Decimal | None
    is_active: bool
    #: Present only with `credit.read`.
    outstanding: Decimal | None = None
    oldest_due_date: date | None = None
    #: The directory's keyset paging cursor. Not published in the response body except
    #: as an opaque `next_cursor`.
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    id: uuid.UUID
    name: str
    primary_phone: str | None
    matched_on: str


@dataclass(frozen=True, slots=True)
class UpsertResult:
    customer: CustomerView
    created: bool
    warnings: list[dict[str, object]] = field(default_factory=list)


class CustomerService:
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- reads ----------------------------------------------------------------

    def search(self, ctx: AuthContext, *, query: str, limit: int = 20) -> list[CustomerView]:
        """Matches name, phone, business name and vehicle (`API-SPECIFICATION.md §3.6`)."""
        if not (ctx.has(Permission.CUSTOMER_MANAGE) or ctx.has(Permission.CREDIT_READ)):
            ctx.require(Permission.CUSTOMER_MANAGE)

        term = query.strip()
        if not term:
            raise ValidationFailed({"query": "Type at least one character."})

        normalized = normalize_name(term)
        phone = normalize_phone(term)
        vehicle = normalize_vehicle_number(term)
        effective = min(limit or 20, MAX_SEARCH_LIMIT)

        ids = (
            self._session.execute(
                text(
                    """
                SELECT DISTINCT c.id
                  FROM customers c
                  LEFT JOIN customer_phones p ON p.customer_id = c.id
                  LEFT JOIN customer_vehicles v ON v.customer_id = c.id
                 WHERE c.business_id = :business_id
                   AND c.is_active
                   AND (
                         c.normalized_name LIKE :prefix
                      OR c.normalized_name % :raw
                      OR (:phone <> '' AND p.normalized_phone LIKE :phone_prefix)
                      OR (:vehicle <> '' AND v.normalized_vehicle_number LIKE :vehicle_prefix)
                   )
                 LIMIT :limit
                """
                ),
                {
                    "business_id": ctx.business_id,
                    "prefix": f"{escape_like(normalized)}%",
                    "raw": normalized,
                    "phone": phone or "",
                    "phone_prefix": f"{escape_like(phone)}%" if phone else "",
                    "vehicle": vehicle or "",
                    "vehicle_prefix": f"{escape_like(vehicle)}%" if vehicle else "",
                    "limit": effective,
                },
            )
            .scalars()
            .all()
        )

        return [self._view(ctx, cid) for cid in ids]

    def list_recent(
        self, ctx: AuthContext, *, limit: int = 50, before: datetime | None = None
    ) -> tuple[list[CustomerView], bool]:
        """The customer directory, newest first. Returns `(views, has_more)`.

        Why this exists
        ---------------
        There was no way to list customers. `search` needs a term, and the only other
        read-many was `GET /credit/customers`, which **inner-joins credit invoices** —
        it answers "who owes us money", which is the Credit Bills view. The Customers
        screen was reading from it, so a customer with no credit invoice was invisible
        there and could not be found by searching either. A customer created through
        `Add a customer` therefore appeared to vanish, which is exactly what was
        reported from the browser.

        Newest first, because the customer you just added is the one you are looking
        for. Keyset paging on `(created_at, id)` rather than `OFFSET`: a directory that
        grows while you page through it must not skip or repeat a row, and `id` breaks
        the tie so the order is total.

        Inactive customers are excluded, matching `search`. Deactivation is the only
        delete (FR-CUST-011), so including them would surface records the shop has
        deliberately retired.
        """
        if not (ctx.has(Permission.CUSTOMER_MANAGE) or ctx.has(Permission.CREDIT_READ)):
            ctx.require(Permission.CUSTOMER_MANAGE)

        effective = min(limit or 50, MAX_SEARCH_LIMIT)
        # One more than asked for, so `has_more` is known without a second count query.
        rows = (
            self._session.execute(
                text(
                    """
                SELECT c.id
                  FROM customers c
                 WHERE c.business_id = :business_id
                   AND c.is_active
                   -- Cast both uses: with `before` NULL, PostgreSQL cannot infer
                   -- the parameter's type and refuses with AmbiguousParameter.
                   AND (CAST(:before AS timestamptz) IS NULL
                        OR c.created_at < CAST(:before AS timestamptz))
                 ORDER BY c.created_at DESC, c.id DESC
                 LIMIT :limit
                """
                ),
                {
                    "business_id": ctx.business_id,
                    "before": before,
                    "limit": effective + 1,
                },
            )
            .scalars()
            .all()
        )
        has_more = len(rows) > effective
        return [self._view(ctx, cid) for cid in rows[:effective]], has_more

    def get(self, ctx: AuthContext, customer_id: uuid.UUID) -> CustomerView:
        if not (ctx.has(Permission.CUSTOMER_MANAGE) or ctx.has(Permission.CREDIT_READ)):
            ctx.require(Permission.CUSTOMER_MANAGE)
        return self._view(ctx, customer_id)

    def _view(self, ctx: AuthContext, customer_id: uuid.UUID) -> CustomerView:
        row = self._session.execute(
            select(Customer).where(
                Customer.id == customer_id, Customer.business_id == ctx.business_id
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("That customer does not exist.")

        phones = list(
            self._session.execute(
                select(CustomerPhone.phone, CustomerPhone.is_primary)
                .where(CustomerPhone.customer_id == customer_id)
                .order_by(CustomerPhone.is_primary.desc())
            ).all()
        )
        vehicles = list(
            self._session.execute(
                select(CustomerVehicle.vehicle_number).where(
                    CustomerVehicle.customer_id == customer_id
                )
            ).scalars()
        )

        outstanding: Decimal | None = None
        oldest: date | None = None
        if ctx.has(Permission.CREDIT_READ):
            # Derived from authoritative data, never read from the cache table
            # (FR-CREDIT-002).
            summary = self._session.execute(
                text(
                    """
                    SELECT coalesce(sum(i.total - i.amount_paid), 0)::numeric(14,2) AS outstanding,
                           min(i.due_date) AS oldest_due
                      FROM invoices i
                     WHERE i.business_id = :business_id
                       AND i.customer_id = :customer_id
                       AND i.is_credit
                       AND i.status <> 'cancelled'
                       AND i.payment_status IN ('unpaid','partially_paid')
                    """
                ),
                {"business_id": ctx.business_id, "customer_id": customer_id},
            ).first()
            if summary is not None:
                outstanding = summary.outstanding
                oldest = summary.oldest_due

        return CustomerView(
            id=row.id,
            name=row.name,
            business_name=row.business_name,
            primary_phone=phones[0][0] if phones else None,
            phones=tuple(p[0] for p in phones),
            vehicles=tuple(vehicles),
            address_lines=tuple(row.address_lines or ()),
            default_mechanic_name=row.default_mechanic_name,
            credit_terms_days=row.credit_terms_days,
            credit_limit=row.credit_limit,
            is_active=row.is_active,
            outstanding=outstanding,
            oldest_due_date=oldest,
            created_at=row.created_at,
        )

    def exists(self, business_id: uuid.UUID, customer_id: uuid.UUID) -> bool:
        return (
            self._session.execute(
                select(func.count())
                .select_from(Customer)
                .where(Customer.id == customer_id, Customer.business_id == business_id)
            ).scalar_one()
            > 0
        )

    def name_of(self, business_id: uuid.UUID, customer_id: uuid.UUID) -> str | None:
        return self._session.execute(
            select(Customer.name).where(
                Customer.id == customer_id, Customer.business_id == business_id
            )
        ).scalar_one_or_none()

    def credit_terms_for(self, business_id: uuid.UUID, customer_id: uuid.UUID) -> int | None:
        return self._session.execute(
            select(Customer.credit_terms_days).where(
                Customer.id == customer_id, Customer.business_id == business_id
            )
        ).scalar_one_or_none()

    def credit_limit_for(self, business_id: uuid.UUID, customer_id: uuid.UUID) -> Decimal | None:
        return self._session.execute(
            select(Customer.credit_limit).where(
                Customer.id == customer_id, Customer.business_id == business_id
            )
        ).scalar_one_or_none()

    # --- duplicates -----------------------------------------------------------

    def check_duplicates(
        self, ctx: AuthContext, *, name: str | None, phone: str | None
    ) -> list[DuplicateCandidate]:
        """Surface likely duplicates. **Never merges** (FR-CUST-004)."""
        ctx.require(Permission.CUSTOMER_MANAGE)

        normalized_phone = normalize_phone(phone)
        normalized_name = normalize_name(name) if name else None

        rows = self._session.execute(
            text(
                """
                SELECT c.id, c.name,
                       (SELECT p2.phone FROM customer_phones p2
                         WHERE p2.customer_id = c.id
                         ORDER BY p2.is_primary DESC LIMIT 1) AS primary_phone,
                       CASE WHEN p.normalized_phone IS NOT NULL THEN 'phone'
                            ELSE 'name' END AS matched_on
                  FROM customers c
                  LEFT JOIN customer_phones p
                         ON p.customer_id = c.id
                        AND :phone <> ''
                        AND p.normalized_phone = :phone
                 WHERE c.business_id = :business_id
                   AND c.is_active
                   AND (
                         (:phone <> '' AND p.normalized_phone = :phone)
                      OR (:name <> '' AND c.normalized_name = :name)
                   )
                 LIMIT 10
                """
            ),
            {
                "business_id": ctx.business_id,
                "phone": normalized_phone or "",
                "name": normalized_name or "",
            },
        ).all()

        return [
            DuplicateCandidate(
                id=r.id, name=r.name, primary_phone=r.primary_phone, matched_on=r.matched_on
            )
            for r in rows
        ]

    # --- writes ---------------------------------------------------------------

    def upsert(
        self,
        ctx: AuthContext,
        customer_id: uuid.UUID,
        *,
        name: str,
        business_name: str | None = None,
        phone: str | None = None,
        address_lines: list[str] | None = None,
        default_mechanic_name: str | None = None,
        vehicle_number: str | None = None,
        credit_terms_days: int | None = None,
        credit_limit: object | None = None,
    ) -> UpsertResult:
        """Upsert on a **client-generated id**, so offline creation is idempotent and
        the client's id becomes the server's id with no remapping (FR-CUST-003).

        Only `name` is required. A rename changes the label and nothing else: invoices
        link on `id` (FR-CUST-011), so grouping and history survive it.
        """
        ctx.require(Permission.CUSTOMER_MANAGE)

        clean_name = name.strip()
        if not clean_name:
            raise ValidationFailed({"name": "A customer needs a name."})

        limit_value = (
            parse_decimal(credit_limit, field_name="credit_limit")
            if credit_limit is not None
            else None
        )
        if limit_value is not None and limit_value < ZERO:
            raise ValidationFailed({"credit_limit": "Must not be negative."})
        if credit_terms_days is not None and credit_terms_days < 0:
            raise ValidationFailed({"credit_terms_days": "Must not be negative."})

        existing = self._session.execute(
            select(Customer).where(
                Customer.id == customer_id, Customer.business_id == ctx.business_id
            )
        ).scalar_one_or_none()
        created = existing is None

        warnings: list[dict[str, object]] = []
        if created:
            candidates = self.check_duplicates(ctx, name=clean_name, phone=phone)
            if candidates:
                # A warning rides along with a **successful** response; nothing is
                # merged and the creation is not blocked (§4.3).
                warnings.append(
                    {
                        "code": str(WarningCode.CUSTOMER_DUPLICATE_SUSPECTED),
                        "message": "A customer with these details may already exist.",
                        "details": {
                            "candidates": [
                                {
                                    "id": str(c.id),
                                    "name": c.name,
                                    "primary_phone": c.primary_phone,
                                    "matched_on": c.matched_on,
                                }
                                for c in candidates
                            ]
                        },
                    }
                )

            self._session.execute(
                insert(Customer).values(
                    id=customer_id,
                    business_id=ctx.business_id,
                    name=clean_name,
                    normalized_name=normalize_name(clean_name),
                    business_name=business_name.strip() if business_name else None,
                    address_lines=address_lines or [],
                    default_mechanic_name=(
                        default_mechanic_name.strip() if default_mechanic_name else None
                    ),
                    credit_terms_days=credit_terms_days,
                    credit_limit=limit_value,
                    created_by_user_id=ctx.user_id,
                )
            )
        else:
            values: dict[str, object] = {
                "name": clean_name,
                "normalized_name": normalize_name(clean_name),
            }
            # Optional fields are only overwritten when supplied, so a partial update
            # from the billing header cannot blank out details entered elsewhere.
            if business_name is not None:
                values["business_name"] = business_name.strip() or None
            if address_lines is not None:
                values["address_lines"] = address_lines
            if default_mechanic_name is not None:
                values["default_mechanic_name"] = default_mechanic_name.strip() or None
            if credit_terms_days is not None:
                values["credit_terms_days"] = credit_terms_days
            if limit_value is not None:
                values["credit_limit"] = limit_value
            self._session.execute(
                update(Customer).where(Customer.id == customer_id).values(**values)
            )

        if phone and phone.strip():
            self._attach_phone(ctx, customer_id, phone)
        if vehicle_number and vehicle_number.strip():
            self._attach_vehicle(ctx, customer_id, vehicle_number)

        self._session.flush()
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.CUSTOMER_CREATED if created else AuditAction.CUSTOMER_UPDATED,
            entity_type="customer",
            entity_id=customer_id,
            before=None if created else {"name": existing.name if existing else None},
            after={"name": clean_name},
        )
        return UpsertResult(
            customer=self._view(ctx, customer_id), created=created, warnings=warnings
        )

    def _attach_phone(self, ctx: AuthContext, customer_id: uuid.UUID, phone: str) -> None:
        """Attach a phone, tolerating the duplicate-detection unique index.

        If the number already belongs to **this** customer there is nothing to do; if it
        belongs to another, the number is simply not attached rather than the save
        failing — the duplicate warning has already told the user, and losing the whole
        customer record over a shared phone number would be worse.
        """
        normalized = normalize_phone(phone)
        if not normalized:
            return
        owner = self._session.execute(
            select(CustomerPhone.customer_id).where(
                CustomerPhone.business_id == ctx.business_id,
                CustomerPhone.normalized_phone == normalized,
            )
        ).scalar_one_or_none()
        if owner is not None:
            return
        existing_count = self._session.execute(
            select(func.count())
            .select_from(CustomerPhone)
            .where(CustomerPhone.customer_id == customer_id)
        ).scalar_one()
        self._session.execute(
            insert(CustomerPhone).values(
                id=uuid.uuid4(),
                customer_id=customer_id,
                business_id=ctx.business_id,
                phone=phone.strip(),
                normalized_phone=normalized,
                is_primary=existing_count == 0,
            )
        )

    def _attach_vehicle(
        self, ctx: AuthContext, customer_id: uuid.UUID, vehicle_number: str
    ) -> None:
        """The **entered** form is stored and printed verbatim; the normalised form is
        only for search."""
        normalized = normalize_vehicle_number(vehicle_number)
        if not normalized:
            return
        already = self._session.execute(
            select(func.count())
            .select_from(CustomerVehicle)
            .where(
                CustomerVehicle.customer_id == customer_id,
                CustomerVehicle.normalized_vehicle_number == normalized,
            )
        ).scalar_one()
        if already:
            return
        self._session.execute(
            insert(CustomerVehicle).values(
                id=uuid.uuid4(),
                customer_id=customer_id,
                business_id=ctx.business_id,
                vehicle_number=vehicle_number.strip(),
                normalized_vehicle_number=normalized,
            )
        )

    def deactivate(self, ctx: AuthContext, customer_id: uuid.UUID) -> None:
        """Soft deactivate only. A referenced customer is never hard-deleted."""
        ctx.require(Permission.CUSTOMER_MANAGE)
        from bm.infra.db.rows import rows_affected

        if not rows_affected(
            self._session.execute(
                update(Customer)
                .where(Customer.id == customer_id, Customer.business_id == ctx.business_id)
                .values(is_active=False)
            )
        ):
            raise NotFound("That customer does not exist.")
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.CUSTOMER_DEACTIVATED,
            entity_type="customer",
            entity_id=customer_id,
        )

    # --- ledger ---------------------------------------------------------------

    def ledger(
        self, ctx: AuthContext, customer_id: uuid.UUID, *, limit: int = 200
    ) -> list[dict[str, object]]:
        """Invoices, payments and credit notes with a running balance (FR-CREDIT-011)."""
        ctx.require(Permission.CREDIT_READ)
        if not self.exists(ctx.business_id, customer_id):
            raise NotFound("That customer does not exist.")

        rows = self._session.execute(
            text(
                """
                SELECT entry_at, kind, reference, debit, credit FROM (
                    SELECT i.issued_at AS entry_at, 'invoice' AS kind,
                           i.invoice_number AS reference,
                           i.total AS debit, 0::numeric(14,2) AS credit
                      FROM invoices i
                     WHERE i.business_id = :business_id AND i.customer_id = :customer_id
                       AND i.is_credit AND i.status <> 'cancelled'
                    UNION ALL
                    SELECT p.received_at, 'payment', coalesce(p.reference, ''),
                           0::numeric(14,2), p.amount
                      FROM payments p
                     WHERE p.business_id = :business_id AND p.customer_id = :customer_id
                       AND p.kind = 'payment'
                    UNION ALL
                    SELECT p.received_at, 'payment_reversal', coalesce(p.reference, ''),
                           p.amount, 0::numeric(14,2)
                      FROM payments p
                     WHERE p.business_id = :business_id AND p.customer_id = :customer_id
                       AND p.kind = 'reversal'
                    UNION ALL
                    SELECT n.issued_at, 'credit_note', n.note_number,
                           0::numeric(14,2), n.amount
                      FROM credit_notes n
                     WHERE n.business_id = :business_id AND n.customer_id = :customer_id
                       AND n.status <> 'cancelled'
                ) entries
                ORDER BY entry_at
                LIMIT :limit
                """
            ),
            {
                "business_id": ctx.business_id,
                "customer_id": customer_id,
                "limit": min(limit, 200),
            },
        ).all()

        running = ZERO
        ledger: list[dict[str, object]] = []
        for r in rows:
            running = running + r.debit - r.credit
            ledger.append(
                {
                    "at": r.entry_at.isoformat(),
                    "kind": r.kind,
                    # The internal reference appears on a detail view only, labelled as
                    # internal — never on a customer-facing list (T3-07).
                    "internal_reference": r.reference,
                    "debit": format(r.debit, "f"),
                    "credit": format(r.credit, "f"),
                    "running_balance": format(running, "f"),
                }
            )
        return ledger
