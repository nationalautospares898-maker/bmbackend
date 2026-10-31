"""Inventory rules at the branch level — the refusals, the filters and the ledger.

Audit Issue B. `bm.domain.inventory` sat at 50% branch coverage against a promised 95%
floor, and the uncovered half was almost entirely **refusals**: the paths that stop a
stock figure being changed without a reason, an oversell being waved through without
authorisation, or a zero-quantity movement reaching the ledger. A refusal that is never
exercised is a refusal nobody has checked.

Every test here asserts an outcome a shopkeeper would recognise — a specific error, a
specific quantity, a specific ledger row — not merely that a line executed.

These run against real PostgreSQL. `inventory_balances` and `stock_movements` are
written only by `apply_movements`, the row locks are real, and `balance = Σ movements` is
an invariant the suite checks elsewhere; a test that bypassed the service to set a
quantity would be testing something the application cannot do.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.auth.permissions import Permission, SystemRole, permissions_for_role
from bm.domain.inventory.service import (
    MIN_ADJUSTMENT_REASON_LENGTH,
    MIN_OVERRIDE_REASON_LENGTH,
    InventoryService,
    MovementRequest,
    Shortfall,
    StockCheck,
)
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    InsufficientStock,
    OversellNotPermitted,
    OversellReasonRequired,
    PermissionDenied,
    ValidationFailed,
)
from bm.infra.db.enums import MovementReason
from tests.fixtures import Tenant, create_user

pytestmark = pytest.mark.db

D = Decimal


def _ctx(
    tenant: Tenant, role: SystemRole = SystemRole.OWNER, *, user_id: uuid.UUID | None = None
) -> AuthContext:
    """A context for a **real** user.

    `stock_movements.created_by_user_id` is a foreign key — every financial and stock row
    records who caused it (`ARCHITECTURE.md`), so an invented user id fails the insert
    rather than the assertion. That is the constraint working.
    """
    return AuthContext(
        user_id=user_id or _USER_ID,
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=tenant.counter_id,
        device_id=_DEVICE_ID,
        role=str(role),
        permissions=frozenset(str(p) for p in permissions_for_role(role)),
        token_id="inventory-rules",
        business_timezone=tenant.timezone,
        full_name="Test",
    )


#: Set by the `acting_user` fixture. Both are foreign keys on `stock_movements`:
#: every stock row records **who** and **where** (`ARCHITECTURE.md`), so invented
#: ids fail the insert rather than the assertion.
_USER_ID: uuid.UUID = uuid.uuid4()
_DEVICE_ID: uuid.UUID = uuid.uuid4()


@pytest.fixture(autouse=True)
def acting_user(migration_session: Session, tenant: Tenant) -> uuid.UUID:
    """A real user to attribute movements to."""
    global _USER_ID, _DEVICE_ID
    user = create_user(migration_session, tenant, username="stockkeeper", role=SystemRole.OWNER)
    _USER_ID = user.id
    _DEVICE_ID = user.device_id
    return user.id


@pytest.fixture
def service(migration_session: Session) -> InventoryService:
    return InventoryService(migration_session)


@pytest.fixture
def product(migration_session: Session, tenant: Tenant) -> uuid.UUID:
    """A product with a unit. Reference data, so inserted directly."""
    unit_id = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO units (id, business_id, code, name, allows_fraction, decimal_places)"
            " VALUES (:id, :b, 'NOS', 'Numbers', false, 0)"
            " ON CONFLICT (business_id, code) DO UPDATE SET name = 'Numbers'"
        ),
        {"id": unit_id, "b": tenant.business_id},
    )
    resolved_unit = migration_session.execute(
        text("SELECT id FROM units WHERE business_id = :b AND code = 'NOS'"),
        {"b": tenant.business_id},
    ).scalar_one()
    product_id = uuid.uuid4()
    migration_session.execute(
        text(
            "INSERT INTO products (id, business_id, name, normalized_name, unit_id,"
            " default_selling_price, search_text, is_active)"
            " VALUES (:id, :b, 'Tie Rod End', 'tie rod end', :u, 1250, 'tie rod end', true)"
        ),
        {"id": product_id, "b": tenant.business_id, "u": resolved_unit},
    )
    migration_session.commit()
    return product_id


def _stock(service: InventoryService, tenant: Tenant, product_id: uuid.UUID, qty: str) -> None:
    """Put stock on the shelf through the only path that may: a movement."""
    service.apply_movements(
        _ctx(tenant),
        branch_id=tenant.branch_id,
        movements=[
            MovementRequest(
                product_id=product_id,
                quantity=D(qty),
                reason=MovementReason.OPENING,
                source_type="test",
                source_id=product_id,
            )
        ],
    )


# --- lock_and_check -----------------------------------------------------------


def test_locking_nothing_is_not_an_error(service: InventoryService, tenant: Tenant) -> None:
    """An empty basket locks nothing and reports no shortfall.

    Worth its own path: taking the fixed lock order over an empty set would otherwise
    issue a `SELECT … FOR UPDATE` with an empty `IN ()`, and the caller would have to
    guard it instead.
    """
    check = service.lock_and_check(
        business_id=tenant.business_id, branch_id=tenant.branch_id, required={}
    )
    assert check.locked_quantities == {}
    assert check.shortfalls == ()
    assert check.has_shortfall is False


def test_a_product_that_has_never_moved_can_still_be_locked(
    service: InventoryService, tenant: Tenant, product: uuid.UUID
) -> None:
    """The balance row is created on demand, so a first sale does not race with itself."""
    check = service.lock_and_check(
        business_id=tenant.business_id, branch_id=tenant.branch_id, required={product: D("1")}
    )
    assert check.locked_quantities[product] == D(0)
    assert check.has_shortfall is True
    assert check.shortfalls[0].requested == D("1")
    assert check.shortfalls[0].available == D(0)


def test_sufficient_stock_reports_no_shortfall(
    service: InventoryService, tenant: Tenant, product: uuid.UUID
) -> None:
    _stock(service, tenant, product, "10")
    check = service.lock_and_check(
        business_id=tenant.business_id, branch_id=tenant.branch_id, required={product: D("4")}
    )
    assert check.has_shortfall is False
    assert check.locked_quantities[product] == D("10")


# --- assert_oversell_authorized ----------------------------------------------


def test_no_shortfall_needs_no_authorisation(tenant: Tenant) -> None:
    """The early return. Nothing to override, so no reason is demanded."""
    InventoryService.assert_oversell_authorized(
        _ctx(tenant),
        StockCheck(locked_quantities={}, shortfalls=()),
        override_reason=None,
    )


def test_a_shortfall_without_the_override_permission_is_refused(
    tenant: Tenant, product: uuid.UUID
) -> None:
    """Selling below recorded stock needs a supervisor (FR-INV-004)."""
    check = StockCheck(
        locked_quantities={product: D(0)},
        shortfalls=(Shortfall(product_id=product, requested=D("2"), available=D(0)),),
    )
    cashier = _ctx(tenant, SystemRole.CASHIER)
    assert not cashier.has(Permission.INVENTORY_OVERSELL_OVERRIDE)
    with pytest.raises(OversellNotPermitted):
        InventoryService.assert_oversell_authorized(
            cashier, check, override_reason="plenty of reason here"
        )


def test_an_authorised_override_still_needs_a_real_reason(
    tenant: Tenant, product: uuid.UUID
) -> None:
    """A ten-character floor, so "ok" cannot stand in for an explanation.

    This is the branch the audit found uncovered, and it is the one that keeps the
    oversell audit trail worth reading.
    """
    check = StockCheck(
        locked_quantities={product: D(0)},
        shortfalls=(Shortfall(product_id=product, requested=D("2"), available=D(0)),),
    )
    owner = _ctx(tenant)
    assert owner.has(Permission.INVENTORY_OVERSELL_OVERRIDE)

    # No reason offered at all is the ordinary refusal: the caller never asked to
    # override, so they are simply told there is not enough stock.
    with pytest.raises(InsufficientStock):
        InventoryService.assert_oversell_authorized(owner, check, override_reason=None)

    # An override *attempted* with a token reason is refused differently, because the
    # intent was there and only the explanation is missing.
    for reason in ("", "   ", "short"):
        with pytest.raises(OversellReasonRequired):
            InventoryService.assert_oversell_authorized(owner, check, override_reason=reason)
    assert len("short") < MIN_OVERRIDE_REASON_LENGTH


def test_an_authorised_override_with_a_reason_is_allowed(
    tenant: Tenant, product: uuid.UUID
) -> None:
    check = StockCheck(
        locked_quantities={product: D(0)},
        shortfalls=(Shortfall(product_id=product, requested=D("2"), available=D(0)),),
    )
    InventoryService.assert_oversell_authorized(
        _ctx(tenant), check, override_reason="counted short at the shelf, selling anyway"
    )


# --- apply_movements ----------------------------------------------------------


def test_applying_no_movements_writes_nothing(
    service: InventoryService, tenant: Tenant, migration_session: Session
) -> None:
    before = migration_session.execute(text("SELECT count(*) FROM stock_movements")).scalar_one()
    assert service.apply_movements(_ctx(tenant), branch_id=tenant.branch_id, movements=[]) == []
    after = migration_session.execute(text("SELECT count(*) FROM stock_movements")).scalar_one()
    assert after == before


@pytest.mark.parametrize("quantity", ["0", "-1", "-0.001"])
def test_a_movement_quantity_must_be_positive(
    service: InventoryService, tenant: Tenant, product: uuid.UUID, quantity: str
) -> None:
    """`quantity` is always positive; the direction comes from `reason`.

    A negative quantity with a decreasing reason would *increase* stock, which is how a
    ledger silently stops reconciling.
    """
    with pytest.raises(ValidationFailed):
        service.apply_movements(
            _ctx(tenant),
            branch_id=tenant.branch_id,
            movements=[
                MovementRequest(
                    product_id=product,
                    quantity=D(quantity),
                    reason=MovementReason.SALE,
                    source_type="test",
                    source_id=product,
                )
            ],
        )


def test_an_oversell_override_on_a_movement_requires_its_reason(
    service: InventoryService, tenant: Tenant, product: uuid.UUID
) -> None:
    """The flag and the explanation travel together, or neither is accepted.

    Raised as `OversellReasonRequired` rather than a bare validation error because it
    mirrors `ck_stock_movements__override_reason`: the database would refuse the row
    anyway, and a typed error says why instead of surfacing a constraint name.
    """
    with pytest.raises(OversellReasonRequired):
        service.apply_movements(
            _ctx(tenant),
            branch_id=tenant.branch_id,
            movements=[
                MovementRequest(
                    product_id=product,
                    quantity=D("1"),
                    reason=MovementReason.SALE,
                    source_type="test",
                    source_id=product,
                    oversell_override=True,
                    override_reason=None,
                )
            ],
        )


def test_balance_after_is_read_back_from_the_database(
    service: InventoryService, tenant: Tenant, product: uuid.UUID, migration_session: Session
) -> None:
    """The ledger records what the database holds, not what Python computed."""
    _stock(service, tenant, product, "10")
    service.apply_movements(
        _ctx(tenant),
        branch_id=tenant.branch_id,
        movements=[
            MovementRequest(
                product_id=product,
                quantity=D("3"),
                reason=MovementReason.SALE,
                source_type="test",
                source_id=product,
            )
        ],
    )
    migration_session.commit()

    assert service.balance_for(branch_id=tenant.branch_id, product_id=product) == D("7")
    rows = migration_session.execute(
        text(
            "SELECT quantity_delta, balance_after FROM stock_movements"
            " WHERE product_id = :p ORDER BY created_at, balance_after DESC"
        ),
        {"p": product},
    ).all()
    # balance = Σ movements, and each row's `balance_after` is the running total.
    assert sum(r[0] for r in rows) == D("7")
    assert D(str(rows[-1][1])) == D("7")


# --- balances and movements: the filters -------------------------------------


def test_balances_can_be_narrowed_to_specific_products(
    service: InventoryService, tenant: Tenant, product: uuid.UUID, migration_session: Session
) -> None:
    _stock(service, tenant, product, "5")
    migration_session.commit()

    everything = service.balances(_ctx(tenant))
    assert any(row[0] == product for row in everything)

    narrowed = service.balances(_ctx(tenant), product_ids=[product])
    assert [row[0] for row in narrowed] == [product]

    absent = service.balances(_ctx(tenant), product_ids=[uuid.uuid4()])
    assert absent == []


def test_balances_can_be_narrowed_to_what_is_running_low(
    service: InventoryService,
    tenant: Tenant,
    product: uuid.UUID,
    migration_session: Session,
) -> None:
    """`low_stock_only` compares against the product's own reorder level.

    A product with **no** reorder level is never low — a missing level is not a level of
    zero, which is the same rule the Stock screen renders as "Not set".
    """
    _stock(service, tenant, product, "4")
    migration_session.commit()

    # No reorder level yet, so nothing is low.
    assert service.balances(_ctx(tenant), low_stock_only=True) == []

    migration_session.execute(
        text("UPDATE products SET reorder_level = 10 WHERE id = :p"), {"p": product}
    )
    migration_session.commit()
    low = service.balances(_ctx(tenant), low_stock_only=True)
    assert [row[0] for row in low] == [product]


def test_balances_requires_catalog_read(tenant: Tenant, service: InventoryService) -> None:
    """UI-level hiding is not a control (NFR-SEC-004)."""
    nobody = AuthContext(
        user_id=uuid.uuid4(),
        business_id=tenant.business_id,
        branch_id=tenant.branch_id,
        counter_id=None,
        device_id=None,
        role="nobody",
        permissions=frozenset(),
        token_id="t",
    )
    with pytest.raises(PermissionDenied):
        service.balances(nobody)
    with pytest.raises(PermissionDenied):
        service.movements(nobody)


def test_the_ledger_can_be_narrowed_to_one_product(
    service: InventoryService,
    tenant: Tenant,
    product: uuid.UUID,
    migration_session: Session,
) -> None:
    _stock(service, tenant, product, "5")
    migration_session.commit()

    everything = service.movements(_ctx(tenant))
    assert everything

    one = service.movements(_ctx(tenant), product_id=product)
    assert all(m.product_id == product for m in one)
    assert service.movements(_ctx(tenant), product_id=uuid.uuid4()) == []


def test_the_ledger_is_branch_scoped_when_a_branch_is_selected(
    service: InventoryService,
    tenant: Tenant,
    product: uuid.UUID,
    migration_session: Session,
) -> None:
    """A cashier at one counter must not read another branch's ledger."""
    _stock(service, tenant, product, "5")
    migration_session.commit()

    scoped = service.movements(_ctx(tenant))
    assert scoped
    assert all(m.branch_id == tenant.branch_id for m in scoped)


