# Full Resource Consumption Audit

**Date:** 2026-11-21. This document extends — and does not repeat findings already fully documented
in — `ARCHITECTURE-PERFORMANCE-AUDIT.md` (2026-11-21, the prior pass, findings F1–F11, still
accurate and cross-referenced by ID below). This pass adds live-deployment re-verification, CDN
cache-behaviour evidence, and two new findings (F12, F13) from a deeper look at cross-tab/cross-
browser-instance behaviour and React StrictMode specifically, both requested explicitly this time.

**Telemetry status:** no Vercel, Railway, or Neon API/CLI/dashboard access exists in this
environment — see `PRODUCTION-RESOURCE-CONSUMPTION-BASELINE.md` for the full, explicit account of
what is and isn't available and why. Every finding below is evidence from source code, from a
local disposable PostgreSQL instance, or from direct unauthenticated HTTP requests to the live
production URL (which is legitimate, safe, read-only evidence — not a substitute for dashboard
telemetry, and labelled as such throughout).

---

## Findings carried forward unchanged (full detail in `ARCHITECTURE-PERFORMANCE-AUDIT.md`)

| ID | Summary | Severity | Status |
|---|---|---|---|
| F1 | `notifications` table missing a `(business_id, status)` index for the exact query the 30s poll hits | P1 | Migration written, measured (6.7x faster), **not applied to production** — awaiting approval |
| F2 | HTTP call made while holding a DB row lock in `dispatch_pending` | P1 | Documented, fix proposed, not implemented |
| F3 | Four job kinds registered but never auto-enqueued | P2 | Documented — product decision needed |
| F4 | `settings` re-queried with zero caching (5x in one print payload) | P2 | Documented, fix proposed, not implemented |
| F5 | Four business-wide report queries lack a `business_id`-leading index | P2 | Documented — low current impact, single-branch shop |
| F6 | Migration tooling couldn't satisfy its own `CONCURRENTLY` rule | P2 | **Fixed** (demonstrated in migration `0029`) |
| F7 | Five unused frontend dependencies | P2/P3 | **Fixed** |
| F8 | Worker re-checked `jobs` table existence every second forever | P2/P3 | **Fixed** |
| F9 | Dead `_timezone()` call in `list_invoices` | P3 | Documented |
| F10 | Dead `worker_concurrency` setting | P3 | Documented |
| F11 | Inverted middleware-order comment | P3 | Documented |

---

## New findings this pass

### F12 — Each browser tab independently re-fetches the catalog delta and the pending-orders list on sign-in; not shared across tabs

- **Repository/file:** `bmfrontend/src/app/AppShell.tsx` — the `useEffect` at (approximately)
  lines 287-310, keyed on `[session, bootstrap]`: `void catalogCache.refreshFully()` and
  `void loadPendingOrders()` both run unconditionally whenever a tab transitions into a signed-in
  state with a bound counter — **not** gated by the Web Lock "owner" mechanism the sync scheduler
  uses (`scheduler.start()`, the line directly above both calls, *is* owner-gated internally; these
  two calls are not).
- **Root cause:** `catalogCache` (Dexie/IndexedDB) and `ordersStore` (an in-memory Zustand store)
  are both **per-tab** state — there is no `BroadcastChannel` or other cross-tab channel sharing
  either, unlike the sync scheduler, which coordinates specifically because *mutations* from one
  tab must be visible to others. A read-only fetch has no such requirement baked into the current
  design, so each tab simply fetches its own copy.
- **Evidence:** direct code reading, confirmed against the exact line numbers above (re-read this
  session). `catalogCache.refreshFully()` is delta-based and typically costs one cheap request when
  little has changed since the last cursor (established in the prior audit); `loadPendingOrders()`
  is `GET /orders?status=pending`, one request.
- **Classification:** **Derived from source**, not measured — no multi-tab browser session was
  actually run (no browser-automation tool exists in this environment, a limitation recorded
  earlier in this engagement).
- **Actual resource affected:** API request count — bounded and **one-time per tab per sign-in**,
  not recurring. Two extra requests per additional simultaneously-open tab, once.
