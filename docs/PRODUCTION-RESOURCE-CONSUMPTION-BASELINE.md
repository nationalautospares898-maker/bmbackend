# Production Resource Consumption Baseline

**Date:** 2026-11-21.

## 0. The single most important fact for interpreting any dashboard number right now

**The notification-polling fix (commit `45570db`) finished deploying to production at
`2026-11-21T13:30:13Z`** — confirmed just now via GitHub's own record of Vercel's deployment status
for that commit (`state: success`, `"Deployment has completed"`), and independently re-confirmed by
re-fetching the live production bundle this session: it is still `assets/index-DJl4wgXl.js`, the
same build already fingerprinted in the earlier production-verification pass as containing the fix
(6 occurrences of the `visibilitychange` string the fix adds a second registration of, vs. 4 in the
pre-fix build — see that pass's own report for the method).

Before that timestamp today, an earlier deployment attempt for the same commit had failed with
**"Account is blocked"** (Vercel's own status, timestamped `12:53:09Z`) — meaning the **old, buggy
build was still live and actively polling in a tight loop** for however long before `13:30:13Z`
today, on top of however long it had already been live before today (the original defect predates
this entire engagement).

**Any Vercel dashboard metric whose selected time range includes any part of today before
`13:30:13Z` UTC will show inflated numbers from the already-fixed bug, not a new or ongoing
problem.** This is the first thing to check before interpreting the "35k to 55k" observation
mentioned in this task — see §1.3.

## 1. Vercel

### 1.1 What is and isn't available

**No Vercel API token, CLI, or dashboard access exists in this environment.** This has been true
throughout this entire engagement (confirmed repeatedly: no `vercel` CLI installed, no
`VERCEL_TOKEN`/org/project env vars, no `.vercel/project.json` link, no deploy-hook URL anywhere in
the repository). Everything in this section is either (a) inferred from HTTP responses actually
observed by curling the live production URL just now, which is legitimate evidence but is **not**
the same as a dashboard's own request-count telemetry, or (b) explicitly marked unavailable.

### 1.2 What was directly observed, just now, against the live production URL

| Path | `Cache-Control` | `X-Vercel-Cache` | Meaning |
|---|---|---|---|
| `/assets/index-DJl4wgXl.js` (hashed JS bundle) | `public, max-age=31536000, immutable` | **HIT** (`Age: 8086`s ≈ 2h 15m) | Served from Vercel's edge cache without reaching the origin build. A returning visitor's own browser cache (same `Cache-Control`) means most repeat visits from the *same* browser profile don't even reach Vercel's edge for this file at all. |
| `/` (`index.html`) | `public, max-age=0, must-revalidate` | **HIT** (`Age: 7225`s) | Edge-cached despite `max-age=0` — Vercel's own revalidation keeps this cheap too. |
| `/sw.js` | `no-cache, must-revalidate` | **HIT** | Same pattern. |
| `/api/v1/notifications?limit=50` (and every other `/api/*` path) | `no-store` (set by the backend, `bmbackend/src/bm/api/middleware.py`) | **MISS** | **Every API call is a guaranteed cache miss that proxies through Vercel's edge to Railway and back, every single time — there is no caching layer anywhere in this path that could absorb repeated requests.** |

**This directly answers the task's question "determine whether API requests passing through the
Vercel rewrite are contributing to the metric":** yes, unambiguously. Static assets are cached at
Vercel's edge and (for a returning browser) at the browser itself, so they contribute a small,
front-loaded, mostly one-time cost per client. Every API call — the 30-second notification poll,
every product search keystroke, every sync push, every sale — is **never** cached anywhere between
the browser and Railway, and is therefore counted by Vercel's edge on every single occurrence. The
dominant, recurring contributor to total request volume is and always was API traffic, not static
assets — exactly consistent with the original root cause (a polling loop) being able to single-
handedly exceed a 1,000,000/month budget.

**What could not be determined without dashboard access:** the actual total request count for any
specific period, the split between static-asset and API-proxy requests as a proportion of that
total, bandwidth/transfer volume, and any further per-path breakdown Vercel's own analytics UI may
offer. The reasoning above establishes the *mechanism* (API traffic is structurally incapable of
being cached and therefore dominates recurring volume); it does not substitute for the actual count.

### 1.3 On the "35k to 55k" observation

This task states the owner observed Vercel CDN usage "increasing from approximately 35k to 55k."
**I cannot confirm what this number counts, over what period, or whether the observation window
overlaps the deployment boundary in §0** — I have no dashboard access to check the selected date
range or the exact metric name. Three distinct, equally plausible readings, each with a different
conclusion:

1. **If the window includes meaningful time before `13:30:13Z` UTC today:** this is explained, at
   least in part, by the already-fixed bug — no new problem to find.
2. **If the window is entirely after `13:30:13Z` UTC today (i.e., post-fix only):** at a rough
   20,000-request increase over at most a few hours, that would be a materially **higher** rate
   than this engagement's own post-fix steady-state estimate (≈2,880/day per visible tab from the
   notification bell, plus small amounts from other timers — see
   `bmbackend/docs/RESOURCE-CONSUMPTION-REPORT.md` §1). That would be worth investigating further
   with the actual path breakdown, if available.
3. **If this is a monthly cumulative figure** (the unit the original 1,000,000/month budget is
   denominated in, and the framing used when this engagement started): 55,000 is **5.5% of the
   monthly budget** — not remotely a crisis, and plausibly just the month-to-date count climbing
   normally as the month progresses, including whatever the pre-fix bug contributed before today.

**What would resolve this immediately:** check the dashboard's own date-range selector against
`2026-11-21 13:30 UTC`, and check whether the breakdown (if Vercel's plan tier shows one) attributes
the increase to `/api/v1/notifications` specifically versus other paths. Without that, I can narrow
the explanation to these three readings but cannot pick one.

## 2. Railway

**No Railway CLI, API token, or dashboard access exists in this environment.** CPU/memory/restart-
frequency/deployment-frequency/request-latency telemetry for either the API or worker service is
**entirely unavailable** here. What this report substitutes, from source code only (not a
measurement):

- **Health checks:** `GET /healthz` touches no database (`bmbackend/src/bm/main.py:173-177`);
  `GET /readyz` does exactly one `SELECT 1` plus a cached Alembic-head comparison inside one read-
  only session. Neither is a meaningful resource cost regardless of how often Railway's own
  platform probes them — but this repository contains no `railway.toml`/`railway.json` declaring
  that probe interval, so the *actual* frequency Railway uses could not be confirmed (noted as a
  gap in the prior audit too, still unresolved).
- **Worker idle-loop activity:** confirmed in the prior audit (`ARCHITECTURE-PERFORMANCE-AUDIT.md`
  Finding F8, now fixed) — the worker's one-second loop no longer makes an unconditional database
  round trip on every tick; idle ticks that find no due job and no queued work now cost nothing
  beyond the `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1` claim query itself, which returns
  immediately on an empty queue.
- **Deployment/restart frequency:** cannot be assessed from the repository; this is purely a
  platform-observed metric.

## 3. Neon

**No Neon API token, CLI, or dashboard access exists in this environment.** Active/peak connection
counts, compute consumption, `pg_stat_statements` data, slow-query logs, and actual production
database size/growth are **entirely unavailable** here. What this report substitutes, from source
code only:

- **Theoretical connection ceiling:** API process pool (`pool_size=10, max_overflow=5`) + worker
  process pool (same defaults) = up to **15 connections per process, ~30 total** if both run as
  separate Railway services at default settings (confirmed in the prior audit's
  `CURRENT-SYSTEM-ARCHITECTURE.md` §3.5). Whether production actually approaches this, or what
  Neon's plan-tier connection limit is, could not be checked.
- **Production runtime connection:** confirmed (by your own statement, not independently verified
  by this audit — no production credentials were used at any point) to currently use `neondb_owner`.
  **This audit did not inspect, test, or propose changing that connection, role, or any credential**,
  per your explicit instruction.
- **Query-level evidence:** the one concrete, measured finding from this entire engagement — the
  missing `notifications(business_id, status)` index — was proven against a **local, disposable**
  Postgres instance (§ below), not against Neon. No production query plan, slow-query log, or
  `pg_stat_statements` output was available to confirm the same gap's real-world cost in production
  specifically; the local measurement is the strongest evidence obtainable without that access.

## 4. What the owner can provide to close these gaps

In order of likely usefulness to the *current* question (why is CDN usage climbing):

1. **Vercel → Project → Analytics, filtered to a range starting at `2026-11-21 13:30 UTC`.** This
   single check resolves §1.3's ambiguity directly.
2. **Vercel → Analytics → path breakdown**, if your plan tier shows one, to confirm or rule out
   `/api/v1/notifications` as the dominant contributor going forward.
3. Railway's service dashboard for the API and worker — CPU/memory graphs and the configured
   health-check path/interval.
4. Neon's dashboard — active connections and, if available on your plan, `pg_stat_statements` or
   the query-insights view, which would show the `notifications` table scan directly if production
   has accumulated enough rows for it to be visible.

None of the findings or fixes in this engagement depend on receiving this access — it would
**confirm or sharpen** the estimates already made from code and local measurement, not replace the
need for them.
