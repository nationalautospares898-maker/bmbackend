# Phase 3 Implementation Progress and Resumption Record

| Field | Value |
|---|---|
| Status | **Living record. Update it in the same commit as the work it describes.** |
| Purpose | The master implementation tracker required by the Phase 3 directive: every milestone's real state, the evidence for it, and the exact next action |
| Scope | Both repositories — `bmbackend` and `bmfrontend` |
| Last updated | 2026-10-30 |
| Authority | Implementation status only. It grants no owner, legal, accounting or physical acceptance, and it never supersedes [`PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md`](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) |

> **Read this before writing any Phase 3 code.** It records what is actually built and
> verified, so work resumes from here rather than repeating anything. Status words are
> used strictly, per §1.

---

## 1. Status vocabulary

These five words are used exactly as defined. A milestone is never described with a
stronger word than its evidence supports.

| Word | Means |
|---|---|
| **Implemented & verified** | Code exists, its milestone tests exist and were **executed**, and the static gates passed. The commit hash is recorded. |
| **Partially implemented** | Some deliverables exist and pass; the rest are named explicitly below. |
| **Not started** | No code. Design is complete (Phase 2), implementation has not begun. |
| **Design-complete, awaiting external acceptance** | Everything buildable is built; what remains needs a printer, a person or a legal opinion. |
| **Blocked** | Cannot proceed without a named external input. The input is named. |

"Tests pass" appears only where the suite was run and its output observed.

---

## 2. Milestone status

### 2.1 Backend foundations

