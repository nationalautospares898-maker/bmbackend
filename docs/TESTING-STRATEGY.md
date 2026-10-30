# Testing Strategy — `bmbackend`

| Field | Value |
|---|---|
| Status | **Phase 1 proposal — no tests exist, none have been run** |
| Last updated | 2026-10-29 |

> **No test has been written or executed during Phase 1.** Every pass/fail claim in this document is
> a requirement for Phase 2, not a result.

---

## 1. Principles

1. **Test the money and the stock hardest.** A bug in a report is an annoyance; a bug in
   finalisation is a wrong bill or missing inventory. Coverage targets are deliberately uneven.
2. **Real PostgreSQL in every test that touches data.** SQLite cannot reproduce `FOR UPDATE`,
   `NUMERIC` semantics, partial indexes, enums, `pg_trgm` or privilege-based immutability — the exact
   things this design relies on. **Using SQLite for tests is prohibited** (ADR-017).
3. **Invariants are tests, not comments.** Every `NFR-DATA-*` invariant has a named test that fails
   loudly if violated.
4. **Concurrency is tested with real concurrency**, not by asserting that a lock statement appears in
   the code.
5. **Every documented error code is produced by a test.** A code in the registry that no test emits
   is either dead or unimplemented.
6. **Tests assert behaviour, not implementation.** Repository internals may be refactored freely;
   service contracts may not change silently.

---

## 2. Test layers

| Layer | Directory | Database | What it covers | Target runtime |
|---|---|---|---|---|
| Unit | `tests/unit/` | none | Money arithmetic, rounding, normalisation, ranking, permission resolution, state machines, invoice-number formatting, amount-in-words | < 10 s |
| Integration | `tests/integration/` | real | Services against the schema: transactions, locks, rollback, cascading rules | < 90 s |
| API | `tests/api/` | real | HTTP contract: status codes, error envelope, auth, permissions, pagination, redaction | < 120 s |
| Concurrency | `tests/concurrency/` | real, multi-connection | Overselling, numbering, double-finalisation, allocation races | < 120 s |
| Invariants | `tests/invariants/` | real, seeded | `NFR-DATA-001…010` over generated datasets | < 60 s |
| Migrations | `tests/migrations/` | real | Up/down on every revision, plus invariants after upgrade | < 120 s |
| Sync | `tests/sync/` | real | Operation dedup, ordering, conflicts, revoked device, catalog delta | < 60 s |
| Property | `tests/property/` | real + Hypothesis | Money/stock/allocation laws under generated input | < 180 s |
| Performance | `tests/perf/` | real, seeded 100k | `NFR-PERF-*` budgets | nightly |

Total target for the PR-blocking suite: **under 8 minutes**.

---

## 3. Fixtures and data

```
business_factory()      # a business + branch + counter + invoice series
user_factory(role=)     # user with a role, membership, device, token
product_factory()       # product with unit, optional sku/aliases
stock(product, qty)     # seeds via a real `opening` movement — never a raw UPDATE
draft_factory()         # draft with N lines
invoice_factory()       # finalised through the real service, never by inserting rows
customer_factory()
```

**Rule: fixtures create state through the real service paths wherever a service exists.** Seeding an
invoice with raw inserts would let a test pass against data the application could never produce. The
only raw-insert fixtures are for reference data (units, permissions) and for deliberately
constructing corrupt state in reconciliation tests.

Database per test session, transaction-per-test rollback for speed, except concurrency tests which
need real commits and therefore get a truncate-based reset.

---

## 4. What each module must prove

### 4.1 Money (`domain/shared/money.py`) — unit + property

- Line total = `qty × unit_price − discount`, rounded half-up to 2 dp.
- Invoice total = Σ lines − invoice discount + tax + rounding adjustment (NFR-DATA-002).
- Property: for any legal set of lines, `total` recomputed from the stored snapshot equals the stored
  `total`, to the paisa.
