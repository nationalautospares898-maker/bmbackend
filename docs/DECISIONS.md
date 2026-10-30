# Architecture Decision Records — `bmbackend`

| Field | Value |
|---|---|
| Status | **Phase 1 — all records are `proposed` unless marked otherwise** |
| Numbering | **Global across both repositories.** ADR numbers are never reused. |
| Last updated | 2026-10-29 |

---

## How to read this file

| Category | Meaning |
|---|---|
| 🟢 **Confirmed owner requirement** | Stated by the business owner. Not an ADR — listed in `PRD.md §4` as C-1…C-10. Cannot be changed by engineering. |
| 🔵 **Recommended architectural decision** | Engineering's call. Recorded below with `status: proposed` until reviewed, then `accepted`. |
| 🟡 **Assumption awaiting validation** | `PRD.md §5.3` (A-1…A-5). Safe default chosen; revisit if invalidated. |
| 🔴 **Unresolved business decision** | `PRD.md §5.1`/`§5.2` (Q-*). Needs the owner or the accountant. Some block implementation. |
| ⚪ **Future enhancement** | `PRD.md §13`. Deliberately not decided now. |

**Ownership.** Each ADR has an owning repository. The owner holds the full record; the other
repository carries a one-line stub pointing here (`§Stubs`). An ADR marked **shared** affects both
repositories and its consequences must be reflected in both documentation sets.

**Status values:** `proposed` → `accepted` → (`superseded by ADR-nnn` | `rejected`).

**Owner approval column**: whether the business owner must sign off, as opposed to it being purely a
technical choice.

---

## Index