| Milestone | Status | Evidence |
|---|---|---|
| **M1** Repository, CI, conventions | **Implemented & verified** | `pyproject.toml`, `src/bm/{main,worker,config}.py`, `api/{errors,middleware,deps,route_policy}.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `.github/workflows/ci.yml`, `.importlinter`. `/healthz`, `/readyz` (database + Alembic head), `/api/v1/meta`, `/api/v1/openapi.json`. A deliberate reverse import was introduced and **both** gates failed as designed (import-linter contract + `tests/structural/test_module_boundaries.py`), then reverted. |
| **M2** Database foundations | **Implemented & verified** | Migrations `0001`–`0004`: extensions, every ENUM type, tenancy (incl. **T3-02** `businesses.timezone`), identity, `audit_logs` with `REVOKE UPDATE, DELETE, TRUNCATE` **in the same migration as the table**. Fix **S-3** applied. `alembic downgrade base` → `upgrade head` clean. `tests/invariants/` connects as `bm_app` and asserts the database *refuses* mutation; it skips with a reason when the role is absent rather than passing vacuously. |
| **M3** Authentication and authorisation | **Implemented & verified** | `domain/auth/{service,tokens,hashing,devices,users,permissions}.py`, `api/v1/{auth,admin}.py`, migration `0005` seeds permissions and the six system roles from `PRD.md §3.2`. Rotation with reuse detection, lockout, `must_change_password` gating, device revocation on every request. Permission and isolation matrices are **generated from the live route table**. |
| **M4** Settings and the money engine | **Implemented & verified** | `domain/shared/{money,quantity}.py`, `domain/settings/{defaults,service}.py`, `api/v1/settings.py`. Addendum §3 implemented as a policy-parameterised algorithm. `money.py` **96% branch coverage** (floor 95%). 1,900+ generated Hypothesis cases. |

### 2.2 Backend core domain

| Milestone | Status | Evidence |
|---|---|---|
| **M5** Catalog and search | **Implemented & verified** | `domain/catalog/service.py`, `infra/db/repositories/catalog.py`, `api/v1/catalog.py`, migration `0006`. First-character search; ranking exact SKU -> SKU prefix -> name prefix -> alias -> trigram, with sales frequency then name as the tie-break. `cost_price` **omitted, not nulled**, asserted on the wire. Fix **S-8**: an alias insert or delete rebuilds the parent's `search_text`. |
| **M6** Inventory ledger | **Implemented & verified** | `domain/inventory/service.py`, migration `0007`. **The sole writer.** Append-only `stock_movements`; locks acquired in ascending `product_id` (ADR-008); `balance_after` read from `UPDATE ... RETURNING` rather than recomputed. `reconcile()` **reports and never repairs**, and includes the **T3-04** `max(sequence)+1 == next_number` check. |
| **M7** Customers | **Implemented & verified** | `domain/customers/service.py`, `api/v1/customers.py`, migration `0008`. Client-generated ids, so an offline-created customer syncs as an upsert rather than a duplicate. Duplicate detection **surfaces candidates and never merges**. |
| **M8** Drafts | **Implemented & verified** | `domain/sales/drafts.py`, `api/v1/drafts.py`, migration `0012`. Optimistic concurrency via `If-Match`; `428 PRECONDITION_REQUIRED` when it is missing on an existing draft; **no server-side merge** (ADR-007). The draft limit **refuses** and never discards. `clear()` refuses a finalised draft. |
| **M9** Invoice numbering | **Implemented & verified** | `domain/sales/numbering.py`, migration `0010`. The financial-year series row is created **lazily inside the finalisation transaction** (`P2-D-09`), then locked `FOR UPDATE`. A rolled-back sale consumes no number -- asserted by `test_a_failed_sale_consumes_no_reference`. |
| **M10** Sale finalisation and idempotency | **Implemented & verified** | `domain/sales/service.py`, `domain/sync/idempotency.py`, `api/v1/sales.py`, migration `0011`. **One endpoint, one engine, cash and credit** (ADR-033). The key is claimed as the **first write, before any inventory lock** (`P2-D-06`); canonicalisation implements `P2-D-07` rules 1-10; the stored response is written in the **same transaction** that commits the sale. **T3-03** `invoices.money_policy_version` landed here. |
| **M11** Credit, payments, allocations | **Implemented & verified** | `domain/credit/service.py`, `api/v1/credit.py`, migration `0013`. `payment_status` and `amount_paid` are **recomputed from the allocations**, never incremented, and written only alongside them (ADR-022). Payments move **no stock** (FR-CREDIT-010). Ageing buckets are server-computed. |
| **M12** Invoice read, cancellation, print payload | **Implemented & verified** | `domain/sales/{reader,print_payload,cancellation}.py`, `api/v1/{invoices,serialize}.py`. **T3-01** exact idempotency-key lookup landed. **T3-06**: `print_payload` carries no invoice-number field at all, asserted by an `assert` in the builder *and* by a test. Reprinting creates nothing -- no sale, no number, no movement. |
| **M13** Reports and exports | **Implemented & verified** | `domain/reports/service.py`, `api/v1/reports.py`. `collected` is a distinct figure from `net`; every report states its `excluded` set rather than applying it silently; CSV export escapes formula injection. |
| **M14** Sync and catalog delta | **Implemented & verified** | `domain/sync/service.py`, `api/v1/sync.py`. The operation registry **refuses** any financial type (ADR-011). The catalog delta is bounded by `sync.catalog_cache_max_rows` and never carries `cost_price`, regardless of permission. |
| **M15** Suppliers and purchases | **Implemented & verified** | `domain/purchases/service.py`, `api/v1/purchases.py`, migration `0009`. Creating a purchase changes no stock; **confirming is the only path that receives it**, and is idempotent under a status guard in the `UPDATE`. |
| **Child-table tenancy** | **Implemented & verified** | Migration `0017` adds `business_id NOT NULL` to `draft_bill_items`, `purchase_items`, `sales_return_items`, `stock_count_items` and `import_rows` -- the five tables that derived tenancy from a parent row, which is one forgotten join away from a cross-tenant read (NFR-DATA-010). |
| **Response contract** | **Implemented & verified** | `api/v1/schemas/responses.py` publishes every response shape in the OpenAPI document, so the client's types are **generated** rather than hand-written (ADR-029). `tests/api/test_response_contract.py` validates **real** responses against them with `extra="forbid"`. |

### 2.3 Frontend

| Milestone | Status | Evidence |
|---|---|---|
| **Frontend M1** (scaffold) | **Implemented & verified** | Toolchain, `lib/money.ts` at **100%** coverage, `lib/strings.ts`, the structural suite. |
| **M16** Shell, API client, auth | **Implemented & verified** | `api/{client,errors,endpoints,types,session}.ts`, `api/generated/schema.d.ts` (generated, committed), `db/{schema,meta,drafts,outbox,pendingFinalizations,catalogCache}.ts`, `components/primitives.tsx`, `app/{bootstrap,AppShell}.tsx`, `lib/{ids,normalize,format}.ts`. The **single shared refresh promise** (twenty concurrent 401s produce one refresh, asserted), the exhaustive 53-code `ErrorCode` switch, and the access token in memory only. |
| **M17** Billing counter and draft persistence | **Implemented & verified** | `features/billing/{draftStore.ts,BillingCounter,ItemTable,ProductSearch}.tsx`. Dexie-first writes; derived totals, never stored; 120 ms debounced search with request cancellation; the open-bill limit refuses rather than discarding. |
| **M18** Cash finalisation | **Implemented & verified** | `features/billing/finalize.ts`. The key is persisted **before** the first request; an unknown outcome keeps it and marks the bill `unconfirmed` with **no bare retry offered anywhere**; a definitive refusal releases it. `P2-D-08` honoured -- the illustrative `onDraftEdited()` in `API-INTEGRATION.md §5` was **not** implemented as written, because it discards the key of a sale that may already have committed. |
| **M19** Credit finalisation | **Implemented & verified** | The same endpoint and engine with `sale_type: 'credit'`. `payment` omitted at save time (FR-CREDIT-025); a persisted customer required (FR-CREDIT-009); **no print dialog** opens (OD-21); the confirmation says **"Stock has been updated."** (FR-CREDIT-013/026). Refused offline exactly like cash. |
| **M20** Print layout and counter slip | **Implemented & verified** -- physical validation is M27, an external gate | `features/print/{InvoicePrintLayout,PrintRoot}.tsx`, `styles/print.css`. Renders `print_payload` and nothing else. **T3-10/T3-11** landed: 25-row capacity with **blank** fillers (never the workbook's hand-typed `0`), no tax row at all when `tax` is null, no invoice number, the credit block only when the server says so, and the date order from the payload. |
| **M21** Daily Bills and Credit Bills | **Implemented & verified** | `features/reports/DailyBills.tsx`. One dataset, two views (FR-RPT-003); every figure rendered from the response verbatim; grouping keys on `customer_id`, so a rename does not split a customer's history. |
| **M22** Offline layer | **Implemented & verified** | `features/sync/{scheduler.ts,SyncPanel.tsx}`, `db/{outbox,catalogCache}.ts`. Six triggers (FR-OFFLINE-012), owner-tab election via Web Lock, coalescing draft upserts, `rejected`/`conflicted` surfaced and **never dropped**, catalog cache bounded and cost-price-free. |

### 2.4 Secondary and release

| Milestone | Status | Note |
|---|---|---|
| **M23** Imports (P1) | **Not completed** | The `import_jobs` / `import_rows` schema exists (migrations `0015`, `0017`); there is no parser, no review table and no apply path. P1, and no P0 billing flow depends on it. |
| **M24** Returns and credit notes (P1) | **Implemented & verified** | `domain/returns/service.py`, migration `0014`. Credits the **snapshot** price, never the current one; a damaged return becomes scrap rather than resalable stock; the return window and the per-item returnable quantity are both enforced. |
| **M25** Frontend P1 screens | **Not completed** | Stock, Purchases, Customers, Imports, and the report screens beyond Daily Bills and Credit Bills. The API for each exists and is verified; the screens do not. |
| **M26** Hardening and operational readiness | **Partially implemented** | Done: the reconciliation report, the audit trail with a secret-scan test, the one error envelope, rate limiting, strict CSP, the permission-before-validation ordering check. **Not done:** load tests, the restore drill, and the named alert recipient (OD-19). |
| **M27** Print completion and physical validation | **Design-complete, awaiting external acceptance** | The layout, the A5/A4 profiles and the switchable credit block are implemented and tested against the workbook structure. A printed sample on the shop's own printer, both logos (**T3-12**), and signed owner acceptance remain. No implementation can clear this. |
| **M28** Go-live | **Blocked** | Named external gates in §5. Not a coding task. |

## 3. Phase 3 task register (T3-01 ... T3-12)

| Task | Status | Where |
|---|---|---|
| **T3-01** Invoice lookup by idempotency key | **Implemented & verified** | `domain/sales/reader.py` `list_invoices(idempotency_key=...)` -- an exact lookup, so the `§5.4.5` case-12 arbitration is exact rather than heuristic. Restricted at the route and never in `print_payload`. |
| **T3-02** `businesses.timezone` | **Implemented & verified** | Migration `0002`; `domain/shared/clock.py` evaluates the financial-year boundary in business-local time. |
| **T3-03** `invoices.money_policy_version` | **Implemented & verified** | Migration `0011`; stamped at finalisation and returned on the invoice resource, so any figure can be explained by the policy in force when it was issued. |
| **T3-04** Reconciliation asserts `max(sequence)+1 == next_number` | **Implemented & verified** | `domain/inventory/service.py` `reconcile()`; exercised by `tests/integration/test_billing_workflows.py`. |
| **T3-05** Remove superseded credit-details references | **Implemented & verified** | The client structural suite asserts no `CreditDetailsForm`/`credit-details` is reinstated, and a backend structural test asserts the OpenAPI document contains no second finalisation path. `Credit Bill` finalises in one request. |
| **T3-06** Remove the customer-visible invoice number end to end | **Implemented & verified** | The payload has no such field (an `assert` in `PrintPayloadService.as_dict` plus a test); the client type has no field for one; the rendered bill is asserted to contain no number and no substitute, in both the component suite and the Playwright run. |
| **T3-07** Credit confirmation and payment history without a number | **Implemented & verified** | The confirmation card renders "Stock has been updated.", the outstanding figure and the due date, and no number. |
| **T3-08** No-decimal display without corrupting precision | **Implemented & verified** (both sides) | Backend `format_for_display` + property test; client `formatForPrint`, which **throws** rather than rounding -- asserted. |
| **T3-09** The four primary actions | **Implemented & verified** | `features/billing/BillingCounter.tsx`. The labels, the shortcuts, the behaviour, and a test asserting **no fifth** finalisation-like action exists. |
| **T3-10** Printed bill reconciled to the workbook | **Implemented & verified** as a layout; **M27 gate** for the physical sample | The five workbook columns, the 25-row capacity, blank fillers, the owner's footer line, the grand total as the last and heaviest row. |
| **T3-11** Printed date order | **Implemented & verified** | `print.date_format` is a single configurable source, default `MM-DD-YYYY` from workbook cell E7. The renderer reads it from the payload and is tested for all three orders with a day that cannot be confused with a month. |
| **T3-12** Embed the two workbook logos | **Not completed** | Requires the image assets from the owner's workbook. The workbook is **not** modified and **not** committed (gitignored in both repos). Belongs with the M27 physical sample. |

## 4. Decisions taken during implementation

Recorded here because each departs from, or sharpens, a Phase 2 document. None changes
an owner decision.

| # | Finding | Resolution |
|---|---|---|
| **I-1** | `RoundingMode.INVOICE_HALF_UP` ("keep line totals exact, round only the invoice") **cannot satisfy the data model.** A property test produced `0.001 × 1 = 0.0000001`, which `NUMERIC(14,2)` cannot store; rounding it for storage breaks NFR-DATA-002b, and not rounding it breaks the column. | Mode removed. Any rounding mode must round **at the line**. Reason recorded in the `RoundingMode` docstring; `_assert_storable` now proves every amount is representable at the stored scale. The remaining modes still cover the precision/method/stage/whole-rupee space OD-7 would select from. |
| **I-2** | `ck_invoices__total` and NFR-DATA-002b only agree while tax is zero. | `subtotal` is implemented as `Σ` post-discount pre-tax extensions — the only reading under which the `CHECK` is arithmetically sound. The two coincide exactly in v1 because `estimate_only` makes every `tax_amount` zero. **Enabling GST requires restating NFR-DATA-002b in terms of `taxable_amount`**; recorded, not silently resolved. |
| **I-3** | Failed login attempts and refresh-family revocations were written in the request transaction, which rolls back with the error they describe. Lockout could never trigger and a copied cookie stayed usable. | Both now commit on their own connection. A narrow, documented exception to "one request, one transaction": security evidence must survive the failure it records, and neither path touches money, stock or an invoice. |
| **I-4** | `settings_dep()` read `get_settings()` rather than the settings the app was created with, so an explicitly configured app authenticated against a different pepper. | Reads `request.app.state.settings`, falling back to the process cache only when no app is attached. |
| **I-5** | Migration numbering diverges from `DATA-MODEL.md §6.1` (the seed is `0005`, not `0016`). | Migrations are a dependency chain, not a fixed numbering. `§6.1` records intended **order**, not mandatory revision ids. Documented in the migration's own docstring. |
| **I-6** | `prettier --write .` reformatted the authoritative Phase 2 markdown. | Reverted; `*.md` and `docs/` added to `.prettierignore`. Specifications are records, not source to reflow. |
| **I-7** | The client structural rules were tripping on the comments explaining them. | They now scan code with comments stripped, so the rule means "no such string can be rendered" rather than "nobody may document the rule". |
| **I-8** | `POST /sales:finalize` and `GET /invoices/{id}` described the same customer with different keys (`name` vs `name_snapshot`), and only the detail response carried `returned_quantity`. A client would have broken moving between the confirmation card and the Daily Bills row. | One serialiser, `api/v1/serialize.py` `invoice_dict`, now builds both. `InvoiceReader.record_of` reads the row without the `reports.sales.read` check, because requiring it on the finalisation path would mean a cashier who may bill but may not read reports gets a `403` **after** the sale committed. Found by `tests/api/test_response_contract.py`. |
| **I-9** | Seven routes answered `422 VALIDATION_ERROR` to a caller who should have seen `403 PERMISSION_DENIED`. FastAPI solves dependencies in **parameter order**, and `idempotency_key` was declared before the permission-guarded `ctx`, so the permission check was never reached. | Parameters reordered on all seven, and `tests/structural/test_route_permissions.py` now asserts the ordering for every route that takes a key -- because parameter order is invisible on review. |
| **I-10** | `normalize_phone` kept every digit on the client and the **last ten** on the server, so `+91-9876543210` and `9876543210` hashed differently. The duplicate detector keys on that value, so the same customer would have been filed twice -- once from a bill typed offline and once from the same bill typed online. | The client now implements the server's rule. Both suites run the **shared fixture list** of real part-number and phone spellings, which is what surfaced it. |
| **I-11** | Five child tables (`draft_bill_items`, `purchase_items`, `sales_return_items`, `stock_count_items`, `import_rows`) had no `business_id`, deriving tenancy from a parent row, while `invoice_items` and the allocation tables carried one. | Migration `0017` adds it, backfilled from the parent then set `NOT NULL`. One forgotten join is otherwise a cross-tenant read (NFR-DATA-010). |
| **I-12** | The frontend's `src/lib/money.ts` 100% coverage threshold was **unsatisfiable**: the v8 provider attributes an unreachable artifact to line 1 of every module whose source starts with a comment. A threshold that can never pass is a threshold nobody believes. | Switched to the istanbul provider, which instruments the source. `money.ts` now measures a genuine **100%** on statements, branches, functions and lines. |
| **I-13** | Two structural rules were tripping on the code that satisfies them: a type-only `import type Decimal from 'decimal.js-light'` in the draft store, and a comment in `api/session.ts` explaining that the token is never put in `localStorage`. | `lib/money.ts` now re-exports the `Decimal` type, so **no other file names the decimal package at all**; and the storage rule reads comment-stripped source, for the reason that file already documents. Neither rule was weakened -- the SQL-interpolation rule was *narrowed* the same way and given its own test proving it still rejects a runtime value. |


---

## 5. External gates — unchanged by any of this work

No implementation can clear these. They are listed so nothing below is mistaken for
progress on them.

| Gate | What is still required |
|---|---|
| **OD-25 / R-36** numbering and tax legality | Qualified tax/accounting advice before any document is represented as a compliant tax invoice. **No legal conclusion is made anywhere in this codebase.** |
| **OD-7 / OD-23** money policy | The implemented policy is the documented **engineering default**, not an accountant's approval. A change needs a written replacement policy before M4 exit. |
| **OD-4** business identity | Owner-supplied legal name, address and phone. `businesses.legal_name` is deliberately nullable and is **never** defaulted or invented. |
| **OD-6 / M27** printer and paper | A real printer, real paper, a 25-row sample, cash and credit examples, both logos, no clipping, and signed owner acceptance. The A5 profile is a **proposal**. |
| **OD-22** credit block placement | Sample comparison and owner acceptance. Switchable off via `print.show_credit_block` until then. |
| **OD-26 / M27** date order | `MM-DD-YYYY` matches workbook cell E7, but owner/staff interpretation must be confirmed on a physical sample. |
| **OD-24** document label | `ESTIMATE` retained; owner/accountant terminology confirmation remains a go-live gate. |
| **OD-8** offline expectations | Staff training that offline **cannot** finalise a sale, cash or credit. |
| **OD-19 / M26** hosting and alerts | Named alert recipient, budget, escalation expectation, runbook. |
| **M28** go-live | The full `DEPLOYMENT.md §11.2` checklist: opening stock physically verified, catalog spot-checked by the owner, seeded credentials rotated, full-disk encryption on counter machines, printed sample signed, staff trained, break-glass stored outside the system. |

---

## 6. Verified evidence

Commands run, with observed results, at the time of writing. Nothing is listed here that
was not executed.

### Backend

```
alembic downgrade base && alembic upgrade head   -> clean, head 0017 (17 migrations)
pytest                                            -> 361 passed
ruff check .                                       -> All checks passed
ruff format --check .                              -> 142 files already formatted
mypy                                               -> Success: no issues found in 100 source files
lint-imports                                       -> Contracts: 3 kept, 0 broken
```

Suites that make up those 361:

| Suite | Count | What it proves |
|---|---|---|
| `tests/integration/test_billing_workflows.py` | 71 | End-to-end against real PostgreSQL 16: stock deducted **exactly once**, idempotent replay with `Idempotent-Replay: true`, `IDEMPOTENCY_KEY_REUSE` on a changed payload, a rolled-back attempt leaving the key reusable, credit requiring a persisted customer, payments moving no stock, `amount_paid` equal to the allocation sum after a reversal, no invoice number in the print payload, cancellation reversing stock exactly once while retaining the reference, returns crediting the snapshot price, damaged returns becoming scrap, daily-sales and credit-outstanding reconciling, CSV injection escaped, the sync registry refusing a financial operation, the audit trail complete and secret-free |
| `tests/concurrency/test_parallel_billing.py` | 8 | **Real threads, real connections, `threading.Barrier`.** 20 parallel finalisations on one series -> 20 invoices, no gaps, no duplicates; a failed sale consumes no reference; two counters racing for the last unit -> one sale and one `INSUFFICIENT_STOCK`; ten sales against stock of three; concurrent same-key submits -> exactly one invoice; a **50-parallel multi-product deadlock probe** -> zero deadlocks; `balance = sum(movements)` under contention; concurrent payments cannot over-allocate |
| `tests/api/test_response_contract.py` | 17 | Every published response shape validated against a **real** response with `extra="forbid"`; `cost_price` **absent** (not null) without permission; `totals.tax` null; no invoice number |
| `tests/invariants/`, `tests/structural/`, `tests/property/`, `tests/migrations/`, `tests/api/`, `tests/unit/` | 265 | No float column anywhere; every money column `NUMERIC`; every business-scoped table `business_id NOT NULL`; database enums matching the code exactly; the append-only grant; `api -> domain -> infra` with no reverse imports; no money arithmetic in `api/`; no `float()` in `domain/`; no runtime value interpolated into `text()`; every route declaring a permission; authorisation decided **before** request validation; no settable `payment_status`/`amount_paid` in any request body; no second finalisation path; the shared normalisation fixture list |

### Frontend

```
tsc -b (3 projects)                   -> clean
eslint .                               -> clean
prettier --check src tests configs    -> All matched files use Prettier code style
vitest run                             -> 224 passed (10 files)
playwright test                        -> 6 passed (chromium, against the built app)
vite build                             -> built in ~2s; initial chunk 410.95 kB raw / 130.88 kB gzip
vitest run --coverage                  -> src/lib/money.ts 100% / src/lib/normalize.ts 100%
```

| Suite | Count | What it proves |
|---|---|---|
| `src/api/client.test.ts` | 18 | `Idempotency-Key` **stable** across retries while `X-Request-Id` is **fresh** each attempt; a keyless mutating request never retried; twenty concurrent 401s causing **one** refresh; `DRAFT_ALREADY_FINALIZED` never retried even when the server marks it retryable |
| `src/features/billing/finalize.test.ts` | 19 | Offline creates **nothing** -- no key, no pending row, no request -- for credit as well as cash; the key is on disk **during** the request; an unknown outcome keeps it; a definitive refusal releases it; `retryUnconfirmed` never mints a key |
| `src/db/persistence.test.ts` | 23 | Against real IndexedDB: a draft survives a reopen; the limit refuses rather than discarding; an unconfirmed bill cannot be cleared; one intent one key, and a reordered line list is the same intent; the outbox coalesces, blocks a conflicted entity's later operations, and never drops a rejection |
| `src/features/sync/scheduler.test.ts` | 17 | No financial operation can be queued; `accepted`/`replayed` adopt the server's version; `conflicted` marks the draft and keeps the operation; `rejected` is surfaced; a network failure requeues; the catalog cache has nowhere to put a cost price and clears on `reset_required`; the scheduler marks finalisations stale rather than resending them |
| `src/features/print/InvoicePrintLayout.test.tsx` | 22 | No invoice number or substitute; no tax row when `tax` is null; 25 rows with **blank** fillers; no placeholder for an unsupplied business detail; the credit block only for a credit bill; `ESTIMATE` for both; all three date orders; a fractional amount **throws** rather than rounding |
| `src/api/errors.test.ts` | 28 | All **53** codes present and described actionably with no status code or jargon; business conflicts inline; nothing already-done retryable |
| `src/features/billing/BillingCounter.test.tsx` | 17 | Exactly four actions and **no fifth**; both finalisations disabled offline; the total labelled a preview; the limit message saying nothing will be closed; **axe: zero violations** (wcag2a + wcag2aa); every input labelled |
| `src/lib/normalize.test.ts` | 18 | The **shared fixture list**, matching `bmbackend/tests/unit/test_normalize.py` |
| `tests/e2e/offline-and-keyboard.spec.ts` | 6 | Against the **built** app in Chromium with a genuine `setOffline(true)`: both finalisations refused offline, a bill typed offline surviving a reload, `Save a Draft` queueing offline, a whole bill completed **keyboard-only**, a shortfall rendered inline with per-line detail and no retry affordance, and the printed bill in `#print-root` carrying no invoice number |
| `src/lib/money.test.ts`, `tests/structural/rules.test.ts` | 56 | The money engine at 100%; the structural rules |

