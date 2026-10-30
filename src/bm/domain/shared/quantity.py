"""Quantity validation.

`DATA-MODEL.md §1`: quantities are `NUMERIC(14,3)`, and **integer-only units are
enforced in the domain layer, not by the column type**. A `SET` of brake shoes cannot be
sold in halves; engine oil by the litre can. That distinction is per-unit configuration
(`units.allows_fraction`, `units.decimal_places`), so it has to be checked where the
unit is known.

E-18 is the named case: a fractional quantity on an integer-only unit is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from bm.domain.shared.errors import ValidationFailed
from bm.domain.shared.money import ZERO, assert_scale_within, is_integral

#: Column scale. A unit may permit fewer places, never more.
MAX_QUANTITY_SCALE = 3


@dataclass(frozen=True, slots=True)
class UnitSpec:
    code: str
    allows_fraction: bool
    decimal_places: int

    def __post_init__(self) -> None:
        if not 0 <= self.decimal_places <= MAX_QUANTITY_SCALE:
            raise ValueError("unit decimal_places must be between 0 and 3")


#: Fallback when a product's unit is unknown. Deliberately the strict option: refusing
#: a fractional quantity is recoverable, silently accepting one that the shop cannot
#: actually dispense is not.
STRICT_UNIT = UnitSpec(code="NOS", allows_fraction=False, decimal_places=0)


def validate_quantity(value: Decimal, unit: UnitSpec, *, field_name: str = "quantity") -> Decimal:
    """Validate a quantity against its unit's configured precision."""
    if value <= ZERO:
        raise ValidationFailed({field_name: "Must be greater than zero."})

    assert_scale_within(value, max_places=MAX_QUANTITY_SCALE, field_name=field_name)

    if not unit.allows_fraction:
        if not is_integral(value):
            raise ValidationFailed(
                {field_name: (f"{unit.code} is sold in whole units only. Enter a whole number.")}
            )
        return value

    assert_scale_within(value, max_places=unit.decimal_places, field_name=field_name)
    return value
