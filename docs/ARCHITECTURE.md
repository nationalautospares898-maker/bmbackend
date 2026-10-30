# Backend Architecture — `bmbackend`

| Field | Value |
|---|---|
| Status | **Phase 1 proposal — no code exists yet** |
| Last updated | 2026-10-29 |
| Decisions | See `docs/DECISIONS.md` (ADR-001 … ADR-020) |

> **Verified repository state (2026-10-29).** `bmbackend/` is an **empty directory**. It is not a
> Git repository and contains no source, dependency manifest, configuration or documentation other
> than what Phase 1 creates. Therefore **nothing in this document describes existing code** — every
> technology choice below is a recommendation to be approved before Phase 2.

---

## 1. Architecture at a glance

```
┌──────────────────────────────────────────────────────────────────────┐
│  Counter browsers (PWA)  —  bmfrontend, separate repository          │
│  React + IndexedDB drafts + service worker                           │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ HTTPS, JSON, /api/v1, bearer access token
                            │ refresh token in HttpOnly cookie
┌───────────────────────────▼──────────────────────────────────────────┐
│  Reverse proxy / platform ingress  (TLS, HSTS, body-size cap)        │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────────┐
│  FastAPI application (modular monolith, 1..N identical replicas)     │
│                                                                      │
│   api/        routers, request/response schemas, dep-injected auth    │
│   domain/     modules: auth, catalog, inventory, sales, credit, …     │
│   infra/      db session, repositories, storage, jobs, logging        │
└──────┬─────────────────────────────────┬─────────────────────────────┘
       │                                 │
┌──────▼──────────────┐     ┌────────────▼───────────┐
│ PostgreSQL 16        │     │ Object/file storage    │
│ single primary       │     │ (uploads, local FS in  │
│ PITR backups         │     │  dev, bucket in prod)  │
└──────┬───────────────┘     └────────────────────────┘
       │ job queue table (FOR UPDATE SKIP LOCKED)
┌──────▼───────────────────────────────────────────────┐
│ Worker process (same image, different entrypoint)     │
│ import parsing, reconciliation, outbox dispatch       │
└───────────────────────────────────────────────────────┘
```

**Shape: modular monolith.** One deployable API image, one worker image (same code), one database.
Modules have enforced boundaries so that a future split is possible, but no distributed
infrastructure is introduced without a documented need (ADR-001).

Explicitly **not** used in v1: microservices, Kubernetes, Kafka, Redis, service mesh, CQRS,
event sourcing, GraphQL. Rationale and the conditions that would justify each are in ADR-001 and
ADR-005.

---

## 2. Recommended technology stack

All items are **proposals**, not verified from the repository (there is no repository).

