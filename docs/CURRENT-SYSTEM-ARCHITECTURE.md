# Current System Architecture — National Auto Spares

**Date:** 2026-11-21. Describes the system **as implemented**, verified by reading the code, not
the originally "proposed" stack where the two disagree (`bmfrontend/CLAUDE.md` itself says to
trust the code over the docs when they conflict — this document follows that rule).

---

## 1. Topology

```
┌─────────────┐      HTTPS       ┌──────────────────────────┐
│   Browser    │ ───────────────▶│  Vercel (bmfrontend-      │
│ (counter PC) │                  │  seven.vercel.app)        │
│              │◀──────────────── │  - Static assets (CDN)    │
└──────────────┘   HTML/JS/CSS/   │  - vercel.json rewrites   │
                    SW assets     │    /api/* → Railway        │
                                  └─────────────┬─────────────┘
                                                 │ HTTPS, proxied
                                                 ▼
                                  ┌──────────────────────────┐
                                  │ Railway (bmbackend-        │
                                  │  production.up.railway.app)│
                                  │  - API container            │
                                  │    (uvicorn, bm.main)       │
                                  │  - Worker container         │
                                  │    (python -m bm.worker)    │
                                  └─────────────┬─────────────┘
                                                 │ postgresql+psycopg,
                                                 │ two roles (bm_app, bm_migrate)
                                                 ▼
                                  ┌──────────────────────────┐
                                  │   Neon PostgreSQL 16       │
                                  │   (production database)    │
                                  └──────────────────────────┘
```

Every browser-initiated request — static asset or API call — passes through Vercel's edge, which
is why both count against its CDN request budget (the fact that triggered this whole engagement).
The frontend never talks to Railway directly; `src/api/client.ts`'s `BASE_URL` defaults to the
relative path `/api/v1`, which `vercel.json`'s rewrite (`/api/:path* → .../api/:path*`) resolves to
Railway invisibly to the browser, satisfying the page's own CSP (`connect-src 'self'`) without a
cross-origin exemption.

The API and worker are **one Docker image, two entrypoints** (`bmbackend/Dockerfile`'s own
comment): identical code, so a job handler can never drift from the service logic it calls. Each
process opens its own connection pool to Neon independently (see §4).

---

## 2. Frontend (`bmfrontend`)

### 2.1 Entry and shell

`src/main.tsx` registers the service worker (`virtual:pwa-register`, wired through
`src/app/swUpdate.ts`) and renders `<AppShell />` (`src/app/AppShell.tsx`) inside `StrictMode`.
`AppShell` owns:
- **Startup** (`src/app/bootstrap.ts`): Dexie-first, then a silent token refresh through the
  shared single-flight path, then `GET /sync/bootstrap` for everything the client is not allowed to
  hardcode (money policy, draft limits, server clock, counter identity).
- **Navigation**: a plain `useState<Screen>` switch — **no router is used** (see Finding F7;
  `react-router-dom` is declared but never imported).
- **Session**: an in-memory-only module (`src/api/session.ts`); the access token is never
  persisted to any storage, enforced by a structural test.
- **The sync scheduler** (`src/features/sync/scheduler.ts`), started once per signed-in session,
  stopped on sign-out.

### 2.2 State ownership (as built, matching `CLAUDE.md §4`'s intended boundaries)

| State | Owner | Mechanism |
|---|---|---|
| Server data (products, invoices, customers, reports) | Per-screen `useState` + manual `useEffect` fetch | **Not** TanStack Query — it's declared in `package.json` but never imported; every screen fetches and caches its own copy by hand |
| Draft bills | Zustand (`draftStore.ts`) + Dexie | Dexie-first write, debounced ~300ms for text |
| Shared cross-component state (orders, billing drafts) | Zustand stores (`ordersStore.ts`, `draftStore.ts`) | One store, multiple components read it — see §2.4 |
| UI-only state (dialogs, filters, active tab) | Local `useState` | — |
| Session (token, permissions) | In-memory module, never persisted | — |

### 2.3 The API client — one chokepoint

`src/api/client.ts` is the **only** place `fetch()` appears in `src/` (enforced by a structural
test). It owns:
- Idempotency-key vs. request-id discipline (stable across retries vs. fresh every attempt).
- A single shared, cross-tab-locked (`navigator.locks`) token-refresh promise — twenty concurrent
  401s await one refresh, not twenty.
- Bounded retry: six backoff steps, GET-only or idempotency-keyed mutations, never a bare retry on
  a keyless `POST`.

