"""Suppliers and purchases (M15).

**Only an explicit confirm increases stock** (FR-PUR-003). Creating a purchase records
what arrived on paper; confirming it is the act that says the goods are physically on the
shelf. Conflating the two is how a supplier's delivery note becomes stock that nobody
actually received.

`uq_purchases__supplier_doc` blocks double-receiving the same supplier invoice
(FR-PUR-006), and confirm is idempotent, so a retried confirm cannot receive the same
goods twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.inventory.service import InventoryService, MovementRequest
from bm.domain.shared.clock import Clock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    DuplicateSupplierDocument,
    NotFound,
    ValidationFailed,
)
from bm.domain.shared.money import ZERO, assert_scale_within
from bm.infra.db.enums import MovementReason, PurchaseStatus
from bm.infra.db.models.credit import Purchase, PurchaseItem, Supplier
from bm.infra.db.rows import rows_affected


@dataclass(frozen=True, slots=True)
class PurchaseLine:
    product_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal


@dataclass(frozen=True, slots=True)
class PurchaseView:
    id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    supplier_document_number: str | None
    document_date: date | None
    status: str
    subtotal: Decimal
    total: Decimal
    line_count: int


class PurchaseService:
    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock
        self._inventory = InventoryService(session)

    # --- suppliers ------------------------------------------------------------

    def upsert_supplier(
        self,
        ctx: AuthContext,
        supplier_id: uuid.UUID,
        *,
        name: str,
        phone: str | None = None,
        business_name: str | None = None,
        tax_identifier: str | None = None,
    ) -> uuid.UUID:
        ctx.require(Permission.CATALOG_WRITE)
        from bm.domain.shared.normalize import normalize_name

        clean = name.strip()
        if not clean:
            raise ValidationFailed({"name": "A supplier needs a name."})

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        statement = (
            pg_insert(Supplier)
            .values(
                id=supplier_id,
                business_id=ctx.business_id,
                name=clean,
                normalized_name=normalize_name(clean),
                business_name=business_name,
                phone=phone,
                tax_identifier=tax_identifier,
            )
            .on_conflict_do_update(
                index_elements=[Supplier.business_id, Supplier.normalized_name],
                set_={"name": clean, "phone": phone, "business_name": business_name},
            )
            .returning(Supplier.id)
        )
        return self._session.execute(statement).scalar_one()

    def list_suppliers(self, ctx: AuthContext) -> list[tuple[uuid.UUID, str, str | None]]:
        ctx.require(Permission.INVENTORY_RECEIVE)
        rows = self._session.execute(
            select(Supplier.id, Supplier.name, Supplier.phone)
            .where(Supplier.business_id == ctx.business_id, Supplier.is_active.is_(True))
            .order_by(Supplier.name)
        ).all()
        return [(r[0], r[1], r[2]) for r in rows]

    # --- purchases ------------------------------------------------------------

    def create(
        self,
        ctx: AuthContext,
        *,
        supplier_id: uuid.UUID,
        lines: list[PurchaseLine],
        supplier_document_number: str | None = None,
        document_date: date | None = None,
        notes: str | None = None,
    ) -> uuid.UUID:
        """Creates a `draft` purchase. **No stock change** (FR-PUR-003)."""
        ctx.require(Permission.INVENTORY_RECEIVE)

        if not lines:
            raise ValidationFailed({"items": "A purchase needs at least one line."})
        if (
            self._session.execute(
                select(Supplier.id).where(
                    Supplier.id == supplier_id, Supplier.business_id == ctx.business_id
                )
            ).scalar_one_or_none()
            is None
        ):
            raise NotFound("That supplier does not exist.")

        purchase_id = uuid.uuid4()
        subtotal = ZERO
        prepared: list[dict[str, object]] = []

        for index, line in enumerate(lines, start=1):
            quantity = assert_scale_within(
                line.quantity, max_places=3, field_name=f"items.{index}.quantity"
            )
            cost = assert_scale_within(
                line.unit_cost, max_places=4, field_name=f"items.{index}.unit_cost"
            )
            if quantity <= ZERO:
                raise ValidationFailed({f"items.{index}.quantity": "Must be greater than zero."})
            if cost < ZERO:
                raise ValidationFailed({f"items.{index}.unit_cost": "Must not be negative."})

            name = self._session.execute(
                text("SELECT name FROM products WHERE id = :id AND business_id = :business_id"),
                {"id": line.product_id, "business_id": ctx.business_id},
            ).scalar_one_or_none()
            if name is None:
                raise ValidationFailed(
                    {f"items.{index}.product_id": "That product does not exist."}
                )

            line_total = quantity * cost
            subtotal += line_total
            prepared.append(
                {
                    "product_id": line.product_id,
                    "name": name,
                    "quantity": quantity,
                    "unit_cost": cost,
                    "line_total": line_total,
                    "line_no": index,
                }
            )

        try:
            self._session.execute(
                insert(Purchase).values(
                    id=purchase_id,
                    business_id=ctx.business_id,
                    branch_id=ctx.requires_branch,
                    supplier_id=supplier_id,
                    supplier_document_number=supplier_document_number,
                    document_date=document_date,
                    received_at=self._clock.now(),
                    status=PurchaseStatus.DRAFT,
                    subtotal=subtotal,
                    total=subtotal,
                    notes=notes,
                    created_by_user_id=ctx.user_id,
                )
            )
            self._session.flush()
        except IntegrityError as exc:
            self._session.rollback()
            existing = self._session.execute(
                select(Purchase.id, Purchase.received_at, Purchase.status).where(
                    Purchase.business_id == ctx.business_id,
                    Purchase.supplier_id == supplier_id,
                    Purchase.supplier_document_number == supplier_document_number,
                )
            ).first()
            raise DuplicateSupplierDocument(
                details={
                    "existing_purchase": {
                        "id": str(existing.id),
                        "received_at": existing.received_at.isoformat(),
                        "status": existing.status,
                    }
                }
                if existing
                else {}
            ) from exc

        for item in prepared:
            self._session.execute(
                insert(PurchaseItem).values(
                    id=uuid.uuid4(),
                    purchase_id=purchase_id,
                    business_id=ctx.business_id,
                    product_id=item["product_id"],
                    product_name_snapshot=item["name"],
                    quantity=item["quantity"],
                    unit_cost=item["unit_cost"],
                    line_total=item["line_total"],
                    line_no=item["line_no"],
                )
            )
        return purchase_id

    def confirm(self, ctx: AuthContext, purchase_id: uuid.UUID) -> int:
        """**The only path that receives stock** (FR-PUR-003).

        Idempotent: a purchase already `confirmed` returns without creating a second set
        of movements, so a retried confirm cannot receive the same goods twice. The
        status guard in the `UPDATE` is what makes that atomic rather than a
        check-then-act race.
        """
        ctx.require(Permission.INVENTORY_RECEIVE)

        purchase = self._session.execute(
            select(Purchase)
            .where(Purchase.id == purchase_id, Purchase.business_id == ctx.business_id)
            .with_for_update()
        ).scalar_one_or_none()
        if purchase is None:
            raise NotFound("That purchase does not exist.")
        if str(purchase.status) == str(PurchaseStatus.CONFIRMED):
            return 0  # already received; the caller's intent is satisfied
        if str(purchase.status) == str(PurchaseStatus.CANCELLED):
            raise ValidationFailed({"status": "A cancelled purchase cannot be confirmed."})

        items = list(
            self._session.execute(
                select(PurchaseItem).where(PurchaseItem.purchase_id == purchase_id)
            )
            .scalars()
            .all()
        )
        if not items:
            raise ValidationFailed({"items": "This purchase has no lines."})

        movements = self._inventory.apply_movements(
            ctx,
            branch_id=purchase.branch_id,
            movements=[
                MovementRequest(
                    product_id=item.product_id,
                    quantity=item.quantity,
                    reason=MovementReason.PURCHASE_RECEIPT,
                    source_type="purchase",
                    source_id=purchase_id,
                    unit_cost=item.unit_cost,
                )
                for item in items
            ],
        )

        # The last cost price is updated from the receipt, so margin reporting reflects
        # what the shop actually paid most recently.
        for item in items:
            self._session.execute(
                text(
                    "UPDATE products SET last_cost_price = :cost, updated_at = now()"
                    " WHERE id = :id AND business_id = :business_id"
                ),
                {
                    "cost": item.unit_cost,
                    "id": item.product_id,
                    "business_id": ctx.business_id,
                },
            )

        self._session.execute(
            update(Purchase)
            .where(Purchase.id == purchase_id, Purchase.status == PurchaseStatus.DRAFT)
            .values(status=PurchaseStatus.CONFIRMED, confirmed_at=self._clock.now())
        )
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.PURCHASE_CONFIRMED,
            entity_type="purchase",
            entity_id=purchase_id,
            after={"movements": len(movements), "total": format(purchase.total, "f")},
        )
        return len(movements)

    def cancel(self, ctx: AuthContext, purchase_id: uuid.UUID) -> None:
        """Only while `draft`. A confirmed receipt is immutable — correcting it is a
        supplier return or an adjustment, not an edit."""
        ctx.require(Permission.INVENTORY_RECEIVE)
        affected = rows_affected(
            self._session.execute(
                update(Purchase)
                .where(
                    Purchase.id == purchase_id,
                    Purchase.business_id == ctx.business_id,
                    Purchase.status == PurchaseStatus.DRAFT,
                )
                .values(status=PurchaseStatus.CANCELLED)
            )
        )
        if not affected:
            existing = self._session.execute(
                select(Purchase.status).where(
                    Purchase.id == purchase_id, Purchase.business_id == ctx.business_id
                )
            ).scalar_one_or_none()
            if existing is None:
                raise NotFound("That purchase does not exist.")
            raise ValidationFailed({"status": "Only a draft purchase can be cancelled."})
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.PURCHASE_CANCELLED,
            entity_type="purchase",
            entity_id=purchase_id,
        )

    def list_purchases(
        self, ctx: AuthContext, *, supplier_id: uuid.UUID | None = None, limit: int = 100
    ) -> list[PurchaseView]:
        ctx.require(Permission.INVENTORY_RECEIVE)
        query = (
            select(
                Purchase.id,
                Purchase.supplier_id,
                Supplier.name,
                Purchase.supplier_document_number,
                Purchase.document_date,
                Purchase.status,
                Purchase.subtotal,
                Purchase.total,
            )
            .join(Supplier, Supplier.id == Purchase.supplier_id)
            .where(Purchase.business_id == ctx.business_id)
            .order_by(Purchase.received_at.desc())
            .limit(min(limit, 200))
        )
        if supplier_id is not None:
            query = query.where(Purchase.supplier_id == supplier_id)

        rows = self._session.execute(query).all()
        return [
            PurchaseView(
                id=r[0],
                supplier_id=r[1],
                supplier_name=r[2],
                supplier_document_number=r[3],
                document_date=r[4],
                status=r[5],
                subtotal=r[6],
                total=r[7],
                line_count=int(
                    self._session.execute(
                        text("SELECT count(*) FROM purchase_items WHERE purchase_id = :id"),
                        {"id": r[0]},
                    ).scalar_one()
                ),
            )
            for r in rows
        ]