| Concern | Choice | Why | Alternative considered |
|---|---|---|---|
| Language | Python 3.11 (3.11.9 verified present on this machine) | Owner-preferred; strong decimal and data-tooling story for imports | Node/TypeScript (one language across repos) — rejected, ADR-002 |
| Web framework | FastAPI | Async, Pydantic validation, auto OpenAPI → generated frontend types | Django REST (heavier, less OpenAPI-native) |
| Validation/serialisation | Pydantic v2 | Strict typing at the boundary, `Decimal` support | Marshmallow |
| ORM | SQLAlchemy 2.0, typed, **sync** sessions | Explicit transactions and row locks are easier to reason about and test; this app is DB-bound, not fan-out-bound | Async SQLAlchemy — see ADR-003 |
| Migrations | Alembic | Standard, reversible, reviewable | Hand-written SQL |
| Database | PostgreSQL 16 | `NUMERIC`, strong constraints, `FOR UPDATE`, `pg_trgm`, PITR | MySQL, SQLite (rejected for concurrency) |
| Search | `pg_trgm` + `unaccent` + prefix indexes | Zero extra infrastructure, meets NFR-PERF-001 at 100k SKUs | Elasticsearch/Meilisearch — unjustified at this scale (ADR-005) |
| Background jobs | Postgres-backed queue, `FOR UPDATE SKIP LOCKED`, worker process | No Redis/broker to operate; durable; transactional with business data | Celery+Redis, arq — ADR-004 |
| Password hashing | Argon2id (`argon2-cffi`) | Current best practice | bcrypt |
| Spreadsheet parsing | `openpyxl` (xlsx), stdlib `csv` | Mature | pandas (heavy, loose typing) |
| PDF table extraction | `pdfplumber`, best-effort | Pure Python, no system binaries | Camelot/Ghostscript (system deps) |
| HTTP server | Uvicorn workers behind the platform proxy | Standard | Gunicorn+Uvicorn |
| Containerisation | Docker (29.3.1 verified present) + Compose for local dev | Reproducible local Postgres and worker | Native installs |
| Dependency management | `uv` (0.8.22 verified present) with a locked `pyproject.toml` | Fast, deterministic lockfile | pip-tools, Poetry |
| Testing | pytest, pytest-asyncio, factory fixtures, `testcontainers` or Compose Postgres, Hypothesis for money/stock properties | Real Postgres in tests is mandatory (locks, `NUMERIC`, constraints) | SQLite in tests — **prohibited**, it cannot test what we rely on |
| Static checks | ruff, mypy (strict on domain) | Catch type/money errors early | – |

---

## 3. Layering and dependency rules

```
api  ──────────────►  domain  ──────────────►  infra
(routers, schemas)    (services, rules,        (repositories, session,
                       invariants, errors)       storage, queue, clients)
        ▲                                             │
        └──────────────── no reverse imports ─────────┘
```

**Enforced rules** (checked by an import-linter configuration in CI):

1. `api` may import `domain` and schema types. It must contain **no business rule** — no money
   arithmetic, no stock decision, no permission logic beyond declaring the required permission.
2. `domain` may import `infra` interfaces and other domain modules **only through their public
   service interface** (`domain/<module>/service.py`), never another module's repository or ORM model.
3. `infra` may not import `domain`.
4. ORM models live in `infra/db/models/` and never leak out of repositories as the API response type.
   Services exchange domain dataclasses/Pydantic models.
5. No module writes to another module's tables. Inventory is the only writer of stock balances and
   movements; sales calls `InventoryService`.

### 3.1 Directory layout (proposed)

```
bmbackend/
├── pyproject.toml / uv.lock
├── alembic.ini
├── docker-compose.yml              # local postgres + api + worker
├── Dockerfile
├── docs/                           # this documentation set
├── migrations/                     # alembic versions
├── src/bm/
│   ├── main.py                     # FastAPI app factory, middleware, routers
│   ├── worker.py                   # job worker entrypoint
│   ├── config.py                   # typed settings from env
│   ├── api/
│   │   ├── deps.py                 # auth context, permission guard, db session
│   │   ├── errors.py               # exception handlers → error envelope
│   │   ├── middleware.py           # request id, logging, rate limit
│   │   └── v1/
│   │       ├── auth.py  catalog.py  drafts.py  sales.py  inventory.py
│   │       ├── customers.py  credit.py  payments.py  purchases.py
│   │       ├── returns.py  imports.py  reports.py  sync.py  admin.py
│   │       └── schemas/            # request/response models per resource
│   ├── domain/
│   │   ├── shared/                 # money, quantity, ids, errors, clock
│   │   ├── auth/        catalog/        inventory/     sales/
│   │   ├── customers/   credit/         purchases/     returns/
│   │   ├── imports/     reports/        sync/          audit/
│   │   ├── settings/    tax/            notifications/
│   │   └── <module>/{service.py, rules.py, models.py, repository.py (protocol)}
│   └── infra/
│       ├── db/{session.py, models/, repositories/, locks.py}
│       ├── storage/                # file storage adapter
│       ├── queue/                  # job table producer/consumer
│       ├── logging/                # structured logging, correlation
│       └── outbox/                 # transactional outbox dispatch
└── tests/
    ├── unit/  integration/  api/  concurrency/  migrations/  invariants/
```