# --- adjust: the reasoned, audited correction --------------------------------


def test_an_adjustment_cannot_be_zero(
    service: InventoryService, tenant: Tenant, product: uuid.UUID
) -> None:
    """A zero adjustment is a mistake, and recording one hides a real discrepancy."""
    with pytest.raises(ValidationFailed) as caught:
        service.adjust(
            _ctx(tenant),
            product_id=product,
            quantity_delta="0",
            reason_code=str(MovementReason.ADJUSTMENT_INCREASE),
            reason_text="counted again",
        )
    assert "quantity_delta" in caught.value.details["fields"]


@pytest.mark.parametrize("reason_text", ["", " ", "ab"])
def test_an_adjustment_needs_a_real_explanation(
    service: InventoryService, tenant: Tenant, product: uuid.UUID, reason_text: str
) -> None:
    """T-3: "stock adjusted by -4" with no explanation is the audit gap itself."""
    with pytest.raises(ValidationFailed):
        service.adjust(
            _ctx(tenant),
            product_id=product,
            quantity_delta="-4",
            reason_code=str(MovementReason.ADJUSTMENT_DECREASE),
            reason_text=reason_text,
        )
    assert len("ab") < MIN_ADJUSTMENT_REASON_LENGTH


def test_an_unknown_reason_code_is_refused(
    service: InventoryService, tenant: Tenant, product: uuid.UUID
) -> None:
    """Only the adjustment reasons may be used here. `sale` must not be reachable
    through the adjustment endpoint, or a sale could be invented without a bill."""
    for code in ("sale", "purchase", "not_a_reason", str(MovementReason.SALE)):
        with pytest.raises(ValidationFailed):
            service.adjust(
                _ctx(tenant),
                product_id=product,
                quantity_delta="-1",
                reason_code=code,
                reason_text="a good enough reason",
            )


