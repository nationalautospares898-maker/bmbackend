# Architecture, Performance & Resource-Efficiency Audit

**Date:** 2026-11-21
**Scope:** `bmfrontend`, `bmbackend`, and the Vercel/Railway/Neon infrastructure they run on.
**Trigger:** the Vercel Hobby plan's CDN request budget was exceeded (1.6M/1M); the root cause — a
render-triggered polling loop in `NotificationBell.tsx` — was found and fixed in an earlier pass of
this engagement. This audit is the follow-up asked for: look everywhere else for the same class of
problem (unnecessary work, repeated queries, excessive traffic, poor caching, resource leaks)
before declaring the system healthy.

**Method.** Direct reading of both repositories (file:line citations throughout), two focused
research passes delegated to sub-agents for backend breadth (job/background-task architecture, and
database schema/index coverage) with every load-bearing claim independently re-verified against the
source before being relied on, and — where a claim was about *performance*, not just *existence* —
an actual measurement against a local, disposable PostgreSQL instance (never production). No
Vercel/Railway/Neon dashboard or API access exists in this environment (recorded previously in this
engagement); every finding below is evidence from code, local measurement, or the repositories' own
previously-recorded test results — never invented.

---

## How to read this register

Each finding has:
- **Evidence** — file:line, or a measured result, not a guess.
- **Severity** — P0 (critical data integrity/reliability) · P1 (serious performance/reliability) ·
  P2 (meaningful efficiency/maintainability) · P3 (minor/future).
- **Disposition** — 1. Safe to implement now · 2. Requires further investigation · 3. Requires your
  explicit approval · 4. Not recommended.
- **Status** — Fixed in this pass / Documented only / Not applicable.

---

## P1 findings

### F1 — `GET /notifications` has no supporting index for its actual query

- **Repository/files:** `bmbackend` — `src/bm/domain/notifications/service.py:195-219`
  (`active_for`), `migrations/versions/0015_infrastructure.py:268-274`,
  `migrations/versions/0025_notifications_lifecycle_reminders_orders.py:77-108`.
- **Problem.** The query behind the notification bell's poll —
  `WHERE business_id = :business_id AND (status = 'active' OR (status = 'snoozed' AND
  snoozed_until <= now()))` — has no index that matches its predicate. `0015` created
  `ix_notifications__unread` on `(business_id, created_at DESC) WHERE read_at IS NULL`, back when
  "unread" was the only state. `0025` replaced that model with a five-state `status` lifecycle and
  added only a *dedup* index (`uq_notifications__active_per_entity`); nothing covering
  `(business_id, status)` was added. `read_at` is explicitly independent of `status` (service
  comment, same file: `mark_read` only ever sets `read_at`, never `status`), so Postgres cannot use
  the old partial index for this query at all.
- **Root cause.** A migration that changed the primary filter column of a hot query did not carry
  its index forward — not a resource leak or a loop, but a genuine "the data model moved, the index
  didn't" gap.
- **Evidence (measured, not assumed).** Against a local disposable Postgres (not production),
  seeded with 80,000 synthetic notification rows reflecting a realistic shape (99.7% resolved/
  dismissed/acknowledged, 0.3% still active — see `RESOURCE-CONSUMPTION-REPORT.md` §4 for the exact
  seed and cleanup):

  | | Without index | With index |
  |---|---|---|
  | Plan | `Seq Scan` | `Bitmap Heap Scan` via `BitmapOr` of two index scans |
  | Buffers touched | 1,312 | 192 (189 hit + 3 read) |
  | Execution time | 9.253 ms | 1.387 ms |

  **≈6.7x faster, ≈85% fewer buffer accesses**, at a table size this shop will reach within months
  of normal operation (every low-stock scan, reminder firing, and order creation appends a row;
  nothing purges old ones). The `Seq Scan` cost scales with the *whole table*; the indexed cost
  scales with the handful of currently-active rows, which stays roughly constant regardless of the
  table's age. This is the "gets slower every day of trading" failure mode in slow motion, hitting
  the one endpoint every open, signed-in tab calls every 30 seconds.
- **Severity:** P1 — not a crash risk, but a real, provable, continuously-worsening cost on the
  single endpoint this whole engagement started by investigating.
