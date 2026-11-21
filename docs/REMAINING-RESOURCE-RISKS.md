# Remaining Resource Risks (this pass)

**Date:** 2026-11-21. Carries forward every item from the prior pass's
`REMAINING-RISKS-AND-APPROVALS.md` unchanged (nothing new was learned that changes their risk/
benefit analysis), plus this pass's two new findings and the telemetry-access gap.

## 1. Apply migration `0029` (notifications index) to production Neon — unchanged, still pending

Same as before: `CREATE INDEX CONCURRENTLY`, no downtime, no data change, reversible. Measured
6.7x faster / 85% fewer buffer reads on a realistic synthetic dataset. **Still not applied.**
Apply via your established production-migration command when you approve it; I have not run it.

## 2. HTTP call under a held DB lock in notification dispatch — unchanged, still pending

Same as before: `domain/notifications/service.py:316-386`'s `dispatch_pending` holds a row lock
during a synchronous outbound HTTP call, contradicting the module's own documented design. Low
current risk (alerts are rare), grows with alert volume. Fix proposed, not implemented — touches
job-handler transaction boundaries.

## 3. Repeated `settings` queries — unchanged, still pending

Same as before: up to five separate re-queries of the same tiny table while building one print
payload. Low absolute cost today; the proposed fix (request-scoped reuse, never a time-based cache)
is detailed in `REQUEST-FLOW-AND-CDN-OPTIMIZATION-REPORT.md` §3. Not implemented — touches
`domain/sales/print_payload.py`.

## 4. Four dormant job kinds — unchanged, a decision not a defect

`inventory.reconcile`, `idempotency.prune`, `drafts.mark_stale`, `notifications.dispatch` are built
and idempotent but never auto-scheduled. Your call whether/how they should run.

## 5. Four business-id index gaps — unchanged, low current impact

`invoices`/`stock_movements`/`payments`/`purchases` business-wide queries lack a `business_id`-
leading index. Low impact for a single-branch shop today; revisit if a second branch opens or
these append-only tables grow large enough to matter even with one branch.

## 6. New this pass: live telemetry access would materially sharpen every estimate in this audit

Every frequency, cost, and "low impact" judgement in both passes of this engagement is derived from
source code or a local measurement, never from actual production traffic, CPU/memory, or
connection data — because no Vercel, Railway, or Neon access exists in this environment. This is
not a code risk, but it is the single biggest remaining uncertainty in the whole audit: the "35k to
55k" observation that prompted this pass could not be conclusively explained (three plausible
readings presented in `PRODUCTION-RESOURCE-CONSUMPTION-BASELINE.md` §1.3, not narrowed to one)
without it. Providing dashboard/API access for any one of the three platforms would let a future
pass confirm or correct every estimate in this document with a real number instead of a derivation.

## 7. New this pass: cross-tab duplicate startup fetches (Finding F12) — low impact, no fix proposed

Each browser tab independently fetches its own catalog delta and pending-orders list once per
sign-in. Bounded (not recurring), and not demonstrated to matter at this shop's documented single-
counter usage pattern. No fix proposed unless real multi-tab usage is confirmed to be routine —
building the cross-tab coordination to address it would be new infrastructure for an unmeasured
benefit.

## Explicitly confirmed NOT touched, this pass

Identical to the prior pass's confirmation, re-verified: no production business records, stock,
prices, invoices, payments, or customer balances; no production database runtime connection, role,
password, or grants; no Railway/Vercel/Neon environment variables, domains, or hosting
configuration; no migration run against anything but a local disposable container; no git push,
merge, or deployment triggered by this session (the deployment confirmed live in §0 of
`PRODUCTION-RESOURCE-CONSUMPTION-BASELINE.md` was performed by Vercel's own platform in response to
last session's already-approved push, not by any action taken in this session).
