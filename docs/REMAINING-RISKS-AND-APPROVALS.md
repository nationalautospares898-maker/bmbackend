# Remaining Risks and Approvals Required

**Date:** 2026-11-21, updated 2026-11-22.** Items 2 and 3 (F2, F4) were approved and have now
been implemented, tested, and are described in `PRODUCTION-RESOURCE-REMEDIATION-FINAL-REPORT.md`.
Item 4 (F3) was investigated per instruction and documented in `DORMANT-JOBS-RECOMMENDATION.md` —
**no job was scheduled.** Item 1 (migration 0029) remains explicitly un-applied; the owner has
said to hold off. Everything below is kept as the historical record of what was asked and why.

---

## 1. Apply migration `0029` (notifications index) to production Neon

**What it does:** adds `ix_notifications__business_status` — a plain, non-unique `(business_id,
status)` btree index on the `notifications` table — using `CREATE INDEX CONCURRENTLY`, which does
**not** take an exclusive lock and does **not** block reads or writes to the table while it builds.

**Why it needs your approval:** it is a production database schema change, and this engagement's
rules require explicit approval for any such change. It is **not** a data change — no row is
inserted, updated, or deleted; `CONCURRENTLY` means no downtime; it is reversible
(`alembic downgrade` drops the index, also concurrently).

**Evidence it's worth doing:** `ARCHITECTURE-PERFORMANCE-AUDIT.md` Finding F1 — measured 6.7x
faster, 85% fewer buffer reads, on a realistic 80,000-row synthetic dataset. The table it targets
is read by every open, signed-in tab every 30 seconds and never pruned, so its benefit only grows
with time.

**How to apply it, when you're ready:** the same path this project's own deployment guide already
documents for every other migration —
`./scripts/prod.ps1 python scripts/production_db.py migrate` (or whatever your established
production-migration command is; this audit did not re-derive or change that tooling). **I have not
run this, and will not run it without you saying so.**

**Rollback:** `alembic downgrade 0028` against the same production connection, also concurrent, also
non-destructive.

---

## 2. Fix the HTTP-call-under-a-held-lock defect in notification dispatch (Finding F2)

**What the fix would be:** split `dispatch_pending()` (`domain/notifications/service.py:316-386`)
into two transactions — claim the outbox rows and commit, release the lock; *then*, outside any
lock, attempt delivery and write the outcome in a second, short transaction. This is what the
module's own docstring already says happens; the fix makes the code match it.

**Why it needs your approval rather than being done now:** it changes transaction boundaries inside
job-handler code (`domain/jobs/handlers.py:190-191` and `:338-340` both call this function from
inside their own open transaction) — exactly the category of change this engagement's rules ask to
be surfaced rather than implemented reflexively, even though the affected feature (operational
alerting, not money or stock) is low-stakes today.

**Current risk if left as-is:** low, by the audit's own finding — alerts are rare (today, only a
job that is itself never auto-scheduled, per Finding F3, would ever raise one), and the webhook
timeout is capped at 5 seconds by default. The risk is that it **grows** directly with alert volume,
and a hung webhook endpoint would hold a job-processing lock for up to 30 seconds (`alert_timeout_seconds`'s
hard cap) rather than the 5-second default, if ever configured higher.

**If you approve:** this is a backend-only code change, testable with the existing
`tests/unit/test_worker.py`/`tests/integration/test_jobs.py` style of coverage (claim, assert the
lock is released before the HTTP call, assert the outcome is still recorded correctly on both
success and failure). Not started.

---

## 3. Reduce repeated `settings` queries (Finding F4)

**What the fix would be:** the narrowest safe version is batching `domain/sales/print_payload.py`'s
five separate `SettingsService.get()` calls (lines 143, 145, 148, 151, 261) into one
`resolve_all()` call reused for all five lookups within that one function — not a process-wide
cache (which would risk serving a stale business rule after the owner changes one, directly
contradicting `CLAUDE.md`'s "the owner must be able to change them without a deploy"), just
avoiding re-running the same per-business query five times while building one response.

**Why it needs your approval:** every call site lives inside `domain/sales/`, `domain/credit/`, or
`domain/returns/` — this project's own rules treat that code as requiring the most caution in the
whole application, and this audit's instructions are explicit that financial-code changes need
explicit sign-off regardless of how small.

**Current impact:** low in absolute terms — the `settings` table holds "tens of rows per business"
(confirmed by code reading), so each individual extra query costs well under a millisecond. The
finding is architectural (repeated identical queries within one request), not a measured slowdown.

**If you approve:** scope would be `print_payload.py` only in a first pass (the single clearest,
most self-contained instance); the other 13 `SettingsService` construction sites are lower-value to
touch individually and are better addressed by a proper request-scoped pattern if this is judged
worth doing at all — see `FINAL-ARCHITECTURE-RECOMMENDATIONS.md`. Not started.

---

## 4. Decide whether the four dormant job kinds should run automatically (Finding F3)

**What's dormant:** `inventory.reconcile`, `idempotency.prune`, `drafts.mark_stale`,
`notifications.dispatch` all have working, idempotent handlers registered, but nothing ever
inserts a job row for any of them — they only run if an operator does so manually.

**This is a decision, not a bug fix.** Options, for you to choose among (or reject all of):
- Add each to the worker's ticker (`worker.py`'s `_SCHEDULE_INTERVALS_SECONDS`) at some cadence
  (e.g. nightly for reconciliation and idempotency pruning, matching the "nightly" framing already
  used elsewhere in the docs).
- Leave them manual/operator-triggered, and document that explicitly rather than let the gap be
  rediscovered as a surprise later.
- Something else — these are your operational rules, not something this audit should decide
  unilaterally.

**Nothing changed here.** Flagged for your decision only.

---

## 5. The four `business_id`-unindexed report queries (Finding F5)

**What's missing:** no index leads with `business_id` for the business-wide (no-branch-filter) read
paths on `invoices` (date-range reports), `stock_movements` (Stock Movement Report),
`payments` (payment history), and `purchases` (purchase list).

**Why no fix is proposed for immediate approval:** current impact is low for a single-branch shop
(a `branch_id`-leading index is nearly as selective when there's only one branch), and adding four
speculative indexes "for later" is exactly what this audit's rules caution against. This is
recorded so it doesn't need rediscovering — see `FINAL-ARCHITECTURE-RECOMMENDATIONS.md` for the
trigger conditions under which it would be worth revisiting (a second branch, or visibly slow
reports as these append-only tables grow).

**Nothing changed here.**

---

## Explicitly confirmed NOT touched, anywhere in this audit

- Production business records, stock quantities, prices, invoices, payments, or customer balances.
- The production database runtime connection, role (`neondb_owner`), password, or grants.
- Railway, Vercel, or Neon environment variables, domains, or hosting configuration.
- Any migration was run only against a local, disposable PostgreSQL container created for this
  audit — never against production.
- No git push, merge, or deployment was performed or triggered.
