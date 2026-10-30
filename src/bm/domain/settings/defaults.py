"""Typed settings keys and their documented defaults.

`DATA-MODEL.md §3.1`: **defaults are applied in code, not seeded as rows, so an unset
key is never ambiguous.** There is exactly one place that knows what `tax.mode` means
when nobody has set it, and a fresh database behaves identically to a configured one.

These are **business rules**, which is why they live in the database rather than the
environment (`CLAUDE.md §11`): the owner must be able to change a credit term or a
draft limit without a deploy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from bm.domain.shared.errors import ValidationFailed


class SettingKey(StrEnum):
    """The closed set from `DATA-MODEL.md §3.1`.

    ⚠️ A settings key is spelled differently from the permission and audit-action
    vocabularies for the same concept — `inventory.allow_oversell_override` here versus
    `inventory.oversell.override` (permission) and `inventory.oversell_override`
    (audit action). `DATA-MODEL.md §3.14` is authoritative; using one where another is
    expected is a defect, not a style choice.
    """

    TAX_MODE = "tax.mode"
    DRAFTS_MAX_OPEN_PER_COUNTER = "drafts.max_open_per_counter"
    DRAFTS_STALE_DAYS = "drafts.stale_days"
    DRAFTS_RETENTION_DAYS = "drafts.retention_days"
    SALES_CANCEL_WINDOW_HOURS = "sales.cancel_window_hours"
    SALES_ROUNDING_MODE = "sales.rounding_mode"
    SALES_ROUND_GRAND_TOTAL_TO_RUPEE = "sales.round_grand_total_to_rupee"
    RETURNS_WINDOW_DAYS = "returns.window_days"
    CREDIT_DEFAULT_TERMS_DAYS = "credit.default_terms_days"
    CREDIT_ENFORCE_LIMIT = "credit.enforce_limit"
    DISCOUNT_CASHIER_MAX_PERCENT = "discount.cashier_max_percent"
    INVENTORY_ALLOW_OVERSELL_OVERRIDE = "inventory.allow_oversell_override"
    IMPORTS_MAX_FILE_MB = "imports.max_file_mb"
    IDEMPOTENCY_RETENTION_DAYS = "idempotency.retention_days"
    SYNC_CATALOG_CACHE_MAX_ROWS = "sync.catalog_cache_max_rows"
    SYNC_FINALIZATION_STALE_MINUTES = "sync.finalization_stale_minutes"
    PRINT_MARK_REPRINTS_DUPLICATE = "print.mark_reprints_duplicate"
    PRINT_PAPER_PROFILE = "print.paper_profile"
    PRINT_DATE_FORMAT = "print.date_format"
    PRINT_SHOW_CREDIT_BLOCK = "print.show_credit_block"


class ValueType(StrEnum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    DECIMAL = "decimal"
    STRING = "string"


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: SettingKey
    value_type: ValueType
    default: Any
    description: str
    #: Closed set of permitted values, where one applies.
    allowed: tuple[str, ...] | None = None
    minimum: int | None = None
    maximum: int | None = None
    #: True when the client needs it at bootstrap (`GET /sync/bootstrap`).
    client_visible: bool = False


SPECS: dict[SettingKey, SettingSpec] = {
    SettingKey.TAX_MODE: SettingSpec(
        key=SettingKey.TAX_MODE,
        value_type=ValueType.STRING,
        default="estimate_only",
        description=(
            "Document mode. Stays `estimate_only` until the FR-TAX-003 gate passes; "
            "`gst_enabled` is never set implicitly and never by this endpoint."
        ),
        allowed=("estimate_only",),  # gst_enabled is set only by POST /admin/tax/enable-gst
        client_visible=True,
    ),
    SettingKey.DRAFTS_MAX_OPEN_PER_COUNTER: SettingSpec(
        key=SettingKey.DRAFTS_MAX_OPEN_PER_COUNTER,
        value_type=ValueType.INTEGER,
        default=20,
        description="Open bill tabs per counter. C-5 makes 20 a floor, not a default.",
        minimum=20,
        maximum=200,
        client_visible=True,
    ),
    SettingKey.DRAFTS_STALE_DAYS: SettingSpec(
        key=SettingKey.DRAFTS_STALE_DAYS,
        value_type=ValueType.INTEGER,
        default=7,
        description="Age at which an untouched draft is flagged stale.",
        minimum=1,
        maximum=365,
    ),
    SettingKey.DRAFTS_RETENTION_DAYS: SettingSpec(
        key=SettingKey.DRAFTS_RETENTION_DAYS,
        value_type=ValueType.INTEGER,
        default=180,
        description="How long cancelled and stale drafts are kept before purging.",
        minimum=7,
        maximum=3650,
    ),
    SettingKey.SALES_CANCEL_WINDOW_HOURS: SettingSpec(
        key=SettingKey.SALES_CANCEL_WINDOW_HOURS,
        value_type=ValueType.INTEGER,
        default=24,
        description="Window in which a completed bill may be cancelled with a reason.",
        minimum=0,
        maximum=720,
        client_visible=True,
    ),
    SettingKey.SALES_ROUNDING_MODE: SettingSpec(
        key=SettingKey.SALES_ROUNDING_MODE,
        value_type=ValueType.STRING,
        default="reject_fractional",
        description=(
            "Money policy rounding mode. The v1 default refuses a fractional result "
            "rather than rounding it (addendum §3.4). Changing this requires a written "
            "replacement policy and updated tests — it is not an accountant approval."
        ),
        allowed=("reject_fractional", "line_half_up"),
        client_visible=True,
    ),
    SettingKey.SALES_ROUND_GRAND_TOTAL_TO_RUPEE: SettingSpec(
        key=SettingKey.SALES_ROUND_GRAND_TOTAL_TO_RUPEE,
        value_type=ValueType.BOOLEAN,
        default=False,
        description=(
            "Whole-rupee rounding of the grand total. Never enabled implicitly; the "
            "adjustment is recorded in its own column so it stays visible."
        ),
        client_visible=True,
    ),
    SettingKey.RETURNS_WINDOW_DAYS: SettingSpec(
        key=SettingKey.RETURNS_WINDOW_DAYS,
        value_type=ValueType.INTEGER,
        default=30,
        description="Window in which a customer return may be recorded.",
        minimum=0,
        maximum=365,
    ),
    SettingKey.CREDIT_DEFAULT_TERMS_DAYS: SettingSpec(
        key=SettingKey.CREDIT_DEFAULT_TERMS_DAYS,
        value_type=ValueType.INTEGER,
        default=30,
        description="Default credit period, used to derive a due date.",
        minimum=0,
        maximum=365,
        client_visible=True,
    ),
    SettingKey.CREDIT_ENFORCE_LIMIT: SettingSpec(
        key=SettingKey.CREDIT_ENFORCE_LIMIT,
        value_type=ValueType.BOOLEAN,
        default=False,
        description=("When off, exceeding a credit limit warns; when on, it blocks the sale."),
        client_visible=True,
    ),
    SettingKey.DISCOUNT_CASHIER_MAX_PERCENT: SettingSpec(
        key=SettingKey.DISCOUNT_CASHIER_MAX_PERCENT,
        value_type=ValueType.DECIMAL,
        default=Decimal("0"),
        description=(
            "Cap on a cashier's discount. Default 0 until the owner sets it — the "
            "cashier holds `billing.discount.apply` but it is capped here."
        ),
        client_visible=True,
    ),
    SettingKey.INVENTORY_ALLOW_OVERSELL_OVERRIDE: SettingSpec(
        key=SettingKey.INVENTORY_ALLOW_OVERSELL_OVERRIDE,
        value_type=ValueType.BOOLEAN,
        default=True,
        description=(
            "Whether an authorised override may sell below recorded stock. The "
            "permission and an audited reason of 10+ characters are still required."
        ),
        client_visible=True,
    ),
    SettingKey.IMPORTS_MAX_FILE_MB: SettingSpec(
        key=SettingKey.IMPORTS_MAX_FILE_MB,
        value_type=ValueType.INTEGER,
        default=10,
        description="Upload size cap, also enforced at the proxy.",
        minimum=1,
        maximum=100,
    ),
    SettingKey.IDEMPOTENCY_RETENTION_DAYS: SettingSpec(
        key=SettingKey.IDEMPOTENCY_RETENTION_DAYS,
        value_type=ValueType.INTEGER,
        default=7,
        description=(
            "How long an idempotency key is honoured. Safe to prune because the "
            "client outbox gives up far sooner."
        ),
        minimum=1,
        maximum=90,
    ),
    SettingKey.SYNC_CATALOG_CACHE_MAX_ROWS: SettingSpec(
        key=SettingKey.SYNC_CATALOG_CACHE_MAX_ROWS,
        value_type=ValueType.INTEGER,
        default=5000,
        description="Cap on the offline catalog cache, bounding what a stolen machine holds.",
        minimum=100,
        maximum=100_000,
        client_visible=True,
    ),
    SettingKey.SYNC_FINALIZATION_STALE_MINUTES: SettingSpec(
        key=SettingKey.SYNC_FINALIZATION_STALE_MINUTES,
        value_type=ValueType.INTEGER,
        default=60,
        description=(
            "When a pending finalisation stops auto-retrying and tells the user to "
            "check Daily Bills instead."
        ),
        minimum=1,
        maximum=1440,
        client_visible=True,
    ),
    SettingKey.PRINT_MARK_REPRINTS_DUPLICATE: SettingSpec(
        key=SettingKey.PRINT_MARK_REPRINTS_DUPLICATE,
        value_type=ValueType.BOOLEAN,
        default=True,
        description="Mark a reprinted bill `DUPLICATE COPY`.",
        client_visible=True,
    ),
    SettingKey.PRINT_PAPER_PROFILE: SettingSpec(
        key=SettingKey.PRINT_PAPER_PROFILE,
        value_type=ValueType.STRING,
        default="a5_portrait",
        description=(
            "Print profile. A5 portrait is the engineering default (OD-6); A4 portrait "
            "is the documented fallback. The physical printer and paper remain an M27 "
            "acceptance input, so this is a proposal rather than a validated setting."
        ),
        allowed=("a5_portrait", "a4_portrait"),
        client_visible=True,
    ),
    SettingKey.PRINT_DATE_FORMAT: SettingSpec(
        key=SettingKey.PRINT_DATE_FORMAT,
        value_type=ValueType.STRING,
        default="MM-DD-YYYY",
        description=(
            "Printed date order, from a single configurable source (T3-11). "
            "`MM-DD-YYYY` matches the workbook's cell E7 number format (OD-26). "
            "API and storage always use unambiguous ISO values."
        ),
        allowed=("MM-DD-YYYY", "DD-MM-YYYY"),
        client_visible=True,
    ),
    SettingKey.PRINT_SHOW_CREDIT_BLOCK: SettingSpec(
        key=SettingKey.PRINT_SHOW_CREDIT_BLOCK,
        value_type=ValueType.BOOLEAN,
        default=True,
        description=(
            "Render the credit-only Paid / Balance / Due block beneath GRAND TOTAL "
            "(OD-22). Switchable off until the printed sample is accepted at M27."
        ),
        client_visible=True,
    ),
}


def spec_for(key: str) -> SettingSpec:
    try:
        return SPECS[SettingKey(key)]
    except ValueError as exc:
        raise ValidationFailed({"key": f"`{key}` is not a known setting."}) from exc


def coerce(spec: SettingSpec, raw: Any) -> Any:
    """Validate and convert a submitted value against its spec.

    Strict on purpose: an unknown enum value or an out-of-range number is a `422`, not
    a silently clamped value (`SECURITY.md §4`). A setting that quietly became
    something else is far harder to diagnose than one that refused to change.
    """
    field = "value"

    if spec.value_type is ValueType.BOOLEAN:
        if not isinstance(raw, bool):
            raise ValidationFailed({field: "Must be true or false."})
        return raw

    if spec.value_type is ValueType.INTEGER:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValidationFailed({field: "Must be a whole number."})
        if spec.minimum is not None and raw < spec.minimum:
            raise ValidationFailed({field: f"Must be at least {spec.minimum}."})
        if spec.maximum is not None and raw > spec.maximum:
            raise ValidationFailed({field: f"Must be at most {spec.maximum}."})
        return raw

    if spec.value_type is ValueType.DECIMAL:
        from bm.domain.shared.money import assert_not_negative, parse_decimal

        value = parse_decimal(raw, field_name=field)
        return assert_not_negative(value, field_name=field)

    if not isinstance(raw, str):
        raise ValidationFailed({field: "Must be text."})
    if spec.allowed is not None and raw not in spec.allowed:
        raise ValidationFailed({field: f"Must be one of: {', '.join(spec.allowed)}."})
    return raw