- **Fix implemented:** `bmbackend/migrations/versions/0029_notifications_business_status_index.py`
  — a plain `(business_id, status)` index, built with `CREATE INDEX CONCURRENTLY` (via Alembic's
  `autocommit_block()`, since `migrations/env.py` runs every migration inside a transaction and
  `CONCURRENTLY` is refused inside one — see F6 below) so it can be applied to a live table without
  holding an exclusive lock for the build's duration. The matching ORM index declaration was added
  to `src/bm/infra/db/models/infra.py` (its absence would otherwise show up as schema drift on the
  next `alembic check`, which is this project's own CI gate for exactly that).
- **Disposition: 3 — requires your explicit approval to apply to the production database.** The
  migration file is written, tested (upgrade, downgrade, upgrade again — all clean), and its benefit
  is measured, but *running* it against Neon production is a database change, which you've asked to
  gate explicitly. See `REMAINING-RISKS-AND-APPROVALS.md`.
- **Test:** `uv run alembic upgrade head` / `downgrade 0028` / `upgrade head` round-trip, and
  `uv run alembic check` (schema drift), both run clean against a local scratch database (not
  production). See `REGRESSION-AND-PERFORMANCE-TEST-REPORT.md`.

### F2 — An HTTP call is made while a database row lock is held, violating the module's own documented invariant

- **Repository/files:** `bmbackend` — `src/bm/domain/notifications/service.py:316-386`
  (`dispatch_pending`), called from `src/bm/domain/jobs/handlers.py:190-191` and `:338-340`.
- **Problem.** `dispatch_pending` claims outbox rows with `SELECT ... FOR UPDATE SKIP LOCKED`
  (lines 322-334) and then, **while still holding that lock, inside the same open transaction**,
  calls `channel.send(...)` (line 346) — a synchronous outbound HTTP call with up to a
  30-second timeout — before updating the row and returning.
- **This directly contradicts the module's own stated design.** `service.py:9-11`'s docstring says
  alerts are "dispatched *after* commit, because rule 3 of `ARCHITECTURE.md §5.1` forbids a side
  effect inside a write transaction and an HTTP call inside one would hold locks for the length of a
  network timeout" — and `CLAUDE.md` invariant 12 states the same rule project-wide ("No side
  effects inside a write transaction — no HTTP calls... Use `outbox_events`"). The code does not
  currently do what its own documentation says it does.
- **Severity:** P1 as an architectural defect (it is exactly the failure mode the project's own
  rules exist to prevent), but **low current practical risk**: alerts are rare today (only
  `inventory.reconcile` ever raises one, and per F3 below that job is never auto-scheduled), and the
  webhook timeout is capped at 5 seconds by default (`alert_timeout_seconds`, `config.py:165`). The
  risk grows directly with alert volume.
- **Disposition: 3 — requires explicit approval, not implemented in this pass.** The correct fix
  (claim and commit in one transaction, deliver and record the outcome in a second) touches
  transaction boundaries in job-handler code — exactly the kind of change this engagement's rules
  ask to be flagged rather than rushed, even though the blast radius is alerting, not money or
  stock. A precise fix is proposed in `REMAINING-RISKS-AND-APPROVALS.md`.
- **Status:** Documented only.

---

## P2 findings

### F3 — Four registered job kinds are never automatically enqueued anywhere in the code

- **Repository/files:** `bmbackend` — `src/bm/domain/jobs/handlers.py` `REGISTRY` (lines 349-358);
  grep of every `INSERT INTO jobs` call site in `src/` and `scripts/`.
- **Problem.** `inventory.reconcile`, `idempotency.prune`, `drafts.mark_stale`, and
  `notifications.dispatch` all have working, idempotent handlers — but nothing ever inserts a job
  row for any of them. Only `import.parse` (triggered by a file upload,
  `src/bm/domain/imports/service.py:330`) and the three worker-ticker kinds
  (`low_stock.scan`/`reminders.check_schedules`/`orders.nudge`, `worker.py:45-49`) are ever enqueued.
  `worker.py`'s own docstring (lines 40-44) already says as much.
- **This is the opposite problem from the rest of this audit — dead automation, not wasted
  automation.** Nightly stock reconciliation, idempotency-key pruning, and stale-draft marking
  silently never run unless an operator inserts the row manually.
- **Severity:** P2. Not a resource-waste finding, but worth surfacing precisely because it was
  found rather than assumed, and because "nothing alerts about a drifted balance" (F2's own
  low-current-risk) is partly explained by "nothing ever runs the reconciliation job that would
  raise the alert" in the first place.
- **Disposition: 3 — this is a product/operations decision (should these run nightly, and on what
  schedule?), not a code defect with one obvious fix.** Flagging for you to decide; not implemented.

### F4 — `settings` (business rules) are re-queried from scratch on every single lookup, with no caching at any layer

- **Repository/files:** `bmbackend` — `src/bm/infra/db/repositories/settings.py:31-52`
  (`all_for`), `src/bm/domain/settings/service.py:55-65` (`resolve_all`/`get`); call sites in
  `src/bm/domain/sales/print_payload.py:143,145,148,151,261` (five separate `.get()` calls while
  building **one** print payload) and 14 total `SettingsService(...)` construction sites across
  `domain/sales/service.py`, `domain/sales/drafts.py`, `domain/sales/cancellation.py`,
  `domain/returns/service.py`, `domain/credit/service.py`, `domain/sync/service.py`, etc.
- **Problem.** Every `.get(key)` call re-runs `resolve_all()`, which is a fresh
  `SELECT * FROM settings WHERE business_id = ...` — confirmed absent of any `lru_cache`,
  request-scope memoization, or process cache by grepping `cache|lru_cache|redis` across the
  settings module (the only hits are an unrelated setting *named* `sync.catalog_cache_max_rows`,
  about the offline catalog, not a cache of settings themselves). Printing one bill issues five
  separate round trips to a table holding, per business, only tens of rows.
- **Severity:** P2. Each individual query is cheap (`settings` is tiny) so this is not a page-load-
  breaking defect; it is architectural debt that multiplies with every sale finalisation, print, and
  draft save, and a textbook case of "repeated identical queries" the audit was asked to find.
- **Disposition: 3 — requires explicit approval.** A safe fix exists (resolve once per request and
  reuse, or at minimum batch `print_payload.py`'s five calls into one), but every construction site
  sits inside `domain/sales/`, `domain/credit/`, or `domain/returns/` — the most protected code in
  this application. Proposed fix in `REMAINING-RISKS-AND-APPROVALS.md`; not implemented here.

### F5 — Four business-wide report/list queries have no index leading with `business_id`

- **Repository/files:** `bmbackend` —
  - `src/bm/domain/sales/reader.py:283-326` (Daily Bills / Credit Bills) and
    `src/bm/domain/reports/service.py:102-409` (daily sales, credit outstanding, reconciliation
    check, item sales) — all filter `invoices WHERE business_id = ...`; the only `issued_at`-
    adjacent indexes lead with `branch_id`, not `business_id`.
  - `src/bm/domain/inventory/service.py:502-649` (`movements`, `movement_report` — the Stock
    Movement Report) — filters `stock_movements WHERE business_id = ...`; **no index on this table
    leads with `business_id` at all.**
  - `src/bm/domain/credit/service.py:748-771` (`payments_for`) — filters
    `payments WHERE business_id = ...`; same gap.
  - `src/bm/domain/purchases/service.py:346-367` (`list_purchases`) — same gap.
- **Why this is P2, not P1, for this specific shop today.** National Auto Spares is a
  single-counter, and per its own documentation a single-*branch*, operation. When a business has
  exactly one branch, a `branch_id`-leading index is nearly as selective as a `business_id`-leading
  one would be — the practical cost of this gap is low **right now**. It becomes a real P1 the
  moment the shop (or any future tenant of this codebase) operates more than one branch, or once
  `stock_movements`/`payments` — both append-only, never-pruned tables — accumulate enough rows that
  even a single-branch scan is slow.
- **Disposition: 2 — requires further investigation, not implemented.** Adding four more indexes
  "just in case" is exactly the speculative optimisation this audit's own rules warn against
  ("every proposed... component must have a demonstrated need"); the demonstrated need here is
  future, not current. Documented so it is not rediscovered from scratch if/when the shop adds a
  second branch or these tables grow large — see `FINAL-ARCHITECTURE-RECOMMENDATIONS.md`.

### F6 — This project's migration tooling cannot satisfy its own `CREATE INDEX CONCURRENTLY` rule

- **Repository/files:** `bmbackend` — `migrations/env.py:88` (`transaction_per_migration=True`);
  `migrations/versions/0017_child_table_tenancy.py:42-62`, `0018_search_indexes.py:82-87`,
  `0019_import_duplicate_override.py:36-47`, `0020_import_job_ordinal.py:40-68`,
  `0025_notifications_lifecycle_reminders_orders.py:102-108`.
- **Problem.** `CLAUDE.md §7` requires `CREATE INDEX CONCURRENTLY` on a populated table, in a
  non-transactional migration. Alembic is configured with `transaction_per_migration=True`, and
  Postgres refuses `CREATE INDEX CONCURRENTLY` inside a transaction block. Every one of the five
  migrations listed above adds an index to a table its own docstring says (or shows, via a same-
  migration backfill) may already hold rows, using a plain, non-concurrent `CREATE INDEX` — which
  takes an exclusive lock for the build's duration. The rule and the tooling disagree, and nothing
  in the repository had ever reconciled them before this audit.
- **Severity:** P2 — these five migrations already ran; nothing about re-running history changes
  what's live today. The finding matters for *every future* index migration, including this audit's
  own (F1).
- **Fix implemented:** `0029` (F1's migration) uses `op.get_context().autocommit_block()` to step
  outside the per-migration transaction for its one `CREATE INDEX CONCURRENTLY` statement — the
  pattern Alembic itself documents for exactly this situation. This is now a demonstrated,
  working example in the repository for any future migration that needs it.
- **Disposition: 1 — safe, implemented.** Retroactively rewriting `0017`–`0025` is explicitly not
  proposed: they are already applied, and redoing an applied migration changes nothing about the
  schema those indexes already exist in.

### F7 — Five frontend dependencies are declared but imported nowhere

- **Repository/files:** `bmfrontend` — `package.json` (`dependencies`: `@tanstack/react-query`,
  `react-hook-form`, `react-router-dom`, `zod`; `devDependencies`: `msw`).
- **Problem.** `depcheck` plus a manual `grep` across the **entire** repository (not just `src/`)
  found zero imports of any of these five packages anywhere — not in application code, not in
  tests, not in config. `CLAUDE.md §3` describes them as "the stack" (react-query for server state,
  react-hook-form + zod for forms, MSW for test mocking); the actual, shipped application uses
  manual `useState`/`useEffect` fetching, uncontrolled native inputs with hand-written validation,
  manual screen-state switching (no router), and `vi.mock()` for test doubles instead. The
  documentation describes an aspiration that was never implemented, not the application that exists.
- **Severity:** P2/P3. Because nothing imports them, Vite/Rollup's tree-shaking already excludes
  them from the shipped bundle — confirmed by an identical production bundle size (`492.03 kB`)
  before and after removal (see below). The cost was entirely in `npm ci`/CI install time and
  `npm audit`/dependency-scan surface, not runtime.
- **Fix implemented:** removed all five from `bmfrontend/package.json`; `package-lock.json`
  regenerated. Verified zero behavioural or build-output change (full details in
  `OPTIMIZATION-IMPLEMENTATION-REPORT.md`).
- **Disposition: 1 — safe, implemented.**

### F8 — `claim_and_run_one` re-checks that the `jobs` table exists on every one-second worker tick, forever

- **Repository/files:** `bmbackend` — `src/bm/infra/queue/runner.py` (`_jobs_table_exists`,
  `claim_and_run_one`); `worker.py`'s loop ticks once per second indefinitely.
- **Problem.** `_jobs_table_exists()` opened a full `transaction()` (commit/rollback-wrapped
  session) and ran `SELECT to_regclass('jobs') IS NOT NULL` on **every single call** — i.e. once a
  second, forever, to answer a question whose true answer cannot later become false while the
  worker is running (a migrated table does not un-migrate itself at runtime).
- **Severity:** P2/P3 — individually cheap, but an unconditional, indefinite DB round trip
  (~86,400/day per worker process) serving a one-time bootstrap concern.
- **Fix implemented:** a module-level flag now short-circuits every call after the first `True`
  (`runner.py`). No test in this repository exercises the "table does not exist" path against a
  live database (confirmed by grep — every test runs against an already-migrated database), so this
  cannot regress any existing test's intent.
- **Disposition: 1 — safe, implemented.**

---

## P3 findings (documented only — genuinely minor, not implemented)

### F9 — `InvoiceReader._timezone()` runs even when its result is never used

- **File:** `bmbackend/src/bm/domain/sales/reader.py:127-133, 287`. `list_invoices` always computes
  `params["timezone"] = self._timezone(ctx.business_id)` (a one-row lookup by primary key on
  `businesses`), but the value is only ever referenced in the SQL string when `from_date` or
  `to_date` is supplied (lines 302-307). When a caller passes neither, the lookup's result is built
  and discarded.
- **Severity:** P3 — one single-row, primary-key-indexed lookup on a request path, not a loop.
  **Not implemented**: whether every real caller always supplies a date range (making this
  genuinely dead in practice) was not conclusively established, and the fix is trivial enough to be
  low-value to rush without that confirmation. Noted for a future pass.

### F10 — `worker_concurrency` is configured and logged but has no effect

- **Files:** `bmbackend/src/bm/config.py:145` (`worker_concurrency: int = Field(default=2, ...)`),
  `worker.py:72` (logs it at startup). Nothing in `worker.py` or `runner.py` spawns additional
  threads/processes or claims more than one job per loop iteration — the setting is inert.
- **Severity:** P3 — misleading, not wasteful. **Not implemented**: either wire it up (adds real
  concurrency, a behaviour change outside this audit's remit) or remove it (a config/log change with
  no functional effect either way) are both legitimate; the choice belongs to whoever owns the
  worker's intended operational shape, not to this audit.

### F11 — Middleware registration-order comment is inverted from the actual execution order

- **File:** `bmbackend/src/bm/main.py:139-147`. The comment says "Outermost first," but
  Starlette/FastAPI's `add_middleware` makes the **last**-added middleware the outermost wrapper, so
  actual request-time order is CORS → Correlation → RequestLog → BodySizeLimit → SecurityHeaders →
  route — the reverse of the comment's stated intent. **Not a bug**: nothing here actually depends
  on the order being otherwise (Correlation still runs before RequestLog needs its request-id
  context). **Not implemented**: a comment fix only; left for a routine cleanup pass rather than a
  standalone change in an audit about behaviour.

---

## What was investigated and found already correct (no finding, no change)

Documenting the negative result matters as much as the positive ones — this audit was not rubber-
stamped, these were checked and found sound:

- **Frontend:** the sync scheduler's six-trigger design (zero network calls on idle ticks), the
  offline catalog refresh (bounded, delta-based), proactive token renewal (`NaN`-guarded, floored
  interval), the shared single-flight/cross-tab-locked auth refresh, bounded request retry/backoff,
  the service worker (app-shell only, zero `/api/` caching, confirmed against `vite.config.ts`),
  and every other screen's fetch-on-mount/fetch-on-filter-change pattern (no second polling loop
  exists anywhere in the frontend beyond the one already fixed).
- **Backend:** module-boundary enforcement (`import-linter`: 3/3 contracts kept, 0 broken across 971
  dependencies), connection pooling configuration (sane pool size/overflow/pre-ping/recycle, per-
  connection statement/lock timeouts), session lifecycle (leak-safe on every path checked), the four
  heaviest read endpoints' actual query shapes (invoice listing, inventory balances, credit-folder
  aggregation, catalog search — all single round trips, all paginated, zero N+1), structured
  logging with no body/loop logging, and the single outbound HTTP call's bounded timeout/retry.
- **Notification generation specifically:** confirmed `GET /notifications` does **not** recompute
  low-stock status on every poll — that scan is correctly isolated to a 5-minute worker tick,
  idempotent against re-raising the same alert. The 30-second poll only ever reads already-
  materialised rows (F1's index gap aside).
- **Database:** `products`/`product_aliases` search indexes (GIN trigram + prefix, built for a
  documented 100,000-row catalogue and backed by a real load-test suite — see
  `RESOURCE-CONSUMPTION-REPORT.md` §2 for the previously-recorded, passing figures), every tenancy-
  scoped child table added in migration `0017` (deliberately paired with its own index in the same
  migration), and foreign-key coverage on every column with an actual exercised read query other
  than F5's four.

---

## Summary table

| ID | Severity | Disposition | Status |
|---|---|---|---|
| F1 — `notifications` missing index | P1 | 3 (needs your approval to apply) | Migration written & tested; not applied to production |
| F2 — HTTP call under a held DB lock | P1 | 3 (needs approval) | Documented, fix proposed |
| F3 — job kinds never auto-enqueued | P2 | 3 (product decision) | Documented |
| F4 — settings re-queried with no cache | P2 | 3 (needs approval; touches sales/credit code) | Documented, fix proposed |
| F5 — business-id index gaps (low current impact) | P2 | 2 (further investigation) | Documented |
| F6 — migration tooling can't do CONCURRENTLY | P2 | 1 (safe) | **Fixed** (demonstrated in `0029`) |
| F7 — 5 unused frontend dependencies | P2/P3 | 1 (safe) | **Fixed** |
| F8 — `_jobs_table_exists` re-checked every second | P2/P3 | 1 (safe) | **Fixed** |
| F9 — dead `_timezone()` call | P3 | 2 | Documented |
| F10 — dead `worker_concurrency` setting | P3 | 2 | Documented |
| F11 — inverted middleware-order comment | P3 | 1 (trivial, deferred) | Documented |
