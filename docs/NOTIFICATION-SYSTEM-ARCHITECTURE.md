# Notification System — Architecture

**Status:** implemented 2026-11-22. Supersedes the design described in
`../../docs/NOTIFICATION-SYSTEM-COMPLETE-INVESTIGATION.md`, which remains accurate as a
record of what the system was *before* this change and is the reasoning behind it.

This document is the current contract: what creates a notification, when, how the browser
finds out, and what happens when any of it fails.

---

## 1. The shape of it

```mermaid
flowchart TB
    subgraph browser["Browser — the counter PC"]
        BELL["NotificationBell<br/>bell · badge · state · actions"]
        PANEL["NotificationPanel<br/>popover · severity groups"]
        BANNER["AutoAlertBanner<br/>always-on-screen alerts"]
        FEED["notificationsPoller<br/>module singleton"]
        BELL --> PANEL
        BELL --> BANNER
        BELL <-->|subscribe / receive| FEED
    end

    subgraph api["Railway — ONE service"]
        ROUTES["FastAPI routes<br/>/api/v1/notifications"]
        SCHED["infra/queue/scheduler.py<br/>asyncio task in the lifespan"]
        MOVE["InventoryService.apply_movements<br/>THE sole stock writer"]
        ORDERS["orders.create_order"]
    end

    DB[("PostgreSQL<br/>notifications · jobs<br/>reminder_schedules · orders")]

    FEED -->|"GET ?limit=50<br/>(interaction or scheduled wake)"| ROUTES
    ROUTES -->|"items + next_check_after_seconds"| FEED
    BELL -->|":acknowledge / :snooze / :dismiss"| ROUTES
    ROUTES <--> DB

    MOVE -->|"event-driven: raise/resolve low_stock<br/>IN the caller's transaction"| DB
    ORDERS -->|"immediate order_reminder"| DB
    SCHED -->|"enqueue + drain jobs"| DB
    SCHED -->|"reminders · nudges · daily reconcile"| DB
```

**There is no worker service.** `bm/worker.py` still exists as a standalone entrypoint for
anyone who wants one, but nothing requires it: the scheduler runs inside the API process.

---

## 2. What creates a notification

| Kind | Trigger | Where | Timing |
|---|---|---|---|
| `low_stock` | a stock movement takes a balance **to or below** its reorder level | `InventoryService.apply_movements` → `low_stock.sync_for_products` | **immediate**, in the same transaction as the movement |
| `low_stock` (repair) | the daily sweep finds a balance whose alert is missing or stale | `low_stock.scan_business` | once a day |
| `order_reminder` | an order is saved | `orders.create_order` | **immediate**, same transaction |
| `order_reminder` (nudge) | a pending order has not been re-surfaced for an hour, or its own `remind_at` is due | `orders.nudge_pending` | checked every 60 s, re-surfaces **hourly** |
| `bill_reminder` | ≥1 credit invoice is unpaid or partially paid | `reminders._fire_one` | **hourly** |
| `stock_reminder` | ≥1 active low-stock alert, at or after 17:00 business-local | `reminders._fire_one` | **daily, 5 PM** |
| `custom_reminder` | an owner-created schedule falls due | `reminders._fire_one` | every 3–5 h, or a chosen time |

### Event-driven low stock — the important change

`apply_movements` is the **only** code path permitted to write `inventory_balances` or
`stock_movements`, and a structural test enforces it. Hooking detection there means all
eight stock-moving routes inherit it on one rule:

sale finalisation · stock entry · purchase receipt · return · cancellation · bill
amendment · inventory adjustment · stock count.

It **raises and resolves**. Stock arriving back above the minimum closes the open alert,
which is what frees the dedup slot so a later dip raises a genuinely new one instead of
being absorbed by a stale alert the owner dismissed weeks ago.

Because the hook is inside the caller's transaction, a sale that rolls back takes its
notification with it.

