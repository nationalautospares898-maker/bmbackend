# CLAUDE.md — `bmbackend`

> **Current Phase 2 design disposition (2026-10-30):** [Design Completion Addendum](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) is the current cross-cutting decision record. It resolves superseded open labels and defines the Phase 3 implementation baseline. Historical closure reports/addenda remain unchanged; legal, physical and operational gates remain gates. It does not represent implementation or test completion.


Persistent engineering context for AI-assisted work in this repository. Keep it loaded. It is
**rules**, not a specification — the specifications are in `docs/`.

---

## 1. What this is

Backend API for an automobile spare-parts shop (**National Auto Spares**): billing, inventory,
credit, purchases, imports, reporting. It is the **single source of truth for money and stock** and
must enforce every business rule independently of any client.

Real money, real inventory, one small business depending on it. Bias toward correctness and
auditability over cleverness or speed of delivery.

---

## 2. Current state — verified, not assumed

**As of 2026-10-31 this repository contains a working implementation.** `src/bm/` holds the
FastAPI application, the domain services and the ORM models; `migrations/` holds revisions
`0001`–`0021`; `tests/` holds the unit, API, integration, concurrency, invariant, structural,
property and load suites; `pyproject.toml`, `alembic.ini`, `docker-compose.yml` and
`.github/workflows/ci.yml` are all present.

Phase 1 and Phase 2 did produce documentation only, and the closure reports recording that remain
accurate **as records of those phases**. The sentence that used to stand here — that the repository
contains documentation only — stopped being true once Phase 3 began committing code, and the
30 October 2026 audit found it still in place.

**Current phase: Phase 3 (implementation) in progress.** See `docs/PHASE-3-PROGRESS.md` for the
milestone state. Phase 2 remains CLOSED as a planning phase.

⚠️ **Two things the audit found that this file previously implied were fine, and were not:**

- **CI had never run a single check.** Every job failed at `uv pip install --system …`, which is
  refused on an externally-managed interpreter (PEP 668), so lint, types, tests and migrations were
  all skipped on every commit. The workflow now builds a `uv venv` first. Do not read a green
  historical badge as evidence of anything before 31 October 2026.
- **`.gitignore` was hiding source.** An unanchored `storage/` matched `src/bm/infra/storage/` as
  well as the root upload directory, so that module was never pushed and a fresh clone could not
  import `bm.api.v1.imports`. The pattern is now anchored (`/storage/`), and a `packaging` CI job
  imports every `bm.*` module from a clean checkout so the next such omission fails loudly.
**★ Read `docs/PHASE-2-CLOSURE-ADDENDUM-1.md` first** — the owner's fourth answer set changed four
things. Then read `docs/PHASE-2-IMPLEMENTATION-PLAN.md` and check your milestone's
**"must not build yet"** row.

| The owner has now decided | Consequence |
|---|---|
| **No customer-visible invoice numbering** (OD-3 re-answered) | Keep the **internal** sale reference; **never print or render it**; never invent a replacement scheme. `AC-1a` withdrawn, gapless question **moot**. Statutory exposure is **OD-25** (go-live gate) |
| **Four primary actions** (OD-14 re-answered) | `Print Bill` · `Credit Bill` · `Save a Draft` · `Clear Draft`. **No fifth.** `Clear Draft` must never touch a finalised sale or an unresolved `pending_finalizations` row |
| **No decimals on `PRICE` / line `TOTAL` / `GRAND TOTAL`** | Follow the complete Phase 3 input, exact calculation, storage, no-rounding and display policy in `docs/PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md` §3; fractional monetary values are rejected before finalization |
| **The full bill layout** (OD-5 ✅ **RESOLVED 2026-10-30**) | **The workbook was supplied and inspected.** Layout, geometry, typography, borders, merges, number formats and both logos are **verified**; the header is **`QTY`**; the sheet has **no formulas**. ⚠️ It sets **no page setup at all**, so paper/orientation/scaling stay with **OD-6**, and the date order became **OD-26** |

**Current dispositions:** OD-3/OD-5/OD-7/OD-14/OD-21/OD-22/OD-23/OD-26 are design-resolved as requirements or documented defaults. OD-25 and the true owner, legal, physical and operational gates remain. Read the dated design addendum first; it supersedes stale status text in older records.
✅ **Both repositories are now connected to GitHub and the documentation baseline is pushed**, so
M1's repository gate is met — see `docs/PHASE-2-CLOSURE-ADDENDUM-2.md §7`.

