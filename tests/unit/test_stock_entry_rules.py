"""The owner's four-column stock sheet: Item · Price · Min Qty · Stock Qty.

The test this file exists for is `test_an_existing_product_gains_stock_rather_than_having_it_replaced`.
Reading `Stock Qty` as "set the stock to this" instead of "this much arrived" would
silently destroy real inventory — 20 on the shelf plus a typed 200 must be 220, never
200 — and nothing downstream would reveal it, because the balance table would agree with
the ledger either way. It is asserted on the balance, on the movement delta and on the
returned before/after triple, so no single mistake can satisfy all three.

The rest covers the identity rules: case and spacing do not create a duplicate, a genuine
two-way name collision is refused rather than guessed, and a part the shop withdrew is not
silently revived.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import Permission, SystemRole, permissions_for_role
from bm.domain.inventory.service import InventoryService
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import PermissionDenied, ProductInactive, ValidationFailed
from bm.domain.stockentry.service import (
    SOURCE_TYPE,
    MatchKind,
    StockEntryInput,
    StockEntryService,
)
from bm.infra.db.enums import MovementReason
from tests.fixtures import Tenant, create_user

pytestmark = pytest.mark.db

D = Decimal

_USER_ID: uuid.UUID = uuid.uuid4()
_DEVICE_ID: uuid.UUID = uuid.uuid4()


def _ctx(tenant: Tenant, role: SystemRole = SystemRole.OWNER) -> AuthContext:
    return AuthContext(
        user_id=_USER_ID,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=tenant.counter_id,
        device_id=_DEVICE_ID,
        role=str(role),
        permissions=frozenset(str(p) for p in permissions_for_role(role)),
        token_id="stock-entry",
        business_timezone=tenant.timezone,
        full_name="Test",
    )


@pytest.fixture(autouse=True)
def acting_user(migration_session: Session, tenant: Tenant) -> uuid.UUID:
    global _USER_ID, _DEVICE_ID
    user = create_user(migration_session, tenant, username="sheetkeeper", role=SystemRole.OWNER)
    _USER_ID = user.id
    _DEVICE_ID = user.device_id
    return user.id


@pytest.fixture
def service(migration_session: Session) -> StockEntryService:
    return StockEntryService(migration_session)


@pytest.fixture
def inventory(migration_session: Session) -> InventoryService:
    return InventoryService(migration_session)


def _row(item: str, price: str, min_qty: str, stock_qty: str) -> StockEntryInput:
    return StockEntryInput(item=item, price=D(price), min_qty=D(min_qty), stock_qty=D(stock_qty))


def _quantity(session: Session, tenant: Tenant, product_id: uuid.UUID) -> Decimal:
    return session.execute(
        text("SELECT quantity FROM inventory_balances WHERE product_id = :p AND branch_id = :b"),
        {"p": product_id, "b": tenant.branch_id},
    ).scalar_one()


def _product(session: Session, product_id: uuid.UUID) -> tuple[Decimal, Decimal | None, str]:
    row = session.execute(
        text("SELECT default_selling_price, reorder_level, name FROM products WHERE id = :p"),
        {"p": product_id},
    ).one()
    return row[0], row[1], row[2]


# --- creating a part the shop has never stocked -------------------------------


def test_a_new_item_is_created_with_its_price_minimum_and_opening_stock(
    service: StockEntryService,
    inventory: InventoryService,
    tenant: Tenant,
    migration_session: Session,
) -> None:
    """The owner's own example row: Axel Packing · 30 · 50 · 200."""
    [result] = service.record(_ctx(tenant), [_row("Axel Packing", "30", "50", "200")])

    assert result.created is True
    assert result.quantity_before == D("0")
    assert result.quantity_after == D("200.000")
    assert result.quantity_added == D("200")
    # A part the shop has never held opens at this figure, which is what the owner's
    # sheet calls it.
    assert result.reason == str(MovementReason.OPENING)

    price, min_qty, name = _product(migration_session, result.product_id)
    assert name == "Axel Packing"
    assert price == D("30.0000")
    # Per product, from this row — never one threshold applied to the whole catalog.
    assert min_qty == D("50.000")
    assert _quantity(migration_session, tenant, result.product_id) == D("200.000")


def test_the_opening_movement_records_the_sheet_as_its_source(
    service: StockEntryService, inventory: InventoryService, tenant: Tenant
) -> None:
    """`source_type` separates a sheet arrival from a correction and from the seed.

    Without it the history cannot tell "200 came in" from "somebody corrected the figure
    by 200", and those are different events to the person reading it.
    """
    [result] = service.record(_ctx(tenant), [_row("Fan Belt", "320", "8", "12")])

    [movement] = inventory.movements(_ctx(tenant), product_id=result.product_id)
    assert movement.source_type == SOURCE_TYPE
    assert movement.quantity_delta == D("12.000")
    assert movement.balance_after == D("12.000")
    # Never `purchase_receipt`: that reason belongs to `PurchaseService` and a real
    # supplier document, and claiming it here would put an orphan receipt in the
    # purchase ledger.
    assert str(movement.reason) != str(MovementReason.PURCHASE_RECEIPT)


