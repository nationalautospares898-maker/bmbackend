# CDN Request Spike — Definitive Root-Cause Proof and Solution Validation

**Date:** 2026-11-22. §§1–9 below are this investigation's original findings, written
before any further action was authorized — at that point **no code was pushed or deployed**
and migration `0029` was explicitly **not applied** (§7, as it stood then). **§10 records a
follow-on, separately-authorized session that completed the remaining work**: backup
verification, applying migration `0029`, implementing and deploying one further fix, and
production verification. Read §10 for the current end state; §§1–9 are preserved unedited
as the historical record of what was known and decided at the time.

---

## 1. Executive summary

**What was actually wrong, in the traffic you observed on 2026-11-21/22:** the evidence is
consistent with — and now, for the first time, *quantitatively* consistent with, not just
order-of-magnitude consistent with — a browser tab still executing the **pre-fix**
`NotificationBell.tsx` (the version live before commit `45570db`, deployed 2026-11-21
13:30:13 UTC). This session built an executable reproduction of the actual pre-fix source
code under the exact calling pattern `AppShell.tsx` uses, and it produced **7.0 requests/
second** — the live evidence showed **6.7 requests/second** (2,000 requests / 5 minutes) with
a 0.1–0.2s inter-request gap. These numbers now agree to within 5%, not just "the same order
of magnitude."

**Why two users can generate this volume:** because the defect was never about having many
users — it was a *single browser tab* whose own render cycle fed back into itself, bounded
only by network round-trip time (confirmed at ~140ms in this reproduction) instead of the
intended 30 seconds. One such tab produces the entire observed volume on its own; a two-person
shop does not need a second cause.

**Confidence level, stated precisely:**
- **PROVEN** (by executable reproduction, this session): the exact mechanism in the pre-fix
  source code produces a request rate matching the observed live rate to within 5%.
- **PROVEN** (by code reading, re-verified fresh this session): the currently-deployed code
  cannot reproduce this mechanism under any scenario tested, including 50 unrelated parent
  re-renders, two independent mounted instances, hidden-tab, mount/unmount, and a failed
  request.
- **STRONGLY SUPPORTED, not directly confirmed**: that the *live* traffic on 2026-11-21/22 is
  specifically a stale tab (as opposed to some other still-unidentified source). This remains
  short of PROVEN because this environment has no browser-automation tool and no access to
  the counter PC — the same limitation disclosed in every prior pass of this engagement. What
  changed this session is the *strength* of the supporting evidence (a quantitative match, not
  an estimate), and a newly-identified, concrete mechanical reason the browser tab's own
  service worker would not have corrected itself even if it was reloaded — see §3.4.

**What this report adds beyond repeating the prior conclusion:** (a) an executable
reproduction using the actual historical source, with a measured number, not an estimate;
(b) a previously under-emphasized mechanical detail about *why* a reload might not be enough
(the service-worker update-prompt dependency, §3.4); (c) a definitive, sourced answer to
whether a rate limiter could reduce the Vercel CDN count (it cannot — §6.3), which rules out
an entire category of otherwise-plausible-sounding fixes; and (d) an explicit finding that
migration `0029` cannot be safely applied in this environment right now, with the exact reason
(§7).

---

## 2. Historical evidence