Therefore:
- **Never claim a file, module, test or endpoint exists without reading it first.** This matters
  more now that most of them do exist, not less: the audit found a comment on `SEARCH_SQL`
  asserting that an "M5 catalog suite" proved the search ranking, and no such suite existed.
- The stack below **was** a proposal from `docs/DECISIONS.md`; it is now largely implemented. Where
  code and documentation disagree, the code is the fact — read it and reconcile the documentation
  rather than rewriting the code to match the docs.
- Do not say "tests pass" unless you ran them and saw output.

---

## 3. Proposed stack (ADR-002…005)

Python 3.11 · FastAPI · Pydantic v2 (`extra="forbid"`) · SQLAlchemy 2.0 **sync** · Alembic ·
PostgreSQL 16 · `pg_trgm` + `unaccent` + `citext` · Postgres job queue via `FOR UPDATE SKIP LOCKED`
(**no Redis, no Celery, no Kafka**) · Docker · `uv` · pytest + Hypothesis · ruff + mypy

Do not introduce microservices, Kubernetes, a message broker, GraphQL, a search engine or an ORM
other than SQLAlchemy without a new ADR.

---

## 4. Module boundaries (`docs/ARCHITECTURE.md §3–4`)

```
api  →  domain  →  infra          (no reverse imports; CI enforces this)
```

- `api/` — routers and schemas. **Zero business logic.** No money arithmetic, no stock decisions.
- `domain/<module>/` — services, rules, invariants. Cross-module calls go through
  `domain/<other>/service.py` only — never another module's repository or ORM model.
- `infra/` — sessions, repositories, storage, queue, logging. Must not import `domain`.
- ORM models stay inside repositories; they are never a response type.

**Table ownership is exclusive.** `inventory` is the only writer of `inventory_balances` and
`stock_movements`. `sales` owns invoices and drafts. `credit` owns payments and allocations. Never
write another module's tables.

---

## 5. Invariants — never break these

1. **Finalised financial records are immutable.** No `UPDATE`/`DELETE` on `invoices` (except the
   enumerated cancellation and derived-cache columns), `invoice_items` (except `returned_quantity`),
   `stock_movements`, `payments`, `audit_logs`. Database privileges enforce it; do not try to work
   around them. Corrections are **cancellation, return, credit note or reversal** (ADR-015).
2. **The server computes all money.** `client_computed_total` is a cross-check that produces
   `422 TOTAL_MISMATCH`, never a value to trust.
3. **Money is `Decimal` / `NUMERIC` / JSON string.** No `float` in `domain/`, no float columns, no
   JSON numbers for money (ADR-013). Amounts `NUMERIC(14,2)`, unit prices `(14,4)`, quantities
   `(14,3)`.
4. **Every stock change is a movement row in the same transaction.** Route everything through
   `InventoryService.apply_movements`. Never `UPDATE inventory_balances` directly.
5. **No overselling** unless the caller holds the permission `inventory.oversell.override` and
   supplies a reason of ≥10 characters, which is persisted and audited. The oversell concept has
   **five deliberately different canonical spellings** (permission, audit action, settings key,
   DB columns, API field) — `docs/DATA-MODEL.md §3.14` is the single authoritative table. Never use
   one vocabulary's spelling where another is expected.
6. **Finalisation is one transaction**: validate → recompute money → lock stock → check → allocate
   number → insert invoice + items + movements + payment → audit → outbox → commit.
7. **Lock order is fixed**: `inventory_balances` (ascending `product_id`) → `invoice_series` →
   `customers` → `invoices`. Deviating creates deadlocks (ADR-008).
