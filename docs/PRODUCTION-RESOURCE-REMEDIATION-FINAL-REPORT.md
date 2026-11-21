# Production Resource Remediation — Final Report (This Session)

**Date:** 2026-11-22. This document covers the increment of work authorized and completed in
this session, on top of two prior audit passes already recorded in this directory
(`ARCHITECTURE-PERFORMANCE-AUDIT.md`, `FINAL-PRODUCTION-RESOURCE-REVIEW.md`,
`REMAINING-RISKS-AND-APPROVALS.md`, `CDN-REQUEST-SPIKE-FORENSIC-REPORT.md`). It does not repeat
their content; it records what changed since they were written.

## A. What was authorized this session

1. **F2** — fix the HTTP-call-under-a-held-lock defect in notification dispatch. Approved,
   with regression tests, after diff/impact review.
2. **F4** — consolidate repeated `settings` queries in `print_payload.py`. Approved, with
   regression tests, after diff/impact review.
3. **F3** — investigate the four dormant job kinds and produce a per-job recommendation.
   Explicitly **not** to be scheduled.
4. **Migration 0029** — explicitly held back. Not applied.
5. Finish independently-safe audit work, run the full regression suites, review diffs, and —
   only after all of that passes — commit and push both repositories.

## B. F2 — HTTP call no longer runs under a held row lock

**File changed:** `src/bm/domain/notifications/service.py`. **Caller updated:**
`src/bm/domain/jobs/handlers.py` (two call sites: `handle_inventory_reconcile`,
`handle_notifications_dispatch`).

**Before:** `dispatch_pending(session, channel, limit)` received an already-open transaction,
claimed up to 50 rows with `FOR UPDATE SKIP LOCKED`, then called `channel.send()` (a
synchronous HTTP POST, timeout up to `alert_timeout_seconds`) **once per claimed row, inside
that same open transaction** — holding every claimed row's lock for the full duration of every
delivery attempt in the batch.

**After:** `dispatch_pending(channel, limit)` no longer receives a session. It opens its own
transaction to claim and commit (releasing every row lock immediately), then — with no lock
held — calls `channel.send()` for each event, recording each outcome in its own short,
separate transaction. This matches what the module's own docstring already claimed happened
("delivery happens outside the business transaction... an HTTP call can never hold a database
lock open") — the fix makes the code match the documented design, per
`REMAINING-RISKS-AND-APPROVALS.md`'s own description of this item.

**Why the session managed its own transactions (not a violation of `ARCHITECTURE.md §5.1`
rule 2, "a service that receives a session must not commit"):** both existing call sites
already gave `dispatch_pending` a transaction containing *nothing else* — the entire
`with transaction(): ...` block existed solely to call this one function. Removing the
`session` parameter and the wrapping `with transaction():` at both call sites, in favour of
`dispatch_pending` managing its own transaction lifecycle directly, changes who opens the
transaction without changing what runs inside it.

**Regression test:** `tests/integration/test_jobs.py::test_dispatch_releases_the_claim_lock_before_calling_the_channel`.
Uses a real loopback HTTP server (same technique as `tests/unit/test_alerting.py`) whose
request handler, from inside the delivery call, opens a **second** database connection and
attempts `SELECT ... FOR UPDATE NOWAIT` on the same row. Before this fix, that would have
raised (row still locked by the delivery's own transaction); after the fix, it succeeds
immediately. The test asserts it succeeds, and separately asserts the event still ends up
`dispatched` — proving the fix changes *when* the lock is held, not the outcome.

**Existing tests preserved:** `test_the_dispatcher_leaves_an_event_pending_when_there_is_no_recipient`,
`test_an_event_becomes_failed_after_the_attempt_limit`,
`test_an_event_of_another_type_is_left_for_its_own_consumer`, and the inventory-reconciliation
alert tests all pass unchanged — none of them inspected lock state, only outcomes, which this
fix does not change.

## C. F4 — `print_payload.py`'s settings queries, consolidated inside `build()` only

**File changed:** `src/bm/domain/sales/print_payload.py`.

**What changed:** `build()`'s four separate `self._settings.get(...)` calls (tax mode, print
profile, date format, credit-block flag) are now one `self._settings.resolve_all(...)` call,
indexed four times locally. `SettingsService.get()` is a thin wrapper that calls
`resolve_all()` and reads one key out of it — so four `.get()` calls were already four
identical full-table settings reads; this collapses them to one, with no behaviour change
(`resolve_all()`'s return value is exactly what `get()` reads from).

**What deliberately did NOT change: `reprint_is_duplicate()`'s own settings read.** The
original plan considered caching the resolved-settings dict at the `PrintPayloadService`
instance level, to also fold in `reprint_is_duplicate()`'s fifth query (the two methods are
called back-to-back on one instance in the real reprint request path,
`api/v1/invoices.py:206-208`). **This was reverted after inspecting
`tests/api/test_sales_rules.py::test_reprints_are_marked_duplicate_unless_the_setting_says_otherwise`**,
which calls `reprint_is_duplicate()` twice on the *same* `PrintPayloadService` instance with a
settings row changed directly in the database between the two calls, and asserts the second
call sees the new value. An instance-level cache would have served the first call's stale
value on the second call, silently breaking that test's documented contract (a setting change
must be visible on the very next read). Rather than relax that test to fit a cache, the cache
was narrowed to live only inside one `build()` invocation — where nothing can mutate a setting
mid-call — which gets the same win (one query instead of four) with zero risk to the proven
existing contract.