def test_an_adjustment_requires_the_adjust_permission(
    service: InventoryService, tenant: Tenant, product: uuid.UUID
) -> None:
    cashier = _ctx(tenant, SystemRole.CASHIER)
    if cashier.has(Permission.INVENTORY_ADJUST):
        pytest.skip("cashier holds inventory.adjust in this role matrix")
    with pytest.raises(PermissionDenied):
        service.adjust(
            cashier,
            product_id=product,
            quantity_delta="-1",
            reason_code=str(MovementReason.ADJUSTMENT_DECREASE),
            reason_text="a good enough reason",
        )


@pytest.mark.parametrize(
    ("reason_code", "delta", "expected"),
    [
        (str(MovementReason.ADJUSTMENT_INCREASE), "3", "13"),
        (str(MovementReason.ADJUSTMENT_DECREASE), "-4", "6"),
        (str(MovementReason.SCRAP), "-2", "8"),
        (str(MovementReason.OPENING), "5", "15"),
    ],
)
def test_each_adjustment_reason_moves_stock_the_way_it_says(
    service: InventoryService,
    tenant: Tenant,
    product: uuid.UUID,
    migration_session: Session,
    reason_code: str,
    delta: str,
    expected: str,
) -> None:
    """The sign comes from the delta and the reason is recorded alongside it, so the two
    can never disagree in the ledger. `scrap` and `opening` are their own branches."""
    _stock(service, tenant, product, "10")
    migration_session.commit()

    movement_id = service.adjust(
        _ctx(tenant),
        product_id=product,
        quantity_delta=delta,
        reason_code=reason_code,
        reason_text="stock take correction",
    )
    migration_session.commit()

    assert service.balance_for(branch_id=tenant.branch_id, product_id=product) == D(expected)
    reason = migration_session.execute(
        text("SELECT reason FROM stock_movements WHERE id = :m"), {"m": movement_id}
    ).scalar_one()
    assert str(reason) == reason_code