- Property: rounding never loses or creates money — Σ line totals + rounding adjustment is exactly
  the grand total.
- No `float` anywhere: a structural test greps `domain/` for `float(` and inspects
  `information_schema.columns` for `double precision`/`real` in money columns (NFR-DATA-009).
- Quantity scale: a fractional quantity on an integer-only unit is rejected (E-18).
- `amount_in_words` for 0, 1, 99, 100, 1 00 000, 1 00 00 000 and a paise value.

### 4.2 Catalog and search

- First-character search returns results (FR-CAT-003).
- Ranking order: exact SKU before SKU prefix before name prefix before alias before fuzzy.
- `JK-123`, `jk 123`, `jk123`, `JK.123` all find the same product (FR-CAT-006).
- Alias and barcode lookups.
- `limit` above the hard max is clamped, not honoured (NFR-PERF-006).
- Duplicate SKU creation → `409 DUPLICATE_SKU` naming the conflict (FR-CAT-008).
- Deactivated products are excluded by default, included with the flag.
- `cost_price` is **absent** from the payload without `catalog.cost.read` (FR-CAT-004).
- A product referenced by a sale cannot be hard-deleted (FR-CAT-009).

### 4.3 Drafts

- Upsert with a client-generated id creates, then updates (FR-DRAFT-001).
- 20 drafts open simultaneously; the 21st (at default config) → `DRAFT_LIMIT_REACHED` and **no
  existing draft is touched** (FR-DRAFT-002).
- `version` increments on every change; stale `base_version` → `409 DRAFT_VERSION_CONFLICT` with
  `current_state` (FR-DRAFT-004, E-4).
- Missing `If-Match` on an existing draft → `428`.
- **Opening/editing/holding a draft produces zero stock movements** — asserted by counting
  movements before and after (FR-DRAFT-005).
- Switching drafts, reloading, and 50 rapid alternating updates finalise nothing and lose nothing
  (FR-DRAFT-007).
- A draft owned by another counter → `403`; takeover with permission succeeds and is audited.
- Deleting a draft marks it cancelled and keeps the row.

### 4.4 Sale finalisation — the most heavily tested surface

**Happy paths**
- Cash sale: invoice + items + movements + payment, `payment_status = paid`, stock down by exactly
  the sold quantity.
- Credit sale with zero payment: `unpaid`, `outstanding = total`, `due_date` from terms.
- Credit sale with part payment: `partially_paid`, correct `amount_paid` and `outstanding`.
- **Credit finalises in one call** — no preparatory request exists; a structural test asserts the
  OpenAPI document contains no "prepare credit" / "credit details" endpoint (FR-CREDIT-008).
- **Credit requires a persisted `customer_id`**: free-text name only → `409
  CREDIT_CUSTOMER_REQUIRED`, with no invoice, movement or consumed number (FR-CREDIT-009).
- A customer created with **name alone** can receive a credit invoice (FR-CUST-010).
- Optional header values (vehicle, mechanic, phone, business name, notes) are **persisted on the
  invoice snapshot** when supplied (FR-CREDIT-012).
- **Recording a payment creates zero stock movements** — asserted by counting movements before and
  after `POST /payments` (FR-CREDIT-010).
- Renaming a customer leaves every invoice and payment association intact, and the grouped
  `GET /credit/customers` result still contains them (FR-CUST-011, FR-RPT-029).
- `GET /credit/customers` aggregates equal the per-invoice outstanding sums for the same customer —
  an invariant test, not a spot check (FR-RPT-030).
- Fully paid invoices remain returned under `include_settled=true` and are never deleted
  (FR-RPT-031).
- Cash and credit produce **structurally identical** invoices and movements apart from payment rows —
  asserted by comparing the two results field by field (C-6, FR-SALE-009).

**Atomicity**
- Inject a failure after the invoice insert but before movements → **nothing** persists: no invoice,
  no movement, no payment, **and no consumed invoice number** (FR-SALE-001, FR-SALE-004).