### Not run -- and therefore not claimed

| What | Why |
|---|---|
| Load tests | No staging environment and no agreed throughput target. NFR budgets are stated in Phase 2 but have not been measured under load. |
| Backup restore drill | Requires a real backup target and a restore window (M26, OD-19). |
| Print validation on the shop's printer | **M27, an external gate.** The layout is implemented and tested; a physical sample on real paper with both logos and signed owner acceptance is a separate authorisation. |
| Multi-tab owner-election (FE-E-2) under real contention | The Web Lock path is implemented and the single-tab path is tested; two real browser contexts racing for the lock is not. |
| Any browser other than Chromium | Deliberate: the counter machines run Chrome or Edge, and print fidelity is only verified there. |

## 7. Exact next action

Phase 3's P0 scope -- a working, verified bill -- is complete on both sides. What remains
is P1 scope and the external gates.

**In implementation order:**

1. **M23 imports** (backend parser + apply path, then the review table in the client).
   The schema exists; nothing else does. No P0 flow depends on it.
2. **M25 frontend P1 screens** -- Stock, Purchases, Customers, Imports, and the reports
   beyond Daily and Credit Bills. Every API they need is implemented and verified.
3. **M26 completion** -- load tests against a staging environment, the restore drill, and
   the named alert recipient (OD-19). All three need something outside this repository.
4. **M27** is not an implementation task. It needs a printer, paper, the two workbook
   logos (**T3-12**) and a signature.

**Before any of that reaches a counter**, §5's gates still stand. In particular nothing
in this codebase makes, or may be read as making, a legal or tax conclusion (OD-25/R-36).

## 8. Related documents

[`PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md`](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) ·
[`PHASE-2-IMPLEMENTATION-PLAN.md`](PHASE-2-IMPLEMENTATION-PLAN.md) ·
[`PHASE-2-IMPLEMENTATION-BLUEPRINT.md`](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) ·
[`OWNER-DECISIONS.md`](OWNER-DECISIONS.md) ·
[`PHASE-2-TRACEABILITY-MATRIX.md`](PHASE-2-TRACEABILITY-MATRIX.md) ·
[`DEPLOYMENT.md`](DEPLOYMENT.md)
