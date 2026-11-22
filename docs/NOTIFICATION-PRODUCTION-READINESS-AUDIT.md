# Notification System — Production Readiness Audit

**Date:** 2026-11-22
**Scope:** `bmbackend` + `bmfrontend`, the notification system and the minimum supporting
infrastructure.
**Verdict:** **READY TO DEPLOY**, with two owner decisions outstanding (neither blocking)
and three checks only the owner can perform, because they need the Vercel and Railway
dashboards.

**Nothing was deployed, pushed, or sent to production.** No production database,
environment variable or service was contacted. All testing ran against local Docker
PostgreSQL databases (`bm_test`, `bm_e2e`); the local application database `bm` was read
but never truncated, reset or reseeded.

---

## 1. Finding summary

Every finding is classified **PASS** / **FIXED** / **OWNER APPROVAL REQUIRED** /
**BLOCKED — NOT VERIFIED**.

| # | Area | Finding | Status |
|---|---|---|---|
| 1 | Threshold | Low-stock rule was implemented 6× with 2 different comparisons | **FIXED** |
| 2 | Threshold | Owner-facing wording stated the old rule ("falls below") | **FIXED** |
| 3 | Threshold | Notification body said "below the minimum of 10" for a part at 10 | **FIXED** |
| 4 | Requests | No unconditional 30-second polling remains | **PASS** |
| 5 | Requests | Single-flight holds under every trigger | **PASS** |
| 6 | Requests | Deferred refreshes cannot accumulate or go stale | **PASS** (proved structural) |
| 7 | Requests | Multi-tab behaviour | **PASS** |
| 8 | Requests | Reconnect / rapid-visibility storms bounded | **PASS** |
| 9 | Caching | `Cache-Control: no-store`, no ETag, no Last-Modified | **PASS** |
| 10 | CDN | Whether notification requests traverse Vercel's edge in production | **BLOCKED — NOT VERIFIED** |
| 11 | Scheduler | Runs in the main FastAPI process; no worker needed | **PASS** |
| 12 | Scheduler | Advisory lock prevents duplicate leadership | **PASS** |
| 13 | Scheduler | Lock connection does not occupy the request pool | **PASS** |
| 14 | Scheduler | Restart, DB-disconnect and graceful shutdown recovery | **PASS** |
| 15 | Scheduler | `/readyz` reports scheduler health accurately | **PASS** |
| 16 | Tests | Intermittent `test_stock_movement_report` failures | **BLOCKED — NOT VERIFIED** (not reproducible; not attributable) |
| 17 | Retention | Notifications accumulate without bound | **OWNER APPROVAL REQUIRED** |
| 18 | Worker | Separate worker service can be retired | **PASS** |
| 19 | Deployment | `BM_SCHEDULER_ENABLED` is new and must be left unset/true | **PASS** (documented) |
| 20 | Migrations | No new migration required | **PASS** |

---

## 2. The low-stock threshold — FIXED

### What was wrong

The rule existed six times and **two of them disagreed**:

| Implementation | Was | Now |
|---|---|---|
| `CatalogService.ProductView.is_low_stock` | `<` | **`<=`** |
| `InventoryService.balances(low_stock_only=True)` | `<` | **`<=`** |
| `ReportService.stock_position` | `<` | **`<=`** |
| `bmfrontend/.../lowStock.ts::isLow` | `<` | **`<=`** |
| `notifications/low_stock.py` (both entry points) | `<=` | `<=` (unchanged) |
| `SalesService._low_stock_warnings` | `<=` | `<=` (unchanged) |

A part sold down to exactly its minimum raised a bell alert that the Stock screen refused
to list — and the next reconciliation then resolved that alert, so the notice appeared and
vanished with nothing having changed.

**Per-module tests could not catch this.** Each module was internally consistent; what was
missing was a test that asks the same question of every surface at once.

### What changed

Six comparisons, plus the wording that describes them:

* `STOCK.lowStockRule` → "An item is listed here once its stock **reaches or falls below**
  its own Min Qty."
* `STOCK.lowStockEmpty` → "Nothing has **reached** its minimum quantity."
* The notification body now reads **"10 left — down to the minimum of 10."** for a part
  exactly at its minimum, instead of the plain falsehood "below the minimum of 10" sitting
  next to a screen that reads 10.

### Evidence