# --- the rule that matters: stock is ADDED, never replaced ---------------------


def test_an_existing_product_gains_stock_rather_than_having_it_replaced(
    service: StockEntryService,
    inventory: InventoryService,
    tenant: Tenant,
    migration_session: Session,
) -> None:
    """20 on the shelf, 200 typed, 220 afterwards. **Not 200.**

    The one mistake in this module that destroys real inventory and leaves no trace, so it
    is asserted three independent ways: the balance row, the ledger delta, and the
    before/after the service reports back.
    """
    ctx = _ctx(tenant)
    [first] = service.record(ctx, [_row("Axel Packing", "30", "50", "20")])
    assert _quantity(migration_session, tenant, first.product_id) == D("20.000")

    [second] = service.record(ctx, [_row("Axel Packing", "30", "50", "200")])

    # Same product, not a second one.
    assert second.product_id == first.product_id
    assert second.created is False
    # 1. The service's own account of what it did.
    assert second.quantity_before == D("20.000")
    assert second.quantity_added == D("200")
    assert second.quantity_after == D("220.000")
    # 2. The balance the database actually holds.
    assert _quantity(migration_session, tenant, second.product_id) == D("220.000")
    # 3. The ledger: the arrival is +200, not a correction to 200.
    movements = inventory.movements(ctx, product_id=first.product_id, oldest_first=True)
    assert [m.quantity_delta for m in movements] == [D("20.000"), D("200.000")]
    assert [m.balance_after for m in movements] == [D("20.000"), D("220.000")]
    assert str(movements[1].reason) == str(MovementReason.ADJUSTMENT_INCREASE)


def test_a_later_sheet_row_updates_the_price_and_minimum(
    service: StockEntryService, tenant: Tenant, migration_session: Session
) -> None:
    """The row the owner just typed is the current intent for price and minimum."""
    ctx = _ctx(tenant)
    [first] = service.record(ctx, [_row("Brake Shoe Set Ace", "870", "4", "10")])
    service.record(ctx, [_row("Brake Shoe Set Ace", "920", "6", "5")])

    price, min_qty, _ = _product(migration_session, first.product_id)
    assert price == D("920.0000")
    assert min_qty == D("6.000")
    # The stock still accumulated; a price change does not reset it.
    assert _quantity(migration_session, tenant, first.product_id) == D("15.000")


# --- identity: one part, however it is typed ----------------------------------


@pytest.mark.parametrize(
    "second_spelling",
    [
        "axel packing",  # case
        "AXEL PACKING",
        "  Axel Packing  ",  # surrounding space
        "Axel  Packing",  # collapsed inner space
    ],
)
def test_case_and_spacing_do_not_create_a_duplicate_product(
    service: StockEntryService, tenant: Tenant, migration_session: Session, second_spelling: str
) -> None:
    ctx = _ctx(tenant)
    [first] = service.record(ctx, [_row("Axel Packing", "30", "50", "20")])
    [second] = service.record(ctx, [_row(second_spelling, "30", "50", "5")])

    assert second.product_id == first.product_id
    assert second.created is False
    count = migration_session.execute(
        text(
            "SELECT count(*) FROM products WHERE business_id = :b AND normalized_name = 'axel packing'"
        ),
        {"b": tenant.business_id},
    ).scalar_one()
    assert count == 1


def test_two_parts_sharing_a_long_prefix_stay_two_parts(
    service: StockEntryService, tenant: Tenant
) -> None:
    """Matching is exact, not prefix or fuzzy.

    `Brake Shoe Set Ace` and `Brake Shoe Set Bolero` are different parts for different
    vehicles. A similarity match would merge their stock.
    """
    ctx = _ctx(tenant)
    [ace] = service.record(ctx, [_row("Brake Shoe Set Ace", "870", "4", "10")])
    [bolero] = service.record(ctx, [_row("Brake Shoe Set Bolero", "980", "4", "15")])

    assert ace.product_id != bolero.product_id
    assert ace.created and bolero.created