### 2.4 Polling and timers — the full inventory (post-fix)

| Timer | File | Interval | Network cost while idle | Pauses when hidden? |
|---|---|---|---|---|
| Notification bell | `NotificationBell.tsx` | 30s | 1 request/tick | **Yes** (fixed this session) |
| Sync scheduler (busy/idle) | `scheduler.ts` | 30s busy / 300s idle | **Zero** — only fires `sync.push` when the outbox actually has rows | No (doesn't need to — already free when idle) |
| Catalog cache refresh | `scheduler.ts` | 1,800s (30 min) | 1+ requests only if the delta has rows | No |
| Proactive token renewal | `renewal.ts` | ~13 min (token TTL − margin) | 1 request per actual renewal | Renews immediately on becoming visible, floor of 30s |
| Imports screen job polling | `ImportsScreen.tsx` | 1.5s | Only while a job is in a transient state, **and the screen is unreachable from navigation** (withdrawn — not in the `Screen` union) | n/a (unreachable) |

Before this session, the notification bell's effect was torn down and immediately re-run on every
parent re-render (because its `load()` depended on an unstable inline callback from `AppShell`),
turning the intended 30-second poll into a loop bounded only by round-trip time. That is fixed; see
this engagement's earlier `VERCEL-CDN-REQUEST-AUDIT-2026-11-21.md` for the original finding, and
`RESOURCE-CONSUMPTION-REPORT.md` §1 here for the current, quantified steady state.

### 2.5 Offline / sync architecture

Two Dexie-backed queues, deliberately not merged:
- **`outbox`** — drafts/customers, always safe to replay, may be created offline.
- **`pending_finalizations`** — a sale whose response was lost; retried with its *original*
  idempotency key, never a new one, and never created by an offline action (no offline cash/credit
  finalisation exists at all).

Six sync triggers (`online`, visibility/focus, a jittered timer, a post-mutation debounce, manual
"Try now", Background Sync as an extra, never the only path) — all converging on one `run()` that
is a no-op, network-free, if there is nothing queued. Only the Web-Lock-elected "owner" tab runs the
scheduler at all; other tabs are read-only for sync purposes (though the notification bell, which is
not sync, polls independently per tab — see §2.4's fix).

### 2.6 Service worker

`vite-plugin-pwa`, `registerType: 'prompt'`, hand-wired through `swUpdate.ts` so an update never
force-reloads mid-sale. `workbox.runtimeCaching: []` and `navigateFallbackDenylist: [/^\/api\//]` —
confirmed in `vite.config.ts`: **the app shell only is cached; no `/api/` route is ever served from
the cache**, matching `ADR-031`. This was checked and found already correct — not a contributor to
the original CDN overage, and not changed in this audit.

---

## 3. Backend (`bmbackend`)

### 3.1 Module layering

```
api/  →  domain/<module>/  →  infra/
```
Enforced by `.importlinter` (three contracts: layering, infra-never-imports-upward,
api-never-touches-ORM-directly) — **re-verified in this audit**: `uv run lint-imports` reports 3/3
contracts kept across 971 dependencies, 0 violations.

- `api/` — FastAPI routers and Pydantic schemas. No business logic.
- `domain/<module>/service.py` — the only cross-module entry point; `inventory` is the sole writer
  of `inventory_balances`/`stock_movements`, `sales` owns invoices/drafts, `credit` owns
  payments/allocations.
- `infra/` — sessions, repositories, the job queue, logging, alerting. Never imports `domain`.

### 3.2 Request lifecycle

`src/bm/main.py`'s `create_app()` registers, in the order that actually governs execution
(Starlette wraps in reverse of `add_middleware` call order — see Finding F11 for the inverted
comment): CORS (if configured) → Correlation (binds `X-Request-Id`, tenant context) →
`RequestLogMiddleware` (one structured JSON line per request, no bodies) → `BodySizeLimitMiddleware`
→ `SecurityHeadersMiddleware` (CSP, HSTS, `Cache-Control: no-store` on every response) → the route.

Each request gets **one transaction** via dependency-injected `db_write()`/`db_read()`
(`api/deps.py`), wrapping `infra/db/session.py`'s `transaction()` (commit on success, rollback on
exception, always closed) or `read_session()` (always rolled back, never committed). Services never
call `.commit()` themselves — enforced structurally, not just by convention.

### 3.3 The job queue — Postgres only, no broker

`jobs` table, claimed with `SELECT ... FOR UPDATE SKIP LOCKED` (`infra/queue/runner.py`), one row
per attempt, idempotent per documented key (`handlers.py:20-30`). The worker (`worker.py`) loops
once a second: enqueue anything due on its ticker (`low_stock.scan` 300s, `reminders.check_schedules`
60s, `orders.nudge` 60s — each enqueue deduplicated by a real unique partial index on
`jobs(dedupe_key)`, not just an in-memory guard, so it's safe even with two worker processes), then
claim and run one job. See Findings F3 (several registered kinds never auto-enqueued) and F8 (a
redundant existence check on every tick, now fixed).

### 3.4 Notifications — generation vs. delivery, correctly separated

- **Generation** (expensive): `domain/notifications/low_stock.py`'s `scan_business()` — one query
  joining `inventory_balances`⟗`products` filtered on `quantity < reorder_level` — runs on the
  worker's 300-second ticker, **not** on every poll. Idempotent against re-raising an already-open
  alert for the same product (the `uq_notifications__active_per_entity` partial unique index).
- **Delivery to the bell** (cheap, until F1): `domain/notifications/service.py`'s `active_for()` —
  a single `SELECT` against the `notifications` table only, read by `GET /notifications`, polled
  every 30 seconds per visible tab (§2.4). This is the correct architecture — the 30-second cadence
  never re-triggers the expensive scan — undermined only by the missing index (F1), not by the
  query doing the wrong amount of work.

### 3.5 Database

58 tables (full inventory in `RESOURCE-CONSUMPTION-REPORT.md` §2), tenant-scoped by `business_id`
on every table that needs it, with append-only privilege enforcement (no `UPDATE`/`DELETE` grant
for `bm_app`) on `stock_movements`, `payments`, `payment_allocations`, `audit_logs`,
`invoice_revisions`, `invoice_revision_items`. Connection pool: `pool_size=10`, `max_overflow=5`,
`pool_pre_ping=True`, `pool_recycle=1800s`, per-connection `statement_timeout`/`lock_timeout`/
`idle_in_transaction_session_timeout` set via a `connect` event — a stuck finalisation fails fast
rather than hanging a counter. **Each process (API, worker) pools independently** — up to 15
connections per process at defaults, 30 total if both run as separate Railway services; no problem
observed, noted as something to watch if Neon's connection cap is ever approached.

### 3.6 Operational endpoints

`GET /healthz` — liveness, **no database access** (by explicit design). `GET /readyz` — readiness,
one `SELECT 1` plus a cached Alembic-head comparison, inside one read-only session. Neither is
wasteful; Railway's actual probe interval/path could not be confirmed from this repository (no
`railway.toml`/`railway.json` found) and would need checking directly in Railway's dashboard or
`docs/RAILWAY-ENVIRONMENT-VARIABLES.md`/`PRODUCTION-DEPLOYMENT-GUIDE.md`.

---

## 4. Infrastructure

- **Vercel** (`bmfrontend/vercel.json`): static hosting + `/api/*` rewrite proxy to Railway. Caching
  headers: immutable + 1-year for hashed assets under `/assets/`, `no-cache, must-revalidate` for
  `index.html`/`sw.js`/`registerSW.js`. Every API response carries `Cache-Control: no-store` from
  the backend itself, so nothing between browser and database ever caches a stale figure — correct
  for this application's rules, and confirmed to mean the frontend's render-loop bug (now fixed)
  reached the real backend on every iteration rather than being absorbed anywhere.
- **Railway**: two services from one image (API, worker), per `Dockerfile`/`docker-compose.yml`'s
  topology. Exact health-check configuration not found in-repo (see §3.6).
- **Neon**: PostgreSQL 16, production runtime connection currently `neondb_owner` (per your
  instructions, this audit did **not** touch, test, or propose changing that connection, role, or
  any credential).
- **CI** (`.github/workflows/ci.yml` in each repo): `bmbackend`'s is notably thorough — static
  analysis, real-Postgres tests with coverage floors, a dedicated migration up/down/drift job, a
  **real 100,000-product load-test suite** against the PRD's own latency budgets (see
  `RESOURCE-CONSUMPTION-REPORT.md` §2), a secret scan, a fresh-clone import check, and a dependency
  audit. `bmfrontend`'s mirrors the same shape (lint/types/tests/build, E2E, accessibility, a
  real-backend full-stack job, a generated-API-types contract check, secret scan, dependency audit).