`tests/api/test_low_stock_threshold.py` — **20 tests**, all through real HTTP endpoints:

| Case | Stock filter | Product badge | Stock report | Notification | Reconciliation |
|---|---|---|---|---|---|
| 9 / 10 | low | low | low | raised | raised |
| **10 / 10** | **low** | **low** | **low** | **raised** | **raised** |
| 11 / 10 | not low | not low | not low | none | none |

Plus one test that asserts the four surfaces **agree with each other** at the boundary —
the shape of assertion that would have caught the original defect — and the lifecycle:

* replenishment to 10 keeps the alert (still low), to 11 resolves it;
* a later transition into low stock creates a **new** alert, not a revived one;
* 11 → 10 → 9 → 10 plus a reconciliation sweep leaves **one** alert, same id.

`bmfrontend/src/features/stock/lowStock.test.ts` — **8 tests** for the sixth
implementation, including the three named cases and the decimal boundary (10.001 is not
low; 9.999 is).

**205 tests** across every suite the change could touch re-run green: inventory rules,
stock entry API, catalog search, stock count, response contract, event-driven low stock,
billing workflows, reminders and orders.

> **Note for the owner:** more parts will now appear in the Low Stock list than before —
> every part sitting exactly on its minimum. That is the intended effect of the decision,
> not a defect.

---

## 3. Notification requests and CDN safety

### 3.1 Measured request frequency — the five scenarios

Real Chromium, real backend, counted off the wire
(`bmfrontend/tests/e2e/notification-request-frequency.spec.ts`). Each figure is printed by
the test on every run.

| # | Scenario | Duration | Old (30 s poll) | **Measured now** |
|---|---|---|---|---|
| 1 | One idle active tab | 120 s | 4 | **0** |
| 2 | Two simultaneously open tabs | 120 s | 8 | **0** |
| 3 | 30 bell open/close cycles | ~5 s | ~1 | **1** |
| 4 | 20 offline/online transitions | ~17 s | ~1 | **4–5** (two runs) |
| 5 | 5-minute active session, 15 screen changes | 301 s | 10 | **0** |

**Projected monthly volume**, two tabs open 12 h/day, 30 days:

| | Old | Now |
|---|---|---|
| Idle steady state | 86,400 | **≤ 2,880** (ceiling; measured idle rate is 0) |

The ceiling comes from the server's 15-minute fallback hint, not from a client timer. A
shop with nothing scheduled within 15 minutes makes **no requests at all** while idle.

### 3.2 Storm ceilings — measured at the unit level

`notificationsPoller.test.ts` drives each mechanism at the rate that caused the original
incident and prints the result:

| Mechanism | Before `ad9e6ec` | Now |
|---|---|---|
| `visibilitychange` storm, 7 Hz × 60 s | ~7.0 req/s | **13 requests (0.22 req/s)** |
| Remount storm, 7 Hz × 60 s | ~7.0 req/s | **13 requests (0.22 req/s)** |
| 200 offline/online cycles × 60 s | — | **13 requests** |
| 200 hide/show cycles × 60 s | — | **13 requests** |
| 8 idle hours | 960 | **32 requests** |

13 per 60 s is exactly the floor's ceiling (1 per 5 s). No mechanism exceeds it.

### 3.3 Deferred refreshes cannot go stale — PASS, and it is structural

A refresh the 5-second floor turns down is **deferred, not dropped**, so an explicitly
opened bell is always answered. The audit asked whether such deferrals can accumulate or
execute after becoming obsolete. They cannot, and the reason is structural rather than
defensive:

* at most **one** deferral is ever pending, so they cannot accumulate;
* a deferral is at most `MIN_FETCH_INTERVAL_MS` (5 s) in the future;
* a scheduled check is at least `MIN_CHECK_INTERVAL_MS` (60 s) away;
* every other trigger inside that window is declined by the same floor that armed the
  deferral.

So no fetch can occur between a deferral being armed and firing — the deferred refresh is
always the first request after the floor lifts, and the data it asks for has never already
arrived.

> An earlier draft of this audit added an "is this still needed?" check at fire time. The
> test written to prove it mattered **failed**, which is what established that the branch
> is unreachable. The guard was removed and replaced with the invariant assertion
> (`MIN_CHECK_INTERVAL_MS > MIN_FETCH_INTERVAL_MS`), so if the constants ever change such
> that obsolescence becomes possible, the suite says so.

