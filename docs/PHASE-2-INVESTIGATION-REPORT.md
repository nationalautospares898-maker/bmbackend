# Phase 2 — Technical Investigation Report

| Field | Value |
|---|---|
| Status | **Living Phase 2 record** |
| Purpose | Feature inventory, architecture investigation, alternatives and trade-offs, failure-mode analysis, security review, database and API findings, cross-repository findings |
| Inputs | All 28 Phase 1 documents; [`PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md`](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md) |
| Implementation performed | **None.** No code, migration, dependency or test was created or run |
| Companion | Decisions reached here are recorded in [`PHASE-2-DECISION-REGISTER.md`](PHASE-2-DECISION-REGISTER.md); risks in [`PHASE-2-RISK-REGISTER.md`](PHASE-2-RISK-REGISTER.md) |

> **Reading rule.** Where this report *endorses* a Phase 1 decision, Phase 3 should treat the Phase 1
> document as the spec and this report as the rationale review. Where it *proposes* something new, it
> carries a `P2-D-nn` identifier and is **not** approved until the decision register says so.

---

## 1. What is actually being built — verified baseline

| Fact | Evidence |
|---|---|
| Both repositories contain **documentation only** | 0 non-Markdown files, 0 `.git` directories |
| No schema exists | `DATA-MODEL.md` header: *"no migration has been written or run"* |
| No endpoint exists | `API-SPECIFICATION.md` header: *"no endpoint is implemented"* |
| No test has run | Both testing strategies state this explicitly |
| Nothing is deployed | `DEPLOYMENT.md` header |

Phase 3 is therefore a **greenfield build against a complete specification**, not a change to a
running system. This is the single most important scheduling fact: there is no migration-of-live-data
risk in Phase 3 (that risk appears only at go-live data loading and in later releases), and no
backwards-compatibility constraint on the first schema.

---

## 2. Feature inventory

Derived from both PRDs. **Priority is the PRD's own** (P0 = go-live, P1 = within 3 months, P2 =
later). "Gate" names the owner decision that blocks it, if any.

### 2.1 Backend features

| # | Module | Requirements | P | Owns (tables) | Gate |
|---|---|---|---|---|---|
| B-01 | Auth, users, devices, sessions | FR-AUTH-001…007 | P0 (005 = P1) | `users`, `memberships`, `roles`, `permissions`, `role_permissions`, `devices`, `refresh_tokens`, `login_attempts` | — |
| B-02 | Business / branch / counter / settings | FR-ADMIN-001…005 | P0 (005 = P1) | `businesses`, `branches`, `counters`, `settings` | OD-4 for *content* only |
| B-03 | Audit log | FR-AUDIT-001…005 | P0 (005 = P1) | `audit_logs` | — |
| B-04 | Catalog + search | FR-CAT-001…010 | P0 (007, 009 = P1; 010 = P2) | `products`, `product_aliases`, `brands`, `categories`, `units`, `product_prices` | — |
| B-05 | Inventory ledger + balances | FR-INV-001…010 | P0 (008, 009 = P1; 010 = P2) | `inventory_balances`, `stock_movements`, `stock_count_sessions` | — |
| B-06 | Drafts | FR-DRAFT-001…011 | P0 (009, 010 = P1) | `draft_bills`, `draft_bill_items` | — |
| B-07 | **Invoice numbering** | FR-SALE-004, NFR-DATA-005 | P0 | `invoice_series` | **OD-3** (policy statement — see §5.1) |
| B-08 | **Money engine** | FR-SALE-003, NFR-DATA-002/009 | P0 | — (`domain/shared/money.py`) | **OD-7** (policy value — see §5.2) |
| B-09 | **Sale finalisation** | FR-SALE-001…014, FR-CREDIT-008/009/012 | P0 (013 = P1) | `invoices`, `invoice_items` | Needs B-07, B-08 |
| B-10 | Invoice read + cancellation + print payload | FR-SALE-010/011, FR-PRINT-001…005 | P0 (004 = P1; 005 = P2) | `print_log` | — |
| B-11 | Customers | FR-CUST-001…006, 010…012 | P0 (005 = P1) | `customers`, `customer_phones`, `customer_vehicles` | — |
| B-12 | Credit + payments + allocations | FR-CREDIT-001…012, FR-PAY-001…007 | P0 (006, 007 = P1) | `payments`, `payment_allocations`, `credit_notes`, `credit_note_allocations`, `customer_credit_balances` | — |
| B-13 | Suppliers + purchases | FR-PUR-001…007 | P0 (005, 006 = P1; 007 = P2) | `suppliers`, `purchases`, `purchase_items` | — |
| B-14 | Returns + credit notes | FR-RET-001…005 | **P1** | `sales_returns`, `sales_return_items` | — |
| B-15 | Imports | FR-IMP-001…010 | **P1** (010 = P2) | `import_jobs`, `import_rows` | — |
| B-16 | Reports + exports | FR-RPT-001…008 | P0 (004…007 = P1) | — (read-only) | — |
| B-17 | Sync (drafts, customers, catalog delta) | FR-OFFLINE-001…013 | P0 (013 = P2) | `sync_operations`, `idempotency_keys` | — |
| B-18 | Tax configuration + gate | FR-TAX-001…004 | P0 (002 = P1) | `tax_groups`, `tax_rates`, `tax_config_versions` | OD-1/OD-2 for *enabling*, not for building |
| B-19 | Jobs, outbox, notifications | ARCHITECTURE §7 | P0 (notifications = P1) | `jobs`, `outbox_events`, `notifications` | — |
| B-20 | Idempotency infrastructure | FR-SALE-002, FR-PAY-004, FR-IMP-008 | P0 | `idempotency_keys` | — |

### 2.2 Frontend features

| # | Module | Requirements | P | Gate |
|---|---|---|---|---|
| F-01 | Shell: routing, API client, error envelope, permission gating | FR-NAV-001…007, FR-UI-PERM-001…004, FR-AUTH-010…015 | P0 (006, 014 = P1) | — |
| F-02 | Product search box | FR-CAT-003…015 | P0 (007, 015 = P1) | — |
| F-03 | Bill tabs + item table + totals | FR-BILL-001…013, FR-ITEM-001…009 | P0 (012 = P1; 008 = P1) | OD-7 for the preview's rounding |
| F-04 | Draft persistence (Dexie) | FR-DRAFT-008, FR-OFFLINE-003/011 | P0 | — |
| F-05 | Cash finalisation + idempotency + error states | FR-SALE-020…028 | P0 | — |
| F-06 | Credit finalisation + inline customer | FR-CREDIT-020…031 | P0 | — |
| F-07 | **Print layout** | FR-PRINT-010…021 | P0 (016 = P1) | **OD-5 (bottom), OD-22 (placement)**, OD-6 (paper) |
| F-08 | Counter slip | FR-OFFLINE-015 | P0 | — |
| F-09 | Daily Bills | FR-RPT-020, 022, 025…027 | P0 (026, 027 = P1) | — |
| F-10 | Credit Bills (customer-wise) + payment recording | FR-RPT-021, 023, 024, 028…033 | P0 | — |
| F-11 | Offline layer: outbox, scheduler, sync panel, conflicts | FR-OFFLINE-001…014 | P0 | **CF-3** must be fixed first |
| F-12 | Stock, purchases, customers, imports, reports, settings screens | FR-MOD-001…009 | **P1** | — |
| F-13 | Returns screen | FR-MOD-010 | **P2** | — |

### 2.3 Explicitly out of scope — do not add