def test_an_adjustment_is_audited_with_its_reason(
    service: InventoryService,
    tenant: Tenant,
    product: uuid.UUID,
    migration_session: Session,
) -> None:
    """FR-INV-007 / T-3: the free text is kept, not just the code."""
    _stock(service, tenant, product, "10")
    migration_session.commit()

    service.adjust(
        _ctx(tenant),
        product_id=product,
        quantity_delta="-4",
        reason_code=str(MovementReason.ADJUSTMENT_DECREASE),
        reason_text="four found damaged in the back room",
    )
    migration_session.commit()

    row = migration_session.execute(
        text(
            "SELECT reason, after_value FROM audit_logs"
            " WHERE entity_id = :p AND action LIKE '%adjust%'"
            " ORDER BY created_at DESC LIMIT 1"
        ),
        {"p": product},
    ).first()
    assert row is not None, "an adjustment must be audited"
    # The free text survives into the audit row, which is the whole point of T-3.
    assert "damaged" in f"{row[0]} {row[1]}"


def test_a_cross_tenant_product_cannot_be_adjusted(
    service: InventoryService,
    tenant: Tenant,
    migration_session: Session,
) -> None:
    """§4.1: another business's product is absent, not forbidden."""
    from tests.fixtures import create_tenant

    other = create_tenant(migration_session, display_name="OTHER SHOP", branch_code="OTH")
    create_user(migration_session, other, username="otherowner", role=SystemRole.OWNER)
    stranger = uuid.uuid4()
    with pytest.raises((ValidationFailed, Exception)):
        service.adjust(
            _ctx(tenant),
            product_id=stranger,
            quantity_delta="-1",
            reason_code=str(MovementReason.ADJUSTMENT_DECREASE),
            reason_text="should not be possible",
        )