---

## 4. Modules, ownership and boundaries

Each module owns its tables. "Owns" means: only this module's repository layer writes them.

| Module | Owns (tables) | Public service responsibilities | Depends on |
|---|---|---|---|
| **auth** | `users`, `memberships`, `roles`, `permissions`, `devices`, `refresh_tokens`, `login_attempts` | Authenticate, issue/rotate tokens, resolve auth context, register/revoke devices, PIN switch | audit |
| **admin/settings** | `businesses`, `branches`, `counters`, `settings` | Business/branch/counter CRUD, typed settings read with defaults | audit |
| **catalog** | `products`, `product_aliases`, `brands`, `categories`, `units`, `product_prices` | Search, read product, create/update product, resolve price | audit |
| **inventory** | `inventory_balances`, `stock_movements`, `stock_count_sessions` | **Sole writer of stock.** `apply_movements(tx, [...])`, `lock_and_check(tx, [...])`, adjust, reconcile | catalog, audit |
| **sales** | `draft_bills`, `draft_bill_items`, `invoices`, `invoice_items`, `invoice_series`, `print_log` | Draft CRUD with versioning, **finalise sale (transaction owner)**, cancel, read invoice, print payload | catalog, inventory, customers, credit, tax, audit |
| **customers** | `customers`, `customer_phones`, `customer_vehicles` | Create/find/dedupe customers | audit |
| **credit** | `payments`, `payment_allocations`, `credit_notes`, `credit_note_allocations` | Record payment, allocate, compute outstanding, ageing, write-off | customers, sales (read), audit |
| **purchases** | `suppliers`, `purchases`, `purchase_items` | Record goods receipt (calls inventory), supplier CRUD | catalog, inventory, audit |
| **returns** | `sales_returns`, `sales_return_items` | Create return (calls inventory + credit), validate cumulative quantities | sales, inventory, credit, audit |
| **imports** | `import_jobs`, `import_rows` | Upload, enqueue parse, review, apply (calls catalog/purchases) | catalog, purchases, queue, storage, audit |
| **reports** | — (read-only across modules via dedicated read queries) | Daily sales, credit ageing, stock, margin, exports | sales, credit, inventory |
| **sync** | `sync_operations`, `idempotency_keys` | Batch operation intake, dedupe, dispatch to owning module, catalog delta pull | sales (drafts), customers, catalog, audit |
| **tax** | `tax_groups`, `tax_rates`, `tax_config_versions` | Resolve tax treatment for a line at a date; gate `gst_enabled` | settings, audit |
| **audit** | `audit_logs` | Append audit entries (never update/delete) | — |
| **notifications** | `notifications`, `outbox_events` | Queue low-stock/overdue notices; transactional outbox dispatch | queue |

### 4.1 Where business rules live

| Rule | Home |
|---|---|
| Money arithmetic and rounding | `domain/shared/money.py` — the only place Decimal arithmetic for amounts happens |
| Stock sufficiency and locking | `domain/inventory/service.py` |
| Invoice total composition | `domain/sales/rules.py` |
| Invoice numbering | `domain/sales/numbering.py` |
| Outstanding balance derivation | `domain/credit/rules.py` |
| Permission checks | `api/deps.py` declares; `domain/<m>/service.py` re-asserts for anything destructive |
| Tax resolution | `domain/tax/service.py` |

---

## 5. Transaction ownership and concurrency

### 5.1 Rules

1. **One request, one transaction** for write endpoints. The session is opened by a dependency,
   committed on success, rolled back on any exception.
2. **The service that owns the use case owns the transaction.** `SalesService.finalize_sale` opens
   it; `InventoryService` and `CreditService` participate using the passed session and never commit.
   No nested commits anywhere.
