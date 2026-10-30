"""Declared response schemas, so the client's types can be **generated** (ADR-029).

Why this module exists
---------------------

The routes build their bodies as plain dicts, which is deliberate: several responses
**omit** a field rather than nulling it (`cost_price` without `catalog.cost.read`,
`unit_cost_snapshot` on an invoice item, `match` on a non-search product), and a
response model would either reinstate those keys as `null` or drop them, both of which
break FR-CAT-004.

So the models here are **documentation, not filtering**. They are attached with
``responses={200: {"model": ...}}``, never ``response_model=``, which means:

* `openapi.json` publishes the response shape, so `npm run api:types` in `bmfrontend`
  produces real response types instead of `unknown` — nobody hand-writes a field name;
* the handlers keep returning their dicts unchanged, so omission semantics survive.

The risk this creates is drift: a documented shape that no longer matches the dict.
`tests/api/test_response_contract.py` closes it by validating **actual** responses from
the running app against these models with `extra="forbid"`, so a renamed key fails the
backend build rather than surfacing as a blank column at the counter.

Fields that a route may omit are typed `NotRequired`-style — declared optional here and
asserted present-or-absent (never null-when-absent) by that test.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bm.api.v1.schemas.common import MoneyStr, Page, QuantityStr


class Out(BaseModel):
    """Base for a documented response.

    `extra="forbid"` is what makes the contract test meaningful: an undocumented key in
    a real response is a failure, not a shrug.
    """

    model_config = ConfigDict(extra="forbid")


# --- catalog ------------------------------------------------------------------


class UnitOut(Out):
    code: str
    allows_fraction: bool


class StockOut(Out):
    branch_quantity: QuantityStr
    #: Every stock figure carries the time it was read (FR-OFFLINE-009).
    as_of: str


class MatchOut(Out):
    #: `exact_sku` | `prefix_sku` | `alias` | `barcode` | `name` | `fuzzy`.
    kind: str
    score: int


class ProductOut(Out):
    id: uuid.UUID
    name: str
    sku: str | None
    barcode: str | None
    brand: str | None
    category: str | None
    unit: UnitOut
    default_selling_price: MoneyStr
    stock: StockOut | None
    is_active: bool
    is_low_stock: bool
    hsn_code: str | None
    #: Present on search results only.
    match: MatchOut | None = None
    #: Present only with `catalog.cost.read`. **Omitted**, never nulled (FR-CAT-004).
    cost_price: MoneyStr | None = None


class ProductSearchOut(Out):
    items: list[ProductOut]
    page: Page


# --- drafts -------------------------------------------------------------------


class DraftItemOut(Out):
    id: uuid.UUID
    line_no: int
    product_id: uuid.UUID
    product_name: str
    quantity: QuantityStr
    unit_price: MoneyStr
    catalog_price_at_add: MoneyStr
    price_source: str
    discount_percent: MoneyStr
    discount_amount: MoneyStr
    line_total_preview: MoneyStr


class DraftPreviewTotalsOut(Out):
    """Always advisory. The server recomputes everything at finalisation
    (FR-DRAFT-011)."""

    advisory: bool
    total: MoneyStr


class DraftOut(Out):
    id: uuid.UUID
    counter_id: uuid.UUID
    label: str
    status: str
    #: `null` until the draft has been synced once; the create path omits `If-Match`.
    version: int | None
    customer_id: uuid.UUID | None
    customer_name: str | None
    customer_business_name: str | None
    vehicle_number: str | None
    mechanic_name: str | None
    phone: str | None
    notes: str | None
    is_credit_intent: bool
    items: list[DraftItemOut]
    preview_totals: DraftPreviewTotalsOut
    invoice_id: uuid.UUID | None
    updated_at: datetime


class DraftListOut(Out):
    items: list[DraftOut]


# --- print payload ------------------------------------------------------------


class PrintBusinessOut(Out):
    """Empty optional fields print nothing — **never a placeholder** (FR-PRINT-016)."""

    display_name: str
    address_lines: list[str]
    phone_numbers: list[str]
    tax_identifier: str | None
    tax_identifier_label: str | None


class PrintCustomerOut(Out):
    name: str | None
    business_name: str | None
    vehicle_number: str | None
    mechanic_name: str | None
    phone: str | None


class PrintLineOut(Out):
    sl_no: int
    item: str
    quantity: QuantityStr
    price: MoneyStr
    total: MoneyStr


class PrintTotalsOut(Out):
    subtotal: MoneyStr
    discount: MoneyStr
    #: `null`, not `"0.00"`, while `tax.mode = estimate_only`, so the client renders
    #: **no tax row at all** rather than a misleading zero (ADR-020, FR-PRINT-012).
    tax: MoneyStr | None
    rounding: MoneyStr
    grand_total: MoneyStr


class PrintPaymentOut(Out):
    status: str
    amount_paid: MoneyStr
    outstanding: MoneyStr
    method: str | None
    due_date: date | None
    #: Cash bills omit the credit block entirely (OD-22).
    is_credit: bool


class PrintPayloadOut(Out):
    """⚠️ Carries **no invoice number** of any kind (OD-3, T3-06).

    `document_title` comes from the server only and is `ESTIMATE` for cash and credit
    alike until the FR-TAX-003 gate passes (OD-24, FR-PRINT-021).
    """

    document_title: str
    is_duplicate: bool
    business: PrintBusinessOut
    issued_at: datetime
    customer: PrintCustomerOut
    columns: list[str]
    lines: list[PrintLineOut]
    totals: PrintTotalsOut
    #: Present in the contract but **not rendered** on the bill: an amount-in-words line
    #: is verified absent from the owner's workbook (OD-5).
    amount_in_words: str
    payment: PrintPaymentOut
    footer_notes: list[str]
    tax_mode: str
    print_profile: str
    date_format: str
    show_credit_block: bool
    item_row_capacity: int


# --- invoices -----------------------------------------------------------------


class InvoiceCustomerOut(Out):
    id: uuid.UUID | None
    name_snapshot: str | None
    business_name_snapshot: str | None
    vehicle_number_snapshot: str | None
    mechanic_name_snapshot: str | None
    phone_snapshot: str | None


class InvoiceItemOut(Out):
    line_no: int
    product_name_snapshot: str
    sku_snapshot: str | None
    unit_code_snapshot: str
    quantity: QuantityStr
    unit_price: MoneyStr
    discount_amount: MoneyStr
    tax_rate_snapshot: MoneyStr
    tax_amount: MoneyStr
    line_total: MoneyStr
    #: Zero on a new bill, present regardless, so a client rendering a returnable
    #: quantity does not have to special-case a freshly issued invoice.
    returned_quantity: QuantityStr
    #: Only with `catalog.cost.read`. **Omitted**, never nulled.
    unit_cost_snapshot: MoneyStr | None = None


class InvoiceOut(Out):
    """One shape, returned by `POST /sales:finalize` **and** `GET /invoices/{id}`.

    Built by a single serialiser (`api/v1/serialize.py`), because the confirmation card
    and the Daily Bills detail describe the same bill.
    """

    id: uuid.UUID
    #: ⚠️ **Internal** reference for audit and support. There is no customer-visible
    #: invoice number (OD-3) and this must never be rendered to a customer.
    internal_reference: str
    sequence_number: int
    document_kind: str
    status: str
    issued_at: datetime
    is_credit: bool
    due_date: date | None
    customer: InvoiceCustomerOut
    items: list[InvoiceItemOut]
    subtotal: MoneyStr
    discount_total: MoneyStr
    tax_total: MoneyStr
    rounding_adjustment: MoneyStr
    total: MoneyStr
    amount_paid: MoneyStr
    outstanding: MoneyStr
    #: Derived from the allocations, never settable (ADR-022).
    payment_status: str
    oversell_override: bool
    oversell_reason: str | None
    #: T3-03: the money rule that produced this bill, so a figure can always be
    #: explained by the policy in force when it was issued.
    money_policy_version: int
    cancelled_at: datetime | None
    cancellation_reason: str | None


class InvoiceSummaryOut(Out):
    id: uuid.UUID
    internal_reference: str
    issued_at: datetime
    status: str
    is_credit: bool
    due_date: date | None
    customer_id: uuid.UUID | None
    customer_name: str | None
    counter: str
    total: MoneyStr
    amount_paid: MoneyStr
    outstanding: MoneyStr
    payment_status: str


class InvoiceListOut(Out):
    items: list[InvoiceSummaryOut]
    page: Page
    as_of: datetime


class WarningOut(Out):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class FinalizeOut(Out):
    """The `201` from `POST /sales:finalize` — cash and credit alike (ADR-033)."""

    invoice: InvoiceOut
    print_payload: PrintPayloadOut
    #: Successful-response advisories, handled **permissively** by the client so a new
    #: warning can never break a completed sale (§4.3).
    warnings: list[WarningOut]


class CancelOut(Out):
    invoice_id: uuid.UUID
    credit_note_id: uuid.UUID | None
    movements_reversed: int
    status: str


# --- credit -------------------------------------------------------------------


class AllocationOut(Out):
    invoice_id: uuid.UUID
    amount: MoneyStr


class PaymentOut(Out):
    payment_id: uuid.UUID
    allocations: list[AllocationOut]
    #: The unallocated remainder, held as customer credit rather than discarded.
    on_account: MoneyStr


class ReversalOut(Out):
    reversal_payment_id: uuid.UUID


class CreditCustomerRefOut(Out):
    id: uuid.UUID
    name: str
    primary_phone: str | None


class CreditCustomerOut(Out):
    """Every aggregate is **server-computed**; the client never sums these
    (FR-RPT-030, ADR-032)."""

    customer: CreditCustomerRefOut
    invoice_count: int
    open_invoice_count: int
    total_invoiced: MoneyStr
    total_paid: MoneyStr
    outstanding: MoneyStr
    oldest_due_date: date | None
    days_overdue: int | None
    #: Ageing buckets keyed `current`, `1_30`, `31_60`, `61_90`, `over_90`.
    ageing: dict[str, str]


class CreditCustomersOut(Out):
    items: list[CreditCustomerOut]
    summary: dict[str, Any]
    page: Page
    as_of: datetime


# --- sync ---------------------------------------------------------------------


class MoneyPolicyOut(Out):
    """Published so the client never hardcodes a rounding or whole-rupee rule."""

    version: int
    currency: str
    amount_scale: int
    unit_price_scale: int
    quantity_scale: int
    percent_scale: int
    rounding_mode: str
    round_grand_total_to_rupee: bool
    require_integral_amounts: bool
    display_decimal_places: int


class BootstrapCounterOut(Out):
    id: uuid.UUID
    code: str
    name: str
    max_open_drafts: int


class BootstrapSyncOut(Out):
    allowed_operation_types: list[str]
    #: Always `"refused"`. No financial operation type exists offline (ADR-011).
    offline_finalisation: str
    max_batch_size: int


class BootstrapOut(Out):
    business_id: uuid.UUID
    branch_id: uuid.UUID | None
    counter: BootstrapCounterOut | None
    permissions: list[str]
    money_policy: MoneyPolicyOut
    #: Client-visible settings only; `max_open_drafts`, tax mode, discount caps and the
    #: print profile come from here rather than from client constants.
    settings: dict[str, Any]
    sync: BootstrapSyncOut
    #: The client derives display times from this offset and never derives a business
    #: date of its own (FE-E-3).
    server_time: datetime
    catalog_cursor: str | None


class CachedProductOut(Out):
    id: uuid.UUID
    name: str
    sku: str | None
    barcode: str | None
    unit: UnitOut
    default_selling_price: MoneyStr
    is_active: bool
    stock: StockOut


class CatalogDeltaOut(Out):
    """⚠️ `cost_price` is **never** present, regardless of permission: a stolen counter
    machine must not carry the shop's margins (`SECURITY.md §11`)."""

    products: list[CachedProductOut]
    next_cursor: str | None
    has_more: bool
    reset_required: bool
    cache_policy: dict[str, int]
    as_of: datetime
    warnings: list[WarningOut]