- Inject a failure during payment recording → whole sale rolled back.
- Assert post-rollback that `invoice_series.next_number` is unchanged.

**Idempotency** (FR-SALE-002, E-2)
- Same key + same payload twice → one invoice, second response identical, `Idempotent-Replay: true`.
- Same key + different payload → `409 IDEMPOTENCY_KEY_REUSE`.
- Concurrent requests with the same key → exactly one invoice; the loser gets either the stored
  response or `IDEMPOTENT_REQUEST_IN_PROGRESS`, never a second sale.
- Missing required key → `422`.

**Stock**
- Insufficient stock → `409 INSUFFICIENT_STOCK` with per-line `requested`/`available`, and **no**
  invoice, number or movement created (FR-SALE-007).
- Override without permission → `403 OVERSELL_NOT_PERMITTED`.
- Override with permission but no reason → `422 OVERSELL_REASON_REQUIRED`; reason under 10 chars
  rejected.
- Override with permission and reason → sale succeeds, balance goes negative, movement carries
  `oversell_override = true` and the reason, audit row written with actor and device (FR-SALE-008).

**Prices**
- Catalog price changed after the draft line was added → `409 PRICE_CHANGED` listing draft vs current
  (FR-SALE-006, E-5).
- `accept_price_changes: true` → finalises at the current price and sets
  `price_changes_accepted`.
- A `manual_override` line with permission → finalises at the overridden price; without permission →
  `403 PRICE_OVERRIDE_NOT_PERMITTED`.
- Cashier discount above `discount.cashier_max_percent` → `409 DISCOUNT_EXCEEDS_LIMIT`.
- Zero-price line requires `billing.price.override` (FR-SALE-014).

**Validation**
- Empty bill, zero quantity, negative quantity, negative price → `422`.
- Inactive product → `409 PRODUCT_INACTIVE` naming the line (E-15).
- Credit sale without a customer → `409 CREDIT_CUSTOMER_REQUIRED` (FR-CUST-002).
- Client total mismatch → `422 TOTAL_MISMATCH` with both figures (FR-SALE-003).

**Immutability**
- No route exists to update or delete an invoice or item — asserted by enumerating the OpenAPI
  document, not by trying one URL (FR-SALE-010).
- A direct `UPDATE`/`DELETE` as the application role fails on `invoice_items`, `stock_movements`,
  `payments`, `audit_logs` (NFR-DATA-006, SECURITY §7).

**Cancellation**
- Within the window with permission → status `cancelled`, reversing movements restore stock exactly,
  a full-value credit note exists, the invoice number is retained, the record is still listed
  (FR-SALE-011, E-6).
- Outside the window → `409 CANCEL_WINDOW_EXPIRED`.
- Second cancellation → `409 INVOICE_ALREADY_CANCELLED` (E-17).
- A cancelled invoice is excluded from revenue but present in audit views (FR-RPT-008).

**Double finalisation**
- Finalising the same draft twice → `409 DRAFT_ALREADY_FINALIZED` carrying the existing invoice
  (FR-SALE-012).
- The `uq_draft_bills__invoice` constraint is exercised directly.

### 4.5 Inventory

- Every balance change has a matching movement; a test asserts **no code path other than
  `InventoryService.apply_movement` writes the balance table** (by patching it and running the full
  API suite).
- Reconciliation detects an artificially corrupted balance, reports it, and **does not silently
  fix it** (FR-INV-006).
- Adjustment without permission → 403; without reason → 422; with both → movement + audit.
- Movement reasons are restricted to the enum; an unknown reason is a type error.
- Returns, cancellations, receipts, opening balances each produce the correct signed delta and
  `balance_after`.

### 4.6 Concurrency suite (real parallel connections)