E-invoicing/IRP, e-way bills, GST return filing, accounting ledger/journals, payroll, supplier
payables and ageing, barcode label printing, customer-facing app/portal, SMS/WhatsApp, multi-currency,
branch transfers, serial/batch/expiry, warranty, job cards, loyalty, **offline sale finalisation**,
phone-optimised billing, dark mode, client-side report computation, multi-language UI, custom print
designer. (`PRD.md §12`, `../../bmfrontend/docs/PRD.md §11`.)

**Finding.** The scope boundary is unusually well drawn and needs no change. Two items deserve a note
for Phase 3 sequencing: **branch transfers** (FR-INV-010) are out of MVP yet the schema is
branch-aware, so E-13's documented workaround (two reasoned adjustments) must be *tested*, not just
documented; and **supplier payables** being out of scope means `purchases` carries totals that
nothing reconciles against a payable — acceptable, but it should not be mistaken for a gap.

---

## 3. Architecture investigation

### 3.1 The shape — endorsed

`ARCHITECTURE.md §1` specifies a **modular monolith**: one FastAPI image, one worker image (same
code), one PostgreSQL 16 database, one object store. Explicitly excluded: microservices, Kubernetes,
Kafka, Redis, service mesh, CQRS, event sourcing, GraphQL.

**Investigated alternatives.**

| Option | Correctness | Integrity | Ops complexity | Verdict |
|---|---|---|---|---|
| **Modular monolith** (ADR-001) | The central transaction (invoice + items + movements + payment + number + audit) is **one ACID transaction** | Strongest available: DB constraints + privileges + row locks | One deployable, one backup | **Endorsed** |
| Microservices per domain | Finalisation becomes a distributed saga with compensating transactions; "no overselling" degrades from a guarantee to a probability | Weaker — cross-service invariants need reconciliation | Service discovery, tracing, N databases | **Rejected** |
| Unstructured monolith | Same transaction guarantees | Same | Lowest | **Rejected** — boundaries erode without the CI import linter |

**Load reality check.** Design target is 5 concurrent users, ~500 invoices/day, ≤100k SKUs. Three-year
projection: ~5×10⁵ invoices, ~3×10⁶ invoice items, ~4×10⁶ movements. That is roughly three orders of
magnitude below a single modest Postgres instance's capacity. **No infrastructure beyond Postgres is
justified, and the escalation triggers are already written down** (`DEPLOYMENT.md §9`) — which is
what prevents this from being re-litigated ad hoc.

**One addition proposed.** The layering rule (`api → domain → infra`, no reverse imports) is
correctness-relevant and is currently enforced by "an import-linter configuration in CI". Phase 3
must make that configuration **a merge blocker from milestone M1**, before there is code to untangle.
Recorded as `P2-D-05`.

### 3.2 Transaction ownership — endorsed, with one clarification needed

`ARCHITECTURE.md §5` fixes: one request/one transaction; the use-case service owns the transaction
and participants never commit; **no HTTP/file/job side effects inside a write transaction** (outbox
instead); READ COMMITTED + explicit `FOR UPDATE`; `statement_timeout` / `lock_timeout` per session.

**Lock order is a correctness requirement, not a style rule:**

```
inventory_balances (ascending product_id)  →  invoice_series  →  customers  →  invoices
```

**Investigated alternative: SERIALIZABLE with retry** (ADR-008 option 2). Rejected, and the rationale
holds up: a retry loop on the endpoint that charges a customer interacts badly with idempotency —
specifically, a serialisation failure after the idempotency row is claimed but before commit would
either abandon a consumed key or require the retry to re-enter key claiming, and the distinction
between "retry internally" and "the client retried" becomes hard to reason about. Explicit locks give
the second counter a deterministic `INSUFFICIENT_STOCK` instead of a retry storm, and are far easier
to test deliberately.

**Clarification Phase 3 needs (`P2-D-06`).** The finalisation pseudocode claims the idempotency key
*first* (step 2), then takes locks (steps 3–6). Under concurrent same-key requests this is correct
only if the key insert is the serialisation point — which it is, via
`uq_idempotency__scope (business_id, endpoint, key)`. But the documented behaviour matrix needs
`IDEMPOTENT_REQUEST_IN_PROGRESS` to be returned **without** holding inventory locks, otherwise a
duplicate submit blocks a real sale on the same product for `lock_timeout`. The order
*claim-key → lock-stock* already achieves this; Phase 3 must not "optimise" it by locking first.
This should be stated in `ARCHITECTURE.md §5.3` as an explicit ordering constraint.

### 3.3 Idempotency — the mechanism is sound; one gap

The design (`ARCHITECTURE.md §5.3`) is correct in the two ways that matter most:

1. **The key row is inserted *inside* the business transaction**, so a key is consumed only if the
   work commits. A rolled-back attempt leaves the key reusable — which is what makes client retry
   safe.
2. **`request_fingerprint`** (SHA-256 of the canonicalised body) distinguishes "same intent retried"
   from "different intent, same key" → `409 IDEMPOTENCY_KEY_REUSE`, never silent acceptance.

| Situation | Response | Assessment |
|---|---|---|
| New key | Process, store response | Correct |
| Same key, same fingerprint, completed | Stored response + `Idempotent-Replay: true` | Correct — this is E-2 |
| Same key, same fingerprint, in progress | `409 IDEMPOTENT_REQUEST_IN_PROGRESS` + `retry_after_ms` | Correct |
| Same key, different fingerprint | `409 IDEMPOTENCY_KEY_REUSE` | Correct |

**Gap found — fingerprint canonicalisation is unspecified.** "SHA-256 of the canonicalised body" does
not say *how* the body is canonicalised. If the client reserialises with different key ordering,
different decimal formatting (`"2500.00"` vs `"2500.0"`), or includes/omits an explicit `null`, a
genuine retry of the same intent would be mistaken for key reuse and the sale would fail with a
confusing client-bug error at the counter. This is a realistic Phase 3 defect. **`P2-D-07`** proposes
a normative canonicalisation rule (recursive key sort, `null` fields dropped, money/quantity strings
compared as `Decimal` not as text) plus a cross-repository fixture list, in the same spirit as the
existing shared normalisation fixtures for part numbers.

**Retention interaction checked.** Keys are pruned after 7 days (`idempotency.retention_days`); the
client's pending-finalisation auto-retry stops at 60 minutes
(`sync.finalization_stale_minutes`). 7 days ≫ 60 minutes, so pruning can never strand a retry. Sound.

### 3.4 Offline architecture — the strongest part of the design

`OFFLINE-SYNCHRONIZATION.md §3.1`'s three states are the key insight and this report endorses them
without change:

| State | Server effect so far | Correct handling |
|---|---|---|
| 1 — Safe offline operation | **None** | Queue in `outbox`, replay freely, idempotent on `op_id` |
| 2 — Lost response (sent **online**) | **Possibly complete** | Retry the **same** `Idempotency-Key` |
| 3 — Unconfirmed outcome | **Unknown** | Hold, loud UI, stop auto-retry at the threshold, human checks Daily Bills |

Three structural properties make this hard to break by accident, and all three should be preserved as
*structural* rather than *documented* guarantees:

1. **Two separate queues** (ADR-010), not one generic queue. A unified queue would invite a future
   change letting a sale accumulate offline "like any other operation" — the exact failure this
   design prevents.
2. **The sync operation registry has exactly three types** (`draft.upsert`, `draft.delete`,
   `customer.upsert`) and a structural test asserts no type finalises a sale, records a payment or
   moves stock (AC-OFF-8).
3. **`pending_finalizations` rows are written immediately before the first online attempt** and can
   never be created offline.

