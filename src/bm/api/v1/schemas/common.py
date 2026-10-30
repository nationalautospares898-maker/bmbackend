"""Shared request/response schema conventions.

Three rules from `API-SPECIFICATION.md §1` are encoded here rather than repeated:

* `extra="forbid"` — an unknown field is a `422`, not silently ignored. A client typo
  must surface immediately instead of quietly dropping a discount.
* **Money and quantity are JSON strings**, never numbers (ADR-013). A JSON number would
  be parsed by JavaScript as an IEEE-754 double, which cannot represent every rupee
  value exactly.
* `snake_case` everywhere (ADR-012), since Postgres, SQLAlchemy and Pydantic all use it
  and a conversion layer's only product is a new class of bug.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

T = TypeVar("T")


class StrictModel(BaseModel):
    """Base for every request body and response."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class ResponseModel(BaseModel):
    """Responses accept extra internal fields during construction but still serialise
    `snake_case` and emit money as strings."""

    model_config = ConfigDict(from_attributes=True, frozen=True)


def _parse_decimal(value: Any) -> Any:
    """Accept decimal **strings** only; reject anything that loses precision.

    Exponent notation and floats are refused rather than coerced: `1e3` and `1000.00`
    may be numerically equal, but accepting the first means accepting a format whose
    precision is ambiguous, and accepting a float means accepting a value that has
    already been rounded before it arrived (addendum §3.1).
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError("must be a decimal string")
    if isinstance(value, float):
        raise ValueError("must be a decimal string, not a number — JSON numbers lose precision")
    if isinstance(value, int):
        return Decimal(value)
    if not isinstance(value, str):
        raise ValueError("must be a decimal string")

    text = value.strip()
    if not text:
        raise ValueError("must be a decimal string")
    lowered = text.lower()
    if "e" in lowered or "inf" in lowered or "nan" in lowered:
        raise ValueError("exponent notation and non-finite values are not accepted")
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("must be a decimal string") from exc
    if not parsed.is_finite():
        raise ValueError("must be a finite decimal")
    return parsed


def _serialise_decimal(value: Decimal) -> str:
    """Emit the exact stored value as a string.

    The server never pre-formats, pads, truncates or rounds for presentation
    ([OD-23](OWNER-DECISIONS.md#od-23)); display formatting is the client's concern.
    """
    return format(value, "f")


#: A monetary amount, e.g. `"2500.00"`.
MoneyStr = Annotated[
    Decimal,
    BeforeValidator(_parse_decimal),
    PlainSerializer(_serialise_decimal, return_type=str),
]

#: A quantity, e.g. `"2.000"`.
QuantityStr = MoneyStr

#: A percentage, e.g. `"18.000"`.
PercentStr = MoneyStr


class Page(StrictModel):
    """Cursor pagination envelope (`API-SPECIFICATION.md §1.4`)."""

    next_cursor: str | None = None
    has_more: bool = False
    limit: int


class Paged(ResponseModel, Generic[T]):
    items: list[T]
    page: Page


class WarningItem(ResponseModel):
    """Rides along with a **successful** response.

    Asymmetric by design (`§4.3`): the client handles error codes exhaustively but
    warnings permissively, so a new warning can never break a completed sale.
    """

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


#: Hard caps so a client asking for 10 000 rows gets the maximum, not a table scan
#: (NFR-PERF-006).
SEARCH_PAGE_LIMIT = 50
LIST_PAGE_LIMIT = 200
DEFAULT_PAGE_LIMIT = 20


def clamp_limit(requested: int | None, maximum: int) -> int:
    """Clamp rather than reject: a client asking for too much is served the maximum,
    which keeps a careless caller working instead of failing at the counter."""
    if requested is None or requested <= 0:
        return DEFAULT_PAGE_LIMIT
    return min(requested, maximum)
