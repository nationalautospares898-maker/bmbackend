"""Sale finalisation — the one atomic engine for cash **and** credit (M10).

`Blueprint §5.1` defines the sequence and its non-negotiable properties. The ones this
module exists to guarantee:

1. **Everything between BEGIN and COMMIT is one transaction.** Partial success is
   impossible (FR-SALE-001). Stock is deducted exactly once, or not at all.
2. **The idempotency row is the first write, before any inventory lock** (`P2-D-06`), so
   a double-click fails in microseconds holding no business locks instead of blocking a
   genuine sale of the same product.
3. **Lock order is fixed:** `inventory_balances` (ascending `product_id`) →
   `invoice_series` → `customers` → `invoices` (ADR-008).
4. **The number is allocated after stock is verified**, so a stock failure consumes no
   number and the hot series row is held for the minimum time.
5. **No side effects inside the transaction** — notifications go to `outbox_events`.
6. **Printing happens only after a successful commit.** Printing is not finalisation.

⚠️ **Cash and credit finalise through this same method and the same endpoint**
(FR-CREDIT-008, ADR-033). `Credit Bill` is not a draft-save and has no preparatory step:
it finalises immediately, requires a persisted `customer_id`, and does not require a
payment. There is **no second finalisation path**, and a structural test asserts the
OpenAPI document contains no "prepare credit" endpoint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import insert, text
from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.catalog.service import CatalogService
from bm.domain.customers.service import CustomerService
from bm.domain.inventory.service import InventoryService, MovementRequest
from bm.domain.sales.drafts import DraftService
from bm.domain.sales.numbering import NumberingService
from bm.domain.settings.defaults import SettingKey
from bm.domain.settings.service import SettingsService
from bm.domain.shared.clock import Clock, business_date
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    CreditCustomerRequired,
    CreditLimitExceeded,
    DiscountExceedsLimit,
    DraftAlreadyCancelled,
    DraftAlreadyFinalized,
    DraftVersionConflict,
    EmptySale,
    NotFound,
    PriceChanged,
    PriceOverrideNotPermitted,
    ProductInactive,
    ValidationFailed,
    WarningCode,
)
from bm.domain.shared.money import (
    ZERO,
    InvoiceDiscount,
    InvoiceTotals,
    LineInput,
    assert_client_total_matches,
    assert_scale_within,
    compute_invoice_totals,
    quantize_amount,
)
from bm.domain.shared.quantity import validate_quantity
from bm.domain.sync.idempotency import IdempotencyClaim, IdempotencyService
from bm.infra.db.enums import DocumentKind, DraftStatus, MovementReason, PaymentStatus, PriceSource
from bm.infra.db.models.sales import Invoice, InvoiceItem

FINALIZE_ENDPOINT = "POST /sales:finalize"


@dataclass(frozen=True, slots=True)
class SaleLineCommand:
    product_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal
    price_source: str = str(PriceSource.CATALOG)
    discount_percent: Decimal = ZERO
    discount_amount: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class CustomerSelection:
    """`mode` ∈ `none` | `existing` | `new` (`API-SPECIFICATION.md §3.3`)."""

    mode: str
    customer_id: uuid.UUID | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentCommand:
    method: str
    amount: Decimal
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class SaleHeader:
    customer_name: str | None = None
    customer_business_name: str | None = None
    vehicle_number: str | None = None
    mechanic_name: str | None = None
    phone: str | None = None
    notes: str | None = None
    due_date: date | None = None


@dataclass(frozen=True, slots=True)
class FinalizeCommand:
    draft_id: uuid.UUID | None
    draft_version: int | None
    counter_id: uuid.UUID
    sale_type: str
    customer: CustomerSelection
    header: SaleHeader
    lines: list[SaleLineCommand]
    invoice_discount: InvoiceDiscount = field(default_factory=InvoiceDiscount)
    payment: PaymentCommand | None = None
    client_computed_total: Decimal | None = None
    accept_price_changes: bool = False
    oversell_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedLine:
    """A sale line after the server has resolved it against the live catalog.

    Typed rather than a dict so the finalisation path keeps its types: every price,
    name and unit below comes from the server, never from the request.
    """

    product_id: uuid.UUID
    name: str
    sku: str | None
    unit_code: str
    hsn_code: str | None
    quantity: Decimal
    unit_price: Decimal
    catalog_price: Decimal
    cost_price: Decimal | None
    price_source: str
    discount_percent: Decimal
    discount_amount: Decimal
    reorder_level: Decimal | None


@dataclass(frozen=True, slots=True)
class FinalizeResult:
    invoice_id: uuid.UUID
    #: ⚠️ Internal reference. Never rendered to a customer, never in `print_payload`.
    internal_reference: str
    sequence_number: int
    totals: InvoiceTotals
    is_credit: bool
    payment_status: str
    amount_paid: Decimal
    outstanding: Decimal
    due_date: date | None
    warnings: list[dict[str, object]] = field(default_factory=list)
    replayed: bool = False
    replayed_body: object | None = None


class SalesService:
    """Owns the finalisation transaction (`ARCHITECTURE.md §5.1` rule 2).

    `InventoryService`, `CreditService` and `NumberingService` participate using the
    passed session and **never commit**. No nested commits anywhere.
    """

    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock
        self._settings = SettingsService(session)
        self._inventory = InventoryService(session)
        self._catalog = CatalogService(session)
        self._customers = CustomerService(session)
        self._drafts = DraftService(session)
        self._numbering = NumberingService(session)
        self._idempotency = IdempotencyService(session)

    def finalize(
        self,
        ctx: AuthContext,
        cmd: FinalizeCommand,
        *,
        idempotency_key: uuid.UUID,
        raw_body: object,
    ) -> FinalizeResult:
        ctx.require(Permission.BILLING_SALE_FINALIZE)

        # --- 2. claim the key, BEFORE any inventory lock (P2-D-06) ---------------
        claim = self._idempotency.claim(
            business_id=ctx.business_id,
            endpoint=FINALIZE_ENDPOINT,
            key=idempotency_key,
            body=raw_body,
            user_id=ctx.user_id,
            device_id=ctx.device_id,
        )
        if claim.replayed:
            # The stored response, byte-for-byte. The router adds
            # `Idempotent-Replay: true`.
            return FinalizeResult(
                invoice_id=uuid.UUID(int=0),
                internal_reference="",
                sequence_number=0,
                totals=InvoiceTotals(ZERO, ZERO, ZERO, ZERO, ZERO),
                is_credit=False,
                payment_status="",
                amount_paid=ZERO,
                outstanding=ZERO,
                due_date=None,
                replayed=True,
                replayed_body=claim.stored_body,
            )

        if not cmd.lines:
            raise EmptySale()

        branch_id = ctx.requires_branch
        now = self._clock.now()
        warnings: list[dict[str, object]] = []

        # --- 3. lock the draft and assert it is finalizable ----------------------
        draft = None
        if cmd.draft_id is not None:
            draft = self._drafts.load_for_update(ctx.business_id, cmd.draft_id)
            if draft is None:
                raise NotFound("That bill does not exist.")
            if draft.status == DraftStatus.FINALIZED:
                # Carries the existing invoice so the client shows it instead of
                # billing again. There is deliberately no retry affordance for this.
                raise DraftAlreadyFinalized(
                    details={"invoice_id": str(draft.invoice_id)} if draft.invoice_id else {}
                )
            if draft.status == DraftStatus.CANCELLED:
                raise DraftAlreadyCancelled()
            if cmd.draft_version is not None and cmd.draft_version != draft.version:
                # Staleness is enforced independently of the fingerprint, because
                # `draft_version` is excluded from idempotency identity (§5.2.4).
                raise DraftVersionConflict(details={"current_version": draft.version})

        # --- 4. resolve live prices, check active, compute money -----------------
        resolved = self._resolve_lines(ctx, cmd)
        self._assert_prices_acceptable(ctx, cmd, resolved)
        self._assert_discounts_within_limit(ctx, cmd)

        policy = self._settings.money_policy(ctx.business_id, ctx.branch_id)
        totals = compute_invoice_totals(
            [
                LineInput(
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    discount_percent=line.discount_percent,
                    discount_amount=line.discount_amount,
                    line_no=index + 1,
                )
                for index, line in enumerate(resolved)
            ],
            invoice_discount=cmd.invoice_discount,
            policy=policy,
        )
        # A cross-check, never a value to trust (FR-SALE-003).
        assert_client_total_matches(totals.total, cmd.client_computed_total)

        # --- credit identity ------------------------------------------------------
        is_credit = cmd.sale_type == "credit"
        customer_id = self._resolve_customer(ctx, cmd, is_credit=is_credit)

        if is_credit:
            warnings.extend(self._check_credit_limit(ctx, customer_id, totals.total))

        # --- 5. lock stock and check (ascending product_id) ----------------------
        required: dict[uuid.UUID, Decimal] = {}
        for line in resolved:
            required[line.product_id] = required.get(line.product_id, ZERO) + line.quantity

        check = self._inventory.lock_and_check(
            business_id=ctx.business_id, branch_id=branch_id, required=required
        )
        if check.has_shortfall:
            self._inventory.assert_oversell_authorized(
                ctx,
                check,
                override_reason=cmd.oversell_reason,
                names={line.product_id: line.name for line in resolved},
            )
        oversold = check.has_shortfall

        # --- 6. allocate the internal reference ---------------------------------
        business = self._session.execute(
            text("SELECT timezone, financial_year_start_month FROM businesses WHERE id = :id"),
            {"id": ctx.business_id},
        ).first()
        if business is None:  # pragma: no cover - the token guarantees a business
            raise NotFound()

        allocated = self._numbering.allocate(
            business_id=ctx.business_id,
            branch_id=branch_id,
            now=now,
            timezone=business.timezone,
            financial_year_start_month=business.financial_year_start_month,
            document_kind=DocumentKind.ESTIMATE,
        )

        # --- 7. insert the invoice and its immutable item snapshots --------------
        due_date = self._resolve_due_date(
            ctx, cmd, customer_id, is_credit=is_credit, now=now, timezone=business.timezone
        )
        invoice_id = uuid.uuid4()

        self._session.execute(
            insert(Invoice).values(
                id=invoice_id,
                business_id=ctx.business_id,
                branch_id=branch_id,
                counter_id=cmd.counter_id,
                invoice_series_id=allocated.series_id,
                invoice_number=allocated.reference,
                sequence_number=allocated.sequence_number,
                document_kind=allocated.document_kind,
                issued_at=now,
                customer_id=customer_id,
                customer_name_snapshot=self._customer_name_snapshot(ctx, cmd, customer_id),
                customer_business_name_snapshot=cmd.header.customer_business_name,
                vehicle_number_snapshot=cmd.header.vehicle_number,
                mechanic_name_snapshot=cmd.header.mechanic_name,
                phone_snapshot=cmd.header.phone,
                is_credit=is_credit,
                due_date=due_date,
                subtotal=totals.subtotal,
                discount_total=totals.discount_total,
                tax_total=totals.tax_total,
                rounding_adjustment=totals.rounding_adjustment,
                total=totals.total,
                amount_paid=ZERO,
                payment_status=PaymentStatus.UNPAID,
                oversell_override=oversold,
                oversell_reason=cmd.oversell_reason if oversold else None,
                price_changes_accepted=cmd.accept_price_changes,
                # T3-03: the rule that produced this bill, recorded with it.
                money_policy_version=policy.version,
                draft_bill_id=cmd.draft_id,
                created_by_user_id=ctx.user_id,
                device_id=ctx.device_id,
                idempotency_key=str(idempotency_key),
                notes=cmd.header.notes,
            )
        )

        for index, (line, computed) in enumerate(zip(resolved, totals.lines, strict=True), start=1):
            self._session.execute(
                insert(InvoiceItem).values(
                    id=uuid.uuid4(),
                    invoice_id=invoice_id,
                    business_id=ctx.business_id,
                    line_no=index,
                    product_id=line.product_id,
                    product_name_snapshot=line.name,
                    sku_snapshot=line.sku,
                    unit_code_snapshot=line.unit_code,
                    hsn_snapshot=line.hsn_code,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    discount_percent=line.discount_percent,
                    discount_amount=computed.discount_total,
                    taxable_amount=computed.taxable_amount,
                    tax_rate_snapshot=ZERO,
                    tax_amount=computed.tax_amount,
                    line_total=computed.line_total,
                    unit_cost_snapshot=line.cost_price,
                    price_source=line.price_source,
                    catalog_price_at_sale=line.catalog_price,
                )
            )

        # --- 8. move stock, exactly once -----------------------------------------
        self._inventory.apply_movements(
            ctx,
            branch_id=branch_id,
            movements=[
                MovementRequest(
                    product_id=line.product_id,
                    quantity=line.quantity,
                    reason=MovementReason.SALE,
                    source_type="invoice",
                    source_id=invoice_id,
                    unit_cost=line.cost_price,
                    oversell_override=oversold,
                    override_reason=cmd.oversell_reason if oversold else None,
                )
                for line in resolved
            ],
        )

        # --- 9. optional payment (cash requires it; credit does not) -------------
        amount_paid = ZERO
        payment_status = PaymentStatus.UNPAID
        if cmd.payment is not None:
            from bm.domain.credit.service import CreditService

            amount_paid = CreditService(self._session, self._clock).record_sale_payment(
                ctx,
                invoice_id=invoice_id,
                customer_id=customer_id,
                amount=cmd.payment.amount,
                method=cmd.payment.method,
                reference=cmd.payment.reference,
                invoice_total=totals.total,
            )
        payment_status = self._derive_status(totals.total, amount_paid)

        self._session.execute(
            text(
                "UPDATE invoices SET amount_paid = :paid,"
                " payment_status = CAST(:status AS payment_status), updated_at = now()"
                " WHERE id = :id"
            ),
            {"paid": amount_paid, "status": str(payment_status), "id": invoice_id},
        )

        # --- 10. draft, audit, outbox --------------------------------------------
        if cmd.draft_id is not None:
            self._drafts.mark_finalized(cmd.draft_id, invoice_id)

        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.SALE_FINALIZED,
            entity_type="invoice",
            entity_id=invoice_id,
            reason=cmd.oversell_reason if oversold else None,
            after={
                "internal_reference": allocated.reference,
                "sale_type": cmd.sale_type,
                "total": format(totals.total, "f"),
                "line_count": len(resolved),
                "oversell_override": oversold,
                "money_policy_version": policy.version,
            },
        )
        self._enqueue_outbox(ctx, invoice_id, totals.total)
        warnings.extend(self._low_stock_warnings(ctx, branch_id, resolved))

        return FinalizeResult(
            invoice_id=invoice_id,
            internal_reference=allocated.reference,
            sequence_number=allocated.sequence_number,
            totals=totals,
            is_credit=is_credit,
            payment_status=str(payment_status),
            # Emitted at the stored scale, matching what `NUMERIC(14,2)` holds. A bare
            # `Decimal("0")` would serialise as `"0"` while every other amount carried
            # two places, which makes the canonical fingerprint and the client's
            # formatting needlessly inconsistent.
            amount_paid=quantize_amount(amount_paid, policy),
            outstanding=quantize_amount(totals.total - amount_paid, policy),
            due_date=due_date,
            warnings=warnings,
        )

    # --- helpers --------------------------------------------------------------

    def _resolve_lines(self, ctx: AuthContext, cmd: FinalizeCommand) -> list[ResolvedLine]:
        """Resolve each line against the live catalog.

        Lines are sent explicitly rather than read from the draft, so the client's
        intent is unambiguous (`API-SPECIFICATION.md §3.3`) — but every price, name and
        unit comes from the server.
        """
        resolved: list[ResolvedLine] = []
        inactive: list[dict[str, str]] = []

        for index, line in enumerate(cmd.lines, start=1):
            product = self._catalog.resolve_for_sale(
                ctx.business_id, line.product_id, branch_id=ctx.branch_id
            )
            if product is None:
                raise ValidationFailed(
                    {f"lines.{index}.product_id": "That product does not exist."}
                )
            if not product.is_active:
                inactive.append({"product_id": str(product.id), "name": product.name})
                continue

            unit = self._catalog.unit_spec_for(product.id)
            quantity = validate_quantity(
                assert_scale_within(
                    line.quantity, max_places=3, field_name=f"lines.{index}.quantity"
                ),
                unit,
                field_name=f"lines.{index}.quantity",
            )
            unit_price = assert_scale_within(
                line.unit_price, max_places=4, field_name=f"lines.{index}.unit_price"
            )
            if unit_price < ZERO:
                raise ValidationFailed({f"lines.{index}.unit_price": "Must not be negative."})

            resolved.append(
                ResolvedLine(
                    product_id=product.id,
                    name=product.name,
                    sku=product.sku,
                    unit_code=product.unit_code,
                    hsn_code=product.hsn_code,
                    quantity=quantity,
                    unit_price=unit_price,
                    catalog_price=product.default_selling_price,
                    cost_price=product.last_cost_price,
                    price_source=line.price_source,
                    discount_percent=line.discount_percent,
                    discount_amount=line.discount_amount,
                    reorder_level=product.reorder_level,
                )
            )

        if inactive:
            raise ProductInactive(details={"lines": inactive})
        if not resolved:
            raise EmptySale()
        return resolved

    def _assert_prices_acceptable(
        self, ctx: AuthContext, cmd: FinalizeCommand, resolved: list[ResolvedLine]
    ) -> None:
        """Detect a price that moved since the line was added (FR-SALE-006, E-5).

        Never auto-accepted: the user sees the difference and decides, because a price
        that changed mid-bill is either a catalog correction the cashier should know
        about or a mistake they should not propagate.
        """
        changed: list[dict[str, str]] = []
        overrides: list[int] = []

        for index, (line, original) in enumerate(zip(resolved, cmd.lines, strict=True), start=1):
            catalog_price = line.catalog_price
            submitted = line.unit_price
            if submitted != catalog_price:
                if original.price_source == str(PriceSource.MANUAL_OVERRIDE):
                    overrides.append(index)
                elif not cmd.accept_price_changes:
                    changed.append(
                        {
                            "product_id": str(line.product_id),
                            "name": line.name,
                            "draft_price": format(submitted, "f"),
                            "current_price": format(catalog_price, "f"),
                        }
                    )

        if changed:
            raise PriceChanged(details={"lines": changed})
        if overrides and not ctx.has(Permission.BILLING_PRICE_OVERRIDE):
            raise PriceOverrideNotPermitted()

    def _assert_discounts_within_limit(self, ctx: AuthContext, cmd: FinalizeCommand) -> None:
        """A cashier's discount is capped by `discount.cashier_max_percent`, default 0.

        The permission is granted but bounded, which is the only way the "limited" cell
        in the `PRD.md §3.2` matrix can be expressed.
        """
        cap = Decimal(
            str(
                self._settings.get(
                    ctx.business_id, ctx.branch_id, SettingKey.DISCOUNT_CASHIER_MAX_PERCENT
                )
            )
        )
        if ctx.has(Permission.BILLING_PRICE_OVERRIDE):
            return  # a manager/owner is not capped by the cashier limit

        requested = max(
            [line.discount_percent for line in cmd.lines] + [cmd.invoice_discount.percent]
        )
        if requested > cap:
            raise DiscountExceedsLimit(
                details={
                    "requested_percent": format(requested, "f"),
                    "max_percent": format(cap, "f"),
                }
            )

    def _resolve_customer(
        self, ctx: AuthContext, cmd: FinalizeCommand, *, is_credit: bool
    ) -> uuid.UUID | None:
        """A credit sale requires a **persisted** customer (FR-CREDIT-009).

        A free-text name is rejected with `409 CREDIT_CUSTOMER_REQUIRED` and **no**
        invoice, movement or consumed number — the transaction has not reached any of
        those yet, which is why this check sits before the stock lock.
        """
        selection = cmd.customer

        if selection.mode == "new":
            if selection.customer_id is None:
                raise ValidationFailed({"customer.customer_id": "required for mode `new`"})
            if not (selection.name and selection.name.strip()):
                raise ValidationFailed({"customer.name": "required for mode `new`"})
            # Only a name is required (FR-CUST-010). Optional header values the cashier
            # typed are still persisted (FR-CREDIT-012).
            self._customers.upsert(
                ctx,
                selection.customer_id,
                name=selection.name,
                business_name=cmd.header.customer_business_name,
                phone=cmd.header.phone,
                default_mechanic_name=cmd.header.mechanic_name,
                vehicle_number=cmd.header.vehicle_number,
            )
            return selection.customer_id

        if selection.mode == "existing":
            if selection.customer_id is None:
                raise ValidationFailed({"customer.customer_id": "required for mode `existing`"})
            if not self._customers.exists(ctx.business_id, selection.customer_id):
                raise NotFound("That customer does not exist.")
            return selection.customer_id

        if is_credit:
            raise CreditCustomerRequired()
        return None

    def _customer_name_snapshot(
        self, ctx: AuthContext, cmd: FinalizeCommand, customer_id: uuid.UUID | None
    ) -> str | None:
        """Printed exactly as sold. A later rename never changes a past bill."""
        if cmd.header.customer_name and cmd.header.customer_name.strip():
            return cmd.header.customer_name.strip()
        if customer_id is not None:
            return self._customers.name_of(ctx.business_id, customer_id)
        return None

    def _resolve_due_date(
        self,
        ctx: AuthContext,
        cmd: FinalizeCommand,
        customer_id: uuid.UUID | None,
        *,
        is_credit: bool,
        now: object,
        timezone: str,
    ) -> date | None:
        """Credit terms: the customer's own, else the business default."""
        if not is_credit:
            return None
        if cmd.header.due_date is not None:
            return cmd.header.due_date

        terms = None
        if customer_id is not None:
            terms = self._customers.credit_terms_for(ctx.business_id, customer_id)
        if terms is None:
            terms = int(
                self._settings.get(
                    ctx.business_id, ctx.branch_id, SettingKey.CREDIT_DEFAULT_TERMS_DAYS
                )
            )
        # Evaluated in business-local time (T3-02), so a late-evening sale does not get
        # a due date a day early.
        return business_date(now, timezone) + timedelta(days=terms)  # type: ignore[arg-type]

    def _check_credit_limit(
        self, ctx: AuthContext, customer_id: uuid.UUID | None, total: Decimal
    ) -> list[dict[str, object]]:
        """Warn or block, per `credit.enforce_limit` (default off, FR-CREDIT-006)."""
        if customer_id is None:
            return []
        limit = self._customers.credit_limit_for(ctx.business_id, customer_id)
        if limit is None or limit <= ZERO:
            return []

        current = self._session.execute(
            text(
                "SELECT coalesce(sum(total - amount_paid), 0) FROM invoices"
                " WHERE business_id = :business_id AND customer_id = :customer_id"
                "   AND is_credit AND status <> 'cancelled'"
                "   AND payment_status IN ('unpaid','partially_paid')"
            ),
            {"business_id": ctx.business_id, "customer_id": customer_id},
        ).scalar_one()

        projected = current + total
        enforce = bool(
            self._settings.get(ctx.business_id, ctx.branch_id, SettingKey.CREDIT_ENFORCE_LIMIT)
        )
        if projected > limit:
            if enforce:
                raise CreditLimitExceeded(
                    details={
                        "limit": format(limit, "f"),
                        "current_outstanding": format(current, "f"),
                        "this_sale": format(total, "f"),
                    }
                )
            return [
                {
                    "code": str(WarningCode.CREDIT_LIMIT_APPROACHED),
                    "message": "This customer is over their credit limit.",
                    "details": {
                        "limit": format(limit, "f"),
                        "current_outstanding": format(current, "f"),
                    },
                }
            ]
        if projected >= limit * Decimal("0.9"):
            return [
                {
                    "code": str(WarningCode.CREDIT_LIMIT_APPROACHED),
                    "message": "This customer is close to their credit limit.",
                    "details": {
                        "limit": format(limit, "f"),
                        "current_outstanding": format(current, "f"),
                    },
                }
            ]
        return []

    def _low_stock_warnings(
        self, ctx: AuthContext, branch_id: uuid.UUID, resolved: list[ResolvedLine]
    ) -> list[dict[str, object]]:
        """Rides along with the **successful** sale. Never a reason to retry (§4.3)."""
        low: list[dict[str, str]] = []
        for line in resolved:
            if line.reorder_level is None:
                continue
            remaining = self._inventory.balance_for(branch_id=branch_id, product_id=line.product_id)
            if remaining <= line.reorder_level:
                low.append(
                    {
                        "product_id": str(line.product_id),
                        "name": line.name,
                        "quantity": format(remaining, "f"),
                    }
                )
        if not low:
            return []
        return [
            {
                "code": str(WarningCode.LOW_STOCK_AFTER_SALE),
                "message": f"{len(low)} item{'s' if len(low) != 1 else ''} now at or below the reorder level.",
                "details": {"products": low},
            }
        ]

    def _enqueue_outbox(self, ctx: AuthContext, invoice_id: uuid.UUID, total: Decimal) -> None:
        """No side effect runs inside the transaction (`ARCHITECTURE.md §5.1` rule 3).

        The notification is a row; the worker dispatches it after commit, so a
        notification can never exist for a sale that rolled back.
        """
        from bm.infra.db.models.infra import OutboxEvent

        self._session.execute(
            insert(OutboxEvent).values(
                id=uuid.uuid4(),
                business_id=ctx.business_id,
                event_type="sale.finalized",
                aggregate_type="invoice",
                aggregate_id=invoice_id,
                payload={"invoice_id": str(invoice_id), "total": format(total, "f")},
            )
        )

    @staticmethod
    def _derive_status(total: Decimal, paid: Decimal) -> PaymentStatus:
        """Derived, never set by a caller (ADR-022). There is no "mark as paid"."""
        if paid <= ZERO:
            return PaymentStatus.UNPAID
        if paid < total:
            return PaymentStatus.PARTIALLY_PAID
        if paid == total:
            return PaymentStatus.PAID
        return PaymentStatus.OVERPAID

    def store_idempotent_response(
        self, claim_key: uuid.UUID, *, business_id: uuid.UUID, body: object, invoice_id: uuid.UUID
    ) -> None:
        """Record the committed response under its key, in this same transaction."""
        row_id = self._session.execute(
            text(
                "SELECT id FROM idempotency_keys"
                " WHERE business_id = :business_id AND endpoint = :endpoint AND key = :key"
            ),
            {
                "business_id": business_id,
                "endpoint": FINALIZE_ENDPOINT,
                "key": str(claim_key),
            },
        ).scalar_one_or_none()
        if row_id is None:
            return
        self._idempotency.store_response(
            IdempotencyClaim(key=str(claim_key), fingerprint="", replayed=False, row_id=row_id),
            status_code=201,
            body=body,
            resource_type="invoice",
            resource_id=invoice_id,
        )
