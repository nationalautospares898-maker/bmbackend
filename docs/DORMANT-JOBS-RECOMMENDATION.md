# Dormant Job Kinds — Investigation and Recommendation

**Date:** 2026-11-21. Scope: Finding F3 from `ARCHITECTURE-PERFORMANCE-AUDIT.md` /
`REMAINING-RISKS-AND-APPROVALS.md` — `inventory.reconcile`, `idempotency.prune`,
`drafts.mark_stale`, and `notifications.dispatch` are registered, idempotent job handlers
that nothing ever automatically enqueues (`src/bm/worker.py`'s `_SCHEDULE_INTERVALS_SECONDS`
only contains `low_stock.scan`, `reminders.check_schedules`, `orders.nudge`).

**This document is investigation and recommendation only. Nothing was scheduled.**
`_SCHEDULE_INTERVALS_SECONDS` is unchanged; no job kind was added to it.

## What the codebase's own design doc already says

`docs/ARCHITECTURE.md` §7 (the authoritative job-kind table, not an audit artifact) already
names an intended cadence for every one of these four:

| Job kind | `ARCHITECTURE.md`'s stated cadence | Currently actually runs |
|---|---|---|
| `inventory.reconcile` | Nightly + on demand | Only if an operator enqueues it manually |
| `idempotency.prune` | Daily | Only if an operator enqueues it manually |
| `drafts.mark_stale` | Daily | Only if an operator enqueues it manually |
| `notifications.dispatch` | Outbox poll | Only if an operator enqueues it manually |

This is evidence the gap is an **unfinished implementation of an already-made design
decision**, not an open question — the design intended these to run on their own. That
context shapes the recommendations below, but per your instruction this document stops at
recommending, not scheduling.

## 1. `inventory.reconcile`

**What it does** (`handlers.py:116-191`): for each business, re-derives stock balances from
the movement ledger and compares against the stored `inventory_balances` row. **Read-only
except for its own report** — "reports, never repairs" is enforced in the code (no write to
`inventory_balances` or `stock_movements` appears anywhere in the handler). A discrepancy
raises an in-app `notifications` row (severity `critical`) and queues one `outbox_events`
alert, which the same handler call then flushes via `dispatch_pending` (now fixed for the
held-lock defect, F2).

**Dependencies:** `_business_ids(session)` (iterates every business — one row for this shop),
`InventoryService.reconcile(ctx)`, the alert channel (optional — if unconfigured, the alert
is logged and dropped, not an error).

**Safety:** No write to any billing-relevant table. Cannot touch an active sale, draft, or
stock figure. The only operational risk is cost: it reads every `inventory_balances` row and
replays `stock_movements` for each to recompute a balance — on this shop's tables (per
`PRODUCTION-RESOURCE-CONSUMPTION-BASELINE.md`'s single-branch, two-user scale), this is a
sub-second job today and grows only as slowly as the shop's own catalog and movement history.