8. **The INTERNAL sale reference comes from a `FOR UPDATE` on `invoice_series` inside the
   transaction**, never from a sequence (ADR-009). ⚠️ **It is internal-only.** [OD-3](docs/OWNER-DECISIONS.md#od-3)
   is re-answered — *"No need of invoice numbering."* It must **never** be rendered to a customer and
   must **not** appear in `print_payload`. Do not invent a replacement visible numbering scheme.
9. **Idempotency is required** on `POST /sales:finalize`, `/payments`, `/payments/{id}:reverse`,
   `/returns`, `/purchases/{id}:confirm`, `/inventory/adjustments`, `/invoices/{id}:cancel`,
   `/imports/{id}:apply`. Same key + same payload → stored response. Same key + different payload →
   `409 IDEMPOTENCY_KEY_REUSE`.
10. **`payment_status` and `amount_paid` are derived**, written only in the same transaction as the
    allocations. No endpoint sets them, and there is **no "mark as paid" endpoint** — `paid` is
    reached only when allocations sum to the total. There is no "paid" checkbox in this system
    (ADR-022).
10a. **Cash and credit finalise through the SAME engine and endpoint** (FR-CREDIT-008, ADR-033).
    **`Credit Bill`** (renamed from `Save / Credit Bill` by OD-14's re-answer) **finalises a sale
    immediately** — it is not a draft-save and has no preparatory step. Credit requires a **persisted
    `customer_id`**; a free-text name is rejected (FR-CREDIT-009). `payment` is **optional** for
    credit.
10b. **Payment collection never moves stock.** `POST /payments` writes payment + allocation rows
    only. Sale finalisation and payment collection are distinct financial events (FR-CREDIT-010).
    Never add a stock effect to a payment path.
11. **Drafts never reserve stock** (ADR-006).
12. **No side effects inside a write transaction** — no HTTP calls, no file I/O, no job execution.
    Use `outbox_events`.
13. **Tenant scope comes from the token**, never from the request body. Cross-tenant access returns
    `404`, not `403`.
14. **Audit every state change** in the same transaction as the change, with before/after values for
    sensitive fields. Never log secrets.
15. **No offline sale finalisation — cash or credit.** The sync operation registry contains exactly
    `draft.upsert`, `draft.delete`, `customer.upsert` — adding a financial type needs ADR-011
    revisited and owner approval. **`Credit Bill` is a sale and is refused offline exactly
    like `Print Bill`** (`docs/OFFLINE-SYNCHRONIZATION.md §3.1` invariant 2a). A finalisation whose
    response was lost is a **different thing** from an offline operation: it is retried with its
    original idempotency key against `POST /sales:finalize`, never routed through
    `/sync/operations`.
16. **Tax stays `estimate_only`** until the gate in `FR-TAX-003` passes. Never emit "Tax Invoice",
    never invent a GSTIN, a rate or an HSN code. In `estimate_only`, `print_payload.totals.tax` is
    `null` (not `"0.00"`).

---

## 6. API conventions (`docs/API-SPECIFICATION.md`)

- `/api/v1`, JSON, **`snake_case`** everywhere (ADR-012).
- Money/quantity/percent as **strings**.
- Error envelope on **every** non-2xx:
  `{"error": {"code", "message", "details", "request_id", "retryable"}}`.
- Clients branch on `code`. Codes are `SCREAMING_SNAKE_CASE`, additive-only; adding one requires
  updating the registry in `API-SPECIFICATION.md §4.2` **and** `../bmfrontend/docs/API-INTEGRATION.md`.
- Every route declares its required permission in a dependency. A route without one fails CI.
- Cursor pagination with a hard max page size (50 search / 200 list). No unbounded queries.
- `404` for a resource outside the caller's tenant — never leak existence.
- Status codes: `409` for business-rule conflicts, `422` for semantically invalid input.

---

## 7. Database and migration rules

- Alembic, one logical change per migration, with a **tested `downgrade()`**. No downgrade, no merge.
- **Migrations never run on application start** (ADR-016). `/readyz` fails on head mismatch.
- **Expand → migrate → contract** for any table holding live financial data. Never rename or drop a
  column in the same release that stops using it.
- `CREATE INDEX CONCURRENTLY` on populated tables, in a non-transactional migration.
- Data migrations touching financial rows must be idempotent and log row counts. **Never hand-edit
  `invoices`, `invoice_items`, `payments` or `stock_movements` in SQL** — use the application's
  reversal workflows.
- Seeds cover permissions, system roles and units **only**. Never seed a business, tax rates,
  products or demo data.
- Prefer a database `CHECK` over a comment: if an invariant can be a constraint, make it one.

---

## 8. Authentication and authorisation

- Argon2id passwords. Access JWT 15 min; refresh token opaque, rotating, device-bound,
  `HttpOnly`/`Secure`/`SameSite=Lax` cookie. **Tokens never in `localStorage`.**
- Refresh reuse → revoke the whole token family + audit.
- `AuthContext` (`user, business, branch, counter, device, permissions`) flows to every repository;
  tenant predicates are built there, not by callers.
- Permission names are the strings in `docs/PRD.md §3.2`. Do not invent new ones without updating
  that table and the role mapping.
- UI-level hiding is **not** a control. Re-assert permissions in the service for destructive actions.
- Omit (do not null) `cost_price`/margin fields without `catalog.cost.read`.

---

## 9. File imports

- Allow-list `.xlsx`, `.csv`, `.pdf` — matched on extension **and** magic bytes **and** declared
  content type. Any disagreement → `415`.
- Size cap enforced streaming; never buffer an unbounded upload.
- Generated storage keys. **The original filename never touches a filesystem path.**
- Stored outside any served path. Never executed. Parsed only in the worker, with row/page/time caps
  and zip-bomb limits.
- `file_sha256` unique per business → `409 DUPLICATE_FILE`.
- `intent: price_list` must produce **zero** stock movements, ever.
- Rows with `ambiguous`, `new_product`, `duplicate_in_file` or `invalid` status cannot be applied
  without a per-row human decision.

---

## 10. Testing

- **Real PostgreSQL. SQLite is prohibited** (ADR-017) — it cannot test row locks, `NUMERIC`,
  partial indexes, enums or column privileges, which is most of what matters here.
- Fixtures build state through **real service paths**, not raw inserts (except reference data and
  deliberately corrupt states).
- Every `NFR-DATA-*` invariant has a named test. Every documented error code is produced by a test.
- Concurrency tests use real parallel connections with barriers — not assertions that a lock
  statement appears in the source.
- Coverage: ≥85% overall; **≥95% branch** on `money`, `sales`, `inventory`, `credit`, `sync`.
- Commands: `uv run pytest`, `uv run pytest tests/unit -q`, `uv run ruff check .`,
  `uv run mypy src/`. **Do not claim any of these passed unless you ran them.**

---

## 11. Environment variables

- Infrastructure only: `BM_DATABASE_URL`, `BM_JWT_SIGNING_KEY`, `BM_PASSWORD_PEPPER`,
  `BM_IP_HASH_PEPPER`, `BM_CORS_ALLOWED_ORIGINS`, `BM_STORAGE_*`, `BM_LOG_*`, timeouts, pool sizes.
  Full list in `docs/DEPLOYMENT.md §3`.
- **Business rules are database `settings` rows, not env vars** — tax mode, draft limits, credit
  terms, discount caps, cancellation window, invoice prefixes. The owner must be able to change them
  without a deploy.
- Startup fails fast on a missing or malformed required variable.
- `.env` is git-ignored; only `.env.example` with placeholders is committed. Never print, log or echo
  a secret.
- `BM_MIGRATION_DATABASE_URL` exists only in the migration job, never in the API container.

---

## 12. Things you must not do

- Do not invent business facts: tax status, GSTIN, rates, HSN codes, legal shop name, address, phone,
  paper size, invoice format, credit terms, or the owner's current workflow. If it is not in
  `docs/PRD.md §4` as confirmed, it is an open question — add it to `docs/OWNER-DECISIONS.md` (the
  authoritative register) with options, consequences and a recommended default, and ask.
- **Do not present a recommended default as an owner decision**, and **do not treat a selected
  preference as a legal/accounting approval** — `docs/OWNER-DECISIONS.md` defines the four status
  levels. As of 2026-10-30: **OD-3, OD-5, OD-14, OD-21 are settled**; **OD-3** (gapless undecided),
  **OD-7** (precision/method undecided) and **OD-22** (placement unverified) still block implementation of the numbering,
  print-template and money modules. Everything else may be built on its default.
  - ⚠️ **Two of those blockages were re-scoped in Phase 2 and are narrower than they read** — both
    are now `BLOCKS-TESTING`, not `BLOCKS-IMPLEMENTATION`. `P2-D-02`: the numbering *design* may be
    built now (in-transaction allocation satisfies either answer to OD-3); OD-3 gates the **policy
    statement and its release criterion**. `P2-D-03`: the money engine may be built now as a
    **policy-parameterised algorithm**; OD-7 gates the **policy values and the expected-value
    assertions**. OD-5 and OD-22 still block the print template's **bottom section** and the
    **final placement** of the number / payment block — nothing else.
  - **Invoice numbering vocabulary — keep these three distinct.** **Unique** (settled, enforced by
    `uq_invoices__series_sequence`) · **sequential** (settled, owner-selected) · **gapless**
    (**[OD-3](docs/OWNER-DECISIONS.md#od-3), UNDECIDED**). Gaplessness follows from in-transaction
    allocation and is the implemented default, but it is **not** an owner-approved guarantee, **not**
    a business invariant and **not** a release criterion. `AC-1a` and
    `test_no_gaps_in_committed_numbers` are **informational until OD-3 is answered**. *(Audit CF-1
    corrected this across `docs/PRD.md`, `docs/DATA-MODEL.md`, `docs/DECISIONS.md` (ADR-009),
    `docs/ARCHITECTURE.md`, `docs/TESTING-STRATEGY.md`, `docs/TRACEABILITY.md`,
    `docs/OWNER-DECISIONS.md` and `README.md` during Phase 2 closure.)*
- **No approval date, approver name, accounting sign-off or legal review exists.** Never record one.
- Do not bypass a business rule for convenience, "just in tests", or behind a debug flag.
- Do not add an endpoint that mutates a finalised financial record.
- Do not add a sync operation type that moves money or stock.
- Do not weaken an invariant test to make a build green.
- Do not make reconciliation auto-repair a drifted balance — it must **alert**, because drift means a
  bug to find.
- Do not put a database credential or any privileged secret in `bmfrontend`.
- Do not commit, push, deploy, or run a migration against a shared database unless explicitly asked.
- Do not claim external documentation was consulted, a repository was inspected, or a test was run,
  unless it actually happened.

---

## 13. When a requirement is ambiguous

1. Check `docs/PRD.md §4` (confirmed) and `§5` (open questions) first — it may already be recorded.
2. If it is a business fact, **do not guess**: add it to `§5.1`/`§5.2` with options, consequences and
   a recommended default, and surface it.
3. If it is a technical choice, write an ADR in `docs/DECISIONS.md` (next free number, status
   `proposed`) with options and rationale.
4. If it affects the frontend too, mark the ADR **shared** and add the matching stub in
   `../bmfrontend/docs/DECISIONS.md` in the same change set (ADR-021).

---

## 14. Documentation map

| Need | Document |
|---|---|
| What to build, requirement IDs, priorities | `docs/PRD.md` |
| **Business decisions (authoritative)** | `docs/OWNER-DECISIONS.md` |
| **Phase 1 exit criteria + gates carried forward** | `docs/PHASE-1-EXIT-CRITERIA.md` |
| **Phase 2 closure: findings, corrections, verdict** | `docs/PHASE-2-CLOSURE-REPORT.md` |
| **★ Workbook inspection + repository connection (READ FIRST)** | `docs/PHASE-2-CLOSURE-ADDENDUM-2.md` |
| **Owner answers four–seven** | `docs/PHASE-2-CLOSURE-ADDENDUM-1.md` |
| **What to build next, in order** | `docs/PHASE-2-IMPLEMENTATION-PLAN.md` (M1…M28) |
| **Target design, source-of-truth hierarchy, diagrams** | `docs/PHASE-2-IMPLEMENTATION-BLUEPRINT.md` |
| **Which requirements are confirmed vs default** | `docs/PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md` |
| Phase 2 technical decisions (`P2-D-*`) | `docs/PHASE-2-DECISION-REGISTER.md` |
| Risks and failure modes (`R-*`) | `docs/PHASE-2-RISK-REGISTER.md` |
| What the owner still has to decide | `docs/PHASE-2-OWNER-DECISIONS-REQUIRED.md` |
| Requirement → decision → milestone → test → gate | `docs/PHASE-2-TRACEABILITY-MATRIX.md` |
| Canonical identifier vocabularies (oversell etc.) | `docs/DATA-MODEL.md §3.14` |
| Modules, transactions, locking, jobs | `docs/ARCHITECTURE.md` |
| Tables, constraints, indexes, invariants | `docs/DATA-MODEL.md` |
| Endpoints, schemas, error codes | `docs/API-SPECIFICATION.md` |
| Threat model, auth, isolation, uploads | `docs/SECURITY.md` |
| Offline scope and sync protocol | `docs/OFFLINE-SYNCHRONIZATION.md` |
| Test layers and invariant suite | `docs/TESTING-STRATEGY.md` |
| Environments, migrations, backups, go-live | `docs/DEPLOYMENT.md` |
| Why a choice was made | `docs/DECISIONS.md` |
| Requirement → endpoint → table → test | `docs/TRACEABILITY.md` |
| Client behaviour | `../bmfrontend/docs/` |

When documentation and code disagree, **the code is the fact** — read it, then fix the documentation
and say that you did.
