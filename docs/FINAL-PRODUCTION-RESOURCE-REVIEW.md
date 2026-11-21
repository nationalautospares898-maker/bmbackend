# Final Production Resource Review

**Date:** 2026-11-21.

## Overall architecture assessment

Unchanged from the prior pass: **retain and incrementally improve.** Two full passes across both
repositories, infrastructure config, and (this pass) live production HTTP behaviour found exactly
one real, provable resource-waste defect (the original notification-polling loop, already fixed and
confirmed deployed) and one real, measured-but-unapplied follow-on gap (the missing notifications
index). Everything else is either already correctly engineered, or a low-impact/future-conditional
item with no case for action yet. That is a sign of a genuinely sound architecture for what this
application actually is, not a system that merely hasn't been looked at closely enough.

## What is ready

- **The fix is live.** Commit `45570db` deployed successfully to production at
  `2026-11-21T13:30:13Z`, confirmed via GitHub's record of Vercel's own deployment status and via
  re-fingerprinting the live bundle. The render-triggered polling loop that originally exceeded the
  1,000,000/month CDN budget is not running in production right now.
- **Migration `0029`** (the notifications index) is written, tested end-to-end (upgrade/downgrade/
  upgrade round-trip, schema-drift check, full 1,071-test backend suite — all against a local,
  disposable Postgres, never production), and its benefit is measured (6.7x faster, 85% fewer
  buffer reads). It is ready for you to approve and apply.
- **Two small, zero-risk cleanups are in place:** five unused frontend dependencies removed; the
  worker's job-queue loop no longer re-checks table existence every second forever.

## What is not ready / remaining risk

- **The HTTP-call-under-a-lock defect, the repeated settings queries, the dormant job kinds, and
  the business-id index gaps** are all documented, with proposed fixes where one exists, and none
  implemented — each either needs your decision or touches code this engagement treats as
  requiring extra caution regardless of size. Full detail: `REMAINING-RESOURCE-RISKS.md`.
- **Live platform telemetry remains entirely unavailable** for Vercel, Railway, and Neon. This is
  the main reason this review cannot fully resolve the "35k to 55k" observation that prompted this
  pass — three plausible readings are given in `PRODUCTION-RESOURCE-CONSUMPTION-BASELINE.md` §1.3,
  and none can be selected without a dashboard check against the `13:30:13Z` deployment boundary.

## Exact production steps that require your approval, and only yours

1. Apply migration `0029` to the production Neon database (via your established migration
   command). **Not done.**
2. Decide on the HTTP-call/held-lock fix in notification dispatch. **Not done.**
3. Decide on the settings-query consolidation in `print_payload.py`. **Not done.**
4. Decide whether the four dormant job kinds should be scheduled. **Not done.**

Nothing else in either audit pass requires approval — everything else implemented was judged safe
by this engagement's own stated criteria and has already been done and verified.

## Answering the task's eleven questions directly

1. **What was causing the original CDN overusage?** A render-triggered request loop in
   `NotificationBell.tsx`: an unstable callback dependency caused the poll's `useEffect` to tear
   down and immediately re-fire on every parent re-render, and every successful poll triggered
   exactly such a re-render — a loop bounded by network latency, not the intended 30 seconds.
2. **What is causing current Vercel CDN consumption?** Legitimate, bounded traffic: the (now-fixed)
   30-second notification poll per visible tab, bounded sync/catalog/renewal timers, and ordinary
   business-operation calls (search, draft saves, finalisation). No second loop or leak was found
   in either audit pass. Whether the specific "35k to 55k" figure reflects this normal baseline, a
   tail of the pre-fix bug, or a monthly cumulative total within budget could not be determined
   without dashboard access (§ above).
3. **Which requests are necessary?** All of the ones listed in
   `REQUEST-FLOW-AND-CDN-OPTIMIZATION-REPORT.md` §1 — none were found to be dead weight.
4. **Which requests are duplicated or unnecessary?** None recurring. One bounded, one-time-per-tab
   duplication exists (Finding F12: catalog delta + pending orders, once per sign-in per open tab)
   — not recurring, not proposed for a fix given its low measured-impossible-to-measure-but-bounded
   cost.
5. **What resources are being consumed unnecessarily?** As of this review: nothing with a proven,
   actionable fix left unimplemented, except the notifications index (awaiting your approval) and
   the four lower-priority items awaiting your decision.
6. **What did you actually fix, across both passes?** The polling loop (deployed, confirmed live);
   five unused dependencies; a per-second redundant DB check in the worker; demonstrated a working
   `CREATE INDEX CONCURRENTLY` pattern for future migrations.
7. **What was measured before and after?** Database: 9.253ms→1.387ms, 1,312→192 buffers for the
   notifications query at 80,000 synthetic rows. Frontend bundle: byte-identical, `492.03 kB`,
   before/after dependency removal. CDN: directly observed `HIT` on static assets, `MISS` on every
   API call, confirming the caching model works as designed.
8. **What remains unverified because telemetry was unavailable?** Actual Vercel request counts by
   path and period; actual Railway CPU/memory/health-check configuration; actual Neon connection
   counts, compute usage, and query statistics. Full list: `PRODUCTION-RESOURCE-CONSUMPTION-BASELINE.md`.
9. **What could become a bottleneck as usage grows?** A second branch (business-id index gaps,
   Finding F5); alert volume growing (the held-lock defect, Finding F2); `notifications`/
   `stock_movements`/`payments` continuing to grow unpruned (makes F1/F5's gaps worse over time,
   which is exactly why F1 was fixed now rather than left for later).
10. **Does the application retain exactly the same business workflow?** Yes. No business rule,
    calculation, invoice numbering, stock behaviour, credit workflow, or user-facing interaction was
    changed in either audit pass — confirmed by the full regression suites (795 frontend tests,
    1,071 backend tests, all passing, in both passes) and by every change being scoped to
    dependency manifests, one index, and one internal caching flag.
11. **Are all tests passing?** Yes — frontend 795/795 (re-run fresh this session), backend
    1,071/1,071 (prior session, confirmed still valid via identical `git status`).
12. **Are there any unresolved production risks?** Yes, the four items in §"What is not ready"
    above — all documented, none silently left unresolved.

## Final verdict

**PARTIAL.**

The original problem (the CDN-exceeding polling loop) is genuinely solved and confirmed live in
production — that part of this engagement's overall objective is complete. This pass's broader
objective — identify and safely eliminate *all* unnecessary resource consumption across the
application and infrastructure — surfaced no second instance of that class of bug, confirmed the
CDN caching model is already correctly designed, and found one real, measured, unapplied database
improvement plus a handful of lower-priority items genuinely requiring your decision rather than
this engagement's unilateral action. It is PARTIAL, not COMPLETE, specifically because (a) four
items remain pending your explicit approval by design, and (b) live platform telemetry — which
would let every estimate in this document be confirmed against a real number rather than derived
from code — was unavailable throughout. It is not BLOCKED: everything that could be investigated
and safely acted on without that telemetry was.
