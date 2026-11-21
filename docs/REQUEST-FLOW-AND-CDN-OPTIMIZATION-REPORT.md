# Request Flow and CDN Optimization Report

**Date:** 2026-11-21.

## 1. Full endpoint inventory, caller, trigger, frequency

Extends `ARCHITECTURE-PERFORMANCE-AUDIT.md`'s equivalent table with the CDN-specific columns this
pass was asked to add. Frequencies are **Derived from source** (interval constants, call-site
logic), not measured from live traffic — no Vercel analytics access exists (see
`PRODUCTION-RESOURCE-CONSUMPTION-BASELINE.md`).

| Endpoint | Caller | Trigger | Steady-state frequency (one visible tab) | Vercel cache behaviour | Duplicated across tabs? | Required for correctness? |
|---|---|---|---|---|---|---|
| `GET /api/v1/notifications` | `NotificationBell.tsx` | 30s interval, visibility-gated | ≤2,880/day | **MISS, always** (`no-store`) | Yes, by design — each tab shows its own bell | Yes |
| `sync.push` (outbox drain) | `scheduler.ts` | 30s/300s timer | 0 while idle (no fetch issued) | N/A | No — single owner tab | Yes |
| `sync.catalogDelta` | `scheduler.ts` + sign-in | 1,800s timer + once per sign-in | ~1/refresh, usually small | **MISS, always** | **Yes** — once per tab per sign-in (Finding F12), not recurring | Yes (offline search) |
| `GET /orders?status=pending` | `ordersStore.loadPending`, triggered once by `AppShell` on sign-in | Once per sign-in, plus on explicit user actions (completing/discarding an order) | ~1/sign-in + action-driven | **MISS, always** | **Yes** — once per tab per sign-in (Finding F12) | Yes |
| `auth.refresh` | `renewal.ts` | ~13 min timer | ~110/day | **MISS, always** | No — shared single-flight path | Yes |
| `GET /sync/bootstrap` | `bootstrap.ts` | Once per sign-in/cold start | 1/session | **MISS, always** | No (per-tab, but one-time) | Yes |
| `GET /products/search` | `ProductSearch.tsx` | Keystroke, 120ms debounce | Scales with actual typing | **MISS, always** | No | Yes — core billing feature |
| Static JS/CSS bundles | Browser, on load | First load / after a deploy | Once per browser cache lifetime (1 year, immutable) | **HIT** (edge), and the browser's own cache usually avoids even that after the first visit | No — same hashed URL for every client | Yes (app can't run without them) |
| `index.html`, `sw.js`, `registerSW.js` | Browser, on navigation/update-check | Every navigation / periodic SW update check | Low — these are small (~1-2KB) and edge-cached | **HIT** (edge) despite `no-cache`/`max-age=0` | No | Yes |

## 2. CDN contribution analysis

**Static assets are not the problem and were never the problem.** They're cache-`HIT` at Vercel's
edge (confirmed by direct observation this session), small, and — because of
`immutable`/1-year `Cache-Control` on the hashed bundle — mostly skip even the edge after a
client's first visit, since the browser's own cache serves them with no network request at all.
**Every API call is structurally incapable of being cached anywhere between the browser and
Railway**, because the backend sets `Cache-Control: no-store` on every response
(`bmbackend/src/bm/api/middleware.py`) — this is correct for a business application where stock,
prices, and balances must never be stale, but it means API call *volume* is the only lever that
actually moves total CDN request count. This is exactly consistent with how a single polling loop
was able to exceed a 1,000,000/month budget on its own before the fix.

## 3. What is safe to consolidate or cache, and what is not

Per this task's required format — for every caching proposal: what's cached, where, how long, how
invalidated, how freshness is guaranteed after mutations, why it can't introduce incorrect business
information.

### Proposal considered and rejected: time-based caching of `GET /notifications`

**Not proposed.** A notification is exactly the kind of thing (a low-stock alert, a reminder, an
order nudge) where showing a stale "nothing needs attention" after it's no longer true is a direct
business-correctness risk this task's own rules forbid introducing. The correct fix for this
endpoint is the missing index (F1), which makes the *existing* no-cache, always-fresh query cheaper
to run — not a cache that would make it stale sometimes.