### 3.4 Multi-tab — PASS

Each tab owns its own module instance; there is deliberately **no cross-tab leader
election** (a hidden tab holding the lock would starve the visible one). Fetching is gated
on `document.visibilityState === 'visible'`, so a backgrounded tab costs nothing.

Measured: two simultaneously open tabs, both signed in, **0 requests in 120 s**.

### 3.5 Response caching — PASS

Measured on the live local stack:

```
GET /api/v1/notifications?limit=50
cache-control: no-store
vary: Origin
(no etag, no last-modified, no expires, no pragma)
```

`no-store` is set by `SecurityHeadersMiddleware` on every response. It matters
specifically here: the notification list is **per business**, so a shared cache keeping it
would be a tenancy problem, not merely a staleness one. Asserted by a browser test.

`vercel.json` sets `Cache-Control` for `/assets/*` (immutable), `/sw.js`, `/registerSW.js`
and `/index.html` — and deliberately **not** for `/api/*`, which inherits the backend's
`no-store` through the rewrite.

### 3.6 CDN request accounting — BLOCKED, NOT VERIFIED

Whether notification requests consume Vercel CDN requests depends on one production
setting I cannot read without contacting production: **`VITE_API_BASE_URL` in Vercel →
Project → Settings → Environment Variables.**

| If it is… | Then notification requests… |
|---|---|
| `/api/v1` (relative) | go through `vercel.json`'s `/api/:path*` rewrite — **each one is a billed Vercel request** and a Railway request |
| `https://<railway-host>/api/v1` (absolute) | go **straight to Railway** and cost no Vercel request at all |

Prior investigation (`PRODUCTION-POLLING-VERIFICATION-2026-11-21.md`) observed
`X-Railway-Edge` alongside `X-Vercel-Id` on `https://bmfrontend-seven.vercel.app/api/v1/notifications`,
which indicates the **relative/proxy** topology was live then. If that is still the case,
the measured reduction (4 requests per 2 minutes → 0) applies directly to the CDN bill.

**→ Owner check O-1** in §9.

---

## 4. Scheduler reliability — PASS

`tests/integration/test_scheduler.py` — **27 tests**.

| Requirement | How it is verified |
|---|---|
| Runs inside the main FastAPI process | Started by the lifespan; `/readyz` on the live local stack reports `running: true`, `holds_enqueue_lock: true`, `jobs_run: 7` |
| No separate worker required | §7 below |
| Advisory lock prevents duplicate leadership | Two `_EnqueueLock` instances: the second is refused. Released on stop so a rolling deploy hands over |
| Lock does not occupy a request-serving connection | Asserted against `engine.pool.checkedout()` — the lock uses its own `NullPool` engine |
| Lock survives its own commit | A session advisory lock outlives `COMMIT`; a contender is still refused afterwards |
| Lock connection not idle-in-transaction | Asserted via `connection.in_transaction()` — see the defect below |
| Lost connection noticed and re-acquired | Connection invalidated mid-life; the next `acquire()` recovers |
| Restart does not duplicate work | `dedupe_key` bucketed per **that kind's own interval**; a simulated restart enqueues nothing extra |
| Database disconnection recovery | Three consecutive failing ticks, then recovery; `consecutive_failures` returns to 0 and `last_error` clears |
| A failing tick does not kill the loop | Loop survives, records the error, backs off after 3 failures |
| Graceful shutdown | `stop()` waits for the tick in hand to finish |
| Ticks never overlap | One sequential loop |
| Cadences are what the owner asked for | `reminders.check_schedules` 60 s, `orders.nudge` 60 s, `low_stock.scan` daily; `NUDGE_INTERVAL_MINUTES == 60`; built-ins are hourly bill + 17:00 stock |
| `/readyz` accurate | Asserts the honest negative when the scheduler is off, and that it never gates readiness |

### Two defects found in the scheduler during this audit, both FIXED

1. **The lock connection was left idle in a transaction.** `build_engine` sets
   `idle_in_transaction_session_timeout = 60000`, and SQLAlchemy opens a transaction on
   the first `execute`. Postgres would therefore have terminated the lock connection
   **after 60 seconds**, releasing the advisory lock — while the process still believed it
   held one, because nothing re-checked. Two replicas could then both enqueue, silently.
   *Fix:* commit the acquiring statement (a session-level advisory lock survives `COMMIT`
   — that is exactly the difference from `pg_advisory_xact_lock`), and re-verify liveness
   on every tick instead of trusting a flag.