**Net effect:** the real reprint request (`reprint_is_duplicate` + `build`) now issues 2
settings queries instead of 5 (the one inside `reprint_is_duplicate`, unchanged, plus the one
now inside `build()`). The plain-sale print path (`api/v1/sales.py:246`, `build()` only) goes
from 4 queries to 1.

**Other `SettingsService` call sites, checked and deliberately left alone:**
`src/bm/domain/sales/service.py` (3 separate `.get()` calls), `src/bm/domain/sync/service.py`,
`src/bm/domain/sales/drafts.py`, `src/bm/domain/sales/cancellation.py`,
`src/bm/domain/returns/service.py` each have their own single-or-few settings reads. These are
the same "13 other call sites" `REMAINING-RISKS-AND-APPROVALS.md` already named and explicitly
deferred ("lower-value to touch individually... better addressed by a proper request-scoped
pattern if this is judged worth doing at all") — confirmed still accurate by this session's
own grep, not a newly-discovered gap, and out of scope for the narrowly-approved F4 change.

**Regression test:** `tests/api/test_sales_rules.py::test_build_resolves_settings_once_not_once_per_field`.
Listens on `before_cursor_execute` for the real test database connection during a `build()`
call and asserts exactly one statement touches the `settings` table — a test that would catch
a future regression (e.g. someone adding a fifth `.get()` call back into `build()`) even if
every returned value still happened to be correct.

**Existing tests preserved:** `test_reprints_are_marked_duplicate_unless_the_setting_says_otherwise`
(the test that drove the scope decision above) passes unchanged; every `print_payload`-shaped
test in `test_billing_workflows.py` (invoice-number absence, workbook structure, credit-block
placement, exact-decimal amounts, reprint duplicate-marking, unset-identity blank fields)
passes unchanged.

## D. F3 — dormant jobs, investigated, none scheduled

Full writeup: `docs/DORMANT-JOBS-RECOMMENDATION.md`. Summary: `docs/ARCHITECTURE.md` §7
already documents an intended cadence for all four (`inventory.reconcile`: nightly,
`idempotency.prune`: daily, `drafts.mark_stale`: daily, `notifications.dispatch`: outbox poll)
that `src/bm/worker.py`'s actual scheduler never implements. Per-job safety was verified from
the handler code directly (`drafts.mark_stale`'s 7-day-untouched guard makes it provably
incapable of touching an active session; `inventory.reconcile` has no write path to business
data at all). `notifications.dispatch`'s own recommendation is unusual: it currently has
nothing to do on its own, because the only code path that ever raises an alert
(`handle_inventory_reconcile`) already flushes its own alerts inline — so it is coupled to the
`reconcile` decision, not independently useful today. **`src/bm/worker.py`'s
`_SCHEDULE_INTERVALS_SECONDS` is unchanged. Nothing was scheduled.**

## E. Full resource audit — anything else found?

A targeted sweep looked for two specific patterns beyond the two fixed above, since both F2
and F4 are instances of general classes of defect (a slow external call under a lock; a
redundant per-request query):

- **Other HTTP/outbound-call sites that might run under a held lock:** `grep` for
  `urlopen`/`requests.`/`httpx.` across `src/bm` found exactly one call site —
  `AlertChannel.send()`'s own `urllib.request.urlopen` (`infra/alerting/channel.py:180`), which
  is the one F2 already addresses at its only two call sites. No second instance found.
- **Other repeated-settings-query sites:** confirmed exactly the same "13 other call sites"
  already named in `REMAINING-RISKS-AND-APPROVALS.md` — `domain/sales/service.py` (3 calls),
  `domain/sync/service.py`, `domain/sales/drafts.py`, `domain/sales/cancellation.py`,
  `domain/returns/service.py` (1 each). All previously identified and explicitly deferred, not
  newly discovered. None touched this session, consistent with the narrow F4 approval.

**No new defect was found beyond what the prior two audit passes already documented.** This is
consistent with those passes' own conclusion (`FINAL-PRODUCTION-RESOURCE-REVIEW.md`: "two full
passes... found exactly one real, provable resource-waste defect... and one real,
measured-but-unapplied follow-on gap") — this session's own targeted sweep, run independently
rather than taken on faith, did not surface anything to contradict that.

## F. Testing — proof, not assertion

| Suite | Result | Notes |
|---|---|---|
| Backend `pytest tests/` (full suite) | **1,073 / 1,073 passed**, exit code 0 | Against a fresh local Postgres container (`docker compose`), migrated to head `0029`, pointed at the explicitly-scratch database `bm_test` (the suite refuses to run against anything else, by design). 1,071 pre-existing + 2 new (F2, F4). |
| Backend `ruff check` / `ruff format --check` | Clean | On every changed file. |
| Backend `mypy` | `Success: no issues found in 3 source files` | `service.py`, `handlers.py`, `print_payload.py`. |
| Frontend `tsc -b && vite build` | Clean build, `492.03 kB` main bundle | Byte-identical size to the prior pass's own reported figure — confirms the already-approved, still-uncommitted dependency removal changed nothing else. |
| Frontend `vitest run` | **795 / 795 passed** (39 files) | Unchanged from the prior pass's own report; re-run fresh this session rather than assumed. |
| Frontend `eslint .` | Clean | No output. |

**No test was skipped, weakened, or made to pass by changing what it asserts** — the one place
a test's existing assertion shaped the implementation (F4's scope) is documented in full in
section C above, including which test and why.