def test_an_ambiguous_name_is_refused_rather_than_guessed(
    service: StockEntryService, tenant: Tenant, migration_session: Session
) -> None:
    """`products.normalized_name` has no unique index, so two rows can share one name.

    Choosing between them would merge two different parts' stock, so the sheet refuses and
    says where to resolve it. This is the case a `scalar_one_or_none()` lookup would have
    got wrong.
    """
    ctx = _ctx(tenant)
    [first] = service.record(ctx, [_row("Oil Seal", "120", "5", "10")])
    # A second product with the same normalised name, as an import or a hand edit could
    # legitimately produce.
    migration_session.execute(
        text(
            "INSERT INTO products"
            " (id, business_id, name, normalized_name, unit_id, default_selling_price,"
            "  is_active, search_text)"
            " SELECT gen_random_uuid(), business_id, 'OIL SEAL', normalized_name, unit_id,"
            "        130, true, search_text FROM products WHERE id = :p"
        ),
        {"p": first.product_id},
    )
    migration_session.flush()

    assert service.resolve(ctx, "Oil Seal").kind == MatchKind.AMBIGUOUS
    with pytest.raises(ValidationFailed) as refused:
        service.record(ctx, [_row("Oil Seal", "120", "5", "10")])
    assert "More than one product" in str(refused.value.details)


def test_a_withdrawn_part_is_neither_duplicated_nor_silently_revived(
    service: StockEntryService, tenant: Tenant, migration_session: Session
) -> None:
    ctx = _ctx(tenant)
    [first] = service.record(ctx, [_row("Discontinued Clip", "40", "2", "6")])
    migration_session.execute(
        text("UPDATE products SET is_active = false WHERE id = :p"), {"p": first.product_id}
    )
    migration_session.flush()

    assert service.resolve(ctx, "Discontinued Clip").kind == MatchKind.INACTIVE
    with pytest.raises(ProductInactive):
        service.record(ctx, [_row("Discontinued Clip", "40", "2", "6")])

    # No duplicate was created as a side effect of the refusal.
    count = migration_session.execute(
        text(
            "SELECT count(*) FROM products WHERE business_id = :b AND normalized_name = 'discontinued clip'"
        ),
        {"b": tenant.business_id},
    ).scalar_one()
    assert count == 1


# --- resolution preview --------------------------------------------------------


def test_resolve_tells_the_screen_whether_it_will_add_or_update(
    service: StockEntryService, tenant: Tenant
) -> None:
    """The preview and the save share one matcher, so they cannot promise different things."""
    ctx = _ctx(tenant)
    assert service.resolve(ctx, "Never Stocked").kind == MatchKind.NEW

    service.record(ctx, [_row("Engine Oil 15W40", "420", "20", "44")])
    match = service.resolve(ctx, "engine oil 15w40")
    assert match.kind == MatchKind.EXISTING
    # The figures the screen shows as `44 + n`.
    assert match.current_quantity == D("44.000")
    assert match.current_price == D("420.0000")
    assert match.current_min_qty == D("20.000")


# --- validation ----------------------------------------------------------------


@pytest.mark.parametrize("bad_quantity", ["0", "-1", "-200"])
def test_an_arrival_of_zero_or_less_is_refused(
    service: StockEntryService, tenant: Tenant, bad_quantity: str
) -> None:
    """A reduction is a reasoned correction on its own endpoint, not a negative arrival."""
    with pytest.raises(ValidationFailed) as refused:
        service.record(_ctx(tenant), [_row("Axel Packing", "30", "50", bad_quantity)])
    assert "stock_qty" in str(refused.value.details)


def test_a_blank_item_name_is_refused(service: StockEntryService, tenant: Tenant) -> None:
    with pytest.raises(ValidationFailed):
        service.record(_ctx(tenant), [_row("   ", "30", "50", "10")])


def test_a_negative_price_or_minimum_is_refused(service: StockEntryService, tenant: Tenant) -> None:
    with pytest.raises(ValidationFailed):
        service.record(_ctx(tenant), [_row("Axel Packing", "-1", "50", "10")])
    with pytest.raises(ValidationFailed):
        service.record(_ctx(tenant), [_row("Axel Packing", "30", "-5", "10")])


def test_an_empty_sheet_is_refused(service: StockEntryService, tenant: Tenant) -> None:
    with pytest.raises(ValidationFailed):
        service.record(_ctx(tenant), [])


def test_the_same_part_twice_in_one_sheet_is_refused(
    service: StockEntryService, tenant: Tenant
) -> None:
    """Refused rather than received twice or silently collapsed.

    Whether the user meant 20, 200 or 220 is genuinely unknown, and guessing either way
    gets the shelf wrong.
    """
    with pytest.raises(ValidationFailed) as refused:
        service.record(
            _ctx(tenant),
            [_row("Axel Packing", "30", "50", "20"), _row("axel  packing", "30", "50", "200")],
        )
    assert "already on row 1" in str(refused.value.details)


def test_a_fractional_quantity_is_refused_for_a_whole_unit_part(
    service: StockEntryService, tenant: Tenant
) -> None:
    """A sheet row creates a `PCS` product, which is sold in whole units (E-18)."""
    with pytest.raises(ValidationFailed):
        service.record(_ctx(tenant), [_row("Axel Packing", "30", "50", "2.5")])