| Test | Assertion |
|---|---|
| Two finalisations for the last unit | Exactly one invoice; the other gets `INSUFFICIENT_STOCK` with `available: 0`; final balance 0; two movements never exist (AC-3, E-3) |
| Ten finalisations, stock of 3 | Exactly 3 succeed; balance 0; 7 clean errors; no negative balance |
| Twenty parallel finalisations on one series | 20 invoices, sequence numbers `n…n+19` with **no gaps and no duplicates** (NFR-DATA-005) |
| Mixed finalise + cancel + return on the same product | Ledger sum always equals the balance (NFR-DATA-001) |
| Two payments allocating to the same invoice concurrently | `amount_paid` correct; over-allocation refused; no lost update (NFR-DATA-003/004) |
| Concurrent draft updates from two devices | One wins, one gets a version conflict; no lost line |
| Deadlock probe: 50 random multi-product sales in parallel | **Zero deadlocks**, proving the lock-ordering rule (ARCHITECTURE §5.1) |
| Lock timeout | A sale blocked beyond `lock_timeout` fails fast with a retryable error, not a hang |

These run with real commits across separate connections, with barriers to force the interleaving
rather than hoping for it.

### 4.7 Credit and payments

- Outstanding is derived correctly across: part payment, several payments, over-payment, credit-note
  allocation, cancellation, return.
- `amount_paid` cache always equals the allocation sum (NFR-DATA-003) — including after a reversal.
- Allocation strategies: `oldest_first` across several invoices, `invoice_specific`, `manual`.
- Remainder becomes on-account credit and is allocatable later (FR-PAY-003).
- Over-allocation → `422 OVER_ALLOCATION` (FR-PAY-007).
- Duplicate payment with the same key → one payment (FR-PAY-004, E-7).
- Reversal creates a new row, leaves the original untouched, and corrects the balance (FR-PAY-005).
- **There is no API path that sets `payment_status` or `amount_paid`** — asserted over the OpenAPI
  document (FR-CREDIT-003).
- Ageing buckets at exact boundaries (0, 1, 30, 31, 60, 61, 90, 91 days) with a frozen clock.
- Credit limit: warn when off, `409 CREDIT_LIMIT_EXCEEDED` when enforced, override audited.

### 4.8 Returns

- Partial return: stock in, credit note at snapshot price, `returned_quantity` updated, original
  invoice rows otherwise untouched (E-10, AC-12).
- Cumulative returns exceeding sold quantity → `409 RETURN_QUANTITY_EXCEEDED`; the DB `CHECK` is
  also exercised directly (NFR-DATA-008).
- Concurrent returns of the same item cannot jointly exceed the sold quantity.
- `condition: damaged` with `restock: false` → scrap movement, not sellable stock.
- Outside the window → `409`; against a cancelled invoice → `409 INVOICE_CANCELLED`.
- Each `credit_handling` option produces the right financial effect.

### 4.9 Imports

- Type/size/sniff rejection matrix: `.exe` renamed to `.csv`, HTML as `.csv`, 11 MB file, zip bomb,
  path-traversal filename (SECURITY §5).
- Same bytes twice → `409 DUPLICATE_FILE` with the existing job; with `allow_duplicate` + reason →
  a second job, audited (E-9).
- State machine: every illegal transition rejected; `applied` cannot be re-applied
  (`IMPORT_ALREADY_APPLIED`).
- Row classification: exact SKU, alias, fuzzy, ambiguous, new, duplicate-in-file, invalid — one test
  per status with a crafted fixture.
- Applying with any required row still `pending` → `409 IMPORT_ROWS_PENDING_DECISION` (FR-IMP-007,
  E-12).
- `price_list` intent produces **zero stock movements** under all circumstances (FR-IMP-002).
- `purchase_receipt` intent creates a purchase and movements, in one transaction; a mid-apply failure
  leaves nothing applied.
- Apply twice with the same key → one effect (FR-IMP-008).

### 4.10 Sync