class SyncOperationErrorOut(Out):
    code: str
    message: str


class SyncOperationResultOut(Out):
    op_id: uuid.UUID
    #: `accepted` | `replayed` | `conflicted` | `rejected` | `skipped`.
    status: str
    server_state: dict[str, Any] | None
    #: Present on `conflicted`/`rejected`/`skipped` only.
    error: SyncOperationErrorOut | None = None


class SyncBatchOut(Out):
    results: list[SyncOperationResultOut]
    server_time: datetime


class OperationTypesOut(Out):
    allowed: list[str]
    offline_finalisation: str
    reason: str


# --- reports ------------------------------------------------------------------


class ReportOut(BaseModel):
    """The common report envelope (`API-SPECIFICATION.md §3.11`).

    ⚠️ The **only** permissive model in this module. Each report spreads its own figures
    at the top level — `daily-sales` has `gross`/`net`/`by_day`, `stock-position` has
    `rows` — so `extra="forbid"` would make one model impossible. The envelope is what
    every report shares and what the client relies on; the per-report figures are typed
    where a screen renders them field by field (`DailySalesOut`).
    """

    model_config = ConfigDict(extra="allow")

    as_of: datetime
    #: Echoed back so a printed or exported report states the range it covers.
    filters: dict[str, Any]
    #: What the report deliberately leaves out (cancelled bills, other branches), stated
    #: rather than silently applied.
    excluded: dict[str, Any]


