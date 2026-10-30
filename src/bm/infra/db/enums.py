"""PostgreSQL native ENUM types.

`DATA-MODEL.md §1`: native enums for closed sets that change rarely; lookup tables where
the owner may add values. Every type is created once in migration 0001 and referenced
with `create_type=False` afterwards, so no later migration can accidentally re-create or
diverge from one.

A value is never removed from an enum — issued financial rows reference it forever.
Adding one is a migration (`ALTER TYPE … ADD VALUE`), which is why these sets are small
and deliberate.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy.dialects.postgresql import ENUM


def pg_enum(name: str, *values: str) -> ENUM:
    """Reference an existing type. `create_type=False` keeps DDL in migration 0001."""
    return ENUM(*values, name=name, create_type=False, native_enum=True)


# --- identity -----------------------------------------------------------------


class DeviceStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


DEVICE_STATUS = pg_enum("device_status", *(e.value for e in DeviceStatus))


# --- catalog ------------------------------------------------------------------


class AliasType(StrEnum):
    ALTERNATE_NAME = "alternate_name"
    OLD_PART_NO = "old_part_no"
    OEM_NO = "oem_no"
    LOCAL_NAME = "local_name"
    COMPETITOR_NO = "competitor_no"


ALIAS_TYPE = pg_enum("alias_type", *(e.value for e in AliasType))


class PriceSource(StrEnum):
    CATALOG = "catalog"
    MANUAL_OVERRIDE = "manual_override"


PRICE_SOURCE = pg_enum("price_source", *(e.value for e in PriceSource))


# --- inventory ----------------------------------------------------------------


class MovementReason(StrEnum):
    """Closed set. The sign of `quantity_delta` is derived from this, not supplied."""

    OPENING = "opening"
    PURCHASE_RECEIPT = "purchase_receipt"
    SALE = "sale"
    SALE_CANCELLATION = "sale_cancellation"
    CUSTOMER_RETURN = "customer_return"
    SUPPLIER_RETURN = "supplier_return"
    ADJUSTMENT_INCREASE = "adjustment_increase"
    ADJUSTMENT_DECREASE = "adjustment_decrease"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    IMPORT_APPLY = "import_apply"
    SCRAP = "scrap"


MOVEMENT_REASON = pg_enum("movement_reason", *(e.value for e in MovementReason))


class StockCountStatus(StrEnum):
    OPEN = "open"
    APPLIED = "applied"
    CANCELLED = "cancelled"


STOCK_COUNT_STATUS = pg_enum("stock_count_status", *(e.value for e in StockCountStatus))


# --- purchases ----------------------------------------------------------------


class PurchaseStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


PURCHASE_STATUS = pg_enum("purchase_status", *(e.value for e in PurchaseStatus))


# --- sales --------------------------------------------------------------------


class DraftStatus(StrEnum):
    OPEN = "open"
    FINALIZED = "finalized"
    CANCELLED = "cancelled"
    STALE = "stale"


DRAFT_STATUS = pg_enum("draft_status", *(e.value for e in DraftStatus))


class InvoiceStatus(StrEnum):
    ISSUED = "issued"
    CANCELLED = "cancelled"


INVOICE_STATUS = pg_enum("invoice_status", *(e.value for e in InvoiceStatus))


class DocumentKind(StrEnum):
    """`estimate` while `tax.mode = estimate_only`. A series never changes kind mid-year.

    `tax_invoice` is unreachable until the `FR-TAX-003` gate passes; the value exists so
    enabling GST later is configuration, not a migration on live financial data.
    """

    ESTIMATE = "estimate"
    TAX_INVOICE = "tax_invoice"


DOCUMENT_KIND = pg_enum("document_kind", *(e.value for e in DocumentKind))


class PaymentStatus(StrEnum):
    """Derived, never user-set (FR-CREDIT-003, ADR-022). There is no "paid" checkbox."""

    UNPAID = "unpaid"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERPAID = "overpaid"
    CANCELLED = "cancelled"


PAYMENT_STATUS = pg_enum("payment_status", *(e.value for e in PaymentStatus))


# --- payments and credit ------------------------------------------------------


class PaymentDirection(StrEnum):
    RECEIVED = "received"
    REFUNDED = "refunded"


PAYMENT_DIRECTION = pg_enum("payment_direction", *(e.value for e in PaymentDirection))


class PaymentKind(StrEnum):
    PAYMENT = "payment"
    REVERSAL = "reversal"


PAYMENT_KIND = pg_enum("payment_kind", *(e.value for e in PaymentKind))


class PaymentMethod(StrEnum):
    """No payment instrument data is ever stored — only a method and a free-text
    reference such as a UPI transaction id (`SECURITY.md §10.1`)."""

    CASH = "cash"
    UPI = "upi"
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    CHEQUE = "cheque"
    ADJUSTMENT = "adjustment"


PAYMENT_METHOD = pg_enum("payment_method", *(e.value for e in PaymentMethod))


class AllocationStrategy(StrEnum):
    MANUAL = "manual"
    OLDEST_FIRST = "oldest_first"
    INVOICE_SPECIFIC = "invoice_specific"


ALLOCATION_STRATEGY = pg_enum("allocation_strategy", *(e.value for e in AllocationStrategy))


class CreditNoteSource(StrEnum):
    SALES_RETURN = "sales_return"
    CANCELLATION = "cancellation"
    WRITE_OFF = "write_off"
    ADJUSTMENT = "adjustment"


CREDIT_NOTE_SOURCE = pg_enum("credit_note_source", *(e.value for e in CreditNoteSource))


class CreditNoteStatus(StrEnum):
    OPEN = "open"
    FULLY_ALLOCATED = "fully_allocated"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


CREDIT_NOTE_STATUS = pg_enum("credit_note_status", *(e.value for e in CreditNoteStatus))


# --- returns ------------------------------------------------------------------


class ReturnCondition(StrEnum):
    RESALABLE = "resalable"
    DAMAGED = "damaged"


RETURN_CONDITION = pg_enum("return_condition", *(e.value for e in ReturnCondition))


# --- imports ------------------------------------------------------------------


class ImportIntent(StrEnum):
    """`price_list` must produce **zero** stock movements, ever (FR-IMP-002)."""

    PRICE_LIST = "price_list"
    PURCHASE_RECEIPT = "purchase_receipt"


IMPORT_INTENT = pg_enum("import_intent", *(e.value for e in ImportIntent))


class ImportStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    NEEDS_REVIEW = "needs_review"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    CANCELLED = "cancelled"


IMPORT_STATUS = pg_enum("import_status", *(e.value for e in ImportStatus))


class ImportMatchStatus(StrEnum):
    EXACT_SKU = "exact_sku"
    EXACT_ALIAS = "exact_alias"
    FUZZY_CANDIDATE = "fuzzy_candidate"
    AMBIGUOUS = "ambiguous"
    NEW_PRODUCT = "new_product"
    DUPLICATE_IN_FILE = "duplicate_in_file"
    INVALID = "invalid"


IMPORT_MATCH_STATUS = pg_enum("import_match_status", *(e.value for e in ImportMatchStatus))

#: Rows in these states cannot be applied without a per-row human decision
#: (FR-IMP-007, `ck_import_rows__decision_required`).
DECISION_REQUIRED_MATCH_STATUSES: frozenset[ImportMatchStatus] = frozenset(
    {
        ImportMatchStatus.AMBIGUOUS,
        ImportMatchStatus.NEW_PRODUCT,
        ImportMatchStatus.DUPLICATE_IN_FILE,
        ImportMatchStatus.INVALID,
    }
)


class ImportDecision(StrEnum):
    PENDING = "pending"
    ACCEPT_MATCH = "accept_match"
    CREATE_NEW = "create_new"
    SKIP = "skip"


IMPORT_DECISION = pg_enum("import_decision", *(e.value for e in ImportDecision))


class ImportApplyResult(StrEnum):
    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"


IMPORT_APPLY_RESULT = pg_enum("import_apply_result", *(e.value for e in ImportApplyResult))


# --- infrastructure -----------------------------------------------------------


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


IDEMPOTENCY_STATUS = pg_enum("idempotency_status", *(e.value for e in IdempotencyStatus))


class SyncOperationStatus(StrEnum):
    ACCEPTED = "accepted"
    REPLAYED = "replayed"
    CONFLICTED = "conflicted"
    REJECTED = "rejected"


SYNC_OPERATION_STATUS = pg_enum("sync_operation_status", *(e.value for e in SyncOperationStatus))


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


JOB_STATUS = pg_enum("job_status", *(e.value for e in JobStatus))


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    FAILED = "failed"


OUTBOX_STATUS = pg_enum("outbox_status", *(e.value for e in OutboxStatus))


class NotificationKind(StrEnum):
    LOW_STOCK = "low_stock"
    OVERDUE_CREDIT = "overdue_credit"
    RECONCILIATION_ALERT = "reconciliation_alert"
    IMPORT_COMPLETE = "import_complete"


NOTIFICATION_KIND = pg_enum("notification_kind", *(e.value for e in NotificationKind))


class NotificationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


NOTIFICATION_SEVERITY = pg_enum("notification_severity", *(e.value for e in NotificationSeverity))


# --- tax ----------------------------------------------------------------------


class TaxMode(StrEnum):
    """Stays `estimate_only` until the `FR-TAX-003` gate passes. Never emit
    "Tax Invoice", never invent a GSTIN, a rate or an HSN code."""

    ESTIMATE_ONLY = "estimate_only"
    GST_ENABLED = "gst_enabled"


TAX_MODE = pg_enum("tax_mode", *(e.value for e in TaxMode))


class TaxComponent(StrEnum):
    CGST = "cgst"
    SGST = "sgst"
    IGST = "igst"
    CESS = "cess"


TAX_COMPONENT = pg_enum("tax_component", *(e.value for e in TaxComponent))


#: Every enum type, in creation order, consumed by migration 0001 and by the test that
#: asserts the database's types match this module exactly.
ALL_ENUM_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("device_status", tuple(e.value for e in DeviceStatus)),
    ("alias_type", tuple(e.value for e in AliasType)),
    ("price_source", tuple(e.value for e in PriceSource)),
    ("movement_reason", tuple(e.value for e in MovementReason)),
    ("stock_count_status", tuple(e.value for e in StockCountStatus)),
    ("purchase_status", tuple(e.value for e in PurchaseStatus)),
    ("draft_status", tuple(e.value for e in DraftStatus)),
    ("invoice_status", tuple(e.value for e in InvoiceStatus)),
    ("document_kind", tuple(e.value for e in DocumentKind)),
    ("payment_status", tuple(e.value for e in PaymentStatus)),
    ("payment_direction", tuple(e.value for e in PaymentDirection)),
    ("payment_kind", tuple(e.value for e in PaymentKind)),
    ("payment_method", tuple(e.value for e in PaymentMethod)),
    ("allocation_strategy", tuple(e.value for e in AllocationStrategy)),
    ("credit_note_source", tuple(e.value for e in CreditNoteSource)),
    ("credit_note_status", tuple(e.value for e in CreditNoteStatus)),
    ("return_condition", tuple(e.value for e in ReturnCondition)),
    ("import_intent", tuple(e.value for e in ImportIntent)),
    ("import_status", tuple(e.value for e in ImportStatus)),
    ("import_match_status", tuple(e.value for e in ImportMatchStatus)),
    ("import_decision", tuple(e.value for e in ImportDecision)),
    ("import_apply_result", tuple(e.value for e in ImportApplyResult)),
    ("idempotency_status", tuple(e.value for e in IdempotencyStatus)),
    ("sync_operation_status", tuple(e.value for e in SyncOperationStatus)),
    ("job_status", tuple(e.value for e in JobStatus)),
    ("outbox_status", tuple(e.value for e in OutboxStatus)),
    ("notification_kind", tuple(e.value for e in NotificationKind)),
    ("notification_severity", tuple(e.value for e in NotificationSeverity)),
    ("tax_mode", tuple(e.value for e in TaxMode)),
    ("tax_component", tuple(e.value for e in TaxComponent)),
)