### Deduplication

`uq_notifications__active_per_entity` — `UNIQUE (business_id, kind, entity_id) WHERE
status = 'active' AND entity_id IS NOT NULL` — makes "is there already an open alert for
this exact thing" a database guarantee rather than a check-then-insert race.
`notify_once` uses it as an `ON CONFLICT … DO NOTHING` target, so stock crossing the
threshold repeatedly cannot accumulate alerts.

### The threshold — one rule, settled

**`current_quantity <= minimum_quantity`.** A part with 10 on the shelf and a minimum of
10 **is** low. `reorder_level` means *reorder when you reach this*, so reaching it is the
moment that matters.

This was settled by the owner on 2026-11-22 and closes a split that had existed since the
feature was built: the listing surfaces compared `<` while the notification producers and
the post-sale warning compared `<=`. A part sold down to exactly its minimum therefore
raised a bell alert that the Stock screen refused to list — and the next reconciliation
then resolved the alert, so the notice appeared and vanished with nothing having changed.

Six implementations, all now `<=`. They move together or not at all:

| Where | Decides |
|---|---|
| `CatalogService.ProductView.is_low_stock` | the product badge — **the authority; its docstring is the record** |
| `InventoryService.balances(low_stock_only=True)` | the Stock screen's "Low stock only" filter |
| `ReportService.stock_position` | the stock report |
| `notifications/low_stock.py` (`sync_for_products`, `scan_business`) | which products carry a notification |
| `SalesService._low_stock_warnings` | `LOW_STOCK_AFTER_SALE` — always was `<=` |
| `bmfrontend/src/features/stock/lowStock.ts::isLow` | the client-side badge |

`tests/api/test_low_stock_threshold.py` asks all five server-side surfaces the same
question at once and requires the same answer, which is what a per-module test could not
do — each was internally consistent while disagreeing with its neighbours.
`bmfrontend/src/features/stock/lowStock.test.ts` covers the sixth.

The owner-facing wording moved with the rule: the Stock screen says "reaches or falls
below its own Min Qty", and a notification for a part exactly at its minimum reads "down
to the minimum of 10" rather than the plain falsehood "below the minimum of 10".

### Reminders that become false

A reminder reading "3 credit bill(s) still pending" must not sit in the bell after the
last of those bills is paid. When a reminder has nothing to report, `_fire_one` now
**resolves** the previous firing's notification before returning. `resolved`, not
`dismissed`: the condition genuinely went away, which is the distinction
`NotificationStatus` exists to keep.

---

## 3. The scheduler

`src/bm/infra/queue/scheduler.py`, started and stopped by the FastAPI lifespan in
`src/bm/main.py`. `BM_SCHEDULER_ENABLED` (default **true**) turns it off for a process
that must not do background work — the pytest suite pins it off.

| Kind | Interval | Why |
|---|---|---|
| `reminders.check_schedules` | 60 s | The 5 PM summary has to land on the minute |
| `orders.nudge` | 60 s | A one-time per-order `remind_at` has to land on the minute |
| `low_stock.scan` | 86 400 s | A reconciliation now, not the detector |

**The cadence a reminder re-fires at is a different thing from how often it is checked.**
Credit bills re-fire hourly (`reminder_schedules.interval_hours = 1`); pending orders
re-surface hourly (`orders.NUDGE_INTERVAL_MINUTES = 60`). Both are *checked* every minute.

### What makes it safe inside a request-serving process

1. **It never blocks the event loop.** Every database call goes through
   `asyncio.to_thread`. A slow reconciliation delays the next tick, never a request.
2. **Exactly one process enqueues**, guarded by a Postgres session-level advisory lock on
   its own connection. A second replica — or the parent of a `uvicorn --reload` pair —
   fails to acquire it and quietly does not enqueue.