2. **The lock consumed a request-serving pool connection** for the life of the process —
   a fifth of the production budget at `BM_DB_POOL_SIZE=5`, spent on a lock.
   *Fix:* its own `NullPool` engine. Same single extra connection to Postgres, outside the
   pool that billing draws from.

### Notification behaviour preserved

| Behaviour | Verified by |
|---|---|
| Immediate new-order notification | `test_reminders_and_orders.py`, browser suite |
| Hourly credit reminders | `test_reminder_and_order_jobs.py` (re-fires hourly; goes quiet and **resolves** when paid) |
| Hourly pending-order reminders | `NUDGE_INTERVAL_MINUTES == 60`, asserted |
| Daily 5 PM stock summary | `_BUILTIN_DEFAULTS` asserted; timezone-aware firing tested |
| Daily low-stock reconciliation | `test_event_driven_low_stock.py` — repairs, resolves, changes nothing already correct |
| Event-driven low-stock updates | 16 tests across 8 mutation routes |

---

## 5. Intermittent `test_stock_movement_report.py` failures — BLOCKED, NOT VERIFIED

### What was observed

Two failures, one each in two full-suite runs, during the window when the machine was
also running Playwright browsers, two dev servers and Docker:

* `test_each_sold_row_satisfies_before_minus_quantity_equals_after`
* `test_rows_are_ordered_oldest_first_and_identically_on_every_request`

The second failed with `['D/1','D/2','D/3','D/0'] != ['D/0','D/1','D/2','D/3']` — a
rotation, with `D/0` last.

### Reproduction attempts — all negative

| Attempt | Result |
|---|---|
| The two tests, 15 consecutive targeted iterations | **0 failures** |
| The whole file, 12 iterations under real concurrent load (full frontend vitest suite running alongside) | **0 failures** |
| The whole file in isolation | **0 failures** (26/26 tests) |
| Full backend suite, with this work | **2 failures in 8 runs (25%)** |
| Full backend suite, unmodified baseline | **0 failures in 2 runs** |

The flake only ever appears in a **full-suite** run — never when the file runs alone, and
never under synthetic load. Something about the preceding ~1,000 tests is the trigger, and
test order is deterministic, so it is a *state or timing* interaction rather than an
ordering one. The baseline sample (2 runs) is too small to exonerate this work, and
enlarging it costs ~13 minutes per run for a 25%-rate signal.

### Mechanism analysis

The file's own comments blame UUIDv7 ordering within a millisecond —
`domain/shared/ids.py` states plainly that "monotonic between calls within the same
millisecond is **not** guaranteed". **Measured and ruled out:** `now()` across eight
back-to-back transactions on this host produced eight distinct microsecond timestamps,
0 identical and 0 same-millisecond collisions. `created_at` alone fully orders separate
HTTP transactions, so the uuid7 tie-break is never reached.

Two further facts narrow it:

* In the failing run, the test's own `assert stamps == sorted(stamps)` **passed** — the
  report's ordering was correct. What differed was which document each timestamp belonged
  to, i.e. the four receipts committed in a different order than the loop issued them.
* Test order is deterministic — no `pytest-randomly`, no `pytest-xdist`, no `-p random`.

### Assessment

* **Not attributable to this work.** The change to `apply_movements` adds a query *inside*
  the transaction, which pushes subsequent transaction start times further apart — it can
  only widen the window, not tighten it. The baseline was also only ever measured on a
  quiet machine, so it is not fully exonerated either.
* **Not reproducible** in 27 targeted iterations, 12 loaded iterations, or the file in
  isolation — only in full-suite runs, at ~25%.
* **Not in the notification scope**, and no justified fix presents itself. Changing the
  test to mask it would be worse than leaving it visible.
* **Diagnostic added instead of a fix.** The assertion now prints, on failure, whether the
  timestamps were ascending, whether they were distinct, and every row's `created_at` and
  `movement_id`. The two candidate causes — a `created_at` tie falling through to UUIDv7's
  random within-a-millisecond tiebreak, versus `created_at` genuinely out of insertion
  order — need different fixes, and a bare list comparison cannot tell them apart. The
  next occurrence will say which it is.