- Dedup: replaying a batch → all `replayed`, nothing changed (AC-OFF-1).
- Ordering: out-of-order `client_seq` for one entity is applied in order.
- Isolation: one invalid operation does not block other entities' operations (AC-OFF-7).
- Conflict: offline edit vs online edit → exactly one `conflicted` with both states (AC-OFF-2).
- Draft deleted/finalised server-side vs local edit → correct `rejected` codes.
- Revoked device → `403 DEVICE_REVOKED` on sync (AC-OFF-4).
- Catalog delta: cursor stability under concurrent product updates; `reset_required` on an expired
  cursor; row cap respected; **`cost_price` never present** (AC-OFF-6).
- **Structural test: the sync operation registry contains no type that finalises a sale, records a
  payment, or moves stock** (AC-OFF-8, FR-OFFLINE-002).
- Server time is used for ordering; a client clock set a year back changes nothing (E-14).

### 4.11 Auth and security

Covered in `SECURITY.md §14`; summarised here as the required files: `test_auth.py`,
`test_permissions.py`, `test_isolation.py`, `test_uploads.py`, `test_redaction.py`,
`test_rate_limits.py`, `test_exports.py`, plus the structural route-permission test.

The isolation matrix (AC-8) is generated from the route table, so **a new endpoint is automatically
covered** and a developer cannot add an unscoped route without failing CI.

### 4.12 Reports

- Daily sales includes credit sales for their sale date (FR-RPT-001).
- **Daily sales totals and credit outstanding reconcile exactly** against the invoice table for the
  same period (FR-RPT-003) — the single most valuable report test.
- Cancelled invoices excluded from revenue, present in the `excluded` block.
- Timezone/boundary: a sale at 23:59:59 and one at 00:00:00 land on the right days.
- Margin figures require `catalog.cost.read`.
- CSV export escapes formula-leading characters.
- Pagination and `as_of` present on every report.

### 4.13 Tax gate

- While `estimate_only`: `tax_total` is 0, `print_payload.totals.tax` is `null`,
  `document_title` is `ESTIMATE`, and **no response anywhere contains the string "Tax Invoice"** —
  asserted by scanning serialised responses across the API suite (AC-11, FR-TAX-004).
- `POST /admin/tax/enable-gst` with an incomplete configuration → `422 TAX_CONFIG_INCOMPLETE`
  listing every missing item; with a complete configuration → succeeds and is audited (FR-TAX-003).
- Effective-dated rates: an invoice issued before a rate change uses the old rate; its snapshot never
  changes afterwards.

### 4.14 Migrations

- Every revision upgrades and downgrades cleanly from its predecessor.
- Full upgrade on a production-sized seeded database, then the **invariant suite runs against the
  upgraded database** (§4.14 is what makes NFR-MAINT-004 real).
- No migration leaves a money column as `float`.
- Seed migrations are idempotent when run twice.
- A test asserts **no seed data contains tax rates, business identity or demo products**.

---

## 5. Property-based tests (Hypothesis)