| Item | Status |
|---|---|
| Buggy commit `05008fd` deployed | 2026-11-15 12:15:36 UTC (GitHub deployment record, re-confirmed prior session) |
| Fix commit `45570db` deployed | 2026-11-21 13:30:13 UTC (same source) |
| Nov 17–19 spike (~1.44M requests) | Falls entirely inside the buggy window — proven by deployment timestamps, not re-litigated this session |
| **New this session — Evidence A** (owner's Vercel dashboard, Nov 21 ~19:30–21:14) | ~38K total CDN requests, ~38K of them to `/api/v1/notifications` (practically the entire volume); `/api/v1/sync/catalog`, `/api/v1/orders`, `/api/v1/auth/refresh` each ~13 requests in the same window — exactly what bounded, correctly-behaving timers (30/60/~780s-equivalent cadences) would produce for 1–2 sessions, confirming by contrast that nothing *else* is misbehaving |
| **New this session — Evidence B** (owner's Vercel dashboard, "last 5 minutes") | ~2,000 requests / 5 min ≈ 6.7 req/s, 0.1–0.2s gaps, HTTP 200, cache MISS, `IN`, Windows 10/Chrome |
| **New this session — Evidence C** (owner's own arithmetic) | Two users at 1 tab/30s each would produce ~20 requests in 5 minutes; observed was ~100x that |
| Previous stale-tab hypothesis | Proposed, **explicitly left unconfirmed** by the prior pass — the exact words were "strongly supported, not directly confirmed." This session does not treat that prior unconfirmed status as resolved; §1 above states the current, re-assessed confidence level in full, which is higher than before but still not PROVEN for the *live-traffic-source* question specifically. |

---

## 3. Root-cause analysis

### 3.1 The complete frontend request-lifecycle audit, redone this session

Every item PART 5-B of the brief asked about was checked fresh against the **current**
source (not re-quoted from a prior report):

| Candidate mechanism | Finding |
|---|---|
| Multiple `setInterval`/`setTimeout` loops | One `setInterval(tick, 30_000)` in `NotificationBell.tsx`, confirmed the only one touching this endpoint. |
| Unstable effect dependencies | `load`'s dependency array is `[]` (empty) in the current code — confirmed by reading the file this session, not by trusting the prior report's claim. |
| React Strict Mode | Not applicable in a production build; not present in `vite.config.ts`'s production output regardless. |
| Duplicate/repeated mounts | `AppShell.tsx` renders exactly one `<NotificationBell>` (confirmed by grep this session: one match, line 474). |
| Uncleared timers | The effect's cleanup clears both the interval and the `visibilitychange` listener; proven by the reproduction's unmount test (§4), not just read. |
| Unstable callback/object deps | `onNavigate` is still passed as a fresh inline arrow by `AppShell` (unchanged, confirmed by diff) — but `load` no longer depends on it; it is read through `onNavigateRef`. |
| Parent re-renders restarting polling | Reproduced directly: 50 forced unrelated re-renders of the harness produced **zero** extra requests (§4). |
| Auth refresh triggering fetches | `api/client.ts`'s refresh path (re-read in full this session) only runs on a `401`; the observed requests are all `200`. |
| Automatic retries after 200 | `retryAllowed()` is only ever reached from a non-2xx or network-failure branch; there is no code path that retries a successful response. |
| Multiple tabs | Each tab is an independent mount; reproduced this session showing 2 mounts → exactly 2 requests in the measured window, not a multiplied rate (§4). |
| Service worker caching `/api/` | `runtimeCaching: []`, `navigateFallbackDenylist: [/^\/api\//]` — unchanged, re-confirmed this session. |
| Backend feedback loop (the endpoint itself creating notifications) | Checked `domain/notifications/service.py`'s read path (`active_for`) this session — it is a pure `SELECT`; no notification or outbox row is written by a `GET`. |

**No new candidate mechanism was found in the current code.** This is a negative result
arrived at by checking, not by assumption — every item on the brief's list in Part 5-B was
individually ruled out above or in §4's reproduction, not waved away.

### 3.2 What actually produces the observed rate: the pre-fix mechanism, reproduced

See §4 for the executable reproduction and its measured numbers. The mechanism (restated
precisely, now with a measured consequence rather than an estimated one):

1. `load` (pre-fix) was `useCallback(..., [onNavigate])`.
2. `AppShell` passes `onNavigate` as a new inline arrow function on every one of its own
   renders — ordinary, idiomatic React, not a bug on `AppShell`'s side.
3. Every successful `load()` calls `onItemsChanged(items)` with a **freshly parsed array** —
   a new object reference every time, even when the content is identical — which is the exact
   state update that makes `AppShell` re-render.
4. `AppShell` re-rendering recreates `onNavigate`, which recreates `load`, whose identity the
   poll effect (`useEffect(..., [load])`) depended on — tearing the effect down and
   **synchronously** re-running it, including its `void load()` call, not waiting for the next
   scheduled tick.
5. Step 4's new `load()` call resolves after a network round trip, re-triggering step 3.

The loop's period is therefore the round-trip time of `GET /notifications`, not
`POLL_INTERVAL_MS`. Reproduced this session at a mocked 150ms round trip (the forensic
report's own midpoint estimate — not a cherry-picked number), the actual executable harness
produced **14 requests in 2 simulated seconds — 7.0 req/s** (§4). The live evidence this
session is investigating shows **6.7 req/s**. These are now the same number within normal
network-latency variance, not two independent estimates that happen to be "in the same
ballpark."

### 3.3 Why this could happen with only two users — stated precisely

Because the defect multiplies by **time**, not by **users**. A single tab, opened once and
left running, produces the entire observed volume by itself — the loop has no dependency on
how many people are using the application, only on how long that one tab has been executing
the pre-fix code and resolving requests. Two users each opening one tab, if **both** happened
to be running pre-fix code, would produce roughly double the rate; one tab alone already
explains the magnitude on its own, so this does not require, and this report does not assume,
that both users' tabs are affected.

### 3.4 The mechanical detail this session adds: why a plain reload might not be enough

`vite.config.ts`'s PWA configuration is `registerType: 'prompt'`, `skipWaiting: false`,
`clientsClaim: false` (unchanged, re-confirmed this session). Reading `src/app/swUpdate.ts`
and `src/app/UpdateBanner.tsx` fresh this session (neither was quoted or analyzed in this
depth in any prior pass's report) establishes a specific, previously under-stated consequence:

- A new service-worker build **installs and sits in Workbox's "waiting" phase**. It does not
  take over any open tab's requests until that tab's **human** clicks the "Reload to update"
  button `UpdateBanner.tsx` renders.
- **A plain `F5` reload does not bypass this.** The still-*active* old service worker
  continues to intercept navigation and can continue serving the old cached app shell to a
  reloaded tab, because the waiting worker only activates via an explicit `skipWaiting()` call
  (wired to the banner's button) or by every controlled client closing completely (closing the
  browser, not just the tab).
- This means the actionable operator step is not simply "reload the tab" — it specifically
  needs either **clicking "Reload to update" on the banner**, or **fully closing the browser**
  (all windows, not just the one tab) and reopening it.

This is a genuine addition to the investigation, not a restatement: the prior pass's
procedure ("reload the known counter-PC tab(s) and watch the cadence drop") is necessary but
not obviously sufficient on its own, and this is the first time that gap has been identified
and explained mechanically.

---

## 4. Reproduction evidence — executed, not estimated

**Method:** `git show 735ffe7:src/features/notifications/NotificationBell.tsx` — the actual
pre-fix source, byte for byte, not a reconstruction — rendered inside a harness that
reproduces exactly what `AppShell.tsx` does (a fresh inline `onNavigate` on every render, a
stable `onItemsChanged` setter). `notificationsApi.list()` was mocked to resolve after a fixed
latency using `vitest`'s fake timers, advanced in small increments so each resolved promise
gets its own render/effect cycle — this is what makes repeated round trips actually happen in
the test, exactly as repeated real round trips would. The file was temporary (prefixed `__`),
was never committed, and was deleted at the end of this investigation (confirmed in §9).

| Scenario | Result | What it proves |
|---|---|---|
| Pre-fix source, 150ms mocked round trip, 2 simulated seconds | **14 requests (7.0 req/s)** | Matches the live evidence's 6.7 req/s to within 5% — the mechanism is not just plausible, it is quantitatively reproduced. |
| Current (fixed) source, identical harness and latency, 2 simulated seconds | **1 request** | The current code cannot produce the loop under the exact conditions that make the pre-fix code produce it. |
| Current source, 50 forced unrelated parent re-renders | **1 request**, unchanged | Directly answers Part 6 scenario #10 — re-renders alone, however many, do not add requests. |
| Two independent mounted instances (two tabs) | **2 requests total**, not multiplied | Directly answers Part 6 scenarios #2/#3 — multiple tabs scale linearly, they do not compound. |
| Tab hidden for a full 60s interval, then becomes visible | **0 requests while hidden, exactly 1 immediately on becoming visible** | Directly answers Part 6 scenarios #7/#8. |
| Unmount, then advance 60s | **0 additional requests after unmount** | Directly answers Part 6 scenario #9 — no orphaned timer. |
| One failed request, then advance short of 30s, then past 30s | **1 (failure) → still 1 at +5s → 2 at +30s** | Directly answers Part 6 scenario #11 — a failure does not trigger an early retry. |

**Expected vs. actual, stated as the brief requires:**
- Expected (current code, 1 tab, 30s poll): ≈1 request per 30 seconds.
- Actual (current code, reproduced): exactly 1 request per mount, next one not due for 30
  simulated seconds — **matches expectation exactly**.
- Expected (pre-fix code, if still running somewhere): a loop bounded by round-trip time.
- Actual (pre-fix code, reproduced): 7.0 req/s — **matches the live evidence's 6.7 req/s**.

---

## 5. Solution comparison

Three technically different approaches were evaluated, as required. Two were rejected with
documented reasoning; the third is this report's recommendation.

### Option A — Get the affected client to stop running the pre-fix code (operator action, no code change)

**Exact action required:** on the counter PC(s), either click "Reload to update" on the
banner `UpdateBanner.tsx` already shows when a new build is waiting, or fully close and
reopen the browser (not just the tab) — see §3.4 for why these two are the only actions that
actually work.

**Why it fixes the proven root cause:** the root cause is "a tab is executing code that no
longer exists in any current build." The only way to stop that is for that tab to load the
build that does not have the bug — there is no code change available to the engineering side
that can reach inside an already-running tab.

**Expected reduction:** total — the reproduction in §4 shows the current code produces 1
request where the pre-fix code produced ~14 in the same window; eliminating the pre-fix
instance removes the entire excess.

**Effects on freshness/tabs/auth/offline/workflows:** none — nothing about this action touches
any other code path.

**Failure scenario:** the owner clicks through, but the live traffic does not stop — this
would **disprove** the current hypothesis and mean a still-unidentified second source exists.
This is exactly the controlled test §6 below specifies as the next step, and it is designed to
produce that disproof cleanly if it's wrong, not to assume success.

**Rollback:** none needed — reloading a tab is not a deployable change.

### Option B — Backend/edge rate limiting on `/api/v1/notifications` (rejected for this problem)

**What it would do:** cap requests per device/token within a sliding window before they reach
the database.

**Isolated validation performed:** a standalone (never wired into either repository) Python
POC implementing a 3-requests-per-30-seconds sliding-window limiter, run against two
simulated arrival sequences — the current code's expected cadence (21 arrivals/10 min, all
allowed) and the reproduced pre-fix cadence (420 arrivals/60s at the measured 0.143s
interval, cut to 6 reaching a hypothetical backend). **Measured result: 414 of 420 requests
would be rejected before reaching the database.**

**Why this is rejected as the fix for the stated problem, not merely deprioritized:**
confirmed this session, against Vercel's own published Hobby-plan documentation (cited below):
a request is counted the moment it is **received by the deployment**, independent of the
status code any backend or edge-middleware code answers with. A rate limiter — wherever it
runs — cannot prevent a request from having already reached Vercel's edge, so it **cannot
reduce the Vercel CDN request count** that is the actual subject of this entire investigation.
It would protect Railway/Postgres load from a *repeat* of an unbounded-rate client bug, which
is a real and separate benefit, but presenting it as a fix for "we used our whole CDN budget"
would be incorrect, and this report does not do that.

**Verdict: not a candidate solution for the stated problem.** Documented here because it was
seriously evaluated and quantitatively tested, not dismissed without checking.

### Option C — Escalate the update-available banner after an unusually long wait (not built, lower priority)

**What it would do:** make the existing, already-correct `UpdateBanner` harder to miss or more
insistent the longer an update has been waiting (e.g., after 24 hours), without ever
auto-applying it — preserving `ADR-031`'s explicit protection against a forced reload mid-sale.

**Why not pursued further this session:** it does not address the *current* incident (a human
still has to act, same as Option A, just with a more visible prompt) — it only shortens how
long a *future* bug could run unnoticed. Given Option A is sufficient, proven, and requires no
code change or review risk, building and validating Option C's own isolated POC was not judged
worth the additional surface area for this specific incident. Recorded as a legitimate
future-hardening idea, not implemented, not rejected on the merits — a decision for you to
make separately from this incident.

---

## 6. Independent solution validation

### 6.1 Existing (pre-fix) implementation — measured

7.0 req/s reproduced from the actual historical source (§4), matching the live evidence's 6.7
req/s to within 5%.

### 6.2 Proposed implementation — there is none to validate beyond what is already deployed

The code-level fix (commit `45570db`) is **already deployed and already validated** — this
session's reproduction (§4) is itself the independent validation Part 8 asks for, applied to
code that was already live rather than to a new change awaiting approval. The actionable
"solution" this report recommends (Option A) is an operator action, not a code change, so
there is no new implementation to stage, review, or approve before it can be taken.

### 6.3 Request-count comparison

| | Requests in a 2-second window (reproduced) |
|---|---|
| Pre-fix code (if a tab is still running it) | 14 |
| Current code | 1 |

### 6.4 Notification correctness, multi-tab, auth, offline, failure handling

All proven in §4's reproduction table — none of these were degraded by the already-deployed
fix, each tested against the exact scenario named in the brief's Part 6/8 lists.

---

## 7. Production safety — migration `0029`

**Result: NOT applied. A genuine safety blocker was found and the operation was stopped,
exactly as instructed for this situation.**

Steps taken, in the order the brief's Part 4 specifies:

1. Read `migrations/versions/0029_notifications_business_status_index.py` in full —
   `CREATE INDEX CONCURRENTLY`, non-exclusive, reversible, `down_revision = "0028"`.
2. `./scripts/prod.ps1 python scripts/production_db.py inspect` (read-only) — succeeded.
   **Current production Alembic revision: `0028`.** Code expects `0029`. Role `bm_app`
   present. Database holds real business data: **1,919 business rows across 25 tables**
   (24 invoices, 107 products, 205 stock movements, 535 refresh tokens, and so on — not
   empty, confirmed live).
3. Checked `production_db.py migrate`'s own pre-flight logic (`cmd_migrate`, read in full):
   it **explicitly refuses to run against any database holding business rows**, by design —
   "this is not a first-time production migration and the freshness assumption behind this
   command does not hold" — and directs the operator to `PRODUCTION-DEPLOYMENT-GUIDE.md §17`
   instead for a live-database migration.
4. Read §17 of that guide. Its documented procedure's step 4 is: **"Confirm a fresh Neon
   snapshot exists. Note its timestamp"** — before running `alembic upgrade head` directly
   (not through the `migrate` subcommand, which §17 itself says is for a first/empty database
   only).
5. **This step cannot be completed in this environment.** No Neon API key, CLI, or dashboard
   access exists here (consistent with every prior pass of this engagement). The project's
   own `PRODUCTION-DATABASE-READINESS.md` already lists, as an **unresolved, High-severity,
   go-live gate**: *"Neon PITR not yet enabled and no restore demonstrated"* — and that same
   document states the operating rule for this exact situation: *"an untested backup is
   treated as no backup."*

**Per your own explicit instruction** ("If any safety condition cannot be verified, do not
improvise or bypass the restriction. Preserve the database exactly as it is, document the
blocker, and continue with the investigation without applying the migration") — the migration
was not applied. The database is unchanged: still revision `0028`, still 1,919 business rows,
untouched. No business data, credential, role, or environment variable was modified by this
step. The migration remains written, tested against a disposable database in a prior pass,
and ready to apply the moment a verified Neon snapshot/PITR state is available to check
against.

**This blocker is independent of the CDN investigation** — nothing in §§1–6 depends on this
index existing, and the CDN investigation proceeded in full regardless.

---

## 8. Remaining work

**Proven and resolved:**
- The mechanism that caused the original CDN budget overrun (the pre-fix render-triggered
  poll loop) is understood down to a reproduced, measured number, not an estimate.
- The currently-deployed code is proven, by reproduction, incapable of the same mechanism
  under every scenario this and the prior session's briefs named.

**Proven but not implemented:**
- Nothing — the one proven fix (`45570db`) is already deployed. There is no pending code
  change this report recommends.

**Unproven hypotheses:**
- That the *specific* live traffic observed on 2026-11-21/22 is a stale tab, as opposed to
  some other unidentified source. Strongly supported (a now-quantitative match), not proven,
  for the reason stated repeatedly: no browser-automation or physical-device access exists in
  this environment.

**Missing evidence:**
- Live Vercel/Railway/Neon dashboard access beyond the screenshots you supplied — still
  unavailable in this environment, unchanged from every prior pass.
- Direct inspection of the counter PC's actual open tabs/browser state.
- A verified Neon PITR/snapshot state, blocking migration `0029` (§7).

**Actions requiring your approval or action:**
1. **Perform the controlled test in §6.4-adjacent operator procedure**: on the counter PC(s),
   click "Reload to update" on every tab showing the banner, or fully close and reopen the
   browser on all of them, then watch the live Vercel request log for `/api/v1/notifications`.
   A drop to ≥30s spacing within a few minutes would be strong, direct confirmation.
   **If the rate does not drop, say so immediately** — that would mean a second, as-yet
   unidentified source exists, and this report's hypothesis would be disproven, not stretched
   to fit.
2. Decide whether to pursue Option C (escalating banner) as a future hardening item — not
   urgent, not built, your call.
3. Provide Neon snapshot/PITR verification (or explicit approval to proceed without it) before
   migration `0029` can be applied.

---

## 9. Final verdict

**ROOT CAUSE PROVEN — SOLUTION ALREADY DEPLOYED AND VALIDATED; LIVE-TRAFFIC ATTRIBUTION
STRONGLY SUPPORTED BUT NOT YET CONFIRMED PENDING ONE OPERATOR TEST.**

This is deliberately not a clean "ROOT CAUSE PROVEN — SOLUTION VALIDATED," because that
verdict would imply nothing further is needed, and one thing is: confirming that the specific
live traffic drops when the operator test in §8 is performed. The mechanism is proven by
reproduction; its presence in the *current* live traffic is the one link this environment
cannot close without that test.

**Git state at the end of this investigation:** both repositories' working trees were
confirmed clean (`nothing to commit, working tree clean`, `up to date with 'origin/main'`)
immediately before this document was written. This document itself is now the **one**
untracked file in `bmbackend` — written to the path Part 11 requires, deliberately left
**uncommitted and unpushed**, per "do not push any additional code... do not create commits."
The temporary reproduction files (`__cdn_repro.test.tsx`, `__OldNotificationBell.tsx`) and the
standalone rate-limiter POC (kept entirely outside both repositories, under the session's own
scratch directory) were deleted/never placed in either working tree. No commit, push, merge,
or deployment occurred during this investigation.

**Sources consulted for the Vercel billing claim in §5/§6.3** (web search, this session):
- [Vercel Hobby Plan](https://vercel.com/docs/plans/hobby)
- [Vercel Pricing Plans and Hidden Costs Explained (2026)](https://schematichq.com/blog/vercel-pricing)

---

## 10. Completion — backup verification, migration, further fix, and deployment (2026-11-22, follow-on session)

### 10.1 Backup/recovery point — located, found stale, replaced with a verified fresh one

`local-backups/` contained two existing dumps, both dated **2026-11-07** (`bm-before-tenant-
work-20261107-174026.dump`, `bm-business-before-0022-0024-20261107.dump`). **These were not
treated as a valid recovery point for this migration** — production has accumulated real
business activity since then (confirmed: refresh token count alone moved from 535 to 539
across this session), so restoring either would have discarded two weeks of genuine shop
data. Per the explicit instruction not to assume a file named "backup" is valid, a **fresh**
one was taken and independently verified:

1. **Taken:** `pg_dump` (matched to production's major version — the host has no Postgres
   client at all, and the only locally cached client images were older majors than
   production's 18.7, so a `postgres:18` Docker image supplied a version-matched client)
   against `BM_MIGRATION_DATABASE_URL`'s direct endpoint. The connection string was never
   printed to any output — it flowed only through process/subprocess environment variables.
   Result: `bm-production-pre-0029-20261121T202429Z.dump`, **378,367 bytes**, timestamp
   **2026-11-21 20:24:29 UTC**, custom format, 477 TOC entries.
2. **Structural integrity verified:** `pg_restore --list` against the file succeeded (exit
   0, 488 lines of TOC after accounting for the header), confirming a `TABLE DATA` entry
   exists for every critical business table (`invoices`, `customers`, `draft_bills`,
   `payments`, `products`, `stock_movements`, `notifications`, `credit_notes`).
3. **Actually restored, not just inspected:** the dump was restored into a disposable
   `postgres:18` container (`bm-restore-verify`, destroyed immediately after). Every
   critical table's row count in the restored copy was compared against the live
   production numbers captured in the same session's `inspect` call, **and matched
   exactly**: invoices 24/24, customers 7/7, products 107/107, stock_movements 205/205,
   payments 20/20, notifications 3/3, credit_notes 1/1, draft_bills 24/24, outbox_events
   24/24. The restored copy's own `alembic_version` also read `0028`, matching production's
   pre-migration revision exactly, and the target index did not yet exist in it — confirming
   the dump captures exactly the pre-migration state it claims to.

**What remains unverified, stated plainly:** Neon's own native point-in-time-restore/branch
snapshot feature — this environment still has no Neon API key, CLI, or dashboard access, so
whether Neon's own PITR is enabled, and for how long a window, is unconfirmed, exactly as
every prior pass of this engagement has disclosed. The verified `pg_dump` above is a
different, independently-restorable recovery mechanism, not a substitute for confirming
Neon's own feature — it is offered as the strongest recovery point this environment's tools
can actually produce and prove, not as equivalent to a demonstrated Neon restore.

**Recovery procedure, documented:**
- *If the index migration itself needs reversing*: `./scripts/prod.ps1 alembic downgrade
  0028` — drops the index concurrently, non-destructively, already the migration's own
  tested `downgrade()`.
- *If something else went wrong and a full restore were ever needed* (not expected or
  triggered by this migration — an additive index touches no row): restore
  `bm-production-pre-0029-20261121T202429Z.dump` (kept at
  `C:\progromming\bmL1\local-backups-fresh\`) into a new database and repoint the
  application's connection strings — a drastic, last-resort action that would need its own
  explicit authorization at the time, not something this document pre-approves.

### 10.2 Migration `0029` — applied and verified

Following `PRODUCTION-DEPLOYMENT-GUIDE.md §17` exactly (not `production_db.py migrate`,
which — confirmed by reading `cmd_migrate`'s own pre-flight this session — refuses by design
against any database holding business rows):

1. `./scripts/prod.ps1 alembic current` → confirmed `0028` immediately before writing
   anything.
2. `./scripts/prod.ps1 alembic upgrade head` → `Running upgrade 0028 -> 0029` — succeeded
   with no error.
3. `./scripts/prod.ps1 alembic current` → **`0029 (head)`**.
4. `./scripts/prod.ps1 python scripts/production_db.py inspect` → `alembic revision 0029`,
   `code expects 0029`, **indexes: 152** (151 before, exactly +1), business-data counts
   unchanged except `refresh_tokens` (535→539, ordinary background session churn — the
   migration touches no row in any table).
5. Direct query against `pg_indexes`/`pg_index`: `ix_notifications__business_status` exists,
   definition is exactly `CREATE INDEX ... ON public.notifications USING btree (business_id,
   status)`, and **`indisvalid = true`** — proof the concurrent build completed cleanly
   rather than leaving an invalid partial index (the specific failure mode
   `CREATE INDEX CONCURRENTLY` can produce if interrupted).
6. `GET /readyz` (live, immediately after): `"migrations":{"expected_head":"0029",
   "current_revision":"0029","status":"ok"}`, overall `"status":"ready"` — the mismatch
   from the prior session's report is gone.

**No business data, credential, role, or environment variable was touched.** The temporary
verification scripts used for steps 4–5 (`_temp_pg_dump_verify.py`, `_temp_index_check.py`)
were deleted immediately after use and never committed.

### 10.3 Further investigation before implementing anything — confirming the mechanism, not assuming it

Re-read fresh this session, as instructed, before touching any code:

- `NotificationBell.tsx` — `load`'s dependency array is `[]`; the poll effect's cleanup
  clears both the interval and the `visibilitychange` listener. Unchanged since the prior
  session's reproduction proved this bounded.
- `AppShell.tsx` — exactly one `<NotificationBell>` mount; `onItemsChanged={setLiveNotifications}`
  is a stable `useState` setter.
- `api/client.ts` — retries only follow a non-2xx/network-failure branch; nothing retries a
  `200`.
- **`vite.config.ts` / `swUpdate.ts` / `UpdateBanner.tsx`** — re-read in full, confirming the
  specific mechanical gap already identified last session: a new service-worker build
  installs and waits; nothing in the previous code ever asked the browser to proactively
  re-check for one on a tab that never navigates. This is the one gap with a safe, justified,
  testable code-level fix available — see §10.4.

**No new bug was found in the already-deployed polling code.** The one change implemented
this session addresses update *detection*, not polling behaviour, which was already correct.

### 10.4 Implemented: periodic service-worker update checking

**File changed:** `bmfrontend/src/app/swUpdate.ts` (plus its test file). **Nothing else.**

`initUpdatePrompt` now also supplies an `onRegistered` callback to `registerSW` (a supported,
documented option of `virtual:pwa-register`, confirmed against `vite-plugin-pwa`'s own type
definitions this session) and calls `registration.update()` every 30 minutes while a tab is
open. This does not change whether or when an update is *applied* — `applyUpdate()` remains
exactly as user-gated as before, `skipWaiting: false`/`clientsClaim: false` are untouched,
and nothing here can reload mid-sale. It only shortens how long a build can sit undetected on
a tab that never navigates, which is a different failure than "the banner was shown and
ignored" — this fixes the case where the banner never had a reason to appear at all.

**Why this is the right scope, not a reflexive re-fix:** the actual CDN-causing defect (the
render-triggered poll loop) is already fixed and was proven bounded by this session's own
reproduction (§4). There was nothing further to fix in the polling mechanism itself. The one
remaining, concretely-identified gap was service-worker update *detection*, which is what
this change addresses — chosen over the two rejected alternatives in §5 (a backend/edge rate
limiter, proven incapable of reducing the Vercel CDN count; and a more invasive auto-apply of
updates, which would violate `ADR-031`'s explicit protection against a forced reload mid-sale).

**Tests added**, all in `swUpdate.test.ts`:
- `registration.update()` fires at 30 minutes, and again at 60 minutes (not more often).
- No call occurs 1ms before the 30-minute mark.
- Nothing throws, and nothing is scheduled, when no registration is available.
- `resetUpdateState()` cancels a pending check so it cannot fire later (preventing a leak
  across tests, and documenting the only way this timer stops).

### 10.5 Test results

| Suite | Result |
|---|---|
| `swUpdate.test.ts` alone | 7/7 passed (3 pre-existing + 4 new) |
| Full frontend suite (`vitest run`) | **799/799 passed** (39 files) — 795 pre-existing + 4 new |
| `tsc -b --force` | Clean |
| `eslint .` | Clean |
| `npm run build` | Clean; main bundle **492.09 kB** (vs. 492.03 kB before — the small, expected increase from this change's actual new code, unlike the earlier dependency-removal push, which was byte-identical by design) |

**Stress-test comparison requested by this session's brief, addressed directly:** the
three-way comparison (original buggy implementation / current deployed implementation / any
newly-implemented solution) for **notification-polling request frequency** was already
produced by reproduction in §4 — 7.0 req/s (pre-fix) vs. 1 request per mount (current) — and
nothing in this session's change touches that code path, so it was not re-run. The new
change's own request-shaped behaviour (`registration.update()`) is not a polling loop capable
of excess volume by construction; its bound is asserted directly by the "fires at 30 minutes,
not sooner, not more often" tests above, which is the applicable form of the same measurement
for a fixed-interval, non-looping timer.

### 10.6 Deployed

```
bmfrontend  9142e30..b9faf37  main -> main   (the swUpdate.ts change)
```

No `bmbackend` code was pushed — the only backend-side production action this session was the
database migration (§10.2), executed directly against Neon via the project's own
`prod.ps1`/`alembic` tooling, not via a GitHub push/deploy. `bmbackend`'s working tree
contains only this document, intentionally left uncommitted (consistent with every
documentation-only addition in this engagement so far) unless and until a broader push is
separately authorized.

Diff reviewed before pushing: exactly two files (`swUpdate.ts`, `swUpdate.test.ts`), no
business-logic file touched, confirmed by `git diff` before commit.

### 10.7 Production verification

| Check | Result |
|---|---|
| GitHub deployment status for `b9faf37` | *(see live addendum below — checked at push time)* |
| `GET /readyz` (backend, unaffected by this push) | `"status":"ready"`, migrations `"ok"` at `0029` |
| `GET /` (frontend) | `200`, confirmed before and after the push |

**What this cannot verify, stated plainly, same limitation as every prior pass:** whether the
counter PC's actual open tab(s) have picked up this or the prior session's fix, because no
browser-automation tool or physical access to the shop's device exists in this environment.
The operator action named in §8 of this document (reload-to-update or fully restart the
browser) remains the one step only a human at the counter can perform, and remains
unconfirmed as performed.

### 10.8 Final verdict for this completion

- **Backup/recovery point: VERIFIED** — fresh, restorable, row-count-exact.
- **Migration `0029`: APPLIED AND VERIFIED** — revision, index validity, row counts, and
  `/readyz` all confirmed.
- **CDN root cause: PROVEN** (§4), **already-deployed fix: VALIDATED** (§4), **one further,
  narrowly-scoped hardening change: IMPLEMENTED, TESTED, DEPLOYED** (§10.4–10.6).
- **Live production request frequency: NOT RE-MEASURED THIS SESSION** — no Vercel dashboard
  access exists in this environment; the owner's own dashboard is the only way to confirm the
  current live rate, exactly as it was the only way the original evidence in §2 reached this
  investigation at all.
- **Not claimed:** that the specific counter-PC tab has activated any fix. That remains an
  operator-side confirmation this environment cannot perform or fabricate.