3. **No HTTP calls, file I/O or job execution inside a write transaction.** Side effects go through
   the transactional outbox and run after commit.
4. **Deterministic lock ordering** to make deadlocks structurally impossible:
   `inventory_balances` (ordered by `product_id` ascending) → `invoice_series` → `customers`
   → `invoices`. Every code path acquires locks in this order and no other.
5. Isolation level: **READ COMMITTED** (Postgres default) plus explicit `SELECT … FOR UPDATE`.
   Explicit row locks are preferred to `SERIALIZABLE` because they give predictable blocking rather
   than retry storms at a busy counter (ADR-008).
6. `statement_timeout` and `lock_timeout` are set per connection; a finalisation that cannot get its
   locks fails fast with a retryable error rather than hanging the counter.

### 5.2 Finalisation transaction, step by step

```python
# domain/sales/service.py  (illustrative pseudocode — not implementation)
def finalize_sale(ctx, cmd) -> Invoice:
    with uow.transaction() as tx:                      # 1 BEGIN
        idem = idempotency.claim(tx, ctx, cmd)         # 2 insert key or return stored response
        if idem.replayed:
            return idem.stored_response

        draft = drafts.load_for_update(tx, cmd.draft_id, ctx)   # 3 lock the draft
        rules.assert_draft_finalizable(draft)                   #   not already finalised

        lines = catalog.resolve_lines(tx, draft.items, ctx)     # 4 live prices + active check
        rules.assert_prices_acceptable(lines, cmd.accept_price_changes, ctx.permissions)

        totals = money.compute_invoice_totals(lines, cmd.discounts,
                                              tax.resolve(ctx, lines, clock.now()))
        rules.assert_client_total_matches(totals, cmd.client_total)   # advisory cross-check

        stock = inventory.lock_and_check(tx, ctx.branch_id, lines)   # 5 FOR UPDATE, ordered
        if stock.shortfalls:
            rules.assert_oversell_authorized(ctx, cmd.oversell_override)  # else 409

        number = numbering.allocate(tx, ctx.branch_id, series, clock.now())  # 6 FOR UPDATE

        invoice = invoices.insert(tx, ..., number, totals)      # 7 invoice + item snapshots
        inventory.apply_movements(tx, movements_for(invoice))    # 8 ledger + balances
        if cmd.payment:
            credit.record_payment(tx, ctx, invoice, cmd.payment) # 9 payment + allocation
        drafts.mark_finalized(tx, draft, invoice.id)
        audit.record(tx, ctx, "sale.finalized", invoice.id, details)
        outbox.enqueue(tx, "sale.finalized", invoice.id)         # 10 post-commit side effects
        idempotency.store_response(tx, idem, invoice_response)
    return invoice                                               # 11 COMMIT
```

Everything in that block either all happens or none of it does (FR-SALE-001).

### 5.3 Idempotency mechanism

- Client sends `Idempotency-Key: <uuid>` on `POST /sales:finalize`, `POST /payments`,
  `POST /returns`, `POST /imports/{id}:apply`, and all `/sync/operations`.
- `idempotency_keys(business_id, endpoint, key)` is `UNIQUE`. The row is inserted **inside** the
  business transaction, so a key is only consumed if the work commits.
- Stored with the key: `request_fingerprint` (SHA-256 of the canonicalised body), `status`
  (`in_progress` / `completed`), `response_status`, `response_body`, `created_at`.
- Behaviour matrix:

| Situation | Response |
|---|---|
| New key | Process normally, store response |
| Same key, same fingerprint, completed | Return stored response, header `Idempotent-Replay: true` |
| Same key, same fingerprint, still in progress | `409 IDEMPOTENT_REQUEST_IN_PROGRESS`, client retries with backoff |
| Same key, **different** fingerprint | `409 IDEMPOTENCY_KEY_REUSE` — a client bug, never silently accepted |

- Retention: `settings.idempotency.retention_days`, default 7, pruned by a worker job. Pruning is
  safe because the client outbox gives up far sooner.