| ADR | Title | Owner repo | Status | Owner approval needed |
|---|---|---|---|---|
| [001](#adr-001) | Modular monolith, not microservices | backend | proposed | No |
| [002](#adr-002) | Python + FastAPI for the backend | backend | proposed | No |
| [003](#adr-003) | Synchronous SQLAlchemy sessions | backend | proposed | No |
| [004](#adr-004) | PostgreSQL-backed job queue, no Redis or broker | backend | proposed | No |
| [005](#adr-005) | PostgreSQL trigram search, no search engine | backend | proposed | No |
| [006](#adr-006) | Drafts do not reserve stock | **shared** | proposed | **Yes** |
| [007](#adr-007) | Draft conflicts are resolved by the user, never merged silently | **shared** | proposed | No |
| [008](#adr-008) | READ COMMITTED with explicit row locks, not SERIALIZABLE | backend | proposed | No |
| [009](#adr-009) | Invoice numbers from a locked series row, allocated in-transaction | backend | proposed | **Yes** (Q-TAX-3 / OD-3 — *whether gaplessness is required*, not the mechanism) |
| [010](#adr-010) | Two separate client queues: draft outbox vs pending finalisations | **shared** | proposed | No |
| [011](#adr-011) | No offline sale finalisation in v1 | **shared** | proposed | **Yes** |
| [012](#adr-012) | `snake_case` JSON throughout the API | **shared** | proposed | No |
| [013](#adr-013) | Money and quantities as JSON strings | **shared** | proposed | No |
| [014](#adr-014) | Browser printing is primary; server-side PDF deferred | **shared** | proposed | **Yes** |
| [015](#adr-015) | Financial records are immutable; corrections are reversals | backend | proposed | **Yes** |
| [016](#adr-016) | Migrations are an explicit deploy step | backend | proposed | No |
| [017](#adr-017) | Real PostgreSQL in tests; SQLite prohibited | backend | proposed | No |
| [018](#adr-018) | No Row-Level Security in v1 | backend | proposed | No |
| [019](#adr-019) | Single API replica at launch | backend | proposed | No |
| [020](#adr-020) | Tax defaults to `estimate_only` behind a hard gate | **shared** | proposed | **Yes** (Q-TAX-1/2) |
| [021](#adr-021) | Two repositories, no third; documentation lives with its owner | **shared** | proposed | No |
| [022](#adr-022) | Payment status is derived, with a same-transaction cache | backend | proposed | No |
| 023–032 | Frontend-owned decisions | frontend | — | see `../../bmfrontend/docs/DECISIONS.md` |

---

<a id="adr-001"></a>
## ADR-001 — Modular monolith, not microservices

**Status** proposed · **Owner** backend · **Related** NFR-MAINT-001, `ARCHITECTURE.md §1`

**Context.** One shop, ~5 concurrent users, ~500 invoices/day, a small development team, and an
owner who needs low running costs. The domain is tightly coupled around a single transaction
(sale = invoice + stock + payment) that must be atomic.

**Options.**
1. **Modular monolith** — one deployable, one database, enforced module boundaries.
2. Microservices per domain (catalog, sales, inventory, credit).
3. Unstructured monolith with no enforced boundaries.

**Decision.** Option 1.

**Rationale.** The central invariant — invoice, stock movement and payment commit together — is a
single ACID transaction. Splitting inventory from sales would turn it into a distributed saga with
compensating transactions, which is strictly harder to make correct and would make the
no-overselling guarantee probabilistic. Option 3 is how a monolith becomes unmaintainable; the
import-boundary linter is what distinguishes 1 from 3.

**Consequences.** One deploy unit; one database to back up; simple local development. Module
boundaries must be enforced mechanically (CI import linter) or they will erode. Scaling is vertical
plus replicas (ADR-019). Splitting out a service later is possible because boundaries are explicit,
but is not planned.

**Revisit when** a genuinely independent workload appears (e.g. a heavy analytics or e-invoicing
integration) that needs separate scaling or an independent release cadence.

---

<a id="adr-002"></a>
## ADR-002 — Python + FastAPI for the backend

**Status** proposed · **Owner** backend · **Related** `ARCHITECTURE.md §2`

**Context.** No code exists in either repository. Python 3.11.9, Node 24.11, uv and Docker are all
present on the development machine. The project brief names FastAPI, Python, PostgreSQL, SQLAlchemy,
Alembic and Docker as preferred candidates.

**Options.**
1. **Python 3.11 + FastAPI + SQLAlchemy + Alembic.**
2. TypeScript + NestJS/Fastify + Prisma — one language across both repositories.
3. Django + DRF.

**Decision.** Option 1.

**Rationale.** It matches the stated preference, and its concrete advantages here are real rather
than stylistic: `Decimal` arithmetic is first-class (money correctness is the dominant risk),
spreadsheet/PDF parsing for imports is far better served (`openpyxl`, `pdfplumber`), and FastAPI's
OpenAPI output lets the frontend generate types instead of hand-writing them (ADR-029). Option 2's
single-language appeal is real but does not outweigh money handling and import tooling. Option 3
brings an admin and ORM that fight the explicit-transaction style this design depends on.

**Consequences.** Two languages across the workspace. Developers need both toolchains. `Decimal`
discipline must be enforced by lint, since Python will happily mix `float` in.

**Revisit when** — unlikely; this would be a rewrite, not a revision.

---

<a id="adr-003"></a>
## ADR-003 — Synchronous SQLAlchemy sessions

**Status** proposed · **Owner** backend · **Related** `ARCHITECTURE.md §5`

**Context.** FastAPI supports both. The workload is database-bound with no fan-out to slow external
services. The critical code path uses explicit `SELECT … FOR UPDATE` and precise transaction
boundaries.

**Options.** 1. **Sync sessions in a threadpool.** 2. Async sessions throughout.

**Decision.** Option 1.

**Rationale.** Transaction and lock semantics are far easier to read, review and test in synchronous
code; async adds `await` noise and a class of subtle bugs (session sharing across tasks, accidental
concurrency inside a transaction) for throughput this application will never need — at 500
invoices/day the bottleneck is nowhere near connection concurrency. Correctness of the finalisation
transaction outweighs theoretical scalability.

**Consequences.** Throughput bounded by threadpool size and pool size — orders of magnitude above
the requirement. Mixing async libraries requires care. If a future integration needs genuine async
fan-out it can be an async endpoint with its own session style.

**Revisit when** a workload appears that is I/O-bound on external services rather than on Postgres.

---

<a id="adr-004"></a>
## ADR-004 — PostgreSQL-backed job queue, no Redis or broker

**Status** proposed · **Owner** backend · **Related** FR-IMP-005, `ARCHITECTURE.md §7`

**Context.** Needed for import parsing, nightly reconciliation, pruning and outbox dispatch. Jobs
must be durable; an import job must not exist if its upload rolled back.

**Options.** 1. **`jobs` table consumed with `FOR UPDATE SKIP LOCKED`.** 2. Celery/arq + Redis.
3. In-request background tasks.

**Decision.** Option 1.

**Rationale.** It removes an entire piece of infrastructure to provision, secure, monitor and back
up, and gains something Redis cannot: **enqueueing is transactional with the business data**, which
is exactly what the transactional outbox needs. `SKIP LOCKED` is a mature, well-understood pattern
and multiple workers are safe. Option 3 loses jobs on restart and is unacceptable for a financial
system.

**Consequences.** Polling (1 s interval) rather than push — irrelevant for minute-scale jobs. Queue
depth must be monitored. Very high job throughput would eventually need a real broker; this
application is nowhere near that.

**Revisit when** jobs exceed a few per second sustained, or sub-second dispatch latency is required.

---

<a id="adr-005"></a>
## ADR-005 — PostgreSQL trigram search, no dedicated search engine

**Status** proposed · **Owner** backend · **Related** FR-CAT-003…006, NFR-PERF-001, `ARCHITECTURE.md §8`

**Context.** Autocomplete must respond from the first keystroke across up to ~100k SKUs, matching
names, part codes and aliases, tolerant of separators and case.

**Options.** 1. **`pg_trgm` + `unaccent` + prefix indexes, ranked in SQL.** 2. Elasticsearch or
Meilisearch. 3. Client-side search over a downloaded catalog.

**Decision.** Option 1.

**Rationale.** 100k rows is small. A GIN trigram index plus `text_pattern_ops` prefix indexes answer
these queries in single-digit milliseconds, with no second datastore to run, secure, back up or keep
in sync — and no window where search results disagree with the database. Option 3 is explicitly
prohibited by FR-CAT-005 (and would ship the entire price list to every browser).

**Consequences.** Ranking logic lives in SQL and is less expressive than a search engine's. Index
maintenance cost on product writes — negligible at this write rate. The escalation trigger is
written down (`DEPLOYMENT.md §9`): search p95 above 150 ms at real catalog size **after** index
tuning, measured, not assumed.

---

<a id="adr-006"></a>
## ADR-006 — Drafts do not reserve stock

**Status** proposed · **Owner** shared · **Owner approval: YES** · **Related** FR-DRAFT-005, C-3, C-5

**Context.** 20+ drafts may be open per counter and may sit open for a long time. Stock must not be
oversold. Should opening a draft hold the stock it lists?

**Options.**
1. **No reservation.** Stock is checked and decremented only at finalisation.
2. Reserve on add-to-draft, release on removal/timeout.
3. Soft reservation: show "N reserved in other drafts" but don't enforce it.

**Decision.** Option 1, with Option 3 as a possible later UI enhancement.

**Rationale.** With 20 drafts per counter across several counters, Option 2 would make large amounts
of stock invisible to everyone else, and every abandoned draft would strand inventory until a timeout
expired. It also contradicts physical reality: in a spare-parts shop the part is picked off the
shelf, and a draft is a note-in-progress, not a claim on goods. Option 2 would add a whole
reservation-expiry subsystem to solve a problem the shop does not have.

**Consequences.** A draft can fail at finalisation with `INSUFFICIENT_STOCK` — which is why that
error carries per-line `requested`/`available` and the UI must handle it well (E-3, `PRICE_CHANGED`'s
sibling). Stock figures shown while editing are informational. This is the honest behaviour: the
system tells you the truth at the moment it matters.

**Owner approval needed** because it determines what staff see when two counters want the same part.
If the owner insists on reservation, Option 3 (visibility without enforcement) is the cheap middle
ground; full Option 2 needs its own design.

---

<a id="adr-007"></a>
## ADR-007 — Draft conflicts are resolved by the user, never merged silently

**Status** proposed · **Owner** shared · **Related** FR-DRAFT-004, FR-OFFLINE-006, E-4,
`OFFLINE-SYNCHRONIZATION.md §5`

**Context.** The same counter may edit one draft from two devices, or a device may edit offline while
another syncs.

**Options.** 1. **Version check + explicit user choice.** 2. Last-writer-wins silently.
3. Automatic line-level merge. 4. Server-side pessimistic draft locks.

**Decision.** Option 1, on top of counter-level ownership that prevents most conflicts entirely.

**Rationale.** This is a bill — a wrong one costs money and trust. Option 2 silently deletes lines
someone typed. Option 3 produces plausible-looking wrong bills (doubled quantities, resurrected
deleted lines) which is worse than a visible conflict because nobody checks. Option 4 breaks the
moment a device goes offline holding a lock. Conflicts are rare under counter ownership, so the cost
of asking is one click in an uncommon case.

**Consequences.** The frontend must implement a real conflict UI (keep mine / keep theirs / compare),
which is specified in `../../bmfrontend/docs/UI-UX-SPECIFICATION.md`. The server must always return
`current_state` on a 409 so the client can render both sides.

---

<a id="adr-008"></a>
## ADR-008 — READ COMMITTED with explicit row locks, not SERIALIZABLE

**Status** proposed · **Owner** backend · **Related** FR-INV-004, NFR-DATA-007, `ARCHITECTURE.md §5.1`

**Context.** Concurrent finalisations must not oversell. PostgreSQL offers `SERIALIZABLE` (with
retries) or explicit locking under the default `READ COMMITTED`.

**Options.** 1. **READ COMMITTED + `SELECT … FOR UPDATE` in a deterministic order.**
2. SERIALIZABLE with automatic retry on serialisation failure.

**Decision.** Option 1.

**Rationale.** Explicit locks give predictable blocking: the second counter waits a few
milliseconds and then gets a clear, deterministic answer. `SERIALIZABLE` produces serialisation
failures that must be retried, and a retry loop on an endpoint that charges a customer is a dangerous
place to be subtle — it interacts awkwardly with idempotency and with "did my sale go through?".
Explicit locks are also far easier to test deliberately.

**Consequences.** **Lock ordering becomes a correctness requirement**, not a style preference:
`inventory_balances` by ascending `product_id` → `invoice_series` → `customers` → `invoices`, on
every path. A deadlock-probe test (50 random parallel multi-product sales, zero deadlocks) guards
it. `lock_timeout` ensures a pathological wait fails fast rather than freezing a counter.

---

<a id="adr-009"></a>
## ADR-009 — Invoice numbers allocated from a locked series row inside the transaction

**Status** proposed · **Owner** backend · **Owner approval: YES (Q-TAX-3)** · **Related** FR-SALE-004,
NFR-DATA-005

**Context.** Indian GST rules generally require consecutive serial numbering of invoices, but the
shop's tax status is unconfirmed (Q-TAX-1). A PostgreSQL `SEQUENCE` is non-transactional: a
rolled-back sale permanently burns a number, producing gaps.

**Options.**
1. **Counter row in `invoice_series`, locked `FOR UPDATE` inside the finalisation transaction.**
2. `SEQUENCE` — fast, no contention, but gaps on any rollback.
3. Post-hoc renumbering — rejected outright: renumbering issued documents is falsifying records.

**Decision.** Option 1 — **as the implemented mechanism.** Whether its *gaplessness* is a required
business property is **[OD-3](OWNER-DECISIONS.md#od-3), still undecided**; this ADR does not settle
it and must not be cited as if it had.

**Rationale.** The number is only consumed if the sale commits, which is the only way to get truly
gapless numbering. The cost — finalisations on one series serialise at a single-row update — is
microseconds of lock hold against a design load of a few sales per minute. Option 2 would be chosen
if the owner's accountant confirms gaps are acceptable, which is why this needs sign-off.

> **Phase 2 finding (P2-D-02) — the choice is safe to build before OD-3 is answered.** The options
> are **directionally asymmetric**: Option 1 satisfies *both* a gapless requirement and a
> gap-tolerant one, because gaplessness is strictly stronger. Options 2 and 3 satisfy only a
> gap-tolerant requirement. So building Option 1 now is never wasted work whichever way OD-3 is
> answered, and this ADR's original premise — that *"building one and switching later wastes the
> work"* — holds only in the reverse direction. What OD-3 genuinely gates is whether gaplessness may
> be stated as a **business invariant and release criterion** (NFR-DATA-005, AC-1a), not whether the
> module can be built.

**Consequences.** One hot row per series. The documented remedy for contention is **per-counter
series** (`counters.invoice_series_id`), which is configuration, not a schema change. Note the
interaction with ADR-011: per-counter number *blocks* for offline finalisation would reintroduce
gaps, so Q-TAX-3's answer constrains whether offline billing is ever possible.

---

<a id="adr-010"></a>
## ADR-010 — Two separate client queues: draft outbox vs pending finalisations

**Status** proposed · **Owner** shared · **Related** FR-OFFLINE-003, E-1, E-2,
`OFFLINE-SYNCHRONIZATION.md §3`

**Context.** Draft edits made offline must be replayed later. Sale finalisations whose response was
lost must also be retried. These look similar and are tempting to unify.

**Options.** 1. **Two distinct queues with different rules.** 2. One generic operation queue.

**Decision.** Option 1.

**Rationale.** They have fundamentally different safety properties. A draft operation is *always*
safe to replay and may be *created* offline. A finalisation may *already have charged the customer*
and must *never* be created offline. One queue would invite a future change that lets a sale
accumulate offline "like any other operation" — the exact failure mode this design is built to
prevent. Separation makes the rule structural: the sync operation registry has no finalisation type,
asserted by a test (AC-OFF-8).

**Consequences.** Two IndexedDB stores and two retry policies in the frontend. The UI must present a
pending finalisation very differently from a pending draft sync — one is "not confirmed, do not hand
over a bill", the other is routine.

---

<a id="adr-011"></a>
## ADR-011 — No offline sale finalisation in v1

**Status** proposed · **Owner** shared · **Owner approval: YES** · **Related** FR-OFFLINE-002,
FR-OFFLINE-013, C-1, C-3, `OFFLINE-SYNCHRONIZATION.md §1, §11`

**Context.** The owner asked for offline capability (C-1) **and** no overselling (C-3) **and**
auditable invoices (C-7). Fully offline billing cannot satisfy all three: two disconnected counters
cannot check the same last unit, and neither can allocate a server invoice number.

**Options.**
1. **Offline drafts only; finalisation requires the server.**
2. Offline finalisation with per-counter stock allocation and per-counter number blocks.
3. Offline finalisation with no stock checking — "fix it later".

**Decision.** Option 1 for v1. Option 2 is designed-but-deferred (`OFFLINE-SYNCHRONIZATION.md §11`).
Option 3 is rejected.

**Rationale.** Option 3 abandons C-3 and produces negative stock, duplicate numbers and invoices that
cannot be reconciled — it trades a visible inconvenience for invisible corruption. Option 2 is a
legitimate design but costs: fragmented stock, allocation expiry and reconciliation, and
**permanently gapped invoice numbering**, which may be statutorily unacceptable (Q-TAX-3). Option 1
keeps every confirmed requirement intact and still removes the real pain — no lost work, no
re-typing.

**Consequences.** During an outage staff capture drafts and can print an un-numbered, watermarked
counter slip; the invoice is issued when connectivity returns, dated at finalisation time. **Staff
training on this is a go-live gate.** If the owner rejects this trade-off, Option 2 must be designed
and approved before implementation — it is not a small addition.

**Owner approval needed** — this is the single biggest expectation gap between "offline capability"
as a phrase and what is being built.

---

<a id="adr-012"></a>
## ADR-012 — `snake_case` JSON throughout the API

**Status** proposed · **Owner** shared · **Related** `API-SPECIFICATION.md §1.1`

**Context.** Backend is Python/Postgres (`snake_case`); frontend is TypeScript (conventionally
`camelCase`).

**Options.** 1. **`snake_case` everywhere, types generated from OpenAPI.** 2. `camelCase` in the API.
3. Automatic conversion at the boundary.

**Decision.** Option 1.

**Rationale.** Any conversion layer has exactly one product beyond cosmetics: a new class of bug
where a field is renamed on one side only. Because the frontend generates its types from the OpenAPI
document (ADR-029), nobody hand-writes field names, so the casing is invisible in practice.

**Consequences.** TypeScript code contains `snake_case` properties for API DTOs. The frontend maps to
its own `camelCase` view models only where it genuinely reshapes data — a deliberate boundary, not a
blanket rename.

---

<a id="adr-013"></a>
## ADR-013 — Money and quantities as JSON strings

**Status** proposed · **Owner** shared · **Related** NFR-DATA-009, `API-SPECIFICATION.md §1.5`

**Context.** JSON numbers are IEEE-754 doubles. `JSON.parse("0.1") + JSON.parse("0.2") !== 0.3`.
This system's core promise is that bills add up.

**Options.** 1. **Decimal strings (`"1234.50"`).** 2. JSON numbers. 3. Integer minor units (paise).

**Decision.** Option 1.

**Rationale.** Strings survive the round trip exactly and are readable in logs and in the browser's
network tab — which matters when debugging a disputed bill. Option 2 can silently corrupt a total.
Option 3 is also exact but makes every payload require mental arithmetic and is error-prone with
mixed scales (amounts 2 dp, unit prices 4 dp, quantities 3 dp).

**Consequences.** The frontend must parse with a decimal library (not `parseFloat`) for any
arithmetic, and must never use `+` on two money values as numbers. This is stated as a hard rule in
`../../bmfrontend/CLAUDE.md`. Schema validation enforces scale, so a 3-decimal amount is a `422` rather
than a silent round.

---

<a id="adr-014"></a>
## ADR-014 — Browser printing is primary; server-side PDF deferred

**Status** proposed · **Owner** shared · **Owner approval: YES (Q-PRINT-1)** · **Related** FR-PRINT-001…005

**Context.** The printed bill must closely reproduce the owner's Excel invoice (C-9). The actual
printer and paper size are unconfirmed.

**Options.** 1. **HTML print template + `@page` CSS, printed by the browser.** 2. Server-generated
PDF, downloaded and printed. 3. Direct printer protocol integration.

**Decision.** Option 1 for v1; Option 2 deferred (FR-PRINT-005) for archival and emailing.

**Rationale.** Option 1 needs no extra infrastructure, no rendering binary, and works with whatever
printer the shop has through the driver they already use. The backend still owns the *content* (the
`print_payload`), so the frontend cannot invent or omit a field, and moving to server-side PDF later
is additive — the payload is already the contract. Option 3 is wildly disproportionate.

**Consequences.** Print fidelity depends on the browser and printer; the exact layout must be
verified on the real device against the real Excel bill (a go-live gate). Chrome/Edge is the
supported print target. "Reprint" re-renders deterministically from the stored snapshot, which is
why no PDF needs to be archived to reproduce a past bill.

**Owner approval needed** on paper size, printer and whether a stored PDF copy is required for
record-keeping.

---

<a id="adr-015"></a>
## ADR-015 — Financial records are immutable; corrections are reversals

**Status** proposed · **Owner** shared · **Owner approval: YES (Q-BIZ-8)** · **Related** FR-SALE-010/011,
FR-PAY-005, FR-RET-002, NFR-DATA-006, T-1

**Context.** Mistakes happen at a counter: wrong quantity, wrong customer, wrong price, duplicate
payment. The owner needs them fixable; the business needs an audit trail.

**Options.**
1. **Immutable records; corrections via cancellation, return, credit note or reversal.**
2. Editable records with a change history.
3. Editable records, no history.

**Decision.** Option 1, enforced at the database privilege level, not merely in code.

**Rationale.** This is the control that makes T-1 (a staff member editing away a sale) structurally
impossible rather than a matter of discipline — the application role has no `UPDATE`/`DELETE` on
`invoice_items`, `stock_movements`, `payments` or `audit_logs` (`SECURITY.md §7`). Option 2 is a
weaker version with more code and more ways to be wrong. Option 3 is unacceptable for anything
financial.

**Consequences.** Staff must learn "cancel and re-bill", not "edit the bill" — a training item, and
the main usability cost of the decision. Within `cancel_window_hours` a cancellation is clean;
afterwards corrections are credit notes. A cancelled invoice keeps its number and stays visible, so
the number sequence stays intact.

**Owner approval needed** on who may cancel and for how long after issue (Q-BIZ-8).

---

<a id="adr-016"></a>
## ADR-016 — Migrations are an explicit deploy step

**Status** proposed · **Owner** backend · **Related** NFR-MAINT-004, NFR-OPS-001, `DEPLOYMENT.md §6.2`

**Context.** Alembic can be run from an application entrypoint or as a separate step.

**Options.** 1. **Separate one-off job; `/readyz` fails on head mismatch.** 2. Run on app startup.

**Decision.** Option 1.

**Rationale.** With Option 2, every container restart is a potential schema change, a crash-loop
becomes a migration loop, and two replicas starting together can race on DDL. Making it explicit
also forces the expand/contract discipline: you can see the migration happen, and the app can be
rolled back independently of the schema.

**Consequences.** The deploy pipeline has one more step and one more credential
(`BM_MIGRATION_DATABASE_URL`, never present in the API container). `/readyz` gates traffic until the
heads match, so a half-migrated fleet cannot serve writes.

---

<a id="adr-017"></a>
## ADR-017 — Real PostgreSQL in tests; SQLite prohibited

**Status** proposed · **Owner** backend · **Related** `TESTING-STRATEGY.md §1`

**Context.** SQLite in-memory is a common, fast test database.

**Decision.** All data-touching tests run against real PostgreSQL. **SQLite is prohibited.**

**Rationale.** This design depends on `SELECT … FOR UPDATE`, `NUMERIC` exactness, partial indexes,
native enums, `pg_trgm`, column-level privileges and deferred constraints. SQLite supports almost
none of it. A green SQLite suite would be actively misleading precisely about the things most likely
to be wrong.

**Consequences.** Tests need Docker (present) and run in ~8 minutes rather than seconds. The test
database uses the same privilege grants as production so immutability tests are meaningful.

---

<a id="adr-018"></a>
## ADR-018 — No Row-Level Security in v1

**Status** proposed · **Owner** backend · **Related** NFR-SEC-005, `SECURITY.md §3.2`

**Context.** Postgres RLS can enforce tenant isolation in the database.

**Options.** 1. **Repository-level scoping + exhaustive isolation tests.** 2. RLS policies.
3. Both.

**Decision.** Option 1 for v1; Option 3 recorded as the hardening step.

**Rationale.** With a single application role and one application, RLS would duplicate a boundary
that is already enforced and — crucially — **tested by a generated matrix over every route** (AC-8),
which is what actually catches the mistake. RLS adds session-variable plumbing, complicates
migrations and background jobs, and is easy to get subtly wrong. The honest trade-off: RLS defends
against a missing predicate in new code; the generated matrix test defends against the same thing and
fails in CI rather than in production.

**Consequences.** A new endpoint that forgets tenant scoping is caught by CI, not by the database. If
a second application or a BI tool ever connects to this database, RLS becomes necessary.

---

<a id="adr-019"></a>
## ADR-019 — Single API replica at launch

**Status** proposed · **Owner** backend · **Related** NFR-REL-001, `DEPLOYMENT.md §2, §9`

**Context.** 5 concurrent users. In-process rate limiting is simple and sufficient at one replica.

**Decision.** One API replica and one worker at launch, with the scale-out prerequisites written down.

**Rationale.** Matches the load with large headroom and keeps cost low (Q-OPS-5). Multiple replicas
with an in-process limiter would silently multiply the effective rate limit — a correctness bug, not
just a config detail — so the prerequisite is recorded explicitly.

**Consequences.** A single replica restart is a brief outage; drafts keep working offline, so the
counter is not stopped. **Before adding a replica, the rate limiter must move to a shared store**
(`DEPLOYMENT.md §9`). Availability target is honestly 99.5%, not four nines.

---

<a id="adr-020"></a>
## ADR-020 — Tax defaults to `estimate_only` behind a hard gate

**Status** proposed · **Owner** shared · **Owner approval: YES (Q-TAX-1/2)** · **Related** FR-TAX-001…004

**Context.** The shop's GST registration status, rates and HSN mapping are unknown (C-4). Printing
"TAX INVOICE" without a valid configuration would be wrong and potentially a legal problem.

**Options.** 1. **`estimate_only` default; GST behind a validated gate.** 2. Guess a common GST rate.
3. Block the build until tax is confirmed.

**Decision.** Option 1.

**Rationale.** Option 2 fabricates business facts — explicitly out of bounds. Option 3 stalls
everything else for a question that only affects the document title and one totals row. Option 1
lets the whole system be built and used, while making it **impossible** to accidentally present an
unconfigured document as compliant: `document_title` is `ESTIMATE`, `totals.tax` is `null` (not
`"0.00"`, so no misleading zero-tax row renders), and `POST /admin/tax/enable-gst` returns a precise
checklist until every prerequisite is satisfied.

**Consequences.** Tax tables exist but are unused, so enabling GST later is data entry and
configuration rather than a migration on a live financial database. Invoices issued while
`estimate_only` keep `document_kind = estimate` forever — they are not retroactively relabelled.
A startup warning and a go-live gate keep the state visible.

---

<a id="adr-021"></a>
## ADR-021 — Two repositories, no third; documentation lives with its owner

**Status** proposed · **Owner** shared · **Related** C-10, prompt §2

**Context.** `bm/` is a VS Code workspace folder containing `bmfrontend/` and `bmbackend/`. Some
documentation (owner questions, cross-cutting decisions, traceability) spans both.

**Options.**
1. **Documentation lives in the repository that owns the contract; shared ADRs are fully recorded in
   the owning repository with a stub in the other; `bm/README.md` is a thin, non-authoritative
   pointer file.**
2. A third `bmdocs` repository.
3. Untracked shared documents in `bm/`.

**Decision.** Option 1.

**Rationale.** Option 2 contradicts C-10 and adds a repository with its own history, CI and review
burden for prose. Option 3 means the most important cross-cutting documents are the only ones nobody
version-controls. Option 1 keeps every authoritative document inside a tracked repository and keeps
each contract next to the code that implements it.

**Consequences.**
- `bm/README.md` exists for workspace orientation only. It is **explicitly non-authoritative**, is
  not in any repository, and must contain no facts — only links. If it ever disagrees with a
  repository document, the repository wins. It must be short enough that there is nothing in it to
  go stale.
- Shared decisions carry the **same ADR number in both repositories**; the non-owning repository
  holds a one-line stub (§Stubs below, and the mirror section in the frontend file).
- Business-rule questions are authoritative in **`bmbackend/docs/OWNER-DECISIONS.md`** (the OD-*
  register), because the backend enforces business rules. `PRD.md §5` is a cross-referenced summary;
  the frontend PRD links to the register rather than restating it. Frontend-only UX questions
  (browser, screen size) stay in `bmfrontend/docs/PRD.md §4`.
- Requirement IDs are shared across both PRDs; `docs/TRACEABILITY.md` in this repository is the
  single matrix and is referenced (not duplicated) by the frontend.
- Canonical identifier vocabularies (permission vs audit action vs settings key vs column vs API
  field) are authoritative in `docs/DATA-MODEL.md §3.14`.
- **One narrow exception to "nothing authoritative at the workspace root":**
  `bm/PHASE-1-CLOSURE-REPORT.md` is a **point-in-time audit snapshot**, not a living specification.
  It records what was verified on a given date and is never updated in place — a later audit
  produces a new dated report. It is placed at the root because it spans both repositories and
  belongs to neither; it contains no requirement, rule or contract that a repository document does
  not already own. Because it is untracked by either repository, it must be treated as disposable
  evidence: **if the information matters going forward, it belongs in a repository document.**
- **Maintenance rule:** a change to a shared ADR or a shared requirement ID requires a commit in
  both repositories in the same change set. The PR template includes a checkbox for it.

---

<a id="adr-022"></a>
## ADR-022 — Payment status is derived, with a same-transaction cache

**Status** proposed · **Owner** backend · **Related** FR-CREDIT-002/003, NFR-DATA-003

**Context.** The owner's current practice (and many simple systems) is a "paid" checkbox. Outstanding
balance must be trustworthy, and credit lists must be fast to query.

**Options.**
1. **Derive from allocations; keep `amount_paid`/`payment_status` as a cache written in the same
   transaction as the allocation.**
2. Pure derivation, computed on every read.
3. Editable `payment_status` field.

**Decision.** Option 1.

**Rationale.** Option 3 is rejected outright — a manually editable paid flag is exactly the failure
the owner is trying to escape, and FR-CREDIT-003 forbids it. Option 2 is the most correct but makes
every credit list and ageing report an aggregate over `payment_allocations`, and the partial index on
`payment_status` (which makes the outstanding query fast) becomes impossible. Option 1 keeps the
derived value authoritative-by-construction: it is only ever written in the same transaction as the
rows it summarises, and it is verified by an invariant test plus a nightly reconciliation.

**Consequences.** A cache that can in principle drift, so: no API path writes it directly, column
grants limit which columns the app may update at all, an invariant test asserts equality with the
allocation sum, and the nightly job **alerts** rather than silently repairing — a drift means a bug
that must be found, not papered over.

---

## Stubs — decisions owned by `bmfrontend`

Full records in `../../bmfrontend/docs/DECISIONS.md`. Listed here so this file is a complete index.

| ADR | Title | Why the backend cares |
|---|---|---|
| ADR-023 | React + TypeScript + Vite | — |
| ADR-024 | TanStack Query for server state, Zustand for draft state | — |
| ADR-025 | Dexie/IndexedDB as the offline draft store | Consumes the sync contract in `OFFLINE-SYNCHRONIZATION.md` |
| ADR-026 | Tailwind CSS with a minimal shadcn/ui subset | — |
| ADR-027 | **Keep separate billing actions; fix clarity with wording.** ⚠️ **The labels are now set by [OD-14](OWNER-DECISIONS.md#od-14) as four actions** — `Print Bill` · `Credit Bill` · `Save a Draft` · `Clear Draft`. *(A single `Complete & Print` button was **considered and rejected**; the credit **mechanism** this ADR assumed is superseded by ADR-033; the three-label wording is superseded by OD-14's re-answer.)* | Both finalising actions must map to the one `POST /sales:finalize` endpoint |
| ADR-028 | Printing via a dedicated print root and `@page` CSS | Renders `print_payload` verbatim; adds no fields |
| ADR-029 | API types generated from OpenAPI | Makes ADR-012 (`snake_case`) and ADR-013 (string money) painless |
| ADR-030 | Keyboard-first billing interaction | Drives search latency budget NFR-PERF-001 |
| ADR-031 | `vite-plugin-pwa` / Workbox caching strategy | Must not cache API writes; see FR-OFFLINE-012 |
| ADR-032 | The client never treats its own totals as authoritative | Mirrors FR-SALE-003 |
| **ADR-033** | **`Save / Credit Bill` finalises directly; no mandatory credit-details step** — **`accepted`, owner-specified 2026-10-29** | **Supersedes the credit-dialog mechanism in ADR-027.** The backend is unaffected in shape — credit still finalises through the **same** `POST /sales:finalize` engine (FR-CREDIT-008) — but three backend rules become load-bearing: a persisted `customer_id` is mandatory for credit (FR-CREDIT-009), payment is optional at finalisation (FR-CREDIT-025), and the offline refusal now explicitly covers the credit action (`OFFLINE-SYNCHRONIZATION.md §3.1` invariant 2a) |

---

## Unresolved business decisions (summary)

Full detail, including recommended defaults, in `PRD.md §5.1` (blocking) and `§5.2` (non-blocking).

**Blocking:** Q-TAX-1 (GST status), Q-TAX-2 (rates/HSN), Q-TAX-3 (series format and gapless
requirement), Q-LEGAL-1 (shop identity for the invoice header), Q-PRINT-1 (paper and printer),
Q-MONEY-1 (rounding rule).

**Needs an owner decision but not blocking:** ADR-006 (draft reservation), ADR-011 (offline
finalisation expectations), ADR-014 (print approach and PDF archival), ADR-015 (cancellation window
and who may cancel), ADR-020 (tax mode).

---

## Related documents

`docs/PRD.md` · `docs/ARCHITECTURE.md` · `docs/DATA-MODEL.md` · `docs/API-SPECIFICATION.md` ·
`docs/SECURITY.md` · `docs/OFFLINE-SYNCHRONIZATION.md` · `docs/TESTING-STRATEGY.md` ·
`docs/DEPLOYMENT.md` · `docs/TRACEABILITY.md` · `../../bmfrontend/docs/DECISIONS.md`