| Property | Statement |
|---|---|
| Stock conservation | For any random sequence of receipts, sales, returns, cancellations and adjustments, the balance equals the ledger sum (NFR-DATA-001) |
| Money conservation | For any random bill, `Σ line_totals − discounts + tax + rounding == total`, exactly (NFR-DATA-002) |
| Allocation soundness | For any random set of payments and allocations, `Σ allocations ≤ payment.amount` and `amount_paid` matches (NFR-DATA-003/004) |
| Non-negative stock | Without an override, no sequence of operations can drive a balance below zero (NFR-DATA-007) |
| Return bound | No sequence of returns can exceed the sold quantity (NFR-DATA-008) |
| Numbering | For any interleaving of successes and failures, the **internal** sale reference is **unique and sequential** within (branch, series, FY) (NFR-DATA-005). ⚠️ **Gap detection is dropped as a criterion** — [OD-3](OWNER-DECISIONS.md#od-3) withdrew customer-visible numbering, so AC-1a is withdrawn. A **structural** test asserts **no customer-visible number is rendered or emitted in `print_payload`** |
| Search normalisation | `normalize(normalize(x)) == normalize(x)` and known part-number variants collapse to one key |

---

## 6. Performance tests (nightly, not PR-blocking)

| ID | Scenario | Budget |
|---|---|---|
| NFR-PERF-001 | 100k products, 50 concurrent searches | p95 ≤ 150 ms server time |
| NFR-PERF-002 | 10-line finalisation under 5-counter load | p95 ≤ 600 ms, p99 ≤ 1.5 s |
| NFR-PERF-003 | Draft upsert | p95 ≤ 250 ms |
| NFR-PERF-004 | Daily report (1 day / 1 month) on a 2-year dataset | ≤ 1 s / ≤ 3 s |
| NFR-PERF-005 | 5 counters × 20 drafts sustained | no budget breach, no deadlock |

Results are recorded per run so regressions are visible as a trend, not discovered at go-live. A
budget breach opens an issue; it does not silently relax the budget.

---

## 7. Coverage and quality gates

| Gate | Threshold |
|---|---|
| Overall line coverage | ≥ 85% |
| `domain/shared/money.py`, `domain/sales/`, `domain/inventory/`, `domain/credit/`, `domain/sync/` | ≥ 95% **branch** coverage |
| `ruff`, `mypy --strict` on `domain/` | zero errors |
| Import-boundary linter (ARCHITECTURE §3) | zero violations |
| Every route declares a permission | structural test |
| Every error code in the registry is produced by some test | structural test |
| Dependency audit | no high/critical |
| Secret scan | clean |
| Migration up/down | passes |

Coverage percentage alone is not the gate — the invariant, concurrency and isolation suites must
pass regardless of the number.

---

## 8. CI pipeline

```
on: push, pull_request
  1  checkout, install (uv, locked)
  2  ruff + mypy + import-boundary lint
  3  unit tests                              (no DB)
  4  start postgres service
  5  alembic upgrade head
  6  integration + api + sync tests
  7  invariant tests
  8  concurrency tests
  9  migration up/down test
 10  coverage gate
 11  dependency audit + secret scan
 12  build docker image (push only on main)

nightly:
  property tests (extended), performance suite, restore drill (quarterly)
```

Test database: a Postgres service container matching the production major version, with `pg_trgm`,
`unaccent` and `citext` installed, and the same privilege grants as production so immutability tests
are meaningful.

---

## 9. Manual test checklist (pre-go-live, with the owner present)

Automated tests cannot verify these:

1. The printed bill matches the owner's actual Excel invoice — compared side by side on the real
   printer and paper (go-live gate 3, Q-PRINT-1).
2. Staff can complete a 5-line bill in under 60 seconds without help (BO-4).
3. Staff correctly understand the difference between a reprint and a new sale.
4. Staff correctly understand the "not yet confirmed" state during an outage (E-1) and the counter
   slip fallback.
5. Unplug the internet mid-shift, keep billing for 10 minutes, reconnect, confirm every bill
   finalises exactly once.
6. Unplug the printer, finalise a sale, confirm the invoice exists and reprints correctly (E-8).
7. Revoke a device from the owner's phone and confirm the counter stops syncing.
8. Restore a backup into staging and confirm the data is right (AC-10).

---

## 10. Related documents

`docs/PRD.md` (requirements and AC) · `docs/ARCHITECTURE.md` · `docs/DATA-MODEL.md` (invariants) ·
`docs/API-SPECIFICATION.md` (error codes) · `docs/SECURITY.md §14` ·
`docs/OFFLINE-SYNCHRONIZATION.md §12` · `docs/TRACEABILITY.md` ·
`../../bmfrontend/docs/TESTING-STRATEGY.md`