## G. Git state — reviewed, not yet pushed at the time of writing

`bmbackend`: 7 modified files (`handlers.py`, `service.py`, `print_payload.py`, plus the two
files from the prior approved pass — `infra.py`, `runner.py` — and the two touched test
files), 16 untracked files (15 audit docs + migration `0029`, none of which auto-apply on
deploy per `ADR-016` — migrations are a separate, explicit step). `bmfrontend`: 2 modified
files (`package.json`, `package-lock.json`), the already-approved dependency cleanup, nothing
new this session. No `.env*`, credential, environment-variable, or business-data file appears
in either diff.

## H. What remains outstanding, explicitly

- **Migration 0029** — written, tested against a disposable database, **not applied to
  production**, per your explicit instruction to hold off regardless of other progress.
- **F3's four scheduling decisions** — documented with a recommendation per job in
  `DORMANT-JOBS-RECOMMENDATION.md`; **none implemented**, per your explicit instruction.
- **Live Vercel/Railway/Neon telemetry** — still unavailable in this environment, unchanged
  from every prior pass's own disclosure. Every request-volume figure anywhere in this
  engagement's documents is derived from code or local measurement, never live production
  traffic.
- **Dynamic, authenticated, in-browser verification of the original notification-polling
  fix** (live polling cadence, hidden-tab pause, the stale-tab reload test) — still blocked by
  the absence of a browser-automation tool in this environment, per
  `PRODUCTION-POLLING-VERIFICATION-2026-11-21.md`'s own PARTIAL verdict. The exact remaining
  operator action is unchanged: reload the known counter-PC tab(s) and watch the live Vercel
  log's request cadence drop to ≥30s immediately afterward.