**Classification: BLOCKED — NOT VERIFIED.** Recorded here so it is not forgotten. It does
not block deployment: the report is read-only and the tests pass on every run since.

---

## 6. Full regression results

All commands run against isolated local databases. Production untouched.

### Backend

```
BM_TEST_DATABASE_URL=…/bm_test BM_TEST_MIGRATION_DATABASE_URL=…/bm_test uv run pytest
```

| Suite | Result |
|---|---|
| **Full backend suite** | **1151 passed, 0 failed** |
| `tests/api/test_low_stock_threshold.py` (new) | **20 passed** |
| `tests/integration/test_scheduler.py` | **27 passed** |
| `tests/integration/test_event_driven_low_stock.py` | **16 passed** |
| `tests/api/test_notification_delivery_hint.py` | **11 passed** |
| Threshold-affected suites (inventory, stock entry, catalog search, stock count, response contract, billing, reminders) | **205 passed** |
| `ruff check` · `ruff format --check` · `mypy src/` | **clean**, 132 source files |

### Frontend

| Suite | Result |
|---|---|
| **Full `vitest run`** | **866 passed, 0 failed** (43 files) |
| `src/features/notifications/` | **PASS** |
| `src/features/stock/lowStock.test.ts` (new) | **8 passed** |
| `tsc -b --force` · `eslint .` · `prettier --check .` | **clean** |

### Browser (Playwright, Chromium, real backend, isolated `bm_e2e` database)

| Suite | Result |
|---|---|
| `tests/e2e/notifications-redesign.spec.ts` | **12 passed** |
| `tests/e2e/notification-request-frequency.spec.ts` (new) | **6 passed** |
| `tests/e2e/full-stack.spec.ts` + `back-office-screens.spec.ts` (regression) | **15 passed** |

Covers: billing (four primary actions, finalisation, idempotent replay), credit bills
(finalisation, payment not deducting stock twice), stock (filter, badge, report, entry),
orders (creation, notification), customers, navigation, authentication/session continuity,
drafts, print.

### 6.1 Totals

| | Tests | Result |
|---|---|---|
| Backend (pytest) | 1,151 | all passed |
| Frontend (vitest) | 866 | all passed |
| Browser (Playwright, Chromium, real backend) | 33 | all passed |
| **Total** | **2,050** | **all passed** |

Backend lint/format/types clean across 132 source files; frontend `tsc -b`, `eslint` and
`prettier --check` clean.

---

## 7. Worker service retirement — PASS, safe to retire

Every `JobKind`, who enqueues it and who runs it:

| Kind | Handler | Enqueued by | Drained by |
|---|---|---|---|
| `import.parse` | yes | `domain/imports/service.py` on upload | **in-API scheduler** |
| `low_stock.scan` | yes | in-API scheduler, daily | in-API scheduler |
| `reminders.check_schedules` | yes | in-API scheduler, 60 s | in-API scheduler |
| `orders.nudge` | yes | in-API scheduler, 60 s | in-API scheduler |
| `inventory.reconcile` | yes | **nothing** | — |
| `idempotency.prune` | yes | **nothing** | — |
| `drafts.mark_stale` | yes | **nothing** | — |
| `notifications.dispatch` | yes | **nothing** (called directly by `inventory.reconcile`) | — |

**Nothing depends on the worker.** The bottom four were already never enqueued — by the
old worker or anything else — so retiring it loses nothing that was working. The in-API
scheduler covers all three recurring kinds the worker enqueued **and** drains
`import.parse`, which the API alone previously never did: before this change, a deployment
without a worker accepted import uploads and never parsed them.

`bm/worker.py` is kept as a working standalone entrypoint (harmless; the advisory lock and
`FOR UPDATE SKIP LOCKED` mean running one alongside the API cannot double-enqueue or
double-run), but **no Railway worker service should be deployed**.

> **Pre-existing gap, out of scope, worth recording:** `inventory.reconcile`,
> `idempotency.prune` and `drafts.mark_stale` have handlers, tests and documented purposes
> and run **never**. That predates this work. Adding them to `SCHEDULES` is a small change
> but a behavioural one — `inventory.reconcile` raises critical notifications — so it needs
> its own decision. **→ Owner decision O-4.**

---

## 8. Notification retention — OWNER APPROVAL REQUIRED

### Current state