class DailySalesByDayOut(Out):
    date: date
    bill_count: int
    net: MoneyStr
    #: Part of the day's trading, shown separately because it is not cash taken
    #: (FR-RPT-001).
    credit_sales: MoneyStr


class DailySalesByMethodOut(Out):
    method: str
    collected: MoneyStr


class DailySalesOut(ReportOut):
    """`GET /reports/daily-sales` — the one report a screen renders field by field."""

    bill_count: int
    gross: MoneyStr
    discount: MoneyStr
    tax: MoneyStr
    net: MoneyStr
    cash_sales: MoneyStr
    credit_sales: MoneyStr
    credit_bill_count: int
    #: Cash actually taken, which is **not** `net`: a credit bill adds to `net` and
    #: nothing to the till (FR-RPT-002).
    collected: MoneyStr
    by_day: list[DailySalesByDayOut]
    by_payment_method: list[DailySalesByMethodOut]


class PaymentHistoryOut(Out):
    """One row of `GET /payments`.

    Declared for M25: the customer screen shows a payment history, and an undeclared
    response left the client stringifying `unknown` values — which ESLint correctly
    refused, because `String(someObject)` yields `[object Object]` on a screen showing
    money.
    """

    id: uuid.UUID
    amount: MoneyStr
    #: `null` when the payment has not been allocated to this invoice — a payment can
    #: exist before it is applied, and nulling it is not the same as zero.
    allocated: MoneyStr | None = None
    method: str
    reference: str | None = None
    received_at: datetime
    #: `payment` or `reversal`. A reversal is a new row; the original is never edited
    #: (FR-PAY-005), so both appear in this history.
    kind: str
    invoice_id: uuid.UUID | None = None