### 5.4 Invoice numbering under concurrency

`invoice_series(business_id, branch_id, series_code, financial_year, next_number)` — a single row
locked `FOR UPDATE` inside the finalisation transaction. A rolled-back sale therefore consumes no
number, so the **internal sale reference** is **unique and sequential** (NFR-DATA-005).

> ⚠️ **This reference is INTERNAL ONLY.** [OD-3](OWNER-DECISIONS.md#od-3) is re-answered — *"No need
> of invoice numbering."* It must **never** be rendered to a customer and must **not** appear in
> `print_payload`. It is retained for auditability, reconciliation, stable ordering and the
> **[OD-25](OWNER-DECISIONS.md#od-25)** contingency (if law later requires a visible serial number,
> this mechanism already satisfies it). **Gaplessness is moot** while no visible serial exists.

**Financial-year rollover.** The series row for a new financial year is created **lazily inside the
finalisation transaction** (`INSERT … ON CONFLICT DO NOTHING` on `uq_invoice_series__scope`, then
`SELECT … FOR UPDATE`), so the first sale after the rollover cannot fail for want of a row and two
concurrent first-sales cannot create conflicting rows. Full design, including the inheritance rules
for `prefix_template` and `document_kind`, in
`PHASE-2-IMPLEMENTATION-BLUEPRINT.md §8.2` (P2-D-09).

The cost is that finalisations on one series serialise at that row. Measured budget: the locked
section is a single-row read + update, so throughput is far above the design load. If a future
multi-counter load ever makes this hot, the documented remedy is **one series per counter**
(`NAS/<FY>/C1/00001`), configurable without schema change — not a switch to gap-prone sequences.
See ADR-009 for the gapless-vs-sequence trade-off and the owner question Q-TAX-3.

---

## 6. Cross-cutting concerns

### 6.1 Request pipeline

```
ingress → request-id middleware → structured-log middleware → rate limiter
       → auth dependency (token → AuthContext) → permission guard
       → schema validation → router → service (transaction) → response
       → error handler (domain error → error envelope + code)
```

`AuthContext` is a frozen dataclass: `user_id, business_id, branch_id, counter_id, device_id,
role, permissions, token_id`. Every repository method takes it (or a scope derived from it) so
tenant filtering cannot be forgotten; a test asserts every business-scoped query includes a
`business_id` predicate.

### 6.2 Error model

Domain raises typed exceptions (`InsufficientStock`, `PriceChanged`, `DraftVersionConflict`,
`PermissionDenied`, `IdempotencyKeyReuse`, …). A single handler maps them to the envelope defined in
`docs/API-SPECIFICATION.md §4`. No endpoint returns a bare 500 with a stack trace; unexpected errors
log with the request id and return a generic `INTERNAL_ERROR` carrying that id.

### 6.3 Logging and observability

- JSON logs: `ts, level, msg, request_id, user_id, business_id, branch_id, route, status, duration_ms`.
- Correlation id accepted from the client (`X-Request-Id`) if well-formed, else generated; echoed in
  the response and in the error envelope so a counter can read it out over the phone.
- Metrics (NFR-OPS-004): per-route latency/error rate, finalisation outcome counters (`committed`,
  `insufficient_stock`, `price_changed`, `replayed`), sync queue depth and age, oversell override
  count, reconciliation discrepancy count, job queue lag.
- Error reporting with release/environment tags; PII and tokens scrubbed before send.

### 6.4 Clock

All business timestamps come from `domain/shared/clock.py` (injectable), never `datetime.now()`
scattered in services — this is what makes time-dependent tests (credit ageing, cancellation
windows, tax effective dates) deterministic. Database defaults use `now()` at `timestamptz`.

### 6.5 Money and quantity

- `Decimal` only. A lint rule forbids `float` in `domain/` and in ORM money columns.
- Amounts `NUMERIC(14,2)`, unit prices `NUMERIC(14,4)` (per-ml/per-gram pricing), quantities
  `NUMERIC(14,3)`, percentages `NUMERIC(6,3)`.
- Rounding: `ROUND_HALF_UP` at line level, then sum; optional whole-rupee rounding of the grand
  total recorded explicitly as `invoices.rounding_adjustment` so the arithmetic always reconciles
  (NFR-DATA-002). Pending owner confirmation Q-MONEY-1.

---

## 7. Background jobs and scheduled work

A Postgres job table consumed with `FOR UPDATE SKIP LOCKED` by a worker process running the same
image (ADR-004). Jobs are rows, so enqueueing is transactional with business data — an import job
cannot exist for a file whose upload rolled back.

| Job | Trigger | Idempotent on |
|---|---|---|
| `import.parse` | On upload | `import_job_id` + state machine |
| `import.apply` | On user confirmation | `import_job_id`, terminal `applied` |
| `inventory.reconcile` | Nightly + on demand | Read-only; writes only a discrepancy report |
| `idempotency.prune` | Daily | Naturally |
| `drafts.mark_stale` | Daily | Naturally |
| `notifications.dispatch` | Outbox poll | `outbox_event_id` |
| `credit.ageing_snapshot` | Nightly | `(business, date)` unique |

Job rows carry `attempts`, `max_attempts`, `run_after`, `last_error`. Failures back off
exponentially and land in a dead-letter state that is visible in monitoring — never silently
abandoned.

### 7.1 Transactional outbox

Any effect that must happen "because the sale committed" (notification, future webhook, future
e-invoice push) is written as an `outbox_events` row in the same transaction and dispatched by the
worker after commit, with at-least-once delivery and idempotent consumers.

---

## 8. Search architecture

Meeting FR-CAT-003/006 and NFR-PERF-001 without extra infrastructure:

- A generated/maintained `search_text` column per product concatenating normalised name, SKU, brand
  and aliases. Normalisation: lowercase, `unaccent`, strip `-`, `/`, `.`, and spaces for the
  part-number form, with the readable form kept separately.
- Indexes: B-tree on `normalized_sku`, B-tree on `lower(name) text_pattern_ops` for prefix,
  GIN `pg_trgm` on `search_text` for fuzzy/contains, plus `barcode` unique partial index.
- Ranking is computed in SQL as a tiered score (exact SKU 100, SKU prefix 80, name prefix 60, alias
  prefix 50, trigram similarity ×40) with stable tie-breaking by sales frequency then name.
- Alias rows are joined, not duplicated into the product row, so alias edits do not rewrite products.
- Hard limits: default 20, max 50 results; one query, no N+1; stock joined from
  `inventory_balances` for the caller's branch only.
- Escalation condition (documented so the decision is not re-litigated ad hoc): if p95 exceeds
  150 ms at the real catalog size after index tuning, evaluate a dedicated search engine — not before
  (ADR-005).

---

## 9. Offline synchronisation — backend responsibilities

Full protocol in `docs/OFFLINE-SYNCHRONIZATION.md`. Architecturally:

- `POST /api/v1/sync/operations` accepts a bounded batch of client operations, each with `op_id`,
  `type`, `payload`, `base_version`, `client_created_at`.
- The sync module **does not implement business logic**. It deduplicates on `op_id`, orders
  operations per draft by `client_seq`, and dispatches each to the owning module's normal service
  method. There is exactly one code path for "create draft", online or offline.
- Each operation gets an independent result (`accepted | replayed | conflicted | rejected`) with the
  server state or error attached. A bad operation never blocks the rest of the batch, except that
  operations for the *same draft* stop at the first failure to preserve ordering.
- `GET /api/v1/sync/catalog?since=<cursor>` returns a bounded delta of products/prices/stock for the
  client cache, with a server-decided row cap and a `reset_required` signal when the cursor is too
  old.
- Finalisation is deliberately **absent** from the sync surface (FR-OFFLINE-002).

---

## 10. Multi-user and multi-counter operation

| Concern | Mechanism |
|---|---|
| Two counters billing at once | Independent transactions; contention only at inventory rows and the series row |
| Draft ownership | `(branch_id, counter_id)` on the draft; same-counter second device handled by version conflict; cross-counter edit needs `billing.draft.takeover` |
| Who did what | Every financial row carries `created_by_user_id`, `counter_id`, `device_id` |
| Shift reconciliation | Payments carry counter and user, enabling cash-expected-vs-collected per counter per day |
| Branch isolation | `business_id` + `branch_id` predicates in the repository layer, asserted by tests |

---

## 11. Data protection and retention (architectural view)

- Finalised financial and stock data: **retained indefinitely**, append-only.
- Audit logs: retained indefinitely; `UPDATE`/`DELETE` revoked at the database role level.
- Drafts: cancelled/stale drafts archived, purged after `settings.drafts.retention_days` (default 180).
- Idempotency keys: 7 days. Sync operation records: 30 days. Both configurable.
- Uploaded files: retained with their import job for audit; configurable purge after 365 days.
- Customer PII is limited to name, business name, phone, address, vehicle numbers. No payment
  instrument data is ever stored (FR-PAY-001 records only a method and a free-text reference).

---

## 12. Integration points (present and future)

| Integration | v1 | Notes |
|---|---|---|
| Frontend PWA | Yes | OpenAPI is the contract; frontend types are generated from it |
| Printer | No direct integration | Browser print; backend supplies the print payload only |
| SMS/WhatsApp | No | Outbox + notifications tables exist so adding it is additive |
| Accounting software | No | CSV export is the v1 bridge |
| E-invoicing (IRP) | No | Blocked on tax confirmation; outbox is the designed insertion point |
| Payment gateway | No | Out of scope |

---

## 13. Operational responsibilities of the backend

- Expose `/healthz` (liveness: process up) and `/readyz` (readiness: DB reachable **and** Alembic
  head matches code) — a replica with pending migrations must not take traffic.
- Expose `/api/v1/openapi.json` for frontend type generation.
- Run migrations as a separate, explicit deploy step — never automatically on process start (ADR-016).
- Emit startup self-checks: tax mode, missing business identity fields, seeded-credential warning.
- Graceful shutdown on SIGTERM with in-flight request draining.

---

## 14. Known architectural risks

| Risk | Impact | Mitigation |
|---|---|---|
| Series row lock becomes a bottleneck | Slow finalisation at peak | Per-counter series (config only); monitored lock-wait metric |
| Offline expectations exceed what is delivered | Staff believe they can bill offline | Explicit scope in FR-OFFLINE-001/002, unmistakable UI state, staff training as a go-live gate |
| PDF import accuracy | Wrong stock from bad parsing | Human review mandatory; only `purchase_receipt` intent can touch stock |
| Single-region deployment | Downtime during provider incident | Honest 99.5% target; offline drafts keep the counter working; PITR backups |
| Unconfirmed tax rules | Invalid invoices, legal exposure | `estimate_only` default, hard gate on `gst_enabled`, go-live checklist |
| Denormalised `amount_paid` drifting | Wrong outstanding balances | Written only in the same transaction as allocations + invariant test + nightly reconciliation |
| Local IndexedDB is unencrypted | Data readable on a stolen counter PC | Device revocation, bounded cache, disk-encryption operational requirement |

---

## 15. Related documents

`docs/PRD.md` · `docs/DATA-MODEL.md` · `docs/API-SPECIFICATION.md` · `docs/SECURITY.md` ·
`docs/OFFLINE-SYNCHRONIZATION.md` · `docs/TESTING-STRATEGY.md` · `docs/DEPLOYMENT.md` ·
`docs/DECISIONS.md` · `docs/TRACEABILITY.md` · `../../bmfrontend/docs/ARCHITECTURE.md`