# --- all or nothing ------------------------------------------------------------


def test_a_sheet_with_one_bad_row_applies_none_of_it(
    service: StockEntryService, tenant: Tenant, migration_session: Session
) -> None:
    """A half-received delivery is worse than a rejected one: nobody can tell from the
    screen which half landed."""
    ctx = _ctx(tenant)
    with pytest.raises(ValidationFailed):
        service.record(
            ctx,
            [
                _row("Good Row One", "30", "5", "10"),
                _row("Good Row Two", "40", "5", "10"),
                _row("Bad Row", "40", "5", "0"),  # refused
            ],
        )

    created = migration_session.execute(
        text(
            "SELECT count(*) FROM products WHERE business_id = :b"
            " AND normalized_name IN ('good row one', 'good row two', 'bad row')"
        ),
        {"b": tenant.business_id},
    ).scalar_one()
    assert created == 0


def test_a_whole_sheet_of_several_rows_is_applied_together(
    service: StockEntryService, tenant: Tenant, migration_session: Session
) -> None:
    results = service.record(
        _ctx(tenant),
        [
            _row("Axel Packing", "30", "50", "200"),
            _row("Brake Shoe Set Ace", "870", "4", "10"),
            _row("Engine Oil 15W40", "420", "20", "60"),
        ],
    )
    assert [r.item for r in results] == [
        "Axel Packing",
        "Brake Shoe Set Ace",
        "Engine Oil 15W40",
    ]
    assert all(r.created for r in results)
    assert [_quantity(migration_session, tenant, r.product_id) for r in results] == [
        D("200.000"),
        D("10.000"),
        D("60.000"),
    ]


# --- permissions ---------------------------------------------------------------


def test_saving_a_sheet_needs_both_catalog_write_and_inventory_adjust(
    service: StockEntryService, tenant: Tenant
) -> None:
    """A sheet row writes a product **and** moves stock, so both are re-asserted in the
    service — UI-level hiding is not a control (NFR-SEC-004)."""
    base = _ctx(tenant)

    catalog_only = AuthContext(
        user_id=base.user_id,
        business_id=base.business_id,
        branch_id=base.branch_id,
        counter_id=base.counter_id,
        device_id=base.device_id,
        role="limited",
        permissions=frozenset({str(Permission.CATALOG_READ), str(Permission.CATALOG_WRITE)}),
        token_id="t",
        business_timezone=tenant.timezone,
    )
    with pytest.raises(PermissionDenied):
        service.record(catalog_only, [_row("Axel Packing", "30", "50", "10")])

    stock_only = AuthContext(
        user_id=base.user_id,
        business_id=base.business_id,
        branch_id=base.branch_id,
        counter_id=base.counter_id,
        device_id=base.device_id,
        role="limited",
        permissions=frozenset({str(Permission.CATALOG_READ), str(Permission.INVENTORY_ADJUST)}),
        token_id="t",
        business_timezone=tenant.timezone,
    )
    with pytest.raises(PermissionDenied):
        service.record(stock_only, [_row("Axel Packing", "30", "50", "10")])


def test_resolving_a_name_needs_catalog_read(service: StockEntryService, tenant: Tenant) -> None:
    nobody = AuthContext(
        user_id=uuid.uuid4(),
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=None,
        device_id=None,
        role="nobody",
        permissions=frozenset(),
        token_id="t",
        business_timezone=tenant.timezone,
    )
    with pytest.raises(PermissionDenied):
        service.resolve(nobody, "Axel Packing")


# --- the history the Stock screen shows ---------------------------------------


def test_the_product_history_is_the_whole_ledger_in_order(
    service: StockEntryService, inventory: InventoryService, tenant: Tenant
) -> None:
    """The owner's example history: opening, then a receipt, each with its running total.

    Read from the ledger's own `balance_after`, never reconstructed from today's figure.
    """
    ctx = _ctx(tenant)
    [first] = service.record(ctx, [_row("Axel Packing", "30", "50", "100")])
    service.record(ctx, [_row("Axel Packing", "30", "50", "200")])
    service.record(ctx, [_row("Axel Packing", "30", "50", "50")])

    history = inventory.movements(ctx, product_id=first.product_id, oldest_first=True)
    assert [(str(m.reason), m.quantity_delta, m.balance_after) for m in history] == [
        (str(MovementReason.OPENING), D("100.000"), D("100.000")),
        (str(MovementReason.ADJUSTMENT_INCREASE), D("200.000"), D("300.000")),
        (str(MovementReason.ADJUSTMENT_INCREASE), D("50.000"), D("350.000")),
    ]