class PaymentHistoryListOut(Out):
    items: list[PaymentHistoryOut]


# --- inventory and purchases (declared for M25) -------------------------------
#
# These routes predate `responses.py` and returned undocumented dicts, which meant the
# client's types for them would have had to be hand-written — the exact thing ADR-029
# exists to prevent. Declaring them here is what lets the Stock and Purchases screens
# use generated types like every other screen.


class StockBalanceOut(Out):
    product_id: uuid.UUID
    name: str
    quantity: QuantityStr
    #: `null` when the product has no reorder level set — which is not the same as a
    #: reorder level of zero, so the screen must not render "0" for it.
    reorder_level: QuantityStr | None = None


class StockBalancesOut(Out):
    items: list[StockBalanceOut]
    as_of: datetime


class StockMovementOut(Out):
    id: uuid.UUID
    product_id: uuid.UUID
    #: Signed. The sign is derived from `reason`, never supplied by a caller.
    quantity_delta: QuantityStr
    balance_after: QuantityStr
    reason: str
    source_type: str
    source_id: uuid.UUID
    oversell_override: bool
    override_reason: str | None = None
    created_at: datetime


class StockMovementsOut(Out):
    items: list[StockMovementOut]
    as_of: datetime


class AdjustmentOut(Out):
    movement_id: uuid.UUID


class SupplierOut(Out):
    id: uuid.UUID
    name: str
    phone: str | None = None


class SupplierListOut(Out):
    items: list[SupplierOut]


class SupplierUpsertOut(Out):
    id: uuid.UUID