3. **Draining is deliberately not lock-guarded.** `FOR UPDATE SKIP LOCKED` is what makes a
   Postgres queue safe for several consumers (ADR-004). Gating it would mean a replica
   that lost the enqueue lock also stopped parsing uploaded import files.
4. **Ticks never overlap.** One sequential loop.

### Two subtleties the lock has to get right

* **The lock connection must not sit idle *in a transaction*.** `build_engine` sets
  `idle_in_transaction_session_timeout = 60000`, and SQLAlchemy opens a transaction on the
  first `execute` — so an uncommitted lock connection is terminated after a minute, taking
  the advisory lock with it while the process still believes it holds one. The acquiring
  statement is therefore **committed**; a session-level advisory lock survives `COMMIT`,
  which is exactly the difference from `pg_advisory_xact_lock`.
* **It must not consume a request-serving connection.** It uses its own `NullPool` engine,
  so the one extra connection is outside the pool billing draws from.

Liveness is re-checked every tick rather than trusted, so a connection the database closed
is noticed and the lock re-acquired — or found to be another process's now.

### Observability

`GET /readyz` reports `checks.scheduler`: whether it is running, whether this replica
holds the enqueue lock, the last tick, what was last enqueued, jobs run, consecutive
failures and the last error. It does **not** make the replica unready — refusing to serve
bills because a background loop is wedged would be the worse outcome.

```json
"scheduler": {
  "running": true, "holds_enqueue_lock": true,
  "last_tick_at": "…", "jobs_run": 3,
  "consecutive_failures": 0, "last_error": null,
  "schedules_seconds": {"low_stock.scan": 86400, "reminders.check_schedules": 60, "orders.nudge": 60}
}
```

`holds_enqueue_lock: false` on a single-replica deployment means something is wrong. On a
multi-replica one it is the normal state for every replica but one.

---

## 4. How the browser finds out

**The unconditional 30-second poll is gone.** It cost 120 requests an hour per visible
tab, every one through Vercel's `/api/*` rewrite and on to Railway, almost all answering
"nothing has changed".

`src/features/notifications/notificationsPoller.ts` now fetches for two kinds of reason:

**The person did something** — fetches once, immediately:

| Trigger | Mechanism |
|---|---|
| Sign-in / application open | first subscriber |
| Opening the bell | `refreshNotifications()` from `NotificationBell` |
| Returning to the tab | `visibilitychange` → visible |
| Network reconnect | `online` |

**A scheduled notification is about to exist** — the server says when:

Every `GET /notifications` response carries **`next_check_after_seconds`**, computed by
`reminders.next_due_in_seconds` from the same `reminder_schedules` rows the firing logic
reads, plus the soonest pending-order nudge. The client arms a single `setTimeout` for
exactly that long. Bounded to **60 s … 900 s** on both sides.

The hint comes from the server because a client-side constant would be a second copy of
the schedule: the owner can disable, delete or add a reminder at any time, and a hardcoded
cadence would carry on regardless.

### Delivery delay, by notification type

| Notification | Created | Visible in the browser |
|---|---|---|
| `low_stock` from a stock movement | immediately | next interaction; ≤ 15 min unattended |
| `order_reminder` on save | immediately | next interaction; ≤ 15 min unattended |
| `bill_reminder` (hourly) | ≤ 60 s after due | ≤ ~2 min — the hint shortens as it approaches |
| `stock_reminder` (5 PM) | ≤ 60 s after 17:00 local | ≤ ~2 min — same |
| `order_reminder` nudge (hourly) | ≤ 60 s after due | ≤ ~2 min — same |

A scheduled notification is never more than ~15 minutes undiscovered even in the worst
case, and in practice lands within about two minutes because the hint shrinks as the
schedule approaches. An idle tab with nothing scheduled soon makes **zero** requests.

### The guards, unchanged

* **Single-flight** — at most one `GET /notifications` in flight; a refresh asked for while
  one is running joins it.
