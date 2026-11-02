"""Spreadsheet-style stock entry — the owner's Excel inventory sheet, as a workflow.

The shop keeps stock in a four-column sheet: **Item · Price · Min Qty · Stock Qty**. One
row is one delivery of one part. This module is that row, applied.

Three things make it a domain service rather than a router helper:

* **It spans two modules.** The product (name, price, minimum) belongs to `catalog`;
  the stock belongs to `inventory`. Each is written only through its own service, so
  table ownership is intact (`CLAUDE.md §4`) and the orchestration lives here — the same
  shape `imports` already uses for the same reason.
* **`Stock Qty` is an arrival, never a correction.** `200` means *two hundred more
  arrived*, so an existing balance of `20` becomes `220`. Reading it as "set the stock to
  200" would silently destroy 20 units of real inventory, and it is the single most
  expensive mistake this file can make. There is deliberately no code path here that
  assigns a balance.
* **Matching has to be able to say "I don't know".** `products.normalized_name` carries
  no unique index — only `normalized_sku` and `barcode` do — so an exact normalised match
  can legitimately return two rows. Guessing between them would merge two genuinely
  different parts and their stock. `AMBIGUOUS` is a refusal the user resolves, not a
  branch that picks one.

Matching is **exact on the normalised name** (`normalize_name`: lower-cased,
accent-stripped, whitespace-collapsed), which is what makes `Axel Packing`,
`axel packing` and `Axel  Packing` one part. It is deliberately *not* fuzzy or
prefix-based: `Brake Shoe Set Ace` and `Brake Shoe Set Bolero` share a long prefix and
are different parts, and trigram similarity is the right tool for *finding* a product,
never for deciding that two names are the same one.

The ledger reason is chosen from the product's history, not from the caller:

| Situation | `movement_reason` | Shown as |
|---|---|---|
| A part the shop has never stocked | `opening` | "Opening stock" |
| More of a part it already stocks | `adjustment_increase` | "Stock received" |

`purchase_receipt` is **not** used, even though "stock received" is what it sounds like:
that reason receives stock only through `PurchaseService`, against a real supplier
document (`imports/service.py` states the same rule). A sheet row has no purchase behind
it, so claiming one would put an orphan receipt into the purchase ledger. `source_type`
is `stock_entry`, which keeps these apart from a reasoned correction (`adjustment`) and
from the seed (`seed`) in the product's history.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.catalog.service import CatalogService
from bm.domain.inventory.service import InventoryService, MovementRequest
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import ProductInactive, ValidationFailed
from bm.domain.shared.money import (
    ZERO,
    assert_not_negative,
    assert_scale_within,
    parse_decimal,
)
from bm.domain.shared.normalize import normalize_name
from bm.domain.shared.quantity import UnitSpec, validate_quantity
from bm.infra.db.enums import MovementReason

#: `source_type` on every movement this module writes. Distinguishes a sheet arrival
#: from a reasoned correction (`adjustment`) and from the seed (`seed`) when the
#: product's history is read back.
SOURCE_TYPE = "stock_entry"

#: Unit given to a product created from a sheet row. The sheet has no unit column, and
#: a spare part is counted in pieces. `PCS` is integer-only, which is also the strict
#: choice: refusing `2.5` pieces is recoverable, accepting it is not.
DEFAULT_UNIT_CODE = "PCS"

#: One save is one sheet. Bounded so a scripted caller cannot hold a write transaction
#: open across thousands of products.
MAX_ENTRIES_PER_BATCH = 200


@dataclass(frozen=True, slots=True)
class StockEntryInput:
    """One row of the sheet, exactly as typed."""

    item: str
    price: object
    min_qty: object
    stock_qty: object


class MatchKind:
    """What a name resolved to. Strings, because they cross the API boundary."""

    NEW = "new"
    EXISTING = "existing"
    AMBIGUOUS = "ambiguous"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class StockEntryMatch:
    """The answer to "what will saving this row do?".

    Returned by `resolve` so the screen can say *"will update existing stock"* or
    *"will add a new product"* **before** anything is written, and produced by the same
    code the save uses — a preview that disagreed with the save would be worse than no
    preview.
    """

    kind: str
    product_id: uuid.UUID | None = None
    product_name: str | None = None
    current_quantity: Decimal | None = None
    current_price: Decimal | None = None
    current_min_qty: Decimal | None = None
    #: Populated for `AMBIGUOUS` only: the rows the user has to choose between.
    candidates: tuple[tuple[uuid.UUID, str], ...] = ()


@dataclass(frozen=True, slots=True)
class StockEntryResult:
    """What one saved row actually did."""

    item: str
    product_id: uuid.UUID
    created: bool
    quantity_added: Decimal
    quantity_before: Decimal
    quantity_after: Decimal
    movement_id: uuid.UUID
    reason: str


class StockEntryService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._catalog = CatalogService(session)
        self._inventory = InventoryService(session)

    # --- resolution -----------------------------------------------------------

    def resolve(self, ctx: AuthContext, name: str) -> StockEntryMatch:
        """Decide what this name means, without writing anything.

        Read-only, and the authority for the save path too (`_match`), so the preview on
        screen and the behaviour on save cannot drift apart.
        """
        ctx.require(Permission.CATALOG_READ)
        return self._match(ctx, name)

    def _match(self, ctx: AuthContext, name: str) -> StockEntryMatch:
        clean = name.strip()
        if not clean:
            raise ValidationFailed({"item": "Type the item name."})

        matches = self._catalog.find_by_exact_name(ctx, clean)

        if not matches:
            return StockEntryMatch(kind=MatchKind.NEW)

        if len(matches) > 1:
            # Two products share this normalised name. Picking one would merge the stock
            # of two different parts, so the choice goes back to the person who can see
            # the shelf.
            return StockEntryMatch(
                kind=MatchKind.AMBIGUOUS,
                candidates=tuple(
                    (product_id, product_name) for product_id, product_name, _ in matches
                ),
            )

        product_id, product_name, is_active = matches[0]
        if not is_active:
            # Not created as a duplicate and not silently revived either: a part that was
            # withdrawn is a decision somebody took, and undoing it belongs on the
            # product, not in a stock arrival.
            return StockEntryMatch(
                kind=MatchKind.INACTIVE,
                product_id=product_id,
                product_name=product_name,
            )

        view = self._catalog.get(ctx, product_id)
        return StockEntryMatch(
            kind=MatchKind.EXISTING,
            product_id=product_id,
            product_name=view.name,
            current_quantity=(view.branch_quantity if view.branch_quantity is not None else ZERO),
            current_price=view.default_selling_price,
            current_min_qty=view.reorder_level,
        )

    # --- the write path -------------------------------------------------------

    def record(self, ctx: AuthContext, entries: list[StockEntryInput]) -> list[StockEntryResult]:
        """Apply a whole sheet, or none of it.

        **All or nothing.** Every row is validated and resolved before any of them is
        written, so a sheet with one ambiguous name does not leave the first four rows
        received and the rest not — a half-applied delivery is worse than a rejected one,
        because nobody can tell from the screen which half landed.

        It does not commit: it joins the request's transaction, so the products and the
        movements either both exist or neither does. Called behind an
        `Idempotency-Key`, which is what makes a double-click or a retried request one
        delivery rather than two.
        """
        # Re-asserted here as well as on the route: a sheet row writes a product *and*
        # stock, and an internal caller must not be able to do either by going around
        # the router (`SECURITY.md §3.1` rule 2).
        ctx.require(Permission.CATALOG_WRITE)
        ctx.require(Permission.INVENTORY_ADJUST)
        branch_id = ctx.requires_branch

        if not entries:
            raise ValidationFailed({"entries": "Add at least one item."})
        if len(entries) > MAX_ENTRIES_PER_BATCH:
            raise ValidationFailed(
                {"entries": f"Save at most {MAX_ENTRIES_PER_BATCH} items at a time."}
            )

        # --- phase 1: validate and resolve every row, writing nothing ---------
        prepared: list[tuple[StockEntryInput, Decimal, Decimal, Decimal, StockEntryMatch]] = []
        seen_names: dict[str, int] = {}

        for index, entry in enumerate(entries):
            field = f"entries[{index}]"
            clean_name = entry.item.strip()
            if not clean_name:
                raise ValidationFailed({f"{field}.item": "Type the item name."})

            price = assert_not_negative(
                assert_scale_within(
                    parse_decimal(entry.price, field_name=f"{field}.price"),
                    max_places=4,
                    field_name=f"{field}.price",
                ),
                field_name=f"{field}.price",
            )
            min_qty = assert_not_negative(
                assert_scale_within(
                    parse_decimal(entry.min_qty, field_name=f"{field}.min_qty"),
                    max_places=3,
                    field_name=f"{field}.min_qty",
                ),
                field_name=f"{field}.min_qty",
            )
            incoming = parse_decimal(entry.stock_qty, field_name=f"{field}.stock_qty")
            if incoming <= ZERO:
                # An arrival of zero or less is not an arrival. A reduction is a reasoned
                # adjustment, which is a different endpoint with a mandatory explanation.
                raise ValidationFailed(
                    {
                        f"{field}.stock_qty": (
                            "Enter the quantity that came in, above zero. "
                            "To reduce stock, use a stock correction instead."
                        )
                    }
                )

            # The same part twice in one sheet: refused rather than added twice or
            # silently collapsed, because which the user meant is genuinely unknown.
            normalized = normalize_name(clean_name)
            if normalized in seen_names:
                raise ValidationFailed(
                    {
                        f"{field}.item": (
                            f"{clean_name!r} is already on row {seen_names[normalized] + 1} "
                            "of this sheet. Put the whole quantity on one row."
                        )
                    }
                )
            seen_names[normalized] = index

            match = self._match(ctx, clean_name)
            if match.kind == MatchKind.AMBIGUOUS:
                raise ValidationFailed(
                    {
                        f"{field}.item": (
                            f"More than one product is called {clean_name!r}. "
                            "Open Stock and add the quantity to the right one."
                        )
                    }
                )
            if match.kind == MatchKind.INACTIVE:
                raise ProductInactive(
                    f"{clean_name!r} is no longer stocked. Make it active again before "
                    "adding stock to it."
                )

            unit = (
                self._catalog.unit_spec_for(match.product_id)
                if match.product_id is not None
                else UnitSpec(code=DEFAULT_UNIT_CODE, allows_fraction=False, decimal_places=0)
            )
            validate_quantity(incoming, unit, field_name=f"{field}.stock_qty")

            prepared.append((entry, price, min_qty, incoming, match))

        # --- phase 2: write ---------------------------------------------------
        results: list[StockEntryResult] = []
        for entry, price, min_qty, incoming, match in prepared:
            clean_name = entry.item.strip()

            if match.kind == MatchKind.NEW:
                product_id = self._create(ctx, clean_name, price=price, min_qty=min_qty)
                quantity_before = ZERO
                # A part the shop has never held: its first figure is opening stock, which
                # is what the owner's own sheet calls it.
                reason = MovementReason.OPENING
            else:
                assert match.product_id is not None
                product_id = match.product_id
                quantity_before = (
                    match.current_quantity if match.current_quantity is not None else ZERO
                )
                # Price and minimum follow the sheet — the row the owner just typed is the
                # current intent — and go through `update_product`, so a price change is
                # audited with before/after like any other (FR-AUDIT-003).
                self._catalog.update_product(
                    ctx,
                    product_id,
                    default_selling_price=price,
                    reorder_level=min_qty,
                )
                reason = MovementReason.ADJUSTMENT_INCREASE

            entry_id = uuid.uuid4()
            movement_ids = self._inventory.apply_movements(
                ctx,
                branch_id=branch_id,
                movements=[
                    MovementRequest(
                        product_id=product_id,
                        # Positive, always. The direction comes from `reason`, and both
                        # reasons used here increase stock — so this quantity is added to
                        # whatever is already on the shelf and can never replace it.
                        quantity=incoming,
                        reason=reason,
                        source_type=SOURCE_TYPE,
                        source_id=entry_id,
                    )
                ],
            )
            movement_id = movement_ids[0]
            # Read back rather than computed: `apply_movements` records what the database
            # actually holds after the update, which is the figure that matters under
            # concurrency.
            quantity_after = self._inventory.balance_for(branch_id=branch_id, product_id=product_id)

            audit.record_for(
                self._session,
                ctx,
                action=AuditAction.INVENTORY_ADJUSTED,
                entity_type="product",
                entity_id=product_id,
                reason="Stock entry sheet",
                before={"quantity": format(quantity_before, "f")},
                after={
                    "quantity": format(quantity_after, "f"),
                    "quantity_added": format(incoming, "f"),
                    "price": format(price, "f"),
                    "min_qty": format(min_qty, "f"),
                    "reason_code": str(reason),
                    "source_type": SOURCE_TYPE,
                    "movement_id": str(movement_id),
                    "created_product": match.kind == MatchKind.NEW,
                },
            )

            results.append(
                StockEntryResult(
                    item=clean_name,
                    product_id=product_id,
                    created=match.kind == MatchKind.NEW,
                    quantity_added=incoming,
                    quantity_before=quantity_before,
                    quantity_after=quantity_after,
                    movement_id=movement_id,
                    reason=str(reason),
                )
            )

        return results

    def _create(
        self, ctx: AuthContext, name: str, *, price: Decimal, min_qty: Decimal
    ) -> uuid.UUID:
        """Create the product a sheet row named, with no part code.

        No SKU is invented. The sheet has no part-number column, and a generated code
        would collide with the real one the next price list brings in — `normalized_sku`
        is uniquely indexed, so an invented code is a duplicate waiting to happen.
        """
        units = self._catalog.ensure_default_units(ctx)
        unit_code = next(
            (u.code for u in units if u.code.upper() == DEFAULT_UNIT_CODE),
            units[0].code if units else DEFAULT_UNIT_CODE,
        )
        product = self._catalog.create_product(
            ctx,
            name=name,
            unit_code=unit_code,
            default_selling_price=price,
            reorder_level=min_qty,
        )
        return product.id