### Proposal considered and rejected: caching `settings` (business rules) with a TTL

**Not proposed**, for the same reason, explicitly: `CLAUDE.md` states the owner must be able to
change a business rule (tax mode, draft limits, credit terms) "without a deploy," and a time-based
cache necessarily serves a stale value for up to its TTL after such a change. Finding F4's proposed
fix (in `REMAINING-RESOURCE-RISKS.md`) is a **request-scoped** reuse — resolved once per incoming
request, discarded immediately after — which cannot ever serve a value staler than "the one request
currently being handled," and is the only shape of caching considered safe for this data.
1. **What's cached:** nothing across requests — only the result of one `resolve_all()` call,
   reused by the handful of `.get()` calls inside the *same* request/transaction that already
   construct a `SettingsService`.
2. **Where:** in-memory, scoped to one Python call stack, never persisted.
3. **How long valid:** for the lifetime of one request's handling — microseconds to a few
   milliseconds, never longer.
4. **Invalidation:** automatic — the cache doesn't outlive the request that created it, so there is
   nothing to invalidate.
5. **Freshness after mutation:** the *next* request always calls `resolve_all()` fresh; a setting
   changed by the owner takes effect on the very next request, identical to today's behaviour.
6. **Why it can't introduce incorrect business information:** it never serves a value computed
   before the current request started, and it never persists past that request's own transaction
   boundary.
   Not implemented in this pass (requires approval — see `REMAINING-RESOURCE-RISKS.md`).

### Proposal considered and rejected: cross-tab sharing of catalog-delta/orders-pending fetches (Finding F12)

**Not proposed for implementation.** Would require new `BroadcastChannel`-based coordination
infrastructure to address a cost that is already bounded (one-time per tab per sign-in, not
recurring) and not demonstrated to matter at this shop's actual usage pattern (documented as
single-counter, not multi-tab). Revisit only if real multi-tab usage is confirmed to be routine.

### What's already correctly cached, left unchanged

- Hashed static assets: immutable, 1-year, edge + browser cached. No change needed or proposed.
- `vercel.json`'s existing header rules (`/sw.js`, `/registerSW.js`, `/index.html` all
  `no-cache, must-revalidate` — correct, so an update is always discoverable rather than stuck in a
  stale edge cache).
- The service worker's own scope: app-shell only, zero `/api/` caching (`vite.config.ts`'s
  `runtimeCaching: []`). Confirmed unchanged and correct in this pass.

## 4. In-flight request sharing / deduplication — what exists, what doesn't, and why nothing new is proposed

**Exists today:** the auth-refresh single-flight promise (`client.ts`) is the one place genuine
in-flight request *sharing* happens — twenty concurrent 401s await one refresh, not twenty. Product
search aborts its own prior in-flight request on a new keystroke (cancellation, not sharing, but
serves the same anti-waste purpose for that endpoint specifically).

**Does not exist, and isn't proposed:** a general-purpose request-deduplication layer (e.g., for
two components that happen to request the same resource at the same moment) was not found to be
needed anywhere in the frontend. The one place that pattern would have mattered —
`NotificationBell` and `AutoAlertBanner` both wanting the same notification list — is already
solved architecturally (one poll, one prop passed down, confirmed in the prior audit), not by a
generic deduplication mechanism. Introducing one (e.g., adopting TanStack Query, which is already
an unused dependency per Finding F7) for a problem that doesn't currently exist would be exactly
the "fashionable pattern" this task's rules say not to add.

## 5. Before/after evidence

See `RESOURCE-PERFORMANCE-TEST-REPORT.md` for the measured database-level before/after
(`EXPLAIN (ANALYZE, BUFFERS)`, 6.7x/85% improvement on the notifications index), and the frontend
bundle-size before/after (byte-identical, confirming the dependency cleanup's zero runtime cost).
No new before/after request-count measurement was produced in this pass beyond what the prior
audit already established for the notification-polling fix itself (the original defect, already
shipped) — this pass found no second loop to produce a comparable "before" for.