* **A floor between request starts** — `MIN_FETCH_INTERVAL_MS = 5_000`, whatever asks.
  A request the floor turns down is **deferred to the moment the floor expires, not
  dropped**: an explicit "show me" that answers with stale data is a worse failure than an
  extra request, and only one deferral is ever pending, so the ceiling is unchanged.
* **Visibility gate** — a hidden tab never fetches and does not consume the floor.

### Measured request volume

**Measured in a real browser** against a real backend
(`tests/e2e/notification-request-frequency.spec.ts`, which prints each figure):

| Scenario | Old (30 s poll) | Measured now |
|---|---|---|
| 1 idle active tab, 2 min | 4 | **0** |
| 2 simultaneously open tabs, 2 min | 8 | **0** |
| 30 bell open/close cycles (~5 s) | ~1 | **1** |
| 20 offline/online transitions (~17 s) | ~1 | **4** |
| 5 min active session, 15 screen changes | 10 | **0** |

Unit-level storm ceilings (`notificationsPoller.test.ts`, printed per run): a 7 Hz
`visibilitychange` storm, a 7 Hz remount storm, 200 offline/online cycles and 200
hide/show cycles each produce **13 requests per 60 s** — the floor's 1-per-5-seconds
ceiling — against the ~7 req/s those same mechanisms produced before `ad9e6ec`. Eight
idle hours cost **32 requests**, against 960 under the old poll.

---

## 5. Appearance

Scoped to notifications. No shared primitive, token, layout, screen or workflow changed.

* **`NotificationPanel.tsx`** (new) — a right-anchored popover, not the shared `Dialog`. A
  centred modal covered the billing grid and had to be dismissed to get back to the bill.
  `role="dialog"` + `aria-modal="false"`, Escape to close, outside-click to close
  (`mousedown`, so a click behind does not land on whatever moved under the cursor), focus
  into the panel on open and back to the bell on close.
* **Severity groups** — Urgent / Needs attention / For information, each heading carrying
  its own count (`Urgent · 2`). The server already sorts by severity then recency, so this
  adds headings to an order that is already correct.
* **Cards** — a coloured rail for the glance, a chip stating the severity **in words** for
  everyone else (status is never colour alone), a relative timestamp, and the existing
  four actions unchanged in meaning.
* **Badge** — navy for an ordinary count, red only when something is genuinely urgent, so
  red keeps meaning something. A 40 px target.
* **`AutoAlertBanner`** — same chip treatment; placement, height cap, scrolling and
  `role="group"` untouched.
* **Motion** — ≤ 140 ms, appearance only, and the global `prefers-reduced-motion` block
  already reduces it to nothing.

---

## 6. Failure modes

| Situation | Behaviour |
|---|---|
| Backend restarts near a scheduled time | `last_enqueued` resets, so each kind is due at once; the interval-bucketed `dedupe_key` stops a duplicate row |
| Backend unavailable | The fetch fails, the last list stays on screen, the next check is armed at the fallback interval — the feed cannot die silently |
| A tick raises | Recorded on `status()`, logged, loop survives; three consecutive failures back off to 30 s |
| Scheduler task takes longer than its interval | The next tick is delayed; ticks never overlap |
| Database unavailable | Ticks fail and back off; `/readyz` reports it |
| A job fails halfway | The queue's own backoff and dead-letter apply (`runner.py`), unchanged |
| Duplicate job execution | Handlers are idempotent: `notify_once` + the partial unique index, `last_fired_at`, `last_reminder_at` |
| Two replicas | One holds the enqueue lock; both drain, which is safe by `FOR UPDATE SKIP LOCKED` |
| Lock connection dies | Noticed on the next tick's liveness check and re-acquired |
| Stock operation retried | The idempotency key replays the stored response and moves no stock, so no second notification |
| Stock crosses the threshold repeatedly | One open alert; the index absorbs the rest |
| A credit bill is paid off | The next hourly check resolves the standing reminder |
| User offline when a notification is created | It waits in the database; the `online` listener fetches on reconnect |
| Tab hidden / visible | No fetches while hidden; an immediate one on becoming visible |
| Panel opened repeatedly | Floor-bounded; a declined refresh is deferred, not lost |

