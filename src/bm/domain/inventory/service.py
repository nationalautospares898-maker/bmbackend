"""Inventory service — the **sole writer** of stock (M6).

Everything that changes stock goes through `apply_movements`. Nothing else may
`UPDATE inventory_balances`, and a test proves it by patching this one method and running
the entire API suite: if any other path writes the balance table, the patch does not
catch it and the test fails.

Why a single writer rather than careful call sites: NFR-DATA-001 (`balance = Σ
movements`) cannot be expressed as a row constraint. The only way to keep it true is for
every balance change to be inseparable from its ledger row, in the same transaction, in
one function.

Lock order is **ascending `product_id`**, always (ADR-008). Two counters selling the
same two products in opposite orders is the textbook deadlock, and a fixed order is what
makes it structurally impossible rather than rare.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, and_, cast, func, insert, select, text, update
from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    InsufficientStock,
    OversellNotPermitted,
    OversellReasonRequired,
    ValidationFailed,
)
from bm.domain.shared.ids import uuid7
from bm.domain.shared.money import ZERO, parse_decimal
from bm.infra.db.enums import MovementReason
from bm.infra.db.models.inventory import InventoryBalance, StockMovement

#: Reasons that increase stock. Everything else decreases it. The sign is derived from
#: the reason, never supplied by a caller — a caller that could choose the sign could
#: turn a sale into a receipt.
_INCREASING: frozenset[MovementReason] = frozenset(
    {
        MovementReason.OPENING,
        MovementReason.PURCHASE_RECEIPT,
        MovementReason.SALE_CANCELLATION,
        MovementReason.CUSTOMER_RETURN,
        MovementReason.ADJUSTMENT_INCREASE,
        MovementReason.TRANSFER_IN,
        MovementReason.IMPORT_APPLY,
    }
)

MIN_OVERRIDE_REASON_LENGTH = 10
MIN_ADJUSTMENT_REASON_LENGTH = 3


@dataclass(frozen=True, slots=True)
class MovementRequest:
    """One intended stock change. `quantity` is always **positive**; the direction comes
    from `reason`."""

    product_id: uuid.UUID
    quantity: Decimal
    reason: MovementReason
    source_type: str
    source_id: uuid.UUID
    unit_cost: Decimal | None = None
    oversell_override: bool = False
    override_reason: str | None = None

    @property
    def signed_delta(self) -> Decimal:
        return self.quantity if self.reason in _INCREASING else -self.quantity


@dataclass(frozen=True, slots=True)
class Shortfall:
    product_id: uuid.UUID
    requested: Decimal
    available: Decimal


@dataclass(frozen=True, slots=True)
class MovementReportLine:
    """One stock movement, as the day's report shows it.

    `stock_before` and `stock_after` are the ledger's own figures — `balance_after` as it
    was written, and `balance_after - quantity_delta` for the value immediately before.
    Neither is derived from today's total, which is the difference between a report the
    owner can act on and one that merely looks plausible.
    """

    movement_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    sku: str | None
    #: Unsigned; the report's kind says the direction.
    quantity: Decimal
    stock_before: Decimal
    stock_after: Decimal
    reason: str
    #: `stock_count`, `invoice`, `purchase`, `stock_sheet`, `manual`, … — lets the screen
    #: say "Stock Count" rather than leaving a correction and a count indistinguishable
    #: once both show up as `adjustment_increase`.
    source_type: str | None
    #: The invoice number or the supplier's document number, when the movement has one.
    #: `None` for a stock-sheet entry, a correction or the opening figure — those have no
    #: document, and a placeholder would imply one exists.
    reference: str | None
    #: `True` credit, `False` cash, `None` when the movement was not a sale at all.
    is_credit: bool | None
    #: Whether the bill behind a sale was later cancelled, so a deduction that has since
    #: been reversed is not read as stock still gone.
    invoice_cancelled: bool | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MovementReport:
    """A day's (or a date range's) movements plus the totals the owner reads first."""

    on_date: date
    #: Equal to `on_date` for a single-day report; the last day of the range otherwise.
    end_date: date
    kind: str
    lines: list[MovementReportLine]
    #: Distinct products, not rows: five movements of one part affect one product.
    products: int
    total_quantity: Decimal
    movements: int
    #: Distinct bills contributing deductions. Zero for the `added` report.
    invoices: int
    #: True when the day held more movements than were returned, so the screen can say so
    #: instead of showing a short list that looks complete.
    truncated: bool


@dataclass(frozen=True, slots=True)
class StockRow:
    """One product's stock position, as the Stock screen shows it.

    A dataclass rather than the tuple this used to return: the row grew from four values
    to six, and a positional tuple of six is how a caller ends up rendering the part code
    in the price column.
    """

    product_id: uuid.UUID
    name: str
    quantity: Decimal
    #: This product's own minimum. `None` means no threshold is set, which is **not** a
    #: threshold of zero — the screen must not render "0" for it.
    reorder_level: Decimal | None
    sku: str | None
    default_selling_price: Decimal


@dataclass(frozen=True, slots=True)
class StockCheck:
    """The result of locking and checking. `shortfalls` is empty when every line can be
    satisfied from recorded stock."""

    locked_quantities: dict[uuid.UUID, Decimal]
    shortfalls: tuple[Shortfall, ...]

    @property
    def has_shortfall(self) -> bool:
        return bool(self.shortfalls)


class InventoryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- locking and checking -------------------------------------------------

    def lock_and_check(
        self,
        *,
        business_id: uuid.UUID,
        branch_id: uuid.UUID,
        required: dict[uuid.UUID, Decimal],
    ) -> StockCheck:
        """Lock the balance rows for these products and report any shortfall.

        Rows are created on demand with `ON CONFLICT DO NOTHING` so a product that has
        never moved can still be locked — otherwise the first sale of a new product
        would race with itself.

        **Ordered by `product_id` ascending.** This is the first step of the fixed lock
        order; deviating here is what creates deadlocks (ADR-008).
        """
        if not required:
            return StockCheck(locked_quantities={}, shortfalls=())

        product_ids = sorted(required)

        for product_id in product_ids:
            self._session.execute(
                text(
                    "INSERT INTO inventory_balances"
                    " (id, business_id, branch_id, product_id, quantity)"
                    " VALUES (gen_random_uuid(), :business_id, :branch_id, :product_id, 0)"
                    " ON CONFLICT (branch_id, product_id) DO NOTHING"
                ),
                {
                    "business_id": business_id,
                    "branch_id": branch_id,
                    "product_id": product_id,
                },
            )

        rows = self._session.execute(
            select(InventoryBalance.product_id, InventoryBalance.quantity)
            .where(
                InventoryBalance.branch_id == branch_id,
                InventoryBalance.product_id.in_(product_ids),
            )
            .order_by(InventoryBalance.product_id)
            .with_for_update()
        ).all()

        locked = {row[0]: row[1] for row in rows}
        shortfalls = tuple(
            Shortfall(
                product_id=product_id,
                requested=required[product_id],
                available=locked.get(product_id, ZERO),
            )
            for product_id in product_ids
            if locked.get(product_id, ZERO) < required[product_id]
        )
        return StockCheck(locked_quantities=locked, shortfalls=shortfalls)

    @staticmethod
    def assert_oversell_authorized(
        ctx: AuthContext,
        check: StockCheck,
        *,
        override_reason: str | None,
        names: dict[uuid.UUID, str] | None = None,
    ) -> None:
        """Refuse a shortfall unless the caller is authorised **and** gave a reason.

        C-3: overselling is prevented by default; the override exists, is explicit, is
        permissioned, and is audited with a reason that is persisted on both the invoice
        and every movement row it creates.
        """
        if not check.has_shortfall:
            return

        display = names or {}
        details = {
            "lines": [
                {
                    "product_id": str(s.product_id),
                    "name": display.get(s.product_id, ""),
                    "requested": format(s.requested, "f"),
                    "available": format(s.available, "f"),
                }
                for s in check.shortfalls
            ]
        }

        if override_reason is None:
            raise InsufficientStock(
                f"Not enough stock for {len(check.shortfalls)} item"
                f"{'s' if len(check.shortfalls) != 1 else ''}.",
                details=details,
            )
        if not ctx.has(Permission.INVENTORY_OVERSELL_OVERRIDE):
            raise OversellNotPermitted()
        if len(override_reason.strip()) < MIN_OVERRIDE_REASON_LENGTH:
            raise OversellReasonRequired(
                f"Give a reason of at least {MIN_OVERRIDE_REASON_LENGTH} characters."
            )

    # --- the single write path ------------------------------------------------

    def apply_movements(
        self,
        ctx: AuthContext,
        *,
        branch_id: uuid.UUID,
        movements: list[MovementRequest],
    ) -> list[uuid.UUID]:
        """Insert ledger rows and update balances, in one transaction.

        This is the **only** code path that writes `inventory_balances` or
        `stock_movements`. It does not commit — it participates in the caller's
        transaction, so a sale that rolls back takes its stock change with it.

        `balance_after` is read back from the `UPDATE ... RETURNING`, not computed in
        Python, so the ledger records what the database actually holds even under
        concurrency.
        """
        if not movements:
            return []

        # Ascending product_id, consistent with lock_and_check.
        ordered = sorted(movements, key=lambda m: m.product_id)
        created: list[uuid.UUID] = []

        for movement in ordered:
            if movement.quantity <= ZERO:
                raise ValidationFailed(
                    {"quantity": "A stock movement quantity must be greater than zero."}
                )
            if movement.oversell_override and (
                movement.override_reason is None
                or len(movement.override_reason.strip()) < MIN_OVERRIDE_REASON_LENGTH
            ):
                # Mirrors ck_stock_movements__override_reason so the failure is a typed
                # validation error rather than a constraint violation.
                raise OversellReasonRequired()

            self._session.execute(
                text(
                    "INSERT INTO inventory_balances"
                    " (id, business_id, branch_id, product_id, quantity)"
                    " VALUES (gen_random_uuid(), :business_id, :branch_id, :product_id, 0)"
                    " ON CONFLICT (branch_id, product_id) DO NOTHING"
                ),
                {
                    "business_id": ctx.business_id,
                    "branch_id": branch_id,
                    "product_id": movement.product_id,
                },
            )

            balance_after = self._session.execute(
                update(InventoryBalance)
                .where(
                    InventoryBalance.branch_id == branch_id,
                    InventoryBalance.product_id == movement.product_id,
                )
                .values(quantity=InventoryBalance.quantity + movement.signed_delta)
                .returning(InventoryBalance.quantity)
            ).scalar_one()

            movement_id = self._session.execute(
                insert(StockMovement)
                .values(
                    # Time-ordered, **not** `uuid4`. `created_at` is the transaction
                    # timestamp, so every row one transaction writes shares it; a UUIDv7
                    # makes `(created_at, id)` a real insertion order and keeps a
                    # product's running-balance history readable downwards. See
                    # `domain/shared/ids.py`.
                    id=uuid7(),
                    business_id=ctx.business_id,
                    branch_id=branch_id,
                    product_id=movement.product_id,
                    quantity_delta=movement.signed_delta,
                    balance_after=balance_after,
                    reason=str(movement.reason),
                    source_type=movement.source_type,
                    source_id=movement.source_id,
                    unit_cost=movement.unit_cost,
                    oversell_override=movement.oversell_override,
                    override_reason=movement.override_reason,
                    created_by_user_id=ctx.user_id,
                    counter_id=ctx.counter_id,
                    device_id=ctx.device_id,
                )
                .returning(StockMovement.id)
            ).scalar_one()

            self._session.execute(
                update(InventoryBalance)
                .where(
                    InventoryBalance.branch_id == branch_id,
                    InventoryBalance.product_id == movement.product_id,
                )
                .values(last_movement_id=movement_id)
            )
            created.append(movement_id)

            if movement.oversell_override:
                audit.record_for(
                    self._session,
                    ctx,
                    # ⚠️ The audit-action spelling differs from the permission and the
                    # column names on purpose — `DATA-MODEL.md §3.14`.
                    action=AuditAction.INVENTORY_OVERSELL_OVERRIDE,
                    entity_type="stock_movement",
                    entity_id=movement_id,
                    reason=movement.override_reason,
                    after={
                        "product_id": str(movement.product_id),
                        "delta": format(movement.signed_delta, "f"),
                        "balance_after": format(balance_after, "f"),
                    },
                )

        return created

    # --- reads ----------------------------------------------------------------

    def balance_for(self, *, branch_id: uuid.UUID, product_id: uuid.UUID) -> Decimal:
        value = self._session.execute(
            select(InventoryBalance.quantity).where(
                InventoryBalance.branch_id == branch_id,
                InventoryBalance.product_id == product_id,
            )
        ).scalar_one_or_none()
        return value if value is not None else ZERO

    def balances(
        self,
        ctx: AuthContext,
        *,
        product_ids: list[uuid.UUID] | None = None,
        low_stock_only: bool = False,
        limit: int = 200,
    ) -> list[StockRow]:
        """One row per product held at the caller's branch.

        Carries the **selling price and part code** as well as the quantity, because the
        Stock screen shows the owner's own four columns — Item, Price, Min Qty, Stock Qty
        — and fetching the price separately would mean a second request per product just
        to render a list.

        Never the cost price: that is gated on `catalog.cost.read` and is omitted from
        this shape entirely rather than nulled (FR-CAT-004).
        """
        ctx.require(Permission.CATALOG_READ)
        from bm.infra.db.models.catalog import Product

        query = (
            select(
                InventoryBalance.product_id,
                Product.name,
                InventoryBalance.quantity,
                Product.reorder_level,
                Product.sku,
                Product.default_selling_price,
            )
            .join(Product, Product.id == InventoryBalance.product_id)
            .where(
                InventoryBalance.business_id == ctx.business_id,
                InventoryBalance.branch_id == ctx.requires_branch,
            )
            .order_by(Product.name)
            .limit(min(limit, 200))
        )
        if product_ids:
            query = query.where(InventoryBalance.product_id.in_(product_ids))
        if low_stock_only:
            # **Strictly below** this product's own minimum (FR-INV-008). The threshold
            # is per product — `products.reorder_level`, set item by item — never one
            # figure applied to the whole catalog.
            #
            # `<` rather than `<=`, matching `CatalogService.ProductView.is_low_stock`,
            # so the Stock screen's list and a product's own badge can never disagree.
            # A product with no minimum set is not "low"; it has no threshold to be under,
            # which is why the `is_not(None)` test comes first rather than treating a
            # missing minimum as zero.
            query = query.where(
                Product.reorder_level.is_not(None),
                InventoryBalance.quantity < Product.reorder_level,
            )
        return [
            StockRow(
                product_id=r[0],
                name=r[1],
                quantity=r[2],
                reorder_level=r[3],
                sku=r[4],
                default_selling_price=r[5],
            )
            for r in self._session.execute(query).all()
        ]

    def movements(
        self,
        ctx: AuthContext,
        *,
        product_id: uuid.UUID | None = None,
        limit: int = 200,
        oldest_first: bool = False,
    ) -> list[StockMovement]:
        """The product ledger (FR-INV-001).

        `oldest_first` is what a product's **history** wants: read downwards, each row's
        `balance_after` follows from the one above it, which is how a shopkeeper checks a
        figure. Newest-first stays the default so no existing caller changes behaviour.
        """
        ctx.require(Permission.CATALOG_READ)
        order = (
            (StockMovement.created_at.asc(), StockMovement.id.asc())
            if oldest_first
            # `id` breaks the tie: the seed wrote fifteen opening rows inside one
            # transaction, so `created_at` alone leaves their order undefined and the
            # history could reshuffle between two identical requests.
            else (StockMovement.created_at.desc(), StockMovement.id.desc())
        )
        query = (
            select(StockMovement)
            .where(StockMovement.business_id == ctx.business_id)
            .order_by(*order)
            .limit(min(limit, 200))
        )
        if ctx.branch_id is not None:
            query = query.where(StockMovement.branch_id == ctx.branch_id)
        if product_id is not None:
            query = query.where(StockMovement.product_id == product_id)
        return list(self._session.execute(query).scalars().all())

    def movement_report(
        self,
        ctx: AuthContext,
        *,
        on_date: date,
        kind: str,
        end_date: date | None = None,
        limit: int = 1000,
    ) -> MovementReport:
        """Every stock movement of a business-local date range, grouped by `kind`.

        `end_date` is optional and defaults to `on_date`, so every existing single-day
        caller is unaffected — passing a later `end_date` is strictly additive, widening
        the same query to a range (the owner's Stock Report, start date + end date) rather
        than introducing a second code path that could disagree with this one.

        The owner already has a per-product history; this answers the other question —
        *what moved today?* — and it is read from the same append-only ledger, so the two
        can never disagree.

        **Nothing here is reconstructed.** `stock_movements` records `balance_after` at the
        moment the movement was written, and `quantity_delta` is signed, so

            stock_before = balance_after - quantity_delta

        is arithmetic on two stored columns rather than a replay of history or a guess
        from today's total. That identity is what makes the report trustworthy on a
        product that moved five times in a day, and it is asserted directly in the tests.

        **What counts as each kind**, stated as a rule rather than a list so a reason
        added later cannot be silently dropped:

        * `added` — **every movement that increased stock** (`quantity_delta > 0`):
          opening stock, a purchase receipt, the owner's stock sheet, a correction
          upwards, a customer return, a transfer in, an applied import, and the reversal
          of a cancelled bill. A reversal genuinely puts goods back on the shelf, so
          hiding it would make this report disagree with the ledger it is reading.
        * `sold` — **`reason = 'sale'` only.** Not corrections downwards, not scrap: the
          question is what customers took away. A sale movement exists only because a bill
          was finalised, which is the whole of "drafts and unconfirmed sales are excluded"
          — they never reach this table. Reprinting a bill writes no movement, and a
          credit payment moves money rather than goods, so neither can appear.
        * `adjusted` — every **other** decrease (`quantity_delta < 0` and `reason !=
          'sale'`): a correction downwards, scrap, a transfer out, a supplier return.
          Without this bucket these rows appeared in neither `added` nor `sold` and the
          report silently under-counted what left the shelf — the Stock Report (owner
          request) is what exposed the gap, and the fix is additive rather than a
          reclassification of what `added`/`sold` already meant.

        A bill cancelled **later** keeps its original deduction on the day it happened and
        gains a reversing row on the day it was cancelled. That is the ledger's own
        account of events and the two days reconcile; rewriting history to hide it would
        be the fabrication this docstring exists to rule out.

        One query, joined to `products` for the name and code and left-joined to the two
        documents a movement can have, so a day with two hundred rows costs one round
        trip rather than two hundred.
        """
        ctx.require(Permission.CATALOG_READ)
        if kind not in {"added", "sold", "adjusted"}:
            raise ValidationFailed({"kind": "Choose stock added, stock sold, or adjustments."})
        end = end_date or on_date
        if end < on_date:
            raise ValidationFailed({"end_date": "The end date cannot be before the start date."})

        # The same business-local day boundary `ReportService.daily_sales` uses, so
        # "today" means the same thing on this report as it does on Daily Bills. A sale at
        # 23:59 belongs to the day the shop was open, not to whatever UTC had reached.
        timezone = self._session.execute(
            text("SELECT timezone FROM businesses WHERE id = :id"),
            {"id": ctx.business_id},
        ).scalar_one()

        # Built with the query builder rather than assembled as SQL text. Two of these
        # predicates are chosen at runtime, and `tests/structural` forbids interpolating
        # anything conditional into `text()` (T-8) — correctly, because that is the shape
        # injection takes even when today's inputs happen to be safe.
        #
        # Imported here rather than at module level, the same way `movement_references`
        # reaches for `Invoice`: a cross-module **read** is allowed, and keeping it local
        # keeps the import graph the layering contracts check unchanged.
        from bm.infra.db.models.catalog import Product
        from bm.infra.db.models.credit import Purchase
        from bm.infra.db.models.sales import Invoice

        # `timezone(tz, timestamptz)` is Postgres's `AT TIME ZONE`, so this is the same
        # business-local day boundary `ReportService.daily_sales` applies to invoices.
        local_day = cast(func.timezone(timezone, StockMovement.created_at), Date)

        query = (
            select(
                StockMovement.id,
                StockMovement.product_id,
                Product.name.label("product_name"),
                Product.sku.label("sku"),
                StockMovement.quantity_delta,
                StockMovement.balance_after,
                StockMovement.reason,
                StockMovement.source_type,
                StockMovement.created_at,
                Invoice.invoice_number.label("invoice_number"),
                Invoice.is_credit.label("is_credit"),
                Invoice.status.label("invoice_status"),
                Purchase.supplier_document_number.label("supplier_document_number"),
            )
            .join(Product, Product.id == StockMovement.product_id)
            # Left joins, because most movements have no document: a correction and the
            # opening figure have none, and neither should drop out of the report.
            .outerjoin(
                Invoice,
                and_(
                    StockMovement.source_type == "invoice",
                    Invoice.id == StockMovement.source_id,
                    Invoice.business_id == StockMovement.business_id,
                ),
            )
            .outerjoin(
                Purchase,
                and_(
                    StockMovement.source_type == "purchase",
                    Purchase.id == StockMovement.source_id,
                    Purchase.business_id == StockMovement.business_id,
                ),
            )
            .where(
                StockMovement.business_id == ctx.business_id,
                local_day.between(on_date, end),
            )
            # Deterministic: two identical requests return the same order, and a
            # same-transaction tie is broken by the time-ordered movement id.
            .order_by(StockMovement.created_at.asc(), StockMovement.id.asc())
            # One row over the limit, so the caller can be **told** it was truncated
            # rather than shown a short list that looks complete.
            .limit(limit + 1)
        )
        if ctx.branch_id is not None:
            query = query.where(StockMovement.branch_id == ctx.branch_id)
        if kind == "added":
            query = query.where(StockMovement.quantity_delta > 0)
        elif kind == "sold":
            query = query.where(StockMovement.reason == MovementReason.SALE)
        else:
            query = query.where(
                StockMovement.quantity_delta < 0,
                StockMovement.reason != MovementReason.SALE,
            )

        rows = self._session.execute(query).all()

        truncated = len(rows) > limit
        rows = rows[:limit]

        lines = [
            MovementReportLine(
                movement_id=row.id,
                product_id=row.product_id,
                product_name=row.product_name,
                sku=row.sku,
                # Reported unsigned: the column already says whether it was added or
                # sold, and a shopkeeper reading "sold -3" has to do a double take.
                quantity=abs(row.quantity_delta),
                stock_before=row.balance_after - row.quantity_delta,
                stock_after=row.balance_after,
                reason=str(row.reason),
                source_type=row.source_type,
                reference=row.invoice_number or row.supplier_document_number,
                # `None` for anything that is not a sale, so the client shows nothing
                # rather than guessing "cash" for a correction.
                is_credit=row.is_credit if row.source_type == "invoice" else None,
                invoice_cancelled=(
                    row.invoice_status == "cancelled" if row.invoice_number else None
                ),
                created_at=row.created_at,
            )
            for row in rows
        ]

        return MovementReport(
            on_date=on_date,
            end_date=end,
            kind=kind,
            lines=lines,
            products=len({line.product_id for line in lines}),
            total_quantity=sum((line.quantity for line in lines), ZERO),
            movements=len(lines),
            # Only meaningful for `sold`, and counted from the references actually present
            # rather than assumed to equal the number of rows: one bill commonly moves
            # several products.
            invoices=len({line.reference for line in lines if line.reference})
            if kind == "sold"
            else 0,
            truncated=truncated,
        )

    def movement_references(
        self, ctx: AuthContext, movements: list[StockMovement]
    ) -> dict[uuid.UUID, str]:
        """`movement_id → the document it came from`, for the ones that have one.

        A movement records `source_type` and `source_id`, which identify the cause but
        are not readable: "sale, 0f3c…" tells a shopkeeper nothing, while
        "Sale · NAS/2026-27/00004" is the bill they can go and look at. Only invoices are
        resolved — they are the only source with a reference a human already knows.
        A sheet entry, an adjustment or the seed has no document, and is left absent
        rather than given a placeholder.

        One query for the whole page, not one per row.
        """
        ctx.require(Permission.CATALOG_READ)
        invoice_ids = {
            movement.source_id
            for movement in movements
            if movement.source_type == "invoice" and movement.source_id is not None
        }
        if not invoice_ids:
            return {}

        # A cross-module **read** of a table `sales` owns, the same way `balances()`
        # reads `products`. Ownership is about who may *write* a table
        # (`CLAUDE.md §4`), and resolving a label needs no sales rule applied.
        # `business_id` is still predicated, so a reference cannot leak across tenants.
        from bm.infra.db.models.sales import Invoice

        rows = self._session.execute(
            select(Invoice.id, Invoice.invoice_number).where(
                Invoice.business_id == ctx.business_id,
                Invoice.id.in_(invoice_ids),
            )
        ).all()
        by_invoice = {row[0]: row[1] for row in rows if row[1]}
        return {
            movement.id: by_invoice[movement.source_id]
            for movement in movements
            if movement.source_id in by_invoice
        }

    # --- adjustments ----------------------------------------------------------

    def adjust(
        self,
        ctx: AuthContext,
        *,
        product_id: uuid.UUID,
        quantity_delta: object,
        reason_code: str,
        reason_text: str,
    ) -> uuid.UUID:
        """A reasoned, audited correction (FR-INV-007).

        The reason is **mandatory free text** on top of a reason code, because "stock
        adjusted by -4" with no explanation is exactly the audit gap T-3 describes.
        """
        ctx.require(Permission.INVENTORY_ADJUST)

        delta = parse_decimal(quantity_delta, field_name="quantity_delta")
        if delta == ZERO:
            raise ValidationFailed({"quantity_delta": "An adjustment cannot be zero."})
        if len(reason_text.strip()) < MIN_ADJUSTMENT_REASON_LENGTH:
            raise ValidationFailed(
                {"reason_text": "Say briefly why the stock figure is being corrected."}
            )

        # The direction comes from the sign of the delta, not from the caller's reason
        # code — so a code of `adjustment_increase` with a negative delta cannot record
        # an increase that did not happen.
        reason = (
            MovementReason.ADJUSTMENT_INCREASE
            if delta > ZERO
            else MovementReason.ADJUSTMENT_DECREASE
        )
        if reason_code and reason_code not in {
            str(MovementReason.ADJUSTMENT_INCREASE),
            str(MovementReason.ADJUSTMENT_DECREASE),
            str(MovementReason.SCRAP),
            str(MovementReason.OPENING),
        }:
            raise ValidationFailed(
                {
                    "reason_code": (
                        f"`{reason_code}` is not an adjustment reason. "
                        "A sale, purchase or return is recorded through its own endpoint."
                    )
                }
            )
        if reason_code == str(MovementReason.SCRAP):
            reason = MovementReason.SCRAP
        elif reason_code == str(MovementReason.OPENING):
            reason = MovementReason.OPENING

        branch_id = ctx.requires_branch
        adjustment_id = uuid.uuid4()
        movement_ids = self.apply_movements(
            ctx,
            branch_id=branch_id,
            movements=[
                MovementRequest(
                    product_id=product_id,
                    quantity=abs(delta),
                    reason=reason,
                    source_type="adjustment",
                    source_id=adjustment_id,
                )
            ],
        )
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.INVENTORY_ADJUSTED,
            entity_type="product",
            entity_id=product_id,
            reason=reason_text.strip(),
            after={
                "delta": format(delta, "f"),
                "reason_code": reason_code,
                "movement_id": str(movement_ids[0]),
            },
        )
        return movement_ids[0]

    # --- reconciliation -------------------------------------------------------

    def reconcile(self, ctx: AuthContext) -> dict[str, object]:
        """Compare every balance with the sum of its movements.

        **Reports and never repairs** (FR-INV-006). Drift means there is a bug to find,
        and a self-healing balance table would hide it — which is why this returns a
        report and raises an alert rather than issuing a correcting movement.

        Also performs T3-04's check: for each series, `max(sequence_number) + 1` must
        equal `next_number` for committed invoices. A mismatch indicates a lost update.
        """
        ctx.require(Permission.AUDIT_READ)

        ledger_sum = (
            select(
                StockMovement.branch_id.label("branch_id"),
                StockMovement.product_id.label("product_id"),
                func.sum(StockMovement.quantity_delta).label("ledger_total"),
            )
            .where(StockMovement.business_id == ctx.business_id)
            .group_by(StockMovement.branch_id, StockMovement.product_id)
            .subquery()
        )

        rows = self._session.execute(
            select(
                InventoryBalance.branch_id,
                InventoryBalance.product_id,
                InventoryBalance.quantity,
                func.coalesce(ledger_sum.c.ledger_total, 0),
            )
            .outerjoin(
                ledger_sum,
                (ledger_sum.c.branch_id == InventoryBalance.branch_id)
                & (ledger_sum.c.product_id == InventoryBalance.product_id),
            )
            .where(InventoryBalance.business_id == ctx.business_id)
        ).all()

        discrepancies = [
            {
                "branch_id": str(r[0]),
                "product_id": str(r[1]),
                "balance": format(r[2], "f"),
                "ledger_total": format(r[3], "f"),
                "difference": format(r[2] - r[3], "f"),
            }
            for r in rows
            if r[2] != r[3]
        ]

        numbering = self._session.execute(
            text(
                """
                SELECT s.id::text AS series_id,
                       s.next_number,
                       coalesce(max(i.sequence_number), 0) AS max_sequence
                  FROM invoice_series s
                  LEFT JOIN invoices i ON i.invoice_series_id = s.id
                 WHERE s.business_id = :business_id
                 GROUP BY s.id, s.next_number
                """
            ),
            {"business_id": ctx.business_id},
        ).all()
        numbering_issues = [
            {
                "series_id": r.series_id,
                "next_number": r.next_number,
                "max_sequence": r.max_sequence,
                "expected_next": r.max_sequence + 1,
            }
            for r in numbering
            if r.max_sequence + 1 != r.next_number
        ]

        severity = "critical" if discrepancies or numbering_issues else "info"
        return {
            "balances_checked": len(rows),
            "discrepancies_found": len(discrepancies),
            "discrepancies": discrepancies,
            "numbering_issues": numbering_issues,
            "severity": severity,
            # Stated explicitly so no caller mistakes this for a repair tool.
            "action_taken": "none — discrepancies are reported, never corrected",
        }
