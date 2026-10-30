"""Catalog service — products, aliases and search (M5).

Two rules here are security controls rather than conveniences:

* **`cost_price` is omitted from a response, not nulled**, without
  `catalog.cost.read` (FR-CAT-004). A nulled field invites a client to render "—" where
  a margin should be; an absent field cannot be rendered at all.
* **A product is deactivated, never deleted** (FR-CAT-009). It is referenced by every
  invoice line that ever sold it, so deleting it would orphan the history those lines
  explain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    DuplicateBarcode,
    DuplicateSku,
    NotFound,
    ValidationFailed,
)
from bm.domain.shared.money import assert_not_negative, assert_scale_within, parse_decimal
from bm.domain.shared.normalize import (
    build_search_text,
    normalize_name,
    normalize_sku,
)
from bm.domain.shared.quantity import UnitSpec
from bm.infra.db.repositories.catalog import CatalogRepository, ProductRow, UnitRow

#: Hard caps so a client asking for 10 000 rows gets the maximum, not a table scan.
MAX_SEARCH_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 20

#: FR-CAT-003: search answers from the **first** character. This is the behaviour that
#: makes keyboard-only billing possible, so the minimum is 1, not 3.
MIN_QUERY_LENGTH = 1


@dataclass(frozen=True, slots=True)
class ProductView:
    """What the API returns. `cost_price` is `None` **and omitted** by the router when
    the caller lacks `catalog.cost.read`."""

    id: uuid.UUID
    name: str
    sku: str | None
    barcode: str | None
    brand: str | None
    category: str | None
    unit_code: str
    unit_allows_fraction: bool
    default_selling_price: Decimal
    branch_quantity: Decimal | None
    reorder_level: Decimal | None
    hsn_code: str | None
    is_active: bool
    cost_price: Decimal | None
    match_kind: str | None
    match_score: int | None

    @property
    def is_low_stock(self) -> bool:
        if self.branch_quantity is None or self.reorder_level is None:
            return False
        return self.branch_quantity <= self.reorder_level


class CatalogService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = CatalogRepository(session)

    # --- reference data -------------------------------------------------------

    def list_units(self, ctx: AuthContext) -> list[UnitRow]:
        ctx.require(Permission.CATALOG_READ)
        return self._repo.list_units(ctx.business_id)

    def list_brands(self, ctx: AuthContext) -> list[tuple[uuid.UUID, str]]:
        ctx.require(Permission.CATALOG_READ)
        return self._repo.list_brands(ctx.business_id)

    def list_categories(self, ctx: AuthContext) -> list[tuple[uuid.UUID, str]]:
        ctx.require(Permission.CATALOG_READ)
        return self._repo.list_categories(ctx.business_id)

    def resolve_for_sale(
        self, business_id: uuid.UUID, product_id: uuid.UUID, *, branch_id: uuid.UUID | None
    ) -> ProductRow | None:
        """The raw catalog row a sale needs: live price, active flag, unit and cost.

        Returns the repository row rather than a `ProductView` because finalisation
        needs the cost price for the movement's `unit_cost` snapshot regardless of
        whether the *caller* may see cost prices — the redaction rule is about what
        leaves the API, not what the engine may compute with.
        """
        return self._repo.get_product(product_id, business_id, branch_id=branch_id)

    def unit_spec_for(self, product_id: uuid.UUID) -> UnitSpec:
        """The unit rule used to validate a quantity (E-18).

        Falls back to the **strict** option if the unit cannot be read: refusing a
        fractional quantity is recoverable, silently accepting one the shop cannot
        dispense is not.
        """
        unit = self._repo.unit_for_product(product_id)
        if unit is None:
            from bm.domain.shared.quantity import STRICT_UNIT

            return STRICT_UNIT
        return UnitSpec(
            code=unit.code,
            allows_fraction=unit.allows_fraction,
            decimal_places=unit.decimal_places,
        )

    def ensure_default_units(self, ctx: AuthContext) -> list[UnitRow]:
        """Seed the default units for a business that has none.

        Migration 0016 covers businesses that already existed; this covers one created
        afterwards. Units are reference data a spare-parts shop cannot operate without —
        unlike a tax rate or a product, which are never seeded.
        """
        ctx.require(Permission.CATALOG_WRITE)
        existing = {u.code for u in self._repo.list_units(ctx.business_id)}
        from bm.domain.catalog.units import DEFAULT_UNITS

        for code, name, allows_fraction, places in DEFAULT_UNITS:
            if code not in existing:
                self._repo.ensure_unit(
                    ctx.business_id,
                    code=code,
                    name=name,
                    allows_fraction=allows_fraction,
                    decimal_places=places,
                )
        return self._repo.list_units(ctx.business_id)

    # --- search ---------------------------------------------------------------

    def search(
        self,
        ctx: AuthContext,
        *,
        query: str,
        limit: int | None = None,
        include_inactive: bool = False,
    ) -> list[ProductView]:
        ctx.require(Permission.CATALOG_READ)

        text = query.strip()
        if len(text) < MIN_QUERY_LENGTH:
            raise ValidationFailed({"query": "Type at least one character."})

        # Clamped, not rejected: a client asking for too much is served the maximum,
        # which keeps a careless caller working instead of failing at the counter.
        effective = min(limit or DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT)
        if effective <= 0:
            effective = DEFAULT_SEARCH_LIMIT

        rows = self._repo.search(
            business_id=ctx.business_id,
            branch_id=ctx.branch_id,
            normalized_query=normalize_name(text),
            normalized_sku_query=normalize_sku(text) or normalize_name(text),
            limit=effective,
            include_inactive=include_inactive,
        )
        return [self._view(r, ctx) for r in rows]

    def lookup_barcode(self, ctx: AuthContext, code: str) -> ProductView:
        ctx.require(Permission.CATALOG_READ)
        product_id = self._repo.find_by_barcode(ctx.business_id, code.strip())
        if product_id is None:
            raise NotFound("No product has that barcode.")
        return self.get(ctx, product_id)

    def get(self, ctx: AuthContext, product_id: uuid.UUID) -> ProductView:
        ctx.require(Permission.CATALOG_READ)
        row = self._repo.get_product(product_id, ctx.business_id, branch_id=ctx.branch_id)
        if row is None:
            # A cross-tenant id is indistinguishable from an absent one (§4.1).
            raise NotFound("That product does not exist.")
        return self._view(row, ctx)

    def _view(self, row: ProductRow, ctx: AuthContext) -> ProductView:
        may_see_cost = ctx.has(Permission.CATALOG_COST_READ)
        return ProductView(
            id=row.id,
            name=row.name,
            sku=row.sku,
            barcode=row.barcode,
            brand=row.brand_name,
            category=row.category_name,
            unit_code=row.unit_code,
            unit_allows_fraction=row.unit_allows_fraction,
            default_selling_price=row.default_selling_price,
            branch_quantity=row.branch_quantity,
            reorder_level=row.reorder_level,
            hsn_code=row.hsn_code,
            is_active=row.is_active,
            cost_price=row.last_cost_price if may_see_cost else None,
            match_kind=row.match_kind,
            match_score=row.match_score,
        )

    # --- writes ---------------------------------------------------------------

    def create_product(
        self,
        ctx: AuthContext,
        *,
        name: str,
        unit_code: str,
        default_selling_price: object,
        sku: str | None = None,
        barcode: str | None = None,
        brand: str | None = None,
        category: str | None = None,
        cost_price: object | None = None,
        reorder_level: object | None = None,
        hsn_code: str | None = None,
        product_id: uuid.UUID | None = None,
    ) -> ProductView:
        ctx.require(Permission.CATALOG_WRITE)

        clean_name = name.strip()
        if not clean_name:
            raise ValidationFailed({"name": "A product needs a name."})

        price = assert_not_negative(
            assert_scale_within(
                parse_decimal(default_selling_price, field_name="default_selling_price"),
                max_places=4,
                field_name="default_selling_price",
            ),
            field_name="default_selling_price",
        )

        unit = next(
            (u for u in self._repo.list_units(ctx.business_id) if u.code == unit_code), None
        )
        if unit is None:
            raise ValidationFailed({"unit_code": f"`{unit_code}` is not a known unit."})

        brand_id = (
            self._repo.ensure_brand(
                ctx.business_id, name=brand.strip(), normalized=normalize_name(brand)
            )
            if brand and brand.strip()
            else None
        )
        category_id = (
            self._repo.ensure_category(
                ctx.business_id, name=category.strip(), normalized=normalize_name(category)
            )
            if category and category.strip()
            else None
        )

        values = {
            "id": product_id or uuid.uuid4(),
            "business_id": ctx.business_id,
            "name": clean_name,
            "normalized_name": normalize_name(clean_name),
            "sku": sku.strip() if sku and sku.strip() else None,
            "normalized_sku": normalize_sku(sku),
            "barcode": barcode.strip() if barcode and barcode.strip() else None,
            "brand_id": brand_id,
            "category_id": category_id,
            "unit_id": unit.id,
            "hsn_code": hsn_code.strip() if hsn_code and hsn_code.strip() else None,
            "default_selling_price": price,
            "last_cost_price": (
                assert_not_negative(
                    parse_decimal(cost_price, field_name="cost_price"), field_name="cost_price"
                )
                if cost_price is not None
                else None
            ),
            "reorder_level": (
                assert_not_negative(
                    parse_decimal(reorder_level, field_name="reorder_level"),
                    field_name="reorder_level",
                )
                if reorder_level is not None
                else None
            ),
            "search_text": build_search_text(name=clean_name, sku=sku, brand=brand),
        }

        try:
            new_id = self._repo.insert_product(values)
            self._session.flush()
        except IntegrityError as exc:
            self._session.rollback()
            raise self._duplicate_error(exc, sku=sku, barcode=barcode) from exc

        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.PRODUCT_CREATED,
            entity_type="product",
            entity_id=new_id,
            after={"name": clean_name, "sku": sku, "price": str(price)},
        )
        return self.get(ctx, new_id)

    def update_product(
        self,
        ctx: AuthContext,
        product_id: uuid.UUID,
        *,
        name: str | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        default_selling_price: object | None = None,
        cost_price: object | None = None,
        reorder_level: object | None = None,
        hsn_code: str | None = None,
        brand: str | None = None,
        category: str | None = None,
    ) -> ProductView:
        ctx.require(Permission.CATALOG_WRITE)

        before = self._repo.get_product(product_id, ctx.business_id)
        if before is None:
            raise NotFound("That product does not exist.")

        values: dict[str, object] = {}
        if name is not None and name.strip():
            values["name"] = name.strip()
            values["normalized_name"] = normalize_name(name)
        if sku is not None:
            values["sku"] = sku.strip() or None
            values["normalized_sku"] = normalize_sku(sku)
        if barcode is not None:
            values["barcode"] = barcode.strip() or None
        if hsn_code is not None:
            values["hsn_code"] = hsn_code.strip() or None
        if default_selling_price is not None:
            values["default_selling_price"] = assert_not_negative(
                assert_scale_within(
                    parse_decimal(default_selling_price, field_name="default_selling_price"),
                    max_places=4,
                    field_name="default_selling_price",
                ),
                field_name="default_selling_price",
            )
        if cost_price is not None:
            values["last_cost_price"] = assert_not_negative(
                parse_decimal(cost_price, field_name="cost_price"), field_name="cost_price"
            )
        if reorder_level is not None:
            values["reorder_level"] = assert_not_negative(
                parse_decimal(reorder_level, field_name="reorder_level"),
                field_name="reorder_level",
            )
        if brand is not None and brand.strip():
            values["brand_id"] = self._repo.ensure_brand(
                ctx.business_id, name=brand.strip(), normalized=normalize_name(brand)
            )
        if category is not None and category.strip():
            values["category_id"] = self._repo.ensure_category(
                ctx.business_id, name=category.strip(), normalized=normalize_name(category)
            )

        if not values:
            return self.get(ctx, product_id)

        try:
            self._repo.update_product(product_id, ctx.business_id, values)
            self._session.flush()
        except IntegrityError as exc:
            self._session.rollback()
            raise self._duplicate_error(exc, sku=sku, barcode=barcode) from exc

        self._rebuild_search_text(product_id, ctx.business_id)

        after = self._repo.get_product(product_id, ctx.business_id)
        assert after is not None
        # A price change is audited with before/after because it is one of the named
        # sensitive actions (FR-AUDIT-003, T-2).
        action = (
            AuditAction.PRODUCT_PRICE_OVERRIDDEN
            if before.default_selling_price != after.default_selling_price
            else AuditAction.PRODUCT_UPDATED
        )
        audit.record_for(
            self._session,
            ctx,
            action=action,
            entity_type="product",
            entity_id=product_id,
            before={"name": before.name, "price": str(before.default_selling_price)},
            after={"name": after.name, "price": str(after.default_selling_price)},
        )
        return self.get(ctx, product_id)

    def deactivate(self, ctx: AuthContext, product_id: uuid.UUID) -> None:
        """Never deletes (FR-CAT-009). A product is referenced by every invoice line
        that ever sold it."""
        ctx.require(Permission.CATALOG_WRITE)
        if not self._repo.deactivate_product(product_id, ctx.business_id):
            raise NotFound("That product does not exist.")
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.PRODUCT_DEACTIVATED,
            entity_type="product",
            entity_id=product_id,
        )

    # --- aliases --------------------------------------------------------------

    def add_alias(
        self, ctx: AuthContext, product_id: uuid.UUID, *, alias: str, alias_type: str
    ) -> uuid.UUID:
        ctx.require(Permission.CATALOG_WRITE)
        if self._repo.get_product(product_id, ctx.business_id) is None:
            raise NotFound("That product does not exist.")

        clean = alias.strip()
        normalized = normalize_sku(clean) or normalize_name(clean)
        if not normalized:
            raise ValidationFailed({"alias": "An alias cannot be blank."})

        try:
            alias_id = self._repo.add_alias(
                business_id=ctx.business_id,
                product_id=product_id,
                alias=clean,
                normalized_alias=normalized,
                alias_type=alias_type,
            )
            self._session.flush()
        except IntegrityError as exc:
            self._session.rollback()
            raise ValidationFailed({"alias": "Another product already uses this alias."}) from exc

        # Fix S-8. Without this the alias is stored but unsearchable.
        self._rebuild_search_text(product_id, ctx.business_id)
        return alias_id

    def delete_alias(self, ctx: AuthContext, alias_id: uuid.UUID) -> None:
        ctx.require(Permission.CATALOG_WRITE)
        product_id = self._repo.delete_alias(alias_id, ctx.business_id)
        if product_id is None:
            raise NotFound("That alias does not exist.")
        self._rebuild_search_text(product_id, ctx.business_id)

    def aliases_for(
        self, ctx: AuthContext, product_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, str, str]]:
        ctx.require(Permission.CATALOG_READ)
        return self._repo.aliases_for(product_id)

    def _rebuild_search_text(self, product_id: uuid.UUID, business_id: uuid.UUID) -> None:
        """Recompute the product's search haystack from its current name, SKU, brand
        and aliases (fix S-8)."""
        name, sku, brand = self._repo.product_name_and_brand(product_id)
        aliases = self._repo.alias_texts_for(product_id)
        self._repo.refresh_search_text(
            product_id, build_search_text(name=name, sku=sku, brand=brand, aliases=aliases)
        )

    @staticmethod
    def _duplicate_error(exc: IntegrityError, *, sku: str | None, barcode: str | None) -> Exception:
        """Map a unique-violation to the documented code, naming the conflict."""
        message = str(exc.orig).lower() if exc.orig else str(exc).lower()
        if "barcode" in message:
            return DuplicateBarcode(
                f"Another product already uses the barcode {barcode!r}."
                if barcode
                else "Another product already uses this barcode."
            )
        if "sku" in message:
            return DuplicateSku(
                f"Another product already uses the part code {sku!r}."
                if sku
                else "Another product already uses this part code."
            )
        return ValidationFailed({"product": "That product could not be saved."})