**Is it needed?** Yes, on the evidence already in this codebase: it is the only mechanism
that would ever catch a stock-balance/ledger disagreement — i.e., a bug — before the owner
notices it the hard way (a sale that can't be fulfilled, or a report that doesn't reconcile).
Nothing else in the system checks this invariant. `docs/ARCHITECTURE.md` calling it "nightly"
is not an arbitrary label; FR-INV-006/T3-04 (referenced in the handler's own docstring) treat
this as a standing correctness check, not a one-off diagnostic.

**Recommendation: schedule it, nightly, at an off-hours time (e.g. 02:00–04:00 local), if and
when you approve a change to `_SCHEDULE_INTERVALS_SECONDS`.** It is read-only, idempotent, and
its own cost is bounded by today's data volume. This is the one of the four with the clearest
case for action — not because this audit is proposing new behaviour, but because the
application's own design document already specifies this cadence and nothing currently
honours it.

## 2. `idempotency.prune`

**What it does** (`handlers.py:197-220`): deletes idempotency keys older than
`idempotency.retention_days` (default **7 days**, per `SPECS` in `settings/defaults.py`).
"Naturally idempotent" per its own docstring — deleting rows that are already gone removes
nothing.

**Dependencies:** `SettingsService` (one query, to read the retention window),
`IdempotencyService.prune()`.

**Safety:** Deletes only rows whose purpose (deduplicating a retried write) has already
expired by the configured window. Cannot affect a request made within the retention period —
a client retrying a write older than 7 days (the default) is not a scenario the idempotency
mechanism is designed to protect against in the first place, so pruning it is not a loss of
protection, only of now-useless bookkeeping rows.

**Is it needed?** This is pure housekeeping against unbounded table growth: every finalized
sale, draft save, and sync push that uses an idempotency key leaves one row, forever, if
nothing prunes it. At this shop's volume the table grows slowly, but "slowly" compounds over
years of operation with nothing ever removing a row — exactly the kind of gap
`REMAINING-RESOURCE-RISKS.md` §5 flags as "makes the index gaps worse over time, which is
exactly why F1 was fixed now rather than left for later." The same logic applies here: there
is no reason *not* to prune data whose own documented retention window has already passed.

**Recommendation: schedule it, daily, matching `ARCHITECTURE.md`'s own stated cadence, when
you approve a scheduling change.** Low risk, low cost, and the alternative (never pruning) is
strictly worse with no offsetting benefit — there is no scenario where keeping an
already-expired idempotency key serves any purpose.

## 3. `drafts.mark_stale`

**What it does** (`handlers.py:226-264`): for each business, transitions `draft_bills` rows
from `status = 'open'` to `status = 'stale'` where `updated_at < now() - drafts.stale_days`
(default **7 days**). Explicitly **archived, never deleted, never auto-finalised** — a stale
draft is still a bill someone started; the status change only removes it from the counter's
*open* list so it can be reviewed, per the handler's own docstring (FR-DRAFT-009).

**Dependencies:** `SettingsService` (one query per business for the threshold), a plain
`UPDATE ... WHERE status = 'open' AND updated_at < now() - make_interval(days => :days)`.

**Safety — the one of the four worth checking most carefully against "do not interrupt
active billing":** the `updated_at < now() - N days` guard means a draft touched at any point
within the last 7 days (the default) is never selected, regardless of how often this job
runs. A cashier's draft from earlier today, or even from six days ago, cannot be marked stale.
This job can only ever reach a draft that has already sat untouched for a full week or more —
by definition not an active session. No cancellation, no stock reversal, no total
recalculation happens; it is a label change on an abandoned row.

**Is it needed?** Its purpose is operational hygiene — an owner working the open-drafts list
should see bills someone is actually mid-way through, not a growing pile of weeks-old
abandoned carts. Whether that list has in practice ever grown large enough to matter is not
established by this audit (no evidence either way was found — this shop's draft volume was
not measured). This is the one recommendation in this document that rests more on the
documented intent (`ARCHITECTURE.md`, FR-DRAFT-009) than on a demonstrated present pain point.

**Recommendation: schedule it, daily, but treat this as the lowest-urgency of the three
non-notification jobs.** The safety case is solid (proven above, not just asserted), but
unlike `inventory.reconcile` (a correctness check) or `idempotency.prune` (unbounded growth
with zero offsetting benefit to not pruning), this one's benefit is UX tidiness whose present
size is unmeasured. Fine to schedule alongside the other two if you're making one change; also
fine to leave manual if you'd rather see evidence of an actual pile-up first.

## 4. `notifications.dispatch`

**What it does** (`handlers.py:335-341`, the handler whose underlying `dispatch_pending`
function F2 just restructured): claims pending `outbox_events` rows and attempts delivery.

**The fact that changes the recommendation here versus the other three:** `raise_alert` —
the *only* function in this codebase that ever inserts an `outbox_events` row (confirmed by
exhaustive grep: `handlers.py:169` is the sole call site) — is itself only ever called from
inside `handle_inventory_reconcile`, which **already flushes its own alerts** at the end of
the same handler call (`handlers.py:191`, `notifications.dispatch_pending(channel)`). So as
long as `inventory.reconcile` is the only thing raising alerts, a *standalone*
`notifications.dispatch` run has structurally nothing new to do except retry whatever didn't
successfully deliver on reconcile's own flush attempt (a transient webhook failure, for
example) — it is a retry net, not a primary delivery path, under the system exactly as it
exists today.

**Dependencies:** none beyond the outbox table and the alert channel — no coupling to
anything billing-related.

**Safety:** Identical to F2's own analysis — claim and deliver are now two separate, short
transactions; nothing here can hold a lock across a webhook call or touch business data.

**Is it needed on its own schedule?** Only as a safety net, and only once `inventory.reconcile`
is actually running on a schedule (recommendation 1) — scheduling `notifications.dispatch`
*without* also scheduling `inventory.reconcile` would give it nothing to do, since nothing
else raises an alert. If `reconcile` is scheduled, a nightly alert's dispatch already happens
inline, as part of that same job; a *separate*, more frequent `notifications.dispatch` tick
would only matter for retrying a delivery that failed on the first attempt before the next
scheduled reconcile run — a narrow, low-stakes gap given alerts are rare and
`MAX_DELIVERY_ATTEMPTS`-bounded retries already exist within one dispatch call.

**Recommendation: tie this to the `inventory.reconcile` decision rather than treating it as
independent.** If you schedule `reconcile` nightly, consider also scheduling
`notifications.dispatch` on a shorter interval (e.g. every 30–60 minutes) purely as a retry
net for any alert that failed reconcile's own inline flush — not because anything is
accumulating unsent otherwise. If you leave `reconcile` manual, there is no case for
scheduling this one alone; it would poll an outbox that nothing is filling.

## Summary table

| Job | Business purpose established? | Safety against active sessions | Recommendation |
|---|---|---|---|
| `inventory.reconcile` | Yes — the only stock/ledger correctness check in the system | No write path at all; read-only | Schedule nightly |
| `idempotency.prune` | Yes — unbounded growth with no offsetting benefit to not pruning | Only removes already-expired keys | Schedule daily |
| `drafts.mark_stale` | Documented intent (FR-DRAFT-009); present scale unmeasured | Proven safe — 7-day-untouched guard | Schedule daily, lowest urgency of the three |
| `notifications.dispatch` | Only as a retry net, contingent on `reconcile` running | Same as F2's fix — no held locks | Couple to the `reconcile` decision; not independently useful today |

**Nothing above has been implemented.** `_SCHEDULE_INTERVALS_SECONDS` in `src/bm/worker.py`
is unchanged. This is a decision document for you to act on (or not) separately from the
rest of this engagement's changes.