**Finding — one invariant is weaker than it reads.** Invariant 4 says *"If the user has edited the
bill, the old attempt must first be resolved — a new key for an edited bill is a different sale, and
the UI must not allow that while one is unconfirmed."* But `../../bmfrontend/docs/API-INTEGRATION.md
§5` shows `onDraftEdited()` **deleting** the `pending_finalizations` row. If the draft is edited while
an attempt is genuinely in flight or unresolved, deleting the row discards the only record of the key
that may already have created an invoice. The two documents describe compatible intentions but
incompatible mechanics. **`P2-D-08`**: the edit path must be *blocked* while a pending finalisation
exists (per invariant 4), and `onDraftEdited` must only clear the row once the attempt has
**resolved** — never as a side effect of typing.

### 3.5 Frontend architecture — endorsed

State boundaries (`../../bmfrontend/docs/ARCHITECTURE.md §4`) are the right ones and the rules are
stated where they are most likely to be broken (`CLAUDE.md §4`):

- Drafts in **Dexie + Zustand**, never the query cache (ADR-025). Correct: a query cache is keyed to
  responses and cleared freely; a draft must survive offline.
- Server data **never** duplicated into Zustand. One cache, one invalidation story.
- Access token **in memory only**.
- Every draft mutation is a **Dexie transaction first**, then the store — bounding the loss window to
  the ~300 ms text debounce.
- Totals are **derived selectors, never stored**, so they cannot go stale.
- Invoices and print payloads are immutable → `staleTime: Infinity`, so a reprint can never show
  different figures from the original. This is a genuinely good detail.

**Service worker scope** (ADR-031) is correct and its reasoning is the important part: caching API
`GET`s would show stale stock **with no staleness label**, defeating FR-OFFLINE-009, because a cached
HTTP response has no `as_of` while Dexie data does. And service-worker mutation queueing (Workbox
Background Sync) would replay a finalisation without the app's idempotency and ownership rules —
exactly what ADR-010/011 exist to prevent.

**Cross-tab coordination** (Web Lock owner-tab election, FE-E-2) is necessary and often omitted in
designs like this; keep it.

---

## 4. Alternatives review of Phase 1 technical decisions

Each `proposed` ADR was re-examined against the project's actual constraints. **All are endorsed
unchanged.** Only the notable ones are discussed.

| ADR | Decision | Verdict | Note |
|---|---|---|---|
| 002 | Python 3.11 + FastAPI | **Endorsed** | The decisive factors are real, not stylistic: first-class `Decimal` (money correctness is the dominant risk), far better spreadsheet/PDF parsing for imports (`openpyxl`, `pdfplumber`), and OpenAPI output that lets the frontend *generate* types (ADR-029). A single-language TypeScript stack would trade both away |
| 003 | **Sync** SQLAlchemy sessions | **Endorsed** | Lock and transaction semantics are materially easier to read, review and test synchronously. At 500 invoices/day the bottleneck is nowhere near connection concurrency. Async would add a class of subtle bugs (session sharing, accidental concurrency inside a transaction) for throughput never needed |
| 004 | Postgres job queue via `FOR UPDATE SKIP LOCKED` | **Endorsed** | Gains something Redis cannot: **enqueueing is transactional with business data**, which is exactly what the outbox needs. An import job cannot exist for an upload that rolled back |
| 005 | `pg_trgm` + prefix indexes, no search engine | **Endorsed** | 100k rows is small. Avoids a second datastore *and* any window where search disagrees with the database. Escalation trigger is measured, not intuited |
| 008 | READ COMMITTED + ordered row locks | **Endorsed** | See §3.2 |
| 013 | Money/quantity as **JSON strings** | **Endorsed** | Strings survive the round trip exactly and are readable in logs and the network tab when debugging a disputed bill. Integer minor units are also exact but error-prone across mixed scales (amounts 2 dp, unit prices 4 dp, quantities 3 dp) |
| 015 | Immutable financial records, enforced by **DB privileges** | **Endorsed — this is the keystone control** | Makes T-1 (a cashier editing away a sale) *structurally impossible* rather than a matter of code discipline. Column-level grants enumerate the few legitimately mutable columns |
| 016 | Migrations as an explicit deploy step | **Endorsed** | Start-up migrations make every restart a potential schema change and a crash-loop a migration loop; two replicas starting together race on DDL |
| 017 | Real PostgreSQL in tests; SQLite prohibited | **Endorsed** | The design depends on `FOR UPDATE`, `NUMERIC` exactness, partial indexes, native enums, `pg_trgm` and column privileges. A green SQLite suite would be *actively misleading* about exactly what is most likely to be wrong |
| 018 | No RLS in v1 | **Endorsed, with the stated honesty** | The generated isolation matrix over every route (AC-8) catches a missing predicate in CI rather than in production. RLS becomes necessary if a second application or BI tool connects |
| 019 | Single API replica | **Endorsed** | And the prerequisite is correctly recorded: multiple replicas with an in-process limiter would silently multiply the effective rate limit — a correctness bug, not a config detail |
| 022 | Derived `payment_status` + same-transaction cache | **Endorsed** | Option 2 (pure derivation) is most correct but makes the partial index on `payment_status` impossible, which is what makes the outstanding query fast. The cache is authoritative-by-construction: written only in the allocation transaction, column-grant restricted, invariant-tested, and the nightly job **alerts** rather than repairing |
| 029 | Generated API types + CI drift check | **Endorsed** | Makes ADR-012 (`snake_case`) and ADR-013 (string money) painless because nobody types a field name. New error codes become compile errors — see CF-3 for why the registry must be complete |
| 031 | Service worker caches the shell only | **Endorsed** | See §3.5 |
| 033 | `Save / Credit Bill` finalises directly | **Owner-specified — not reopened** | Its option 3 ("finalise directly but *queue* for later confirmation") is rejected outright and the rationale is correct: a credit sale reduces stock and consumes a number exactly as a cash sale does |

---

## 5. The two gated engine decisions, investigated in depth

### 5.1 Invoice numbering (OD-3)

**Problem.** Allocate an invoice number per (business, branch, series, financial year) such that
committed invoices carry increasing numbers, concurrent finalisations never collide, and a failed
sale does not create a visible anomaly — without knowing yet whether **gapless** is a statutory
requirement for this business.

**Confirmed requirements and invariants.** Server-allocated and unique (OD-3 "Already confirmed");
format `NAS/{fy}/{seq:05d}` with `NAS` a placeholder pending OD-4; FY from
`businesses.financial_year_start_month` (default 4); allocation inside the finalisation transaction
(FR-SALE-004); `uq_invoices__series_sequence`, `uq_invoices__business_number`; cancelled invoices
**retain** their number and stay visible (FR-SALE-011).

**Three properties that must not be conflated:**

| Property | Statement | Satisfied by |
|---|---|---|
| **Unique** | No two invoices share a number within the scope | A unique constraint. Non-negotiable; already settled |
| **Sequential** | Numbers are issued in increasing order from a series | Both candidate designs. **OWNER-CONFIRMED** (OD-3) |
| **Gapless** | *Every* number in the range corresponds to a committed invoice | Only in-transaction allocation. **OPEN** (OD-3) |

**Alternatives.**

| Option | Mechanism | Gapless? | Concurrency cost | Failure behaviour |
|---|---|---|---|---|
| **(a) Locked counter row** | `SELECT next_number FROM invoice_series … FOR UPDATE` inside the sale transaction | **Yes** | Finalisations on one series serialise at one row for the duration of the transaction | Rollback consumes nothing |
| (b) `SEQUENCE` | `nextval()` | No — non-transactional, any rollback burns a number | None | Permanent gaps |
| (c) Per-counter series | Separate series per counter | Per series only; unused numbers in each are permanent gaps | None across counters | Gaps guaranteed |
| (d) Post-hoc renumbering | Renumber after the fact | — | — | **Rejected outright: falsifying issued records** |

**Recommendation: (a), unchanged from ADR-009.** The decisive point is the asymmetry identified in
the fidelity audit (CF-1): **(a) also satisfies a gap-tolerant requirement, while (b) and (c) cannot
satisfy a gapless one.** Building (a) is therefore never wasted work regardless of OD-3's answer.

**Cost analysis of (a)'s serialisation.** The lock is held from allocation to commit — i.e. for the
remaining inserts (invoice, items, movements, payment, audit, outbox). At a 10-line bill this is a
handful of indexed inserts; the finalisation p95 budget is 600 ms (NFR-PERF-002) and the design load
is a few sales per minute. Even at a pessimistic 200 ms held-lock duration, one series supports ~5
finalisations/second serially — four orders of magnitude above requirement. **However**, the lock is
acquired *after* `inventory_balances` per the fixed lock order, which is correct (it keeps the hot
series row held for the shortest possible time and prevents a deadlock against a sale holding stock
locks).

**Documented escalation** if contention ever appears: per-counter series via
`counters.invoice_series_id` — configuration, not a schema change. **But this is incompatible with
gaplessness**, so it must not be applied before OD-3 is answered. Phase 3 must not treat it as a free
performance knob.

**Interaction that must not be forgotten.** A gapless answer makes **offline finalisation permanently
impossible**, not merely deferred, because per-counter number blocks guarantee gaps
(`OFFLINE-SYNCHRONIZATION.md §11`). OD-3 and OD-8 must be answered together.

**Failure modes and recovery.**

| Failure | Behaviour | Recovery |
|---|---|---|
| Transaction rolls back after allocation | `next_number` reverts with the transaction; nothing consumed | None needed. Test: `test_rollback_consumes_no_number` asserts `invoice_series.next_number` unchanged |
| Two concurrent finalisations, same series | Second waits on the row lock, then takes the next number | Test: 20 parallel → `n…n+19`, no gaps, no duplicates |
| `lock_timeout` (3 s) exceeded | Fail fast with a retryable error; no partial sale | Client retries the **same** idempotency key |
| Database restart mid-transaction | Uncommitted transaction lost; no number consumed | None needed |
| Financial-year rollover | New `invoice_series` row for the new FY; first allocation creates it | **Needs a test** — see below |
| Cancellation | Number retained, `status='cancelled'`, still listed | Sequence integrity preserved by design |
| Two application instances | Row lock is database-side, so correctness is unaffected | — |

**Gap found.** No document specifies **who creates the `invoice_series` row for a new financial
year**, or what happens to the first sale after midnight on 1 April. Lazy creation inside the
finalisation transaction (a single `INSERT … ON CONFLICT DO NOTHING` then the `FOR UPDATE`) is the
natural answer and keeps the "no number without a sale" property, but it must be specified and tested
(frozen-clock test crossing the boundary). Recorded as **`P2-D-09`**.

**Acceptance criteria.** 20 parallel finalisations on one series → 20 invoices with contiguous
sequence numbers; rollback leaves `next_number` unchanged; FY boundary allocates from a new series;
cancellation leaves no hole; a gap-detection query over committed rows returns empty.

### 5.2 Money and rounding (OD-7)

**Problem.** Define precision, rounding method and rounding stage such that printed line totals add
up to the printed grand total, stored values reconcile exactly, and the client's preview agrees with
the server to the paisa — while the accounting policy itself remains the owner's/accountant's
decision.

**Already binding (OWNER-CONFIRMED principles, OD-7).** A documented policy must exist and must not
be implicit, emergent or per-module; **the backend is the authoritative calculator**; the frontend and
printed bill display backend values **without changing their meaning**.

**Already settled structurally.** `NUMERIC` in Postgres, `Decimal` in Python, **strings in JSON**
(ADR-013, NFR-DATA-009). Amounts `NUMERIC(14,2)`, unit prices `NUMERIC(14,4)`, quantities
`NUMERIC(14,3)`, percentages `NUMERIC(6,3)`. `invoices.rounding_adjustment` exists so arithmetic
always reconciles, and `ck_invoices__total` makes NFR-DATA-002 a **database guarantee**. No `float`
anywhere, enforced by lint in `domain/` and by a test inspecting `information_schema` for
`double precision`/`real` in money columns.

**The open questions and why they matter — worked examples.**

Take 3 × `33.333` at unit price `14.5550`, with a 2.5% line discount.

| Policy | Line computation | Line total | Invoice total (3 identical lines) |
|---|---|---|---|
| **(a) Round each line half-up to 2 dp, then sum** | `33.333 × 14.5550 = 485.1611…` → `−2.5%` = `473.0320…` → **`473.03`** | `473.03` | `1419.09` |
| **(b) Full precision per line, round only the total** | `473.0320…` kept | prints `473.03` | `1419.0961…` → **`1419.10`** |
| **(c) (a) + whole-rupee rounding of the grand total** | as (a) | `473.03` | `1419.09` → **`1419.00`**, `rounding_adjustment = −0.09` |

The difference between (a) and (b) is **₹0.01** on this bill — but under (b) the printed lines
(`473.03 × 3 = 1419.09`) **visibly do not sum to the printed total (`1419.10`)**. At a counter, a
customer who adds up the column and gets a different number will query it, and staff cannot explain
it. Under (a) the bill always adds up. Under (c) coins are avoided but the adjustment **must** be
visible on the bill or the total looks arbitrary — and the bottom of the bill is `[UNSEEN]` (OD-5),
so there is nowhere verified to print it.

| Rounding method on `.005` | `2.005` → |
|---|---|
| `ROUND_HALF_UP` | `2.01` |
| `ROUND_HALF_EVEN` (banker's) | `2.00` |
| `ROUND_DOWN` (truncate) | `2.00` |

Half-even reduces aggregate bias over many transactions; half-up matches how a hand-written bill and
most Indian retail practice work and is far easier to explain at a counter. Truncation systematically
favours the customer and is not recommended.

**Recommendation (TECHNICAL RECOMMENDATION, requires OD-7 approval to become policy).**
Amounts 2 dp; unit prices 4 dp (needed for per-ml/per-gram pricing, already in the schema);
quantities 3 dp; **`ROUND_HALF_UP` at line level, then sum**; **whole-rupee rounding off by default**;
no domain-specific exceptions. Rationale: option (a) is the only one where the printed bill adds up,
which is the property the shop's customers will actually check; it is already the documented default
(`sales.rounding_mode = line_half_up`, `sales.round_grand_total_to_rupee = false`); and
`rounding_adjustment` keeps whole-rupee rounding available later **without a schema change or a data
migration**.

**The important architectural finding — OD-7 need not block the money module.**
`GET /sync/bootstrap` already publishes the rounding policy to the client
(`API-SPECIFICATION.md §3.12`), and `../../bmfrontend/CLAUDE.md §5` rule 15 forbids the client from
hardcoding any business rule that bootstrap provides. So the policy is **already specified as
configuration, not as a constant.** If `domain/shared/money.py` takes `(precision, method, stage,
whole_rupee)` as an explicit policy object resolved from `settings`, then:

- the module, its property tests and the client's `lib/money.ts` can all be **built now**,
  parameterised by policy;
- what OD-7 blocks shrinks to **(i)** selecting the production policy values and **(ii)** freezing
  the *expected-value* assertions in the two money acceptance suites;
- a later change of policy affects **newly computed** values only — issued invoices keep their
  snapshots and are never recomputed (FR-SALE-005, ADR-015), so there is no migration on live
  financial data.

This is proposed as **`P2-D-03`**, and the corresponding owner question is narrowed accordingly in
[`PHASE-2-OWNER-DECISIONS-REQUIRED.md`](PHASE-2-OWNER-DECISIONS-REQUIRED.md). It does **not**
pre-empt OD-7: the policy *values* remain the accountant's decision.

**Where rounding must be applied — the full surface.** Phase 3 must apply one policy at every one of
these points, and a property test must assert the chain reconciles end to end:

line gross (`qty × unit_price`) → line discount → `line_total` → `taxable_amount` → `tax_amount` (when
`gst_enabled`) → `subtotal` (Σ line totals) → invoice discount → `tax_total` → `rounding_adjustment`
→ `total` → payment allocation amounts → `amount_paid` → outstanding → ageing buckets → report
aggregates → CSV export → printed display (OD-23 formatting, which **renders** and never changes a
value).

**Reconciliation requirement.** `NFR-DATA-002` (DB `CHECK`) covers `total` from its components;
`NFR-DATA-002b` covers `subtotal` from the line items — and **002b is currently an orphan**
(fidelity audit CF-9). Both are needed: without 002b a bill could satisfy the `CHECK` and still
disagree with its own lines.

---

## 6. Database investigation

### 6.1 Assessment

The schema in `DATA-MODEL.md` is complete, coherent and unusually constraint-forward. **It needs no
structural change for Phase 3.** The discipline *"if an invariant can be a constraint, make it one"*
is applied consistently:

| Invariant | Enforcement | Strength |
|---|---|---|
| NFR-DATA-002 `total` composition | **DB `CHECK`** `ck_invoices__total` | Strongest |
| NFR-DATA-008 returns ≤ sold | **DB `CHECK`** `ck_invoice_items__returned` | Strongest |
| NFR-DATA-005 unique numbering | **Unique constraint** + in-transaction allocation | Strong |
| NFR-DATA-006 immutability | **Column-level DB privileges** (`SECURITY.md §7`) | Strongest — structural, not disciplinary |
| NFR-DATA-010 `business_id` present | `NOT NULL` + a test enumerating tables | Strong |
| NFR-DATA-001 balance = ledger | Single-writer service + nightly reconciliation + invariant test | Service-level (correctly — it is a cross-row aggregate) |
| NFR-DATA-003/004 allocations | Same-transaction write + invariant test + reconciliation | Service-level (cannot be a single-row `CHECK`) |
| NFR-DATA-007 negative only via override | Service gate + reconciliation report | Service-level |

**Reconciliation alerts rather than repairs** (FR-INV-006). This is the right call and worth
restating: an auto-healing balance table would hide the bug that caused the drift.

### 6.2 Specific findings

| # | Finding | Severity | Action |
|---|---|---|---|
| D-1 | `invoice_items.returned_quantity` is the **only** mutable column on an otherwise append-only table, updated solely by the returns module under a row lock, with a matching column grant | — | Correct as designed. Phase 3 must verify the grant is *exactly* `UPDATE (returned_quantity)` |
| D-2 | `draft_bills` has `ck_draft_bills__finalized CHECK ((status='finalized') = (invoice_id IS NOT NULL))` **and** `uq_draft_bills__invoice UNIQUE (invoice_id) WHERE invoice_id IS NOT NULL` | — | Together these make double-finalisation of one draft impossible at the DB level. Excellent |
| D-3 | **No `CHECK` ties `is_credit` to `payment_status`** | Low | A cash sale is expected to commit fully paid, but nothing prevents `is_credit=false, payment_status='unpaid'`. API-SPEC requires `payment` for cash, so the service enforces it. Recommend an invariant test rather than a constraint (a cancelled cash invoice legitimately has `payment_status='cancelled'`) |
| D-4 | `settings` unique constraint uses `coalesce(branch_id, '00000000-…')` | — | Correct approach for nullable-column uniqueness in Postgres; must be a **unique index on the expression**, not a table constraint. Phase 3 migration detail |
| D-5 | `customer_credit_balances` is a derived cache marked "rebuildable, never authoritative" (P1) | — | Correct. Phase 3 must not let any read path treat it as truth; `GET /credit/customers` aggregates are server-computed from `invoices` + `payment_allocations` |
| D-6 | Section ordering defect (§3.14 before §3.13; `jobs`/`outbox_events`/`notifications` nested under §3.14) | Low | Fidelity audit **CF-10** |
| D-7 | `products.search_text` is "maintained by the app" (not a trigger or generated column) | — | Deliberate and correct — it keeps the normalisation rule in one testable place. But it creates a **write-path obligation**: every product and alias write must refresh it. Phase 3 needs a test that an alias insert updates the parent product's `search_text` |
| D-8 | Migration order (`DATA-MODEL.md §6.1`, 0001–0016) respects every FK dependency | — | Verified by inspection: extensions → tenancy → identity → audit → reference → catalog → inventory → purchases → customers → series/drafts → invoices → payments → returns → imports → infrastructure → seeds |
| D-9 | `ix_invoices__customer_outstanding` is proposed in §3.9 for the Credit Bills view but is **not** in the §5 indexing summary | Low | Add to §5 so it is not lost during migration authoring |

### 6.3 Data-retention coherence

Financial and stock data retained **indefinitely**, append-only; audit logs indefinitely with
`UPDATE`/`DELETE` revoked; drafts purged at 180 days; idempotency keys 7 days; sync operations 30
days; uploads 365 days (configurable). These are consistent with `SECURITY.md §13`'s honest position
that the **statutory** retention period is an open question — the system retains more than it is
likely to need, which is the safe direction.

---

## 7. API investigation

### 7.1 Assessment

The contract is well-formed. Conventions that are correct and should not be relaxed:

- **`/api/v1`, `snake_case`, money/quantities as strings**, error envelope on **every** non-2xx with
  `{code, message, details, request_id, retryable}`; clients branch on `code`, never `message`.
- **`404` not `403` for cross-tenant** — existence is never leaked.
- **`business_id` is never accepted as a tenant selector.**
- **Cursor pagination with hard maxima** (50 search / 200 list) — no unbounded queries.
- **Every route declares its required permission in a dependency; a route without one fails CI.**
- **Deliberate asymmetry: errors are handled exhaustively, warnings permissively.** A new warning must
  never break a completed sale; a new error code must break the build until handled. This is a
  genuinely good piece of contract design.
- **`totals.tax` is `null`, not `"0.00"`, in `estimate_only`** so no misleading zero-tax row can
  render.
- **`cost_price` is omitted, not nulled,** without `catalog.cost.read` — so a careless client render
  cannot leak it.

### 7.2 Specific findings

| # | Finding | Severity | Action |
|---|---|---|---|
| A-1 | Four codes used normatively but absent from the registry declared "complete for v1"; `428` absent from the status table | **Medium** | Fidelity audit **CF-3**. Must be fixed before the sync client module (F-11) |
| A-2 | `OVER_ALLOCATION` is specified as **422** in `§3.7` and in `API-INTEGRATION.md`, while `§4.1` assigns **409** to "business-rule conflict (stock, price, version, idempotency, state)" | Low | Over-allocation is arguably a business-rule conflict. The two documents **agree on 422**, so clients are consistent; recommend documenting *why* it is 422 (semantically invalid amount) rather than changing it, to avoid a breaking change later |
| A-3 | `POST /sales:finalize` sends `lines` explicitly **and** `draft_id` + `draft_version` | — | Deliberate: "the client's intent is unambiguous". Correct, but it means the server must decide which wins. API-SPEC implies `lines` are authoritative for the sale and `draft_version` is a staleness check. **Phase 3 must state this explicitly** and test that a `lines`/draft mismatch is not silently reconciled |
| A-4 | `GET /invoices` is documented as backing both Daily Bills and Credit Bills, and `§3.7` uses `GET /invoices?customer_id=&is_credit=true`, but `is_credit` is **not** in the `§3.4` query-parameter list | Low | Add `is_credit` to the `§3.4` parameter list |
| A-5 | `GET /payments?invoice_id=` is used by FR-RPT-033 and `§3.7`'s prose, but the `§3.7` endpoint table lists only `?customer_id=&from=&to=&cursor=` | Low | Add `invoice_id` to the documented parameters |
| A-6 | The permission for `GET /credit/customers` is `credit.read`; the Cashier role **has** `credit.read` | — | Verified against the role matrix. Consistent — a cashier can see credit balances, which the workflow requires (they record payments) |
| A-7 | `POST /invoices/{id}/print-events` requires `reports.sales.read`; the Cashier has `reports.sales.read` scoped to "own counter/shift totals" | Low | The footnote scopes *report* visibility, not print logging. Phase 3 should confirm a cashier can log a print event for an invoice they just created, or printing will silently fail to log |
| A-8 | No endpoint mutates a finalised invoice/item/payment/movement/audit row; none sets `payment_status`, `amount_paid` or `inventory_balances.quantity`; no "mark as paid" exists | — | Verified across §3 and §9. Asserted by OpenAPI-scanning tests. This is the contract's strongest property |
| A-9 | Rate limits are specified per endpoint group with an in-process limiter | — | Correct at one replica; the scale-out prerequisite is recorded (ADR-019, `DEPLOYMENT.md §9`) |

### 7.3 Cross-repository contract coherence

| Dimension | Mechanism | Assessment |
|---|---|---|
| Types | Generated from OpenAPI, committed, CI regenerates and **fails on a diff** (ADR-029) | Strong — drift is caught in the pipeline, not at the counter |
| Casing | `snake_case` both sides (ADR-012) | Consistent |
| Money | Strings end to end; arithmetic only in `lib/money.ts` / `domain/shared/money.py` | Consistent |
| Dates | RFC 3339 with offset; **server time authoritative**; client never derives a business date | Consistent (FE-E-3, E-14, FR-OFFLINE-007) |
| IDs | UUID; **client-generatable** for drafts, draft items and customers so offline creation is idempotent | Consistent, and the "client id *is* the server id — no remapping" rule (FR-DRAFT-001) is important |
| Errors | One registry, exhaustive client switch | **Broken by CF-3** until the four codes are added |
| Rounding | **Duplicated by design** (ADR-032), reconciled by `client_computed_total` → `TOTAL_MISMATCH` | Accepted cost; `P2-D-03` makes the duplicated rule a *published policy* rather than two hardcoded constants, which materially reduces drift risk |
| Normalisation | Duplicated, with a **shared fixture list of part-number variants used by both suites** | Good pattern — `P2-D-07` proposes extending it to idempotency fingerprinting |

**Finding.** The frontend must never become the source of financial truth, and the documents enforce
this at four independent levels: `client_computed_total` is a cross-check only (FR-SALE-003); no
endpoint accepts a client total as authoritative (§9); the client labels its total a preview
(ADR-032); and a structural test forbids money arithmetic outside `lib/money.ts`. Phase 3 should keep
all four — each catches a different mistake.

---

## 8. Cross-cutting failure-mode analysis

Each critical workflow was walked against the 23 failure modes required by the Phase 2 brief. Full
register with likelihood, impact, detection, recovery and residual risk is in
[`PHASE-2-RISK-REGISTER.md`](PHASE-2-RISK-REGISTER.md). Summary of the **sale finalisation** path,
the highest-stakes workflow:

| Failure | Specified behaviour | Covered? |
|---|---|---|
| Duplicate submission (double-click) | Button disabled in flight; same idempotency key; stored response returned | ✅ FR-SALE-021, E-2 |
| Concurrent same-key requests | Exactly one invoice; loser gets stored response or `IDEMPOTENT_REQUEST_IN_PROGRESS` | ✅ tested at BE |
| Concurrent sales, last unit | Ordered row locks serialise; one commits, other gets `INSUFFICIENT_STOCK available: 0` | ✅ AC-3, E-3 |
| Partial DB failure mid-transaction | Single transaction; nothing persists, **no number consumed** | ✅ FR-SALE-001 |
| Request timeout | 30 s client timeout ("a slow commit is still a commit"); then state 2 | ✅ |
| **Lost response after commit** | Retry same key → stored response + `Idempotent-Replay: true` | ✅ E-2 — the central safety property |
| Client retry with a new key after an edit | Must be **blocked** while unconfirmed | ⚠️ **Mechanism conflict — §3.4, `P2-D-08`** |
| Server restart | In-flight transaction lost; idempotency key not consumed | ✅ |
| Database restart | Same | ✅ |
| Stale frontend data (price changed) | `409 PRICE_CHANGED` with draft vs current; never auto-accepted | ✅ E-5 |
| Stale frontend data (draft version) | `409 DRAFT_VERSION_CONFLICT` with `current_state` | ✅ E-4 |
| Authorisation failure | `403` + supervisor-authorisation path, recorded against the approver | ✅ FR-UI-PERM-003 |
| Invalid input | `422` with `details.fields`; `extra="forbid"` rejects unknown fields | ✅ |
| Missing referenced record | Inactive product → `409 PRODUCT_INACTIVE` naming the line; cross-tenant → `404` | ✅ E-15 |
| Duplicate identifiers | Unique constraints on SKU, barcode, supplier document, file hash, invoice number | ✅ |
| Race conditions | Fixed lock order + deadlock-probe test (50 parallel multi-product sales, zero deadlocks) | ✅ |
| Unexpected precision/rounding | `client_computed_total` cross-check → `422 TOTAL_MISMATCH`; scale validation → `422` | ✅ |
| Time-zone / date boundary | Server time authoritative; a sale at 23:59:59 and one at 00:00:00 land on the right days | ✅ tested |
| Audit record omission | Audit row written **in the same transaction** as the change | ✅ FR-AUDIT-001 |
| Backup restoration | Quarterly drill; **invariant suite run against the restored database** | ✅ AC-10 |
| Deployment rollback | Expand/contract; "roll back the app, leave the schema" is always first | ✅ |
| Migration failure | Tested `downgrade()` per revision; `/readyz` gates traffic on head match | ✅ |
| External service failure | None in the hot path by design (no broker, no cache, no gateway) | ✅ |

**Two genuine gaps found in an otherwise complete analysis:** the pending-finalisation edit mechanism
(`P2-D-08`) and the financial-year series creation path (`P2-D-09`). Both are narrow and specified in
the decision register.

### 8.1 Invariants to formalise

The brief's example invariants were checked against the authoritative requirements and restated with
their verification method:

| # | Invariant | Verification |
|---|---|---|
| I-1 | A finalised sale deducts stock exactly once | `test_cash_sale_single_stock_movement`, `test_credit_sale_single_stock_movement`; movement count before/after |
| I-2 | Payment collection creates **zero** stock movements | `test_payment_creates_no_stock_movement`; movement count before/after `POST /payments` |
| I-3 | A failed sale leaves no partial invoice, item, movement, payment **or consumed number** | `test_finalize_rollback_leaves_nothing` + assert `invoice_series.next_number` unchanged |
| I-4 | Invoice numbers are unique within (business, branch, series, FY) | `uq_invoices__series_sequence`, `uq_invoices__business_number` |
| I-5 | Repeating an idempotent operation creates no second business effect | `test_finalize_idempotent_replay`, `test_duplicate_payment_once`, `sync/test_replay_batch_all_replayed` |
| I-6 | Drafts have no finalised-sale side effects | `test_draft_edit_creates_no_movements`, `test_keep_for_later_no_finalize_call` |
| I-7 | `amount_paid` = Σ allocations, and `payment_status` agrees | `invariants/test_payment_allocation.py` + nightly reconciliation that **alerts** |
| I-8 | `inventory_balances.quantity` = Σ `stock_movements.quantity_delta` | `invariants/test_stock_ledger.py`, `property/test_stock_conservation.py` |
| I-9 | No user reads or writes outside their authorisation scope | Generated isolation matrix over every route × 2 businesses × 2 branches × every role (AC-8) |
| I-10 | Historical invoices retain the product/pricing snapshot needed to reproduce the original sale | `test_item_snapshot_survives_catalog_edit`; `staleTime: Infinity` on print payloads |
| I-11 | Finalised financial rows are never updated or deleted | **DB privileges** + `invariants/test_immutability.py` |
| I-12 | `invoices.subtotal` = Σ `invoice_items.line_total` | **NFR-DATA-002b — currently an orphan (CF-9); must be promoted and traced** |

---

## 9. Security review

The threat model in `SECURITY.md §1` is **correctly calibrated**: the realistic risks are insider
error and casual insider abuse plus internet exposure of an API — not a sophisticated attacker. The
controls are strong where the real risk is and absent where ceremony would not be operated.

| Threat | Prevention | Detection | Test | Recovery | Residual |
|---|---|---|---|---|---|
| **Cashier edits/deletes a sale to pocket cash** (T-1) | Append-only tables enforced by **column-level DB privileges**; no mutation endpoints; corrections are cancellation/return/credit note | Audit log with before/after; cancellation alert if frequent | `invariants/test_immutability.py`, `test_no_invoice_mutation_route` (OpenAPI scan) | Reversal workflows; PITR | A manager with `billing.sale.cancel` can cancel within 24 h — **by design**, mitigated by mandatory reason + audit |
| Silent discounting / price change for friends (T-2) | `billing.price.override`, `discount.cashier_max_percent = 0`, price-change detection | Audit before/after; discount report | `test_price_override_permission`, `test_oversell_audited` | — | Owner must actually read the discount report — an **operational** control |
| Stock written down to hide shrinkage (T-3) | `inventory.adjust` permission + mandatory reason | Nightly reconciliation; oversell-override alert > 5/day | `test_adjustment_requires_permission_and_reason` | Reasoned reversing movement | Residual: a permitted user can adjust with a plausible reason |
| Stolen counter PC with offline data (T-4) | Device registry + remote revoke; bounded cache; **no `cost_price` ever cached**; full-disk encryption as a stated operational requirement | `last_seen_at`; revocation audit | `test_revoked_device_blocked_everywhere` | Revoke; refresh family invalidated | **IndexedDB is unencrypted — accepted and documented.** Exposure = cached catalog, stock figures, open drafts |
| Authz bypass / broken object-level authz | Three enforcement points: route dependency, service re-assertion, repository scoping from `AuthContext` | — | **Generated** permission-negative matrix + isolation matrix per route (AC-8) | — | A new route cannot ship unscoped — CI generates its coverage |
| Unauthorised invoice/customer access | Tenant scope from the token only; `business_id` never accepted; **`404` not `403`** | — | `api/test_isolation.py` | — | Low |
| SQL injection (T-8) | ORM/parameterised only; lint flags f-strings into `text()`; `LIKE` wildcards escaped | — | — | — | Low |
| XSS → session theft (T-13) | Access token in memory; refresh `HttpOnly`; strict CSP, no `unsafe-inline`; no `dangerouslySetInnerHTML` (allow-list currently **empty**) | — | Structural test | — | Low |
| CSRF | Refresh cookie `SameSite=Lax` scoped to `/api/v1/auth`; every other endpoint uses a bearer header; refresh additionally bound to `X-Device-Id` | — | — | — | Low |
| Mass assignment | Pydantic v2 `extra="forbid"` — unknown fields **rejected**, not ignored | — | — | — | Low. Note: this makes a client typo a `422` rather than a silently dropped discount |
| Unsafe upload (T-7) | Allow-list on extension **and** magic bytes **and** declared type; streaming size cap; **generated storage key — the original filename never touches a path**; stored outside any served path; never executed; parsed in the worker with row/page/time/zip-bomb caps | Job error records | `api/test_uploads.py` incl. `.exe`-as-`.csv`, HTML-as-CSV, zip bomb, path-traversal name | Re-upload | Best-effort PDF parsing — mitigated by **mandatory** human review |
| Secret exposure (T-10) | `.env` ignored; `.env.example` placeholders only; CI secret scan; **no DB credential may exist in `bmfrontend`**; only 4 `VITE_` variables, CI-enforced | CI | Env allow-list test | Rotate | `BM_PASSWORD_PEPPER` is **documented as unrotatable** — honest and important |
| Sensitive data in logs | Redaction filter on every record and before error reports; raw IPs **hashed** with a pepper; PII limited to ids | — | `test_audit_never_contains_secrets`, `tests/unit/test_logging.py` | — | Low |
| Audit-log tampering (T-14) | `UPDATE`/`DELETE` revoked at the role level; written in the same transaction as the change | — | `invariants/test_immutability.py::audit_logs` | — | A DB superuser could tamper — unavoidable; superuser is sealed break-glass only |
| Excessive privileges | Role→permission matrix; `Auditor` read-only at the API level; `cost_price` omitted without permission | Role-change audit | `api/test_permissions.py` | — | `credit.writeoff` is owner-only — correct |
| Backup access | Encrypted at rest and in transit; restricted; restores audited; **restore drill is a release gate** | Drill result recorded | AC-10 | — | T-11 ("backup exists but cannot be restored") is the right risk to have gated |
| Dependency / supply-chain | CI audit on every run; high/critical **blocks release**; monthly base-image patching; 30-day/7-day patch targets | CI | — | — | Lockfiles are committed both sides — good |
| CSV injection | Export escapes leading `=`, `+`, `-`, `@` | — | `test_csv_injection_escaped` | — | Often missed; correctly handled here |
| Owner locked out (T-15) | Documented, rehearsed break-glass stored **outside** the system; writes an audit row like any action | — | — | — | Requires the procedure to actually be stored — an operational gate |

**No security gap requiring an architecture change was found.** Three observations for Phase 3:

1. **The `dangerouslySetInnerHTML` allow-list is empty and should stay empty.** Any addition needs an
   ADR and sanitisation.
2. **Access tokens are not individually revocable for up to 15 minutes** — accepted, bounded, and
   *documented*, which is the right way to hold that risk. Device revocation stops refresh
   immediately.
3. **No MFA** — correct for shared counter PCs; device binding + PIN is the practical control. The
   documented revisit trigger (owner/manager access from outside the shop) should be kept visible.

**This system is not "perfectly secure."** The material residual risks are: an authorised insider
acting within their permissions (mitigated by audit, not prevented); unencrypted IndexedDB on a
stolen counter machine; and the 15-minute access-token window. All three are documented with their
revisit conditions.

---

## 10. Reporting, search and boundary investigation

| Area | Finding |
|---|---|
| Daily Bills / Credit Bills | **Two views of one dataset** (`GET /invoices`), with `test_daily_and_credit_reconcile` as the single most valuable report test. Credit sales appear in Daily Bills on their **sale date** |
| Ageing buckets | current, 1–30, 31–60, 61–90, 90+, tested at **exact boundaries** (0, 1, 30, 31, 60, 61, 90, 91) with a frozen clock. Correct — off-by-one in ageing is the classic defect |
| Cancelled invoices | Excluded from revenue, retained in an `excluded` block and in audit views |
| `as_of` on every report | Present and required. Important once a read replica is ever introduced (replica lag must be labelled) |
| Pagination | Cursor-based with hard maxima; `(updated_at, id)` cursor for the catalog delta is **stable under concurrent writes** — a detail often got wrong |
| Search | Tiered SQL ranking (exact SKU 100 → SKU prefix 80 → name prefix 60 → alias prefix 50 → trigram ×40), stable tie-break by `sales_rank` then name; aliases **joined, not duplicated** into the product row |
| Search normalisation | Duplicated client/server by necessity, with a **shared fixture list** in both suites. The right way to hold an unavoidable duplication |
| Financial-year boundaries | `financial_year_start_month` default 4. **Series creation at rollover is unspecified — `P2-D-09`** |
| Time zone | Server time authoritative throughout; `timestamptz`; client never derives a business date |

**Reports to build and nothing more.** FR-RPT-001…008 plus the frontend's FR-RPT-020…033 are the
agreed set. No additional report is proposed.

---

## 11. Data import and migration

**Finding: there is no data to import yet.** `PRD.md §5.4` records that **no sample product list,
supplier PDF or customer ledger was provided**. Therefore:

- Import parsing is specified as a **pipeline with mandatory human review**, not a fixed column
  mapping — the only honest design given no sample file.
- **Do not claim an import source exists.** Phase 3 cannot build or test against the shop's real
  catalog until a file is supplied.
- The go-live checklist already requires opening stock loaded via `opening` movements and physically
  verified, and products/prices/aliases loaded and spot-checked by the owner (`DEPLOYMENT.md §11.2`).
  **That is the real data-migration event**, and it is a go-live gate, not a Phase 3 feature.

**Import safety properties worth preserving verbatim:** `intent` is chosen by the user and **cannot
be inferred silently**; `price_list` produces **zero** stock movements under all circumstances;
`ambiguous`/`new_product`/`duplicate_in_file`/`invalid` rows **cannot be applied without a per-row
human decision** (encoded as `ck_import_rows__decision_required`); `applied` is terminal; apply is
one transaction, idempotent on the job id; `file_sha256` unique per business.

---

## 12. Operations and reliability

| Area | Assessment |
|---|---|
| Config split | **Infrastructure in env, business rules in DB `settings`** — so the owner can change tax mode, draft limits, credit terms, discount caps, cancellation window and invoice prefixes **without a deploy**. This split is correct and is the kind of thing usually got wrong |
| Fail-fast startup | Required variable missing/malformed → startup fails. Correct for a financial system |
| Migrations | Explicit deploy step; `/readyz` fails on head mismatch so a half-migrated fleet cannot serve writes; expand→migrate→contract mandatory on live financial tables; `CREATE INDEX CONCURRENTLY` in non-transactional migrations |
| Backups | Daily snapshots (30 d), PITR (7 d, RPO ≤ 5 min), **weekly off-provider encrypted dump (90 d)** — protects against account-level loss, not just instance failure. Thorough |
| Restore | Quarterly drill; **the proof is running the invariant suite against the restored database**, not that the restore completed |
| Health | `/healthz` liveness; `/readyz` = DB reachable **and** Alembic head matches |
| Alerts | 13 alerts with severities. **Reconciliation discrepancy = Critical** is the right call (stock and ledger disagreeing means a bug). **Every alert must reach a named person before go-live** — OD-19 |
| Rollback | Four scenarios with expected times; "roll back the app, leave the schema" first |
| DR | Region outage path accepts up to one week RPO **and says so plainly** rather than implying hot standby. Honest |
| Scaling | Seven steps each with a **prerequisite**, so nobody scales out and quietly breaks the in-process rate limiter |

**Gap found.** `DEPLOYMENT.md §8.1` requires alerts to reach a real person, and OD-19 is the gate —
but no document specifies **what the on-call expectation is** (response time, business hours vs
24/7). For a one-shop system "best effort, owner's phone" is a legitimate answer, but it should be
written down so a 9 p.m. critical alert has a defined expectation. Minor; added to the risk register
as `R-24`.

---

## 13. Summary of findings

### 13.1 Endorsed without change

The modular monolith; the technology stack; the layering rules; the transaction and lock-order
design; the two-queue offline model and its three-state distinction; immutability via DB privileges;
derived payment status with a same-transaction cache; `pg_trgm` search; the Postgres job queue;
generated API types; the service-worker scope; the state boundaries; the threat model and its
controls; the test layering and invariant suites; the operational design.

### 13.2 Corrections required to Phase 1 documents

Eleven, all from the fidelity audit (CF-1…CF-11). **Three are pre-module blockers:** CF-3 (sync
client), CF-4 (credit UI), CF-1 (numbering policy statement).

### 13.3 New Phase 2 decisions proposed

| ID | Subject |
|---|---|
| `P2-D-01` | Phase 2 artifacts live in `bmbackend/docs/` per ADR-021; `bmfrontend` carries a pointer index |
| `P2-D-02` | Re-scope OD-3's blocking level: build the gapless design now, freeze the policy statement later |
| `P2-D-03` | Make the money policy an explicit resolved policy object published via bootstrap; re-scope OD-7 accordingly |
| `P2-D-04` | Source-of-truth hierarchy, stated explicitly |
| `P2-D-05` | Import-boundary linter is a merge blocker from milestone M1 |
| `P2-D-06` | Claim the idempotency key **before** acquiring inventory locks — stated as an ordering constraint |
| `P2-D-07` | Normative idempotency fingerprint canonicalisation + shared cross-repo fixtures |
| `P2-D-08` | Block draft edits while a finalisation is unconfirmed; clear the pending row only on resolution |
| `P2-D-09` | Specify and test financial-year `invoice_series` creation |
| `P2-D-10` | Settings resolution: documented defaults in code, overridable by rows, published to the client |

Full records, including alternatives and consequences, in
[`PHASE-2-DECISION-REGISTER.md`](PHASE-2-DECISION-REGISTER.md).

### 13.4 Remaining uncertainties

| Uncertainty | Nature | Resolvable by |
|---|---|---|
| The bill's lower section | **External evidence the owner cannot currently supply** | OD-5 — the workbook, a bottom screenshot, or a photo of a printed bill |
| Invoice-number / payment-block placement | Depends on the above | OD-22 |
| Rounding precision, method, stage, whole-rupee | **Accounting decision** | OD-7 (narrowed by `P2-D-03`) |
| Whether numbering must be gapless | **Statutory/accounting decision** | OD-3 (narrowed by `P2-D-02`) |
| GST status, rates, HSN, legal identity, paper, hosting, terminology | **Owner/accountant decisions** | OD-1, OD-2, OD-4, OD-6, OD-19, OD-24 |
| The shop's real catalog and opening stock | **Data that does not exist in the project yet** | Go-live data loading |
| Real-device print fidelity and scanner behaviour | **Hardware-dependent** | Manual pre-go-live checklist |

**None of these blocks the majority of Phase 3.** The quantified position is in
[`PHASE-2-IMPLEMENTATION-PLAN.md`](PHASE-2-IMPLEMENTATION-PLAN.md).

---

## 14. Related documents

[`PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md`](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md) ·
[`PHASE-2-IMPLEMENTATION-BLUEPRINT.md`](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) ·
[`PHASE-2-IMPLEMENTATION-PLAN.md`](PHASE-2-IMPLEMENTATION-PLAN.md) ·
[`PHASE-2-DECISION-REGISTER.md`](PHASE-2-DECISION-REGISTER.md) ·
[`PHASE-2-RISK-REGISTER.md`](PHASE-2-RISK-REGISTER.md) ·
[`PHASE-2-OWNER-DECISIONS-REQUIRED.md`](PHASE-2-OWNER-DECISIONS-REQUIRED.md) ·
[`PHASE-2-TRACEABILITY-MATRIX.md`](PHASE-2-TRACEABILITY-MATRIX.md)
