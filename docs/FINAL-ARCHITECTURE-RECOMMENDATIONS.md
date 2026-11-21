# Final Architecture Recommendations

**Date:** 2026-11-21. This is the forward-looking companion to `ARCHITECTURE-PERFORMANCE-AUDIT.md`
— not a list of things wrong today, but a scalability/maintainability roadmap keyed to specific,
named trigger conditions. Per this audit's own instructions: **no microservice, queue, cache
server, or additional database is recommended merely because it is popular.** Every item below
states the simplest change that addresses it, and the condition under which it becomes worth doing.

---

## 1. Verdict on the overall architecture: retain and incrementally improve

Nothing found in this audit — on either repository — suggests the current architecture is wrong
for what this application is: a single-shop, single-counter (today), offline-capable billing
system with modest, bounded concurrency. The Postgres-only job queue (no Redis/Celery/Kafka), the
sync (not async) SQLAlchemy layer, the manual-fetch-per-screen frontend (no router, no query
library) — none of these are under-engineered for the actual load this system carries, and none of
the audit's findings were "this needs a bigger architecture." They were: one missing index, one
documentation/implementation drift (unused frontend deps), one inert worker-loop re-check, one
transaction-boundary slip, and a handful of "re-examine when conditions change" items. That is a
healthy result for a real, two-month-old production system, not a sign of a deeper problem.

## 2. If this shop ever opens a second branch

Revisit Finding F5 (`invoices`/`stock_movements`/`payments`/`purchases` lacking a `business_id`-
leading index for business-wide queries). The condition that currently makes this low-impact —
`branch_id` selectivity ≈ `business_id` selectivity when there's exactly one branch — stops holding
the moment a second branch exists. At that point, add the four missing indexes using the same
`CREATE INDEX CONCURRENTLY` / `autocommit_block()` pattern this audit demonstrated in migration
`0029`, and re-measure with `EXPLAIN (ANALYZE, BUFFERS)` the same way Finding F1 was proven, rather
than assuming the fix helps.

## 3. If `stock_movements` or `payments` ever "feels slow" in reports

Both are append-only and never pruned by design (correctly — they're the ledger). Finding F5 already
flags their missing `business_id`-leading index. The trigger for action isn't a row count target
(this audit found no evidence of actual production row counts to set one), it's a user-reported
symptom ("the Stock Movement Report is slow") or a monitored query-duration metric once Railway/Neon
dashboard access exists. Measure first (as F1 was), then index — don't index speculatively.

## 4. If alert/notification volume grows beyond "a handful a day"

Fix Finding F2 (HTTP call made while holding a database row lock in `dispatch_pending`) before, not
after, volume grows. The current low-risk assessment is explicitly volume-dependent; this is the
one recommendation in this document that is a "do this proactively," not a "wait for a trigger,"
precisely because the trigger (a slow/hung webhook endpoint under real load) is also the failure
mode, and you don't get a warning between them.

## 5. If the billing screen (specifically) is ever reported as feeling slow while typing

Finding (documented, not fixed): `BillingCounter.tsx` and `BillPanel.tsx` both subscribe to the
entire Zustand billing store with no selector, so editing one open bill re-renders every other
currently-visible bill panel too. This audit could not measure whether that's user-perceptible — no
browser/profiler access existed. **Before touching this:** get real React DevTools Profiler data
(render count and duration per keystroke, with 1-3 bill tabs open) from an actual counter session or
a local `npm run dev` + Profiler session. If — and only if — that shows real wasted render time,
the fix is narrow and low-risk: give each `BillPanel` its own selector
(`useBillingStore((s) => s.drafts.find((d) => d.id === draftId))`-shaped, not the whole store), which
changes *what each component subscribes to*, not any business logic. This audit deliberately did not
make that change speculatively, per its own "use profiling evidence" rule.

## 6. If the `settings`-table re-query pattern (Finding F4) is worth generalising

The narrow fix proposed in `REMAINING-RISKS-AND-APPROVALS.md` §3 (batch `print_payload.py`'s five
calls) only addresses one call site. If a broader fix is wanted later, the right shape is a
**request-scoped** cache — resolved once per incoming request/transaction, discarded afterward,
never a process-wide or time-based cache — wired through the existing `db_write()`/`db_read()`
dependency-injection points in `api/deps.py`, so every request still sees the owner's latest
settings the moment they change, with no deploy and no cache-invalidation logic to get wrong. Do
**not** reach for `lru_cache`, Redis, or any TTL-based cache for this table — its whole value to the
owner is that a change takes effect immediately, and this audit found no evidence the per-query cost
(a sub-millisecond lookup against a tens-of-rows table) justifies the staleness risk a time-based
cache would introduce.

## 7. Operational automation gap (Finding F3) — a decision, carried forward

`inventory.reconcile`, `idempotency.prune`, `drafts.mark_stale`, and `notifications.dispatch` are
fully built, idempotent, and simply never triggered automatically. This isn't an architecture
recommendation so much as a flag that the architecture *supports* scheduling them (the worker's
ticker mechanism already exists and is exercised by three other job kinds) the moment you decide
they should run — there is no infrastructure gap to close first, only a configuration decision.

## 8. Dependency hygiene going forward

Finding F7 (five unused frontend dependencies) happened because `CLAUDE.md` described an intended
stack that the implementation diverged from, and nothing caught the divergence until this audit's
`depcheck` run. Recommend adding `depcheck` (or equivalent) as a non-blocking CI step in
`bmfrontend`'s workflow, the same way `pip-audit` already runs non-blocking-but-visible in
`bmbackend`'s — cheap, catches exactly this class of drift early, and does not require a new
runtime dependency itself (it's a dev-time check only).

## 9. What would most improve the quality of the *next* audit

Not a code change: Vercel, Railway, and Neon dashboard/API access. Every "Estimated" or
"Unavailable" label in `RESOURCE-CONSUMPTION-REPORT.md` exists because this audit had to derive
from source code what a dashboard would show directly in seconds. The single highest-leverage
improvement to this project's own observability — cheaper than any code change in this document —
is making sure whoever next investigates a usage or performance question has that access from the
start.
