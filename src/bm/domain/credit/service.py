"""Credit, payments and allocations (M11).

The three rules that define this module:

1. **Recording a payment never moves stock** (FR-CREDIT-010). Nothing in this file
   imports `InventoryService`, and `test_payment_creates_no_stock_movement` counts
   movement rows before and after. Sale finalisation and payment collection are distinct
   financial events.
2. **`amount_paid` and `payment_status` are derived**, written only in the same
   transaction as the allocations (ADR-022). No endpoint sets them, there is no "mark as
   paid", and there is no paid checkbox anywhere in this system.
3. **`payments` is append-only.** A mistake is corrected by a `reversal` row pointing at
   the original (FR-PAY-005); the original is never edited.

Allocation locks the invoice rows it touches, so two payments against one invoice cannot
both believe the full amount is outstanding.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.shared.clock import Clock, business_date
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    InvoiceCancelled,
    NotFound,
    OverAllocation,
    PaymentAlreadyReversed,
    ValidationFailed,
)
from bm.domain.shared.money import ZERO, assert_integral_amount, assert_scale_within, parse_decimal
from bm.infra.db.enums import (
    AllocationStrategy,
    CreditNoteSource,
    CreditNoteStatus,
    PaymentKind,
    PaymentMethod,
    PaymentStatus,
)
from bm.infra.db.models.credit import CreditNote, Payment, PaymentAllocation


@dataclass(frozen=True, slots=True)
class AllocationTarget:
    invoice_id: uuid.UUID
    amount: Decimal


@dataclass(frozen=True, slots=True)
class PaymentResult:
    payment_id: uuid.UUID
    allocated: tuple[AllocationTarget, ...]
    #: Any remainder becomes on-account customer credit, allocatable later
    #: (FR-PAY-003) — never silently discarded.
    on_account: Decimal


@dataclass(frozen=True, slots=True)
class OutstandingInvoice:
    invoice_id: uuid.UUID
    internal_reference: str
    issued_at: datetime
    due_date: date | None
    total: Decimal
    amount_paid: Decimal
    outstanding: Decimal
    payment_status: str
    days_overdue: int


@dataclass(frozen=True, slots=True)
class CustomerCredit:
    customer_id: uuid.UUID
    name: str
    primary_phone: str | None
    invoice_count: int
    open_invoice_count: int
    total_invoiced: Decimal
    total_paid: Decimal
    outstanding: Decimal
    oldest_due_date: date | None
    days_overdue: int
    ageing: dict[str, Decimal] = field(default_factory=dict)


class CreditService:
    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    def _today(self, ctx: AuthContext) -> date:
        """Today, as the **business's** calendar reckons it.

        Every query below compares this against `invoices.due_date`, and `due_date` is a
        `DATE` produced by `business_date(now, timezone) + terms` — a business-local
        calendar date, with a comment in `SalesService` citing T3-02 for exactly that
        reason. Taking `today` from `clock.now().date()` therefore compared a
        business-local date against a **UTC** one: two different calendars either side of
        the same subtraction.

        For `Asia/Kolkata` they disagree for the first 5½ hours of every day, and the
        consequences are financial rather than cosmetic:

        * an invoice due **today** was reported **one day overdue** between 00:00 and
          05:30 local, because `:today` was still yesterday — so `due_date < :today` was
          false while `(:today - due_date) BETWEEN 1 AND 30` had already become true;
        * every ageing bucket shifted with it, moving balances across the 30/60/90
          boundaries a day early;
        * `credit-outstanding`'s `overdue` total disagreed with the ageing buckets
          computed from the same rows.

        This is not a new policy. T3-02's own acceptance criterion
        (`PHASE-2-IMPLEMENTATION-PLAN.md`) is that "the financial-year boundary **and
        report day-boundaries** are evaluated in business-local time rather than UTC",
        and FR-CREDIT-005/FR-RPT-002 are report day-boundaries. It makes both sides of a
        comparison use the same calendar, which is what the code already said it did.

        Read from the business row rather than from `ctx.business_timezone`, matching
        `ReportService._business_timezone`: the context's copy can come from a token
        claim, and one source of truth for a financial boundary is worth the query.
        """
        timezone = (
            self._session.execute(
                text("SELECT timezone FROM businesses WHERE id = :id"),
                {"id": ctx.business_id},
            ).scalar_one_or_none()
            or "Asia/Kolkata"
        )
        return business_date(self._clock.now(), timezone)

    # --- payment recording ----------------------------------------------------

    def record_sale_payment(
        self,
        ctx: AuthContext,
        *,
        invoice_id: uuid.UUID,
        customer_id: uuid.UUID | None,
        amount: Decimal,
        method: str,
        reference: str | None,
        invoice_total: Decimal,
    ) -> Decimal:
        """The payment taken at the counter during finalisation.

        Participates in the caller's transaction and **never commits** — a sale that
        rolls back takes its payment with it. Returns the amount allocated to this
        invoice.
        """
        from bm.domain.settings.service import SettingsService

        policy = SettingsService(self._session).money_policy(ctx.business_id, ctx.branch_id)
        value = assert_integral_amount(
            assert_scale_within(amount, max_places=2, field_name="payment.amount"),
            field_name="payment.amount",
            policy=policy,
        )
        if value <= ZERO:
            raise ValidationFailed({"payment.amount": "Must be greater than zero."})
        if value > invoice_total:
            # Overpaying at the counter is a typo, not an intent. Refusing is kinder
            # than creating on-account credit nobody asked for.
            raise OverAllocation(
                "That is more than the bill total.",
                details={
                    "invoice_total": format(invoice_total, "f"),
                    "offered": format(value, "f"),
                },
            )

        self._validate_method(method)
        payment_id = uuid.uuid4()
        self._session.execute(
            insert(Payment).values(
                id=payment_id,
                business_id=ctx.business_id,
                branch_id=ctx.requires_branch,
                counter_id=ctx.counter_id,
                customer_id=customer_id,
                kind=PaymentKind.PAYMENT,
                amount=value,
                method=method,
                reference=reference,
                allocation_strategy=AllocationStrategy.INVOICE_SPECIFIC,
                received_at=self._clock.now(),
                received_by_user_id=ctx.user_id,
                device_id=ctx.device_id,
            )
        )
        self._session.execute(
            insert(PaymentAllocation).values(
                id=uuid.uuid4(),
                business_id=ctx.business_id,
                payment_id=payment_id,
                invoice_id=invoice_id,
                amount=value,
            )
        )
        return value

    def record_payment(
        self,
        ctx: AuthContext,
        *,
        customer_id: uuid.UUID,
        amount: object,
        method: str,
        strategy: str,
        reference: str | None = None,
        allocations: list[AllocationTarget] | None = None,
        notes: str | None = None,
        received_at: datetime | None = None,
    ) -> PaymentResult:
        """`POST /payments`. **Writes payment and allocation rows only.**

        It touches no `stock_movements` row and cannot re-deduct the sale's inventory
        (FR-CREDIT-010).
        """
        ctx.require(Permission.CREDIT_PAYMENT_RECORD)

        from bm.domain.settings.service import SettingsService

        policy = SettingsService(self._session).money_policy(ctx.business_id, ctx.branch_id)
        value = assert_integral_amount(
            assert_scale_within(
                parse_decimal(amount, field_name="amount"), max_places=2, field_name="amount"
            ),
            field_name="amount",
            policy=policy,
        )
        if value <= ZERO:
            raise ValidationFailed({"amount": "Must be greater than zero."})
        self._validate_method(method)

        try:
            allocation_strategy = AllocationStrategy(strategy)
        except ValueError as exc:
            raise ValidationFailed(
                {"allocation.strategy": f"`{strategy}` is not a known strategy."}
            ) from exc

        now = received_at or self._clock.now()
        payment_id = uuid.uuid4()
        self._session.execute(
            insert(Payment).values(
                id=payment_id,
                business_id=ctx.business_id,
                branch_id=ctx.requires_branch,
                counter_id=ctx.counter_id,
                customer_id=customer_id,
                kind=PaymentKind.PAYMENT,
                amount=value,
                method=method,
                reference=reference,
                allocation_strategy=allocation_strategy,
                received_at=now,
                received_by_user_id=ctx.user_id,
                device_id=ctx.device_id,
                notes=notes,
            )
        )

        targets = self._plan_allocation(
            ctx,
            customer_id=customer_id,
            amount=value,
            strategy=allocation_strategy,
            requested=allocations or [],
        )

        allocated_total = ZERO
        for target in targets:
            self._session.execute(
                insert(PaymentAllocation).values(
                    id=uuid.uuid4(),
                    business_id=ctx.business_id,
                    payment_id=payment_id,
                    invoice_id=target.invoice_id,
                    amount=target.amount,
                )
            )
            allocated_total += target.amount
            self._recompute_invoice_payment(ctx, target.invoice_id)

        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.PAYMENT_RECORDED,
            entity_type="payment",
            entity_id=payment_id,
            after={
                "customer_id": str(customer_id),
                "amount": format(value, "f"),
                "method": method,
                "allocated": format(allocated_total, "f"),
                "on_account": format(value - allocated_total, "f"),
            },
        )
        return PaymentResult(
            payment_id=payment_id,
            allocated=tuple(targets),
            on_account=value - allocated_total,
        )

    def _plan_allocation(
        self,
        ctx: AuthContext,
        *,
        customer_id: uuid.UUID,
        amount: Decimal,
        strategy: AllocationStrategy,
        requested: list[AllocationTarget],
    ) -> list[AllocationTarget]:
        """Decide which invoices this payment pays.

        Invoice rows are locked here, so two concurrent payments cannot both read the
        same outstanding figure and jointly over-allocate.
        """
        if strategy is AllocationStrategy.INVOICE_SPECIFIC or (
            strategy is AllocationStrategy.MANUAL and requested
        ):
            if not requested:
                raise ValidationFailed(
                    {"allocation.allocations": "List the invoices this payment settles."}
                )
            planned: list[AllocationTarget] = []
            total = ZERO
            for target in requested:
                outstanding = self._locked_outstanding(ctx, target.invoice_id)
                if target.amount > outstanding:
                    raise OverAllocation(
                        details={
                            "invoice_id": str(target.invoice_id),
                            "outstanding": format(outstanding, "f"),
                            "offered": format(target.amount, "f"),
                        }
                    )
                planned.append(target)
                total += target.amount
            if total > amount:
                raise OverAllocation(
                    "The allocations add up to more than the payment.",
                    details={"payment": format(amount, "f"), "allocated": format(total, "f")},
                )
            return planned

        # `oldest_first`: settle the oldest due invoice first, which is what a shop
        # owner means by "put this against his account".
        rows = self._session.execute(
            text(
                """
                SELECT id, total - amount_paid AS outstanding
                  FROM invoices
                 WHERE business_id = :business_id
                   AND customer_id = :customer_id
                   AND is_credit
                   AND status <> 'cancelled'
                   AND payment_status IN ('unpaid','partially_paid')
                 ORDER BY coalesce(due_date, issued_at::date), issued_at
                 FOR UPDATE
                """
            ),
            {"business_id": ctx.business_id, "customer_id": customer_id},
        ).all()

        remaining = amount
        planned = []
        for row in rows:
            if remaining <= ZERO:
                break
            take = min(remaining, row.outstanding)
            if take > ZERO:
                planned.append(AllocationTarget(invoice_id=row.id, amount=take))
                remaining -= take
        return planned

    def _locked_outstanding(self, ctx: AuthContext, invoice_id: uuid.UUID) -> Decimal:
        row = self._session.execute(
            text(
                "SELECT total - amount_paid AS outstanding, status FROM invoices"
                " WHERE id = :id AND business_id = :business_id FOR UPDATE"
            ),
            {"id": invoice_id, "business_id": ctx.business_id},
            # `first()`, not `one()`. With `one()` the `row is None` guard below was
            # unreachable and an unknown or cross-tenant invoice id raised
            # `NoResultFound` — surfacing as a 500 rather than the intended 404, in a
            # path a client reaches by naming an invoice in an allocation. The guard was
            # always the intended behaviour; it simply could not run.
        ).first()
        if row is None:
            raise NotFound("That bill does not exist.")
        if row.status == "cancelled":
            raise InvoiceCancelled("That bill was cancelled.")
        return Decimal(row.outstanding)

    def _recompute_invoice_payment(self, ctx: AuthContext, invoice_id: uuid.UUID) -> None:
        """Recompute `amount_paid` and `payment_status` from the allocations.

        NFR-DATA-003: `amount_paid` **always** equals the allocation sum, including after
        a reversal. Recomputing from source rather than incrementing is what keeps that
        true — an increment that runs twice, or not at all, silently drifts.
        """
        row = self._session.execute(
            text(
                """
                SELECT i.total,
                       coalesce((SELECT sum(a.amount) FROM payment_allocations a
                                  JOIN payments p ON p.id = a.payment_id
                                 WHERE a.invoice_id = i.id AND p.kind = 'payment'), 0)
                     - coalesce((SELECT sum(a.amount) FROM payment_allocations a
                                  JOIN payments p ON p.id = a.payment_id
                                 WHERE a.invoice_id = i.id AND p.kind = 'reversal'), 0)
                     + coalesce((SELECT sum(c.amount) FROM credit_note_allocations c
                                 WHERE c.invoice_id = i.id), 0) AS paid
                  FROM invoices i
                 WHERE i.id = :id AND i.business_id = :business_id
                """
            ),
            {"id": invoice_id, "business_id": ctx.business_id},
            # `first()` for the same reason as `_locked_outstanding`: the guard below is
            # the intended behaviour and `one()` made it unreachable.
        ).first()
        if row is None:
            return

        paid = row.paid
        total = row.total
        if paid <= ZERO:
            status = PaymentStatus.UNPAID
        elif paid < total:
            status = PaymentStatus.PARTIALLY_PAID
        elif paid == total:
            status = PaymentStatus.PAID
        else:
            status = PaymentStatus.OVERPAID

        self._session.execute(
            text(
                "UPDATE invoices SET amount_paid = :paid,"
                " payment_status = CAST(:status AS payment_status), updated_at = now()"
                " WHERE id = :id"
            ),
            {"paid": paid, "status": str(status), "id": invoice_id},
        )

    def reverse_payment(self, ctx: AuthContext, payment_id: uuid.UUID, *, reason: str) -> uuid.UUID:
        """Create a reversal row; **never** edit the original (FR-PAY-005)."""
        ctx.require(Permission.CREDIT_PAYMENT_RECORD)
        if len(reason.strip()) < 3:
            raise ValidationFailed({"reason": "Say briefly why this payment is reversed."})

        original = self._session.execute(
            select(Payment).where(Payment.id == payment_id, Payment.business_id == ctx.business_id)
        ).scalar_one_or_none()
        if original is None:
            raise NotFound("That payment does not exist.")
        if str(original.kind) == str(PaymentKind.REVERSAL):
            raise ValidationFailed({"payment_id": "A reversal cannot itself be reversed."})

        already = self._session.execute(
            select(Payment.id).where(Payment.reverses_payment_id == payment_id)
        ).scalar_one_or_none()
        if already is not None:
            raise PaymentAlreadyReversed()

        reversal_id = uuid.uuid4()
        self._session.execute(
            insert(Payment).values(
                id=reversal_id,
                business_id=ctx.business_id,
                branch_id=original.branch_id,
                counter_id=ctx.counter_id,
                customer_id=original.customer_id,
                kind=PaymentKind.REVERSAL,
                reverses_payment_id=payment_id,
                amount=original.amount,
                method=original.method,
                reference=original.reference,
                allocation_strategy=original.allocation_strategy,
                received_at=self._clock.now(),
                received_by_user_id=ctx.user_id,
                device_id=ctx.device_id,
                notes=reason.strip(),
            )
        )

        # Mirror the original's allocations so the derived figures move back.
        originals = self._session.execute(
            select(PaymentAllocation.invoice_id, PaymentAllocation.amount).where(
                PaymentAllocation.payment_id == payment_id
            )
        ).all()
        for invoice_id, amount in originals:
            self._session.execute(
                insert(PaymentAllocation).values(
                    id=uuid.uuid4(),
                    business_id=ctx.business_id,
                    payment_id=reversal_id,
                    invoice_id=invoice_id,
                    amount=amount,
                )
            )
            self._recompute_invoice_payment(ctx, invoice_id)

        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.PAYMENT_REVERSED,
            entity_type="payment",
            entity_id=reversal_id,
            reason=reason.strip(),
            after={"reverses": str(payment_id), "amount": format(original.amount, "f")},
        )
        return reversal_id

    @staticmethod
    def _validate_method(method: str) -> None:
        try:
            PaymentMethod(method)
        except ValueError as exc:
            raise ValidationFailed(
                {"method": f"`{method}` is not a known payment method."}
            ) from exc

    # --- reads ----------------------------------------------------------------

    def outstanding(
        self, ctx: AuthContext, *, customer_id: uuid.UUID | None = None, limit: int = 200
    ) -> list[OutstandingInvoice]:
        """Derived from authoritative data (FR-CREDIT-002), never from the cache table."""
        ctx.require(Permission.CREDIT_READ)
        today = self._today(ctx)

        query = """
            SELECT i.id, i.invoice_number, i.issued_at, i.due_date, i.total,
                   i.amount_paid, i.total - i.amount_paid AS outstanding,
                   i.payment_status
              FROM invoices i
             WHERE i.business_id = :business_id
               AND i.is_credit
               AND i.status <> 'cancelled'
               AND i.payment_status IN ('unpaid','partially_paid')
        """
        params: dict[str, object] = {"business_id": ctx.business_id, "limit": min(limit, 200)}
        if customer_id is not None:
            query += " AND i.customer_id = :customer_id"
            params["customer_id"] = customer_id
        query += " ORDER BY coalesce(i.due_date, i.issued_at::date), i.issued_at LIMIT :limit"

        rows = self._session.execute(text(query), params).all()
        return [
            OutstandingInvoice(
                invoice_id=r.id,
                internal_reference=r.invoice_number,
                issued_at=r.issued_at,
                due_date=r.due_date,
                total=r.total,
                amount_paid=r.amount_paid,
                outstanding=r.outstanding,
                payment_status=r.payment_status,
                days_overdue=max(0, (today - r.due_date).days) if r.due_date else 0,
            )
            for r in rows
        ]

    def customers_with_credit(
        self,
        ctx: AuthContext,
        *,
        include_settled: bool = False,
        query: str | None = None,
        limit: int = 20,
    ) -> tuple[list[CustomerCredit], dict[str, object]]:
        """`GET /credit/customers` — the top level of the Credit Bills view.

        Every aggregate is **server-computed** (FR-RPT-030, ADR-032): the client never
        sums these as authoritative. Grouping keys on `customer_id`, so a rename changes
        only the label (FR-RPT-029).

        Ageing buckets are evaluated against `due_date` at exact boundaries 0/1/30/31/
        60/61/90/91, which is what the boundary test with a frozen clock asserts.
        """
        ctx.require(Permission.CREDIT_READ)
        today = self._today(ctx)

        sql = """
            WITH credit_invoices AS (
                SELECT i.customer_id,
                       i.total,
                       i.amount_paid,
                       i.total - i.amount_paid AS outstanding,
                       i.due_date,
                       i.payment_status
                  FROM invoices i
                 WHERE i.business_id = :business_id
                   AND i.is_credit
                   AND i.status <> 'cancelled'
            )
            SELECT c.id AS customer_id, c.name,
                   (SELECT p.phone FROM customer_phones p
                     WHERE p.customer_id = c.id ORDER BY p.is_primary DESC LIMIT 1) AS phone,
                   count(*) AS invoice_count,
                   count(*) FILTER (
                       WHERE ci.payment_status IN ('unpaid','partially_paid')
                   ) AS open_invoice_count,
                   coalesce(sum(ci.total), 0)::numeric(14,2) AS total_invoiced,
                   coalesce(sum(ci.amount_paid), 0)::numeric(14,2) AS total_paid,
                   coalesce(sum(ci.outstanding), 0)::numeric(14,2) AS outstanding,
                   min(ci.due_date) FILTER (
                       WHERE ci.payment_status IN ('unpaid','partially_paid')
                   ) AS oldest_due_date,
                   coalesce(sum(ci.outstanding) FILTER (
                       WHERE ci.due_date IS NULL OR ci.due_date >= :today
                   ), 0) AS bucket_current,
                   coalesce(sum(ci.outstanding) FILTER (
                       WHERE ci.due_date < :today
                         AND (:today - ci.due_date) BETWEEN 1 AND 30
                   ), 0) AS bucket_1_30,
                   coalesce(sum(ci.outstanding) FILTER (
                       WHERE (:today - ci.due_date) BETWEEN 31 AND 60
                   ), 0) AS bucket_31_60,
                   coalesce(sum(ci.outstanding) FILTER (
                       WHERE (:today - ci.due_date) BETWEEN 61 AND 90
                   ), 0) AS bucket_61_90,
                   coalesce(sum(ci.outstanding) FILTER (
                       WHERE (:today - ci.due_date) >= 91
                   ), 0) AS bucket_90_plus
              FROM customers c
              JOIN credit_invoices ci ON ci.customer_id = c.id
             WHERE c.business_id = :business_id
               AND (:query = '' OR c.normalized_name LIKE :query_prefix)
             GROUP BY c.id, c.name
        """
        if not include_settled:
            # Settled invoices are never deleted or permanently hidden (FR-RPT-031);
            # they are simply behind `include_settled`.
            sql += " HAVING coalesce(sum(ci.outstanding), 0) > 0"
        sql += " ORDER BY outstanding DESC, c.name LIMIT :limit"

        from bm.domain.shared.normalize import escape_like, normalize_name

        normalized = normalize_name(query) if query else ""
        rows = self._session.execute(
            text(sql),
            {
                "business_id": ctx.business_id,
                "today": today,
                "query": normalized,
                "query_prefix": f"{escape_like(normalized)}%" if normalized else "",
                "limit": min(limit, 200),
            },
        ).all()

        items = [
            CustomerCredit(
                customer_id=r.customer_id,
                name=r.name,
                primary_phone=r.phone,
                invoice_count=r.invoice_count,
                open_invoice_count=r.open_invoice_count,
                total_invoiced=r.total_invoiced,
                total_paid=r.total_paid,
                outstanding=r.outstanding,
                oldest_due_date=r.oldest_due_date,
                days_overdue=(max(0, (today - r.oldest_due_date).days) if r.oldest_due_date else 0),
                ageing={
                    "current": r.bucket_current,
                    "d1_30": r.bucket_1_30,
                    "d31_60": r.bucket_31_60,
                    "d61_90": r.bucket_61_90,
                    "d90_plus": r.bucket_90_plus,
                },
            )
            for r in rows
        ]

        summary_row = self._session.execute(
            text(
                """
                SELECT coalesce(sum(i.total - i.amount_paid), 0)::numeric(14,2) AS overall_outstanding,
                       coalesce(sum(i.total - i.amount_paid) FILTER (
                           WHERE i.due_date < :today
                       ), 0) AS overall_overdue,
                       count(DISTINCT i.customer_id) AS customers_with_balance
                  FROM invoices i
                 WHERE i.business_id = :business_id
                   AND i.is_credit
                   AND i.status <> 'cancelled'
                   AND i.payment_status IN ('unpaid','partially_paid')
                """
            ),
            {"business_id": ctx.business_id, "today": today},
        ).one()

        summary = {
            "overall_outstanding": format(summary_row.overall_outstanding, "f"),
            "overall_overdue": format(summary_row.overall_overdue, "f"),
            "customers_with_balance": summary_row.customers_with_balance,
        }
        return items, summary

    def ageing_summary(self, ctx: AuthContext) -> dict[str, str]:
        """Buckets: current, 1-30, 31-60, 61-90, 90+ (`API-SPECIFICATION.md §3.7`)."""
        ctx.require(Permission.CREDIT_READ)
        today = self._today(ctx)
        row = self._session.execute(
            text(
                """
                SELECT coalesce(sum(outstanding) FILTER (
                           WHERE due_date IS NULL OR due_date >= :today), 0) AS current,
                       coalesce(sum(outstanding) FILTER (
                           WHERE (:today - due_date) BETWEEN 1 AND 30), 0) AS d1_30,
                       coalesce(sum(outstanding) FILTER (
                           WHERE (:today - due_date) BETWEEN 31 AND 60), 0) AS d31_60,
                       coalesce(sum(outstanding) FILTER (
                           WHERE (:today - due_date) BETWEEN 61 AND 90), 0) AS d61_90,
                       coalesce(sum(outstanding) FILTER (
                           WHERE (:today - due_date) >= 91), 0) AS d90_plus
                  FROM (
                      SELECT due_date, total - amount_paid AS outstanding
                        FROM invoices
                       WHERE business_id = :business_id AND is_credit
                         AND status <> 'cancelled'
                         AND payment_status IN ('unpaid','partially_paid')
                  ) open_invoices
                """
            ),
            {"business_id": ctx.business_id, "today": today},
        ).one()
        return {
            "current": format(row.current, "f"),
            "d1_30": format(row.d1_30, "f"),
            "d31_60": format(row.d31_60, "f"),
            "d61_90": format(row.d61_90, "f"),
            "d90_plus": format(row.d90_plus, "f"),
        }

    def payments_for(
        self,
        ctx: AuthContext,
        *,
        invoice_id: uuid.UUID | None = None,
        customer_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        ctx.require(Permission.CREDIT_READ)
        sql = """
            SELECT p.id, p.amount, p.method, p.reference, p.received_at, p.kind,
                   a.invoice_id, a.amount AS allocated
              FROM payments p
              LEFT JOIN payment_allocations a ON a.payment_id = p.id
             WHERE p.business_id = :business_id
        """
        params: dict[str, object] = {"business_id": ctx.business_id, "limit": min(limit, 200)}
        if invoice_id is not None:
            sql += " AND a.invoice_id = :invoice_id"
            params["invoice_id"] = invoice_id
        if customer_id is not None:
            sql += " AND p.customer_id = :customer_id"
            params["customer_id"] = customer_id
        sql += " ORDER BY p.received_at DESC LIMIT :limit"

        return [
            {
                "id": str(r.id),
                "amount": format(r.amount, "f"),
                "allocated": format(r.allocated, "f") if r.allocated is not None else None,
                "method": r.method,
                "reference": r.reference,
                "received_at": r.received_at.isoformat(),
                "kind": r.kind,
                "invoice_id": str(r.invoice_id) if r.invoice_id else None,
            }
            for r in self._session.execute(text(sql), params).all()
        ]

    # --- credit notes ---------------------------------------------------------

    def issue_credit_note(
        self,
        ctx: AuthContext,
        *,
        customer_id: uuid.UUID | None,
        amount: Decimal,
        reason: str,
        source_type: CreditNoteSource,
        source_id: uuid.UUID | None = None,
        original_invoice_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Issue a credit note. Participates in the caller's transaction."""
        note_id = uuid.uuid4()
        sequence = self._session.execute(
            text("SELECT count(*) + 1 FROM credit_notes WHERE business_id = :business_id"),
            {"business_id": ctx.business_id},
        ).scalar_one()
        self._session.execute(
            insert(CreditNote).values(
                id=note_id,
                business_id=ctx.business_id,
                branch_id=ctx.requires_branch,
                customer_id=customer_id,
                # Internal reference, like the invoice reference. Not customer-visible.
                note_number=f"CN/{sequence:05d}",
                source_type=source_type,
                source_id=source_id,
                original_invoice_id=original_invoice_id,
                amount=amount,
                reason=reason,
                issued_at=self._clock.now(),
                created_by_user_id=ctx.user_id,
                status=CreditNoteStatus.OPEN,
            )
        )
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.CREDIT_NOTE_ISSUED,
            entity_type="credit_note",
            entity_id=note_id,
            reason=reason,
            after={"amount": format(amount, "f"), "source": str(source_type)},
        )
        return note_id

    def write_off(
        self, ctx: AuthContext, *, customer_id: uuid.UUID, amount: object, reason: str
    ) -> uuid.UUID:
        """Owner-only (`credit.writeoff`). Produces a credit note with a reason."""
        ctx.require(Permission.CREDIT_WRITEOFF)
        if len(reason.strip()) < 10:
            raise ValidationFailed(
                {"reason": "A write-off needs an explanation of at least 10 characters."}
            )
        value = parse_decimal(amount, field_name="amount")
        if value <= ZERO:
            raise ValidationFailed({"amount": "Must be greater than zero."})

        note_id = self.issue_credit_note(
            ctx,
            customer_id=customer_id,
            amount=value,
            reason=reason.strip(),
            source_type=CreditNoteSource.WRITE_OFF,
        )
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.CREDIT_WRITTEN_OFF,
            entity_type="customer",
            entity_id=customer_id,
            reason=reason.strip(),
            after={"amount": format(value, "f"), "credit_note_id": str(note_id)},
        )
        return note_id

    def allocate_credit_note(
        self, ctx: AuthContext, note_id: uuid.UUID, targets: list[AllocationTarget]
    ) -> None:
        ctx.require(Permission.CREDIT_PAYMENT_RECORD)
        from bm.infra.db.models.credit import CreditNoteAllocation

        note = self._session.execute(
            select(CreditNote).where(
                CreditNote.id == note_id, CreditNote.business_id == ctx.business_id
            )
        ).scalar_one_or_none()
        if note is None:
            raise NotFound("That credit note does not exist.")

        already = self._session.execute(
            text(
                "SELECT coalesce(sum(amount), 0)::numeric(14,2) FROM credit_note_allocations"
                " WHERE credit_note_id = :id"
            ),
            {"id": note_id},
        ).scalar_one()
        remaining = note.amount - already

        total = sum((t.amount for t in targets), start=ZERO)
        if total > remaining:
            raise OverAllocation(
                details={"remaining": format(remaining, "f"), "offered": format(total, "f")}
            )

        for target in targets:
            outstanding = self._locked_outstanding(ctx, target.invoice_id)
            if target.amount > outstanding:
                raise OverAllocation(
                    details={
                        "invoice_id": str(target.invoice_id),
                        "outstanding": format(outstanding, "f"),
                    }
                )
            self._session.execute(
                insert(CreditNoteAllocation).values(
                    id=uuid.uuid4(),
                    business_id=ctx.business_id,
                    credit_note_id=note_id,
                    invoice_id=target.invoice_id,
                    amount=target.amount,
                )
            )
            self._recompute_invoice_payment(ctx, target.invoice_id)

        if total == remaining:
            self._session.execute(
                text(
                    "UPDATE credit_notes SET status = CAST(:status AS credit_note_status)"
                    " WHERE id = :id"
                ),
                {"status": str(CreditNoteStatus.FULLY_ALLOCATED), "id": note_id},
            )
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.CREDIT_NOTE_ALLOCATED,
            entity_type="credit_note",
            entity_id=note_id,
            after={"allocated": format(total, "f")},
        )