- **Severity: P3.** This is real duplicated work, but it is the opposite shape from the original
  defect (bounded, not a loop) and this shop's own documentation describes itself as a single-
  counter, effectively single-tab operation — the condition that would make this costly (several
  tabs open routinely) is not the documented normal use.
- **Recommended fix:** none proposed for implementation. Building cross-tab read-sharing (e.g., a
  `BroadcastChannel` that lets a non-owner tab ask the owner for its already-fetched data) is new
  coordination infrastructure for a currently-low-impact, bounded cost — exactly what this
  engagement's rules caution against introducing without a demonstrated need. If the shop's real
  usage pattern turns out to routinely involve several simultaneously open tabs, worth revisiting.
- **Risk of changing it:** low technically, but not worth taking on for an unmeasured benefit.
- **Status:** Documented only.

### F13 — React StrictMode: already fixed for the one case that mattered, and inert in production for every other case

- **Repository/file:** `bmfrontend/src/main.tsx` (`<StrictMode>` wraps `<AppShell />`);
  `bmfrontend/src/app/bootstrap.ts`'s own docstring (lines 108-117).
- **What StrictMode actually does:** in **development only** (`npm run dev`), React deliberately
  double-invokes component effects on mount, to surface effects that aren't idempotent. **This
  behaviour does not exist in a production build** — `vite build`'s production React runtime does
  not do this — so `StrictMode` has **zero effect on the deployed application's request volume**.
- **The one place this previously mattered, already fixed (not by this audit — pre-existing):**
  `bootstrap.ts`'s own docstring documents a real bug this caused in development: the silent token
  refresh used to bypass the shared single-flight guard, so React's dev-mode double-mount issued
  two refreshes against one single-use rotating cookie, and the second one correctly tripped reuse
  detection and logged the developer out on every dev-server reload. The fix (routing through
  `refreshSession()`'s shared promise instead of calling `auth.refresh()` directly) means the second
  of any double-invocation now awaits and receives the *same* result as the first, rather than
  racing it — which also means this is now safe even in a hypothetical future scenario, not just
  masked by being production-only.
- **Classification:** **Derived from source** (confirmed by reading the exact code and comment),
  cross-checked against documented React behaviour (StrictMode's double-effect-invocation is
  explicitly a development-mode-only feature).
- **Severity: not applicable / informational.** Not a production resource concern. Included here
  only because this task explicitly asked whether StrictMode interactions were investigated, and
  the honest answer — "checked, confirmed dev-only, and the one real instance of it causing a bug
  was already fixed independently of this audit" — is worth stating rather than silently omitting.
- **Status:** No action needed.

---

## CDN caching behaviour — directly observed (not a finding; confirms existing design is correct)

Verified this session by curling the live production URL (safe, read-only, unauthenticated GET
requests only — no business data touched):

- Hashed static assets (`/assets/*.js`, `*.css`): `Cache-Control: public, max-age=31536000,
  immutable`, confirmed served as `X-Vercel-Cache: HIT` from Vercel's edge (`Age: 8086`s on the main
  bundle at time of check).
- `/`, `/sw.js`, `/registerSW.js`: shorter/no browser cache lifetime by design (so an update is
  discoverable), but still observed as edge `HIT` — Vercel's own revalidation keeps these cheap too.
- **Every `/api/*` path: `Cache-Control: no-store` (set by the backend on every response,
  `bmbackend/src/bm/api/middleware.py`), observed as `X-Vercel-Cache: MISS` on every call, with no
  exception.** This is correct and intentional for this application — stock, prices, credit
  balances, and notifications must never be served stale — and it is also exactly why API traffic,
  not static assets, dominates recurring CDN request volume. See
  `REQUEST-FLOW-AND-CDN-OPTIMIZATION-REPORT.md` for what is and isn't safe to change here.

## What else was checked in this pass and found unchanged / still correct

Re-confirmed, not re-litigated in full (see the prior audit for the original evidence): the sync
scheduler's zero-network idle ticks, bounded catalog-delta refresh, the single-flight/cross-tab-
locked auth refresh, bounded retry/backoff, the service worker's app-shell-only caching, and the
backend's connection pooling, session lifecycle, and four heaviest read-endpoint query shapes. No
new regression or drift was found in any of these on re-inspection.