### Retention

Unchanged, deliberately: **nothing is deleted**. There is no expiry column, no prune job
and none was added — removing a notification history is not something a notification
rework gets to decide. It remains a known consideration: the table grows without bound,
and `ix_notifications__business_status` is what keeps reads fast as it does.

---

## 7. Running and testing it locally

```bash
# Postgres (already running as bm-postgres-1 on 55439 in this environment)
cd bmbackend && docker compose up -d postgres

# The API — the scheduler starts with it
BM_DATABASE_URL=postgresql+psycopg://bm_app:bm_app_local_only@localhost:55439/bm \
BM_JWT_SIGNING_KEY=… BM_PASSWORD_PEPPER=… BM_IP_HASH_PEPPER=… \
  uv run uvicorn bm.main:create_app --factory --port 8000

# The counter application
cd ../bmfrontend && npm run dev     # http://localhost:5173
```

Confirm the scheduler: `curl -s localhost:8000/readyz | jq .checks.scheduler`

### Tests

```bash
# Backend — needs a scratch database; the suite refuses to run against `bm`
cd bmbackend
BM_TEST_DATABASE_URL=postgresql+psycopg://bm_app:bm_app_local_only@localhost:55439/bm_test \
BM_TEST_MIGRATION_DATABASE_URL=postgresql+psycopg://bm_migrate:bm_migrate_local_only@localhost:55439/bm_test \
  uv run pytest tests/integration/test_scheduler.py \
                tests/integration/test_event_driven_low_stock.py \
                tests/api/test_notification_delivery_hint.py

# Frontend
cd ../bmfrontend && npx vitest run src/features/notifications/

# Browser — against a real API on an isolated database (see the spec's own header)
BM_DEV_API_PROXY=http://127.0.0.1:8011 BM_E2E_PORT=4273 \
  npx playwright test tests/e2e/notifications-redesign.spec.ts
```

---

## 8. Decisions, and what was rejected

| Decision | Rejected alternatives, and why |
|---|---|
| **Scheduler as an asyncio task in the FastAPI lifespan** | *APScheduler* — a new dependency needs an ADR, and nothing here needs its cron parser. *External cron* — the owner asked for one service, not two. *Keeping the worker* — the thing being removed. |
| **A Postgres advisory lock for single-enqueue** | *A leader-election table* — more moving parts and a liveness problem of its own. *Relying on the dedupe key alone* — the audit found its bucket mismatched the interval. |
| **Hook low stock into `apply_movements`** | *Hooking each of the eight callers* — guaranteed drift, which is exactly the defect being fixed. *Database triggers* — business logic outside the application, invisible to the test suite. |
| **A server-advertised `next_check_after_seconds`** | *A longer fixed interval* — either too slow for the 5 PM summary or too fast when idle, and it would be a second copy of the schedule. *Fetch only on interaction* — a cashier on the billing screen since four o'clock performs none, so the 5 PM summary would never appear. *SSE / WebSockets* — a long-lived endpoint, `LISTEN/NOTIFY` on a pooled Neon URL, and `EventSource` cannot send an `Authorization` header; a large amount of new infrastructure for a two-person shop. *Web Push* — the only option that reaches a closed browser, and the only one that needs a new table, VAPID keys and a third-party dependency; whether the owner wants to be notified at home is a business question nobody has asked. |
| **Defer a floor-declined refresh rather than drop it** | *Dropping it* — found by the browser suite to show stale data after a sale. *Removing the floor* — reinstates the storm the floor exists to bound. |
| **A local popover component** | *Adding `Popover` to `primitives.tsx`* — a new shared abstraction in front of every screen's future, on the strength of one caller. |