class PurchaseSummaryOut(Out):
    id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    supplier_document_number: str | None = None
    document_date: date | None = None
    status: str
    subtotal: MoneyStr
    total: MoneyStr
    line_count: int


class PurchaseListOut(Out):
    items: list[PurchaseSummaryOut]


class PurchaseCreatedOut(Out):
    id: uuid.UUID


class PurchaseConfirmedOut(Out):
    #: How many stock movements the confirmation produced. `0` means it was already
    #: confirmed — the idempotent case, which the screen reports as "already received"
    #: rather than as a second receipt.
    movements: int


# --- imports (M23) ------------------------------------------------------------


class ImportUploadOut(Out):
    """`202` from `POST /imports`.

    `duplicate_of_job_id` is present and null on a normal upload rather than omitted:
    unlike `cost_price`, its absence carries no permission meaning, and a client
    checking `=== null` is clearer than one checking `in`.
    """

    job_id: uuid.UUID
    status: str
    duplicate_of_job_id: uuid.UUID | None = None


class ImportJobOut(Out):
    id: uuid.UUID
    #: Monotonic upload order, and the `?before=` cursor for the history list. A
    #: timestamp cursor over a non-unique column can skip or repeat a row (migration
    #: `0020`), which is why this is here rather than `created_at`.
    ordinal: int
    intent: str
    status: str
    original_filename: str
    file_size_bytes: int
    supplier_id: uuid.UUID | None = None
    row_count: int
    rows_applied: int
    rows_skipped: int
    rows_failed: int
    #: Derived, not stored — how many rows still block an apply (FR-IMP-007). The
    #: review screen's progress indicator reads this and nothing else.
    rows_pending_decision: int
    error_summary: str | None = None
    created_purchase_id: uuid.UUID | None = None
    duplicate_of_job_id: uuid.UUID | None = None
    created_at: datetime
    applied_at: datetime | None = None


class ImportJobsOut(Out):
    jobs: list[ImportJobOut]


class ImportRowOut(Out):
    id: uuid.UUID
    row_index: int
    #: The file's own cells, verbatim, header → value as text. Shown beside the
    #: interpreted values so a reviewer can see what the software did to their data
    #: (FR-IMP-006).
    raw_values: dict[str, str | None]
    parsed_name: str | None = None
    parsed_sku: str | None = None
    parsed_quantity: QuantityStr | None = None
    parsed_unit_cost: MoneyStr | None = None
    parsed_selling_price: MoneyStr | None = None
    match_status: str
    matched_product_id: uuid.UUID | None = None
    matched_product_name: str | None = None
    candidate_product_ids: list[uuid.UUID]
    match_confidence: MoneyStr | None = None
    decision: str
    apply_result: str | None = None
    #: Doubles as the matcher's reason before an apply and the failure reason after one.
    #: One field because a reviewer asks the same question at both moments: why is this
    #: row like this?
    apply_error: str | None = None
    needs_decision: bool


class ImportRowsOut(Out):
    rows: list[ImportRowOut]


class ImportDecisionOut(Out):
    row: ImportRowOut


class ImportReportRowOut(Out):
    row_index: int
    parsed_name: str | None = None
    parsed_sku: str | None = None
    match_status: str
    decision: str
    apply_result: str | None = None
    apply_error: str | None = None


class ImportReportOut(Out):
    """FR-IMP-009. Carries the skipped rows and their reasons, not just the counts."""

    job: ImportJobOut
    rows: list[ImportReportRowOut]


# --- operational --------------------------------------------------------------


class AlertingStatusOut(Out):
    """OD-19's gap, made visible.

    `state` is `unconfigured` when alerting is on and no destination is set — an
    operator reading `/readyz` can see that alerts are being dropped instead of
    discovering it during an incident.
    """

    state: str
    recipient_name: str | None = None
    #: Scheme and host only. For most webhook providers the path is the credential.
    destination_hint: str | None = None
    min_severity: str


# --- errors -------------------------------------------------------------------


class ErrorDetailOut(Out):
    #: One of the 53 registered codes. Clients branch on this, never on `message`.
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str
    retryable: bool


class ErrorOut(Out):
    """The envelope every non-2xx response carries, without exception (§4)."""

    error: ErrorDetailOut