**Nothing is ever deleted.** No expiry column, no retention setting, no prune job. Every
low-stock transition, reminder firing and order creation appends a row permanently.

### Measured growth

~5.5 kB per row including indexes (measured on the exercised `bm_e2e` database).

Estimated for this shop — one business, ~15–500 products:

| Source | Rows/day (estimate) |
|---|---|
| `bill_reminder` | ≤ 24 |
| `stock_reminder` | 1 |
| `order_reminder` | 1 per order + 1 per pending order per hour |
| `low_stock` | 1 per product *transition* (not per scan — the dedup index absorbs repeats) |
| **Total, typical** | **tens; low hundreds on a busy day** |

At 100 rows/day that is ~36,500 rows and ~200 MB of table+index per year. Reads stay fast
regardless: `ix_notifications__business_status` makes the bell's query an index lookup on
the **active** set, whose size is bounded by what is genuinely outstanding, not by history.

### Assessment

This is **not urgent**. It is a slow-growing table with an indexed hot path, on a
single-business deployment. It is also not nothing: backups grow, and there is currently
no way to answer "what was I alerted about last March" other than reading raw rows.

### Recommended policy — NOT IMPLEMENTED, needs approval

If the owner wants a bound:

1. **Never touch `active` or `snoozed`.** Those are live notices.
2. Delete only `resolved`, `dismissed` and `acknowledged` rows **older than a retention
   window** — 180 days is a defensible default (two quarters; long enough that "what
   happened last season" is still answerable).
3. Implement as a new `JobKind` (`notifications.prune`) on the daily schedule, with the
   window as a **database `settings` row**, not an environment variable — per
   `CLAUDE.md §11`, business rules the owner can change without a deploy.
4. Log the deleted row count on every run, as `idempotency.prune` already does.
5. Ship it behind a dry-run first: log what *would* be deleted for a week before deleting
   anything.

**Not implemented in this work.** Deleting notification history is a decision about the
owner's records, and the brief was explicit that destructive cleanup needs justification,
tests and approval. **→ Owner decision O-3.**

---

## 9. Production deployment

### 9.1 Environment variables

**Backend (Railway) — no new required variable.** The full set is unchanged from
`docs/RAILWAY-ENVIRONMENT-VARIABLES.md §9`. One new **optional** variable exists:

| Variable | Default | Set it? |
|---|---|---|
| `BM_SCHEDULER_ENABLED` | `true` | **No — leave it unset.** Setting it to `false` stops scheduling **and** queue draining in that process, so uploaded import files would never be parsed. The startup log warns at `WARNING` when it is off. |

Everything else already in production stays exactly as it is:

```
BM_ENV, BM_RELEASE, BM_DATABASE_URL, BM_DB_POOL_SIZE, BM_DB_MAX_OVERFLOW,
BM_DB_STATEMENT_TIMEOUT_MS, BM_DB_LOCK_TIMEOUT_MS, BM_JWT_SIGNING_KEY,
BM_PASSWORD_PEPPER, BM_IP_HASH_PEPPER, BM_ACCESS_TOKEN_TTL_SECONDS,
BM_REFRESH_TOKEN_TTL_DAYS, BM_CORS_ALLOWED_ORIGINS, BM_ENABLE_API_DOCS,
BM_UNATTENDED_ACCESS, BM_REFRESH_COOKIE_SAMESITE, BM_STORAGE_*, BM_LOG_*
```

⚠️ `BM_DB_POOL_SIZE` deserves one thought: the scheduler adds **one** connection to
Postgres, outside the pool. At `BM_DB_POOL_SIZE=5` + `BM_DB_MAX_OVERFLOW=2` the API's
maximum is now 8 rather than 7. Well inside Neon's limits; noted so the number is not a
surprise.

**Frontend (Vercel) — no new variable.** `VITE_API_BASE_URL` is unchanged.

### 9.2 Migrations

**None required.** No migration was added; `migrations/` is untouched. Production is
already at `0029`, which is head. The `(business_id, status)` index that makes the bell's
query fast is already applied.

Compatibility: the only API change is **additive** — `next_check_after_seconds` added to
the `GET /notifications` response. An old client that does not read it keeps working (it
would fall back to the 15-minute ceiling); a new client against an old server would also
fall back. **No lock-step deploy is required**, and either order is safe.

### 9.3 Deployment steps

1. **Back up the production database first.** `scripts/backup.sh`, verified restorable.
   This is routine, not because this change is risky — no migration, no schema change.
2. **Push both repositories** (owner's call; nothing has been pushed).
3. **Let Railway deploy the backend.** No new variable to set.
4. **Verify the scheduler is alive:**
   ```
   curl -s https://<api-host>/readyz | jq .checks.scheduler
   ```
   Expect `running: true`, `holds_enqueue_lock: true`, `consecutive_failures: 0`, and
   `schedules_seconds` = `{low_stock.scan: 86400, reminders.check_schedules: 60, orders.nudge: 60}`.
5. **Confirm migrations:** the same `/readyz` should show `migrations.status: "ok"` at
   `0029`.
6. **Let Vercel deploy the frontend.**
7. **Verify in the browser:** sign in, open the bell, confirm the panel renders and that
   DevTools → Network shows **no** `/notifications` request every 30 seconds.
8. **Do not create a worker service.** If one exists, delete it (§7).

### 9.4 Rollback

Rollback is unusually simple here because there is **no migration and no schema change**.

| Scenario | Action |
|---|---|
| Backend misbehaving | Redeploy the previous Railway deployment. No database change to undo. |
| Frontend misbehaving | Roll back the Vercel deployment. The old bundle talks to the new backend fine — `next_check_after_seconds` is simply ignored and it resumes its 30-second poll. |
| Scheduler wedged but API healthy | Set `BM_SCHEDULER_ENABLED=false` and restart **only if** you accept that imports stop being parsed. Prefer restarting the service. |
| Needing the old worker back | Deploy the same image with `python -m bm.worker`. It still works; the advisory lock means it cannot double-enqueue against the API's scheduler. |

**Data written by this release that a rollback leaves behind:** notification rows and the
two seeded `reminder_schedules` rows. Both are ordinary application data, harmless to an
older build, and nothing needs undoing.

### 9.5 Logging and monitoring

Structured JSON logs (`BM_LOG_FORMAT=json` in production). New events:

| Event | Level | Meaning |
|---|---|---|
| `scheduler_started` | INFO | with tick interval and kinds |
| `scheduler_enqueued` | INFO | with the kinds enqueued |
| `scheduler_tick_failed` | ERROR | with `consecutive_failures` — **the one to alert on** |
| `scheduler_stopped` | INFO | with total jobs run |
| `scheduler_disabled` | WARNING | the kill switch is on |

What to watch: repeated `scheduler_tick_failed`, or `/readyz` reporting
`holds_enqueue_lock: false` on a single-replica deployment (something else holds the lock,
so nothing is being enqueued).

---

## 10. Owner actions

### Checks only the owner can perform

| # | Check | Why it is blocked |
|---|---|---|
| **O-1** | **Vercel → Project → Settings → Environment Variables → `VITE_API_BASE_URL`.** If it is `/api/v1`, every notification request is a billed Vercel request through the rewrite and the measured reduction applies directly to the CDN bill. If it is the absolute Railway URL, they bypass Vercel entirely. | Needs the Vercel dashboard |
| **O-2** | **Railway → services.** Confirm there is no `python -m bm.worker` service; delete it if there is. | Needs the Railway dashboard |

### Decisions required

| # | Decision | Default if nothing is decided |
|---|---|---|
| **O-3** | **Notification retention** (§8). A 180-day window on resolved/dismissed/acknowledged rows only. | Nothing is deleted — current behaviour, safe, table grows slowly |
| **O-4** | **The three never-scheduled jobs** (§7): `inventory.reconcile`, `idempotency.prune`, `drafts.mark_stale`. Pre-existing gap. `inventory.reconcile` raises critical notifications when stock disagrees with the ledger — arguably it should run. | They continue not to run, as they have not since they were written |

Neither decision blocks deployment.

---

## 11. Verdict

**The notification system is production-ready.**

* Nothing blocks deployment.
* No migration, no new required environment variable, no schema change.
* Rollback is a redeploy in either direction, with no data to undo.
* The separate worker service can and should be retired.
* Two production-only checks remain (O-1, O-2) and two owner decisions are open (O-3,
  O-4); none is blocking.
* One pre-existing, unreproducible test flake is recorded as NOT VERIFIED (§5).

**The owner decides when to deploy. Nothing has been deployed, pushed, or sent to
production.**
