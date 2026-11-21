# Resource Consumption Report

**Date:** 2026-11-21. Every figure below is labelled **Measured**, **Reproduced in a test**,
**Derived from source**, or **Estimated** — never presented as more certain than it is. No Vercel
analytics, Railway metrics, or Neon dashboard access exists in this environment; where that would
have been the better source, it's stated explicitly.

---

## 1. Frontend network consumption — endpoint-by-endpoint, post-fix

| Endpoint | Caller | Trigger | Frequency (steady state, one visible tab) | Necessary? | Duplicated? | Caches? |
|---|---|---|---|---|---|---|
| `GET /notifications` | `NotificationBell.tsx` | 30s interval + immediate on tab becoming visible | **Derived**: 1/30s while visible, 0 while hidden. ≤2,880/day ceiling if visible around the clock. | Yes — the owner's own stated requirement (near-live alerts with no server push). | No — `AutoAlertBanner` reuses the same poll via a prop, not a second `GET` (verified: no API import in that component). | Server sends `Cache-Control: no-store` always; client never caches this. |
| `sync.push` (outbox drain) | `scheduler.ts` | Busy 30s / idle 300s timer, **but only fires a request when the outbox has queued rows** | **Derived**: 0 while idle (confirmed by reading `drainOutbox` — it returns before any `fetch` when `nextBatch` is empty). | Yes. | No. | N/A (mutating). |
| Catalog delta refresh | `scheduler.ts` + one call on sign-in | 1,800s timer + once at session start | **Derived**: 1 request per refresh when the delta is empty/small (the normal case for a shop whose catalog doesn't change every half hour); up to 20 requests (`maxPages`) only if the delta is large. | Yes (offline search). | No (two call sites, mutually exclusive in time — not simultaneous). | Delta-based by design; never re-pulls the whole catalogue. |
| Token renewal | `renewal.ts` | ~13-minute timer (token TTL − 2 min margin), floor 30s | **Derived**: ~1 request every ~13 minutes while signed in. | Yes (avoids a thundering-herd of reactive 401 refreshes). | No — shares the same single-flight path as the reactive 401 handler. | N/A. |
| `GET /sync/bootstrap` | `bootstrap.ts` | Once per cold start, once per explicit sign-in (mutually exclusive) | **Derived**: 1 per session start. | Yes. | No (confirmed: the two call sites never both run in one flow). | N/A. |
| Product search | `ProductSearch.tsx` (billing) | Keystroke, debounced ~120ms, prior request aborted | **Derived**: scales with actual typing during billing, not idle-time waste. Bounded by the debounce and by the fact a bill has a handful of lines. | Yes — core billing feature. | No. | Not retried on failure (by design — the user is typing). |

**Before the fix**, the notification bell's poll was not bounded by its 30-second interval at all —
every successful poll fed back into a parent re-render that immediately restarted the poll, so the
real cadence was the network round-trip time, not 30 seconds (full analysis and the exact code
defect: this engagement's earlier `VERCEL-CDN-REQUEST-AUDIT-2026-11-21.md`). That is the finding
this entire audit was launched to check for recurrences of; none were found elsewhere (see
`ARCHITECTURE-PERFORMANCE-AUDIT.md`'s "what was investigated and found already correct" section).

**Estimated monthly ceiling, post-fix, one tab left visible and signed in continuously:**
30 days × 2,880/day ≈ **86,400 requests/month** from the bell alone — this is an **estimate** from
the code's own interval constant, not a measurement (no Vercel analytics access). Every other timer
in the table contributes a small, bounded addition on top (catalog refresh: ≤1,440/month at the
30-minute cadence; token renewal: ≈3,300/month at the 13-minute cadence) — together, comfortably
under 1% of the Hobby plan's 1,000,000/month budget for idle overhead on one tab, which is the
entire point of the earlier fix.

## 2. Backend / database — what's already measured, vs. what's new here

**Already measured, previously, in this repository** (not re-run in this session — reproducible via
the documented procedure, not re-verified live): `bmbackend/docs/PHASE-3-PROGRESS.md` records a real
load-test run against a seeded 100,000-product catalogue (`scripts/seed_demo.py`), through the real
service layer:

| Budget (`PRD.md §7.1`) | Target | Previously measured (p95) | Result |
|---|---|---|---|
| Product search | 150 ms | 141.6 ms (p50 101.9 ms) | PASS |
| Sale finalisation, 10 lines | 600 ms p95 / 1500 ms p99 | 266.9 ms p95 / 271.2 ms p99 | PASS |
| Draft save | 250 ms | 46.7 ms | PASS |
| Daily sales report, 1 day | 1000 ms | 16.6 ms | PASS |
| Daily sales report, 1 month | 3000 ms | 5.7 ms | PASS |
| 5 counters × 20 open drafts | 250 ms | 35.8 ms | PASS |

Reproduction procedure (from the suite's own docstring): `uv run python scripts/seed_demo.py
--products 100000 --reset`, then `uv run pytest tests/load -m slow -q -s`. Not re-run in this
session (it seeds 100,000 rows and takes several minutes; re-running it was not necessary to
support any claim in this audit, since no finding here concerns these specific query paths).

**Newly measured in this audit** (Finding F1, `notifications` index), against a local, disposable
PostgreSQL instance — never production:

- Seeded 80,000 synthetic `notifications` rows for one business, with a realistic lifecycle
  distribution (99.7% resolved/dismissed/acknowledged, 0.3% active) via direct `INSERT ...
  generate_series`, then `ANALYZE`.
- `EXPLAIN (ANALYZE, BUFFERS)` on the exact production query, with and without the new index
  (dropped and recreated in place to isolate the variable):

  | | Without index | With index |
  |---|---|---|
  | Plan | `Seq Scan`, 79,791 rows filtered out | `Bitmap Heap Scan` via `BitmapOr` |
  | Buffers | 1,312 | 192 |
  | Execution time | 9.253 ms | 1.387 ms |

  **≈6.7x faster, ≈85% fewer buffer accesses.** The synthetic data was truncated afterward; the
  local database was left migrated to head with an empty `notifications` table, matching its state
  before the benchmark.

**Derived from source, not measured:** the four `business_id`-unindexed query paths in Finding F5
(invoices/stock_movements/payments/purchases business-wide listing) — no row-count or `EXPLAIN` data
exists for these because this shop's actual production table sizes are unknown here (no database
access). The finding is "no supporting index exists for this query shape," not "this query is slow
in production."

## 3. React rendering — what was checked, and the limit of what could be checked

**Derived from source:** `BillingCounter.tsx` and `BillPanel.tsx` both call `useBillingStore()` with
no selector, subscribing to the entire Zustand store rather than the specific fields each component
reads. In practice this means a keystroke in one open bill tab re-renders every other currently-
visible `BillPanel` (up to three, per the "three columns" windowing confirmed in
`BillingCounter.tsx`), not only the one being edited.

**What could not be established:** whether this is user-perceptible. This audit had no browser or
React DevTools Profiler access (a hard tool limitation recorded elsewhere in this engagement), so
there is no render-count or render-duration measurement to cite. Per this audit's own rule ("do not
blindly add `useMemo`/`useCallback`/`React.memo`... use profiling evidence"), this is recorded as
**Estimated/Unknown impact** and left unimplemented — see `FINAL-ARCHITECTURE-RECOMMENDATIONS.md`
for how to actually measure it before touching the billing screen's hottest code path.

## 4. Infrastructure usage

**Unavailable in this environment — Estimated/Unknown, not measured:**
- Vercel request volume by path, function invocation counts, bandwidth.
- Railway CPU/memory utilisation, actual health-check probe frequency/target.
- Neon connection count, compute/storage usage, query-level `pg_stat_statements` data.

No credentials, CLI, or API token for any of the three platforms exist in this working environment
(consistent with this engagement's earlier findings). What this report substitutes, where it can:
source-derived connection-pool ceilings (§3 above, `CURRENT-SYSTEM-ARCHITECTURE.md` §3.5) and the
code-derived request-frequency table in §1, both clearly marked as such rather than presented as
live telemetry.

**What to check once dashboard access is available** (carried forward from the earlier production
audit in this engagement, still outstanding): Vercel request volume by path over a full billing
period, to confirm §1's steady-state estimate against real traffic; Railway's configured health-
check path and interval; Neon's active connection count against the ~15-30 ceiling in
`CURRENT-SYSTEM-ARCHITECTURE.md` §3.5.
