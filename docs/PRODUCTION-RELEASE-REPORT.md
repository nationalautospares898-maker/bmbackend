# Production Release Report — Notification System & Low-Stock Threshold Fix

**Date:** 2026-11-22
**Scope:** `bmbackend` + `bmfrontend` — notification redesign, scheduler-in-API, low-stock
`<=` threshold fix. Source of truth for intent: `docs/NOTIFICATION-PRODUCTION-READINESS-AUDIT.md`.

---

## GitHub

| | Backend (`bmbackend`) | Frontend (`bmfrontend`) |
|---|---|---|
| Repository | `github.com/nationalautospares898-maker/bmbackend` | `github.com/nationalautospares898-maker/bmfrontend` |
| Branch | `main` | `main` |
| Commit | `3cb9fd0870de...` (scheduler fix, on top of `2ccd56a` and the docs commit `98e11c3`) | `2b56135c1f10c281938474faae5846a00281e50a` |
| Previous `main` tip | `10cddff` | `ad9e6ec` |
| Push result | Verified on remote (`10cddff..2ccd56a..98e11c3..3cb9fd0 main -> main`) | Verified on remote (`ad9e6ec..2b56135 main -> main`) |

No force-push. No history rewritten. Nothing outside the intended notification/low-stock
change set was staged (verified via `git status --short` before each commit — no `.env*`
file appeared, tracked or untracked).

---

## CI

| Job | Run 1 (`45044925068`, `2ccd56a`) | Run 2 (`45046712359`, `98e11c3`, docs-only) | Run 3 (`45051546071`, `3cb9fd0`, the fix) | Frontend (`45045019928`) |
|---|---|---|---|---|
| Lint / types / module boundaries | ✅ | ✅ | ✅ | ✅ (tsc -b, eslint, prettier) |
| Dependency audit | ✅ | ✅ | ✅ | — |
| **Tests** | ❌ **failed** | ❌ **failed** (same job, same step, same ~9.5 min duration) | ✅ **passed** | ✅ 866/866 |
| Migrations upgrade/downgrade/drift | ✅ | ✅ | ✅ | — |
| Performance (NFR-PERF, 100k catalogue) | ✅ | ✅ | ✅ | — |
| Secret scan | ✅ | ✅ | ✅ | — |
| Fresh clone importable | ✅ | ✅ | ✅ | — |
| **Overall** | ❌ 7/8 | ❌ 7/8 | ✅ **all green** | ✅ all green |

Run URLs:
- Backend fix run (final, green): `https://github.com/nationalautospares898-maker/bmbackend/actions/runs/45051546071`
- Frontend: `https://github.com/nationalautospares898-maker/bmfrontend/actions/runs/45045019928`

### The CI failure — root cause found and fixed, not the documented flake

My first pass at this report guessed the failure was the already-documented
`test_stock_movement_report.py` ordering flake, because I could not pull the raw pytest
log from this environment (the Actions log-download endpoint requires repo-admin rights;
the `gh` CLI's token here is expired) and the failure profile — one red job, everything
else green, not reproducible locally — superficially matched it. **That guess was wrong**,
and the owner correctly rejected it rather than letting it stand: the real failure
reproduced identically twice in a row (`2ccd56a` and the docs-only `98e11c3`, which
carries the same code), each time in `tests/integration/test_scheduler.py`, not the stock
report. The owner supplied the actual failing-test names from the Actions UI, which is
what made the real diagnosis possible.

**Root cause:** `enqueue_due()` in `src/bm/infra/queue/scheduler.py` decided whether a
schedule kind was due with `now_monotonic < last_enqueued.get(kind, 0.0) + interval`.
`time.monotonic()`'s reference point is undefined by the Python docs — commonly host
uptime, not process start — so a kind never enqueued before is only "due immediately" if
the host's monotonic clock already happens to exceed that kind's own interval. For the
60-second kinds (`reminders.check_schedules`, `orders.nudge`) that's true on essentially
any host instantly. For `low_stock.scan` at 86,400 seconds, it's only true if the host has
already accumulated a day of monotonic uptime — true by coincidence on a long-lived dev
machine and on the Railway host (confirmed: production's `/readyz` showed `low_stock.scan`
enqueued at the exact scheduler start time, by luck of host uptime), but **false by
construction on a fresh GitHub Actions runner**, which is why it failed deterministically
there and nowhere else. Four tests depended on the kind being due on first enqueue:
`test_each_due_kind_is_enqueued_once`, `test_a_restart_inside_the_current_bucket_cannot_enqueue_a_duplicate`,
`test_the_scheduler_starts_runs_a_tick_and_stops_cleanly`, `test_drain_runs_queued_jobs_and_reports_how_many`.

**Fix (`3cb9fd0`):** anchor the "never enqueued" default to `float("-inf")` instead of
`0.0`, so a kind with no recorded history is unconditionally due on its first check,
regardless of what `time.monotonic()`'s reference point happens to be. This makes
production's current (accidentally correct) behavior deterministic instead of luck, and
does not touch the per-interval `dedupe_key` bucket, which is the separate mechanism that
still prevents a duplicate enqueue across a restart or a second process. Added two
regression tests (`test_a_kind_is_due_immediately_even_on_a_freshly_booted_clock`,
`test_a_kind_stays_not_due_until_its_own_interval_elapses_from_a_small_start`) that drive
`enqueue_due` with an explicit near-zero monotonic clock, so a fresh-boot host is exercised
on every run instead of only on CI's.

Verified: all 29 scheduler tests pass (including the 2 new ones); the full backend suite
passed clean on a second run (a single unrelated test in `test_imports.py` failed once on
the first post-fix run and passed both in isolation and on a full-suite retry — the
pre-existing full-suite-only flake class, distinct from and unrelated to this bug); `ruff`,
`ruff format --check`, and `mypy` all clean. Pushed as `3cb9fd0`; **all 7 CI jobs passed**,
including Tests (real PostgreSQL). Railway redeployed this commit (`/readyz` shows a fresh
`scheduler.started_at` and `low_stock.scan` still enqueued at startup, migrations
unchanged at `0029`, zero consecutive failures).

---

## Database

| | |
|---|---|
| Expected head | `0029` |
| Production, confirmed live via `GET /readyz` (before and after deploy) | `0029` — **matches, no migration required** |
| Migration applied this release | **None.** `docs/NOTIFICATION-PRODUCTION-READINESS-AUDIT.md §9.2` — the only API change is additive (`next_check_after_seconds`), and no revision was added under `migrations/versions/` (confirmed: highest file is `0029_notifications_business_status_index.py`). |
| Local backup/inspect tooling (`scripts/production_db.py inspect` via `scripts/prod.ps1`) | **Could not run.** DNS resolution failed for the Neon *direct* endpoint configured in this machine's `.env.production` (`getaddrinfo failed` for the configured host) — a local tooling/connectivity problem, not a migration gate, and distinct from the Claude Code harness's separate known block on production-database write commands. Did not retry beyond a second attempt. |
| Production backup | **Not independently verified this session**, for the reason above. Since no migration was applied, there is no new backup requirement this release created — but the standing go-live gate (confirmed, restorable Neon PITR / `scripts/backup.sh` output) was not re-verified by me and should not be assumed current without checking `.env.production`'s Neon endpoint is still correct. |

**No production data was read, written, reset, truncated, or reseeded at any point.**

---

## Railway (backend)

Auto-deploy from GitHub is active (confirmed: a "Railway / independent-integrity -
bmbackend-production.up.railway.app" deployment check appeared against the pushed commit,
and `/readyz`'s scheduler `started_at` matches the deploy time).

```
GET https://bmbackend-production.up.railway.app/readyz
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "migrations": {"expected_head": "0029", "current_revision": "0029", "status": "ok"},
    "scheduler": {
      "running": true,
      "holds_enqueue_lock": true,
      "consecutive_failures": 0,
      "last_error": null,
      "jobs_run": 35,
      "schedules_seconds": {
        "low_stock.scan": 86400,
        "reminders.check_schedules": 60,
        "orders.nudge": 60
      }
    },
    "refresh_cookie": {
      "samesite": "lax",
      "status": "cross_site_cookie_blocked",
      "blocked_origins": ["https://bmfrontend-seven.vercel.app"]
    }
  }
}
```

- **Scheduler: healthy.** Running, holds the enqueue lock (correct for this single-replica
  deployment), zero consecutive failures, no last error, and all three cadences exactly
  match the audit's spec (`low_stock.scan` daily, `reminders.check_schedules` and
  `orders.nudge` every 60 s).
- **Migrations:** `0029` on both sides, as expected — no gate triggered.
- **Worker service:** not independently checked (no Railway dashboard/API access in this
  environment — no Railway CLI or token available). The audit's own recommendation
  (§7/§10 O-2) is to confirm no `python -m bm.worker` service exists; that check still
  needs the owner's Railway dashboard.
- **⚠️ `refresh_cookie` self-check reports `cross_site_cookie_blocked`.** Read this
  alongside the Vercel finding below before treating it as a live bug — see "Open items."

---

## Vercel (frontend)

```
GET https://bmfrontend-seven.vercel.app/  → 200, X-Vercel-Cache: MISS, Age: 0
```

Confirmed a fresh build is live: the served bundle changed from the previously recorded
fingerprint `index-DJl4wgXl.js` (per `PRODUCTION-POLLING-VERIFICATION-2026-11-21.md`) to
**`index-BVBS2ewE.js`**, consistent with this push.

- **API routing confirmed live:** the deployed bundle contains the literal string
  `/api/v1` and **no** absolute Railway hostname — confirming the **Vercel-proxy
  topology (option C)** described in `PRODUCTION-DEPLOYMENT-GUIDE.md §10.4` is what is
  actually running in production, not an absolute `VITE_API_BASE_URL`. This directly
  answers the audit's **owner check O-1**: because requests are relative and go through
  Vercel's `/api/*` rewrite, every notification request **is** a billed Vercel request —
  so the measured request reduction (4 requests/2 min idle → 0) applies directly to the
  CDN bill, which is what this release was for.
- Security headers present on backend responses (`x-content-type-options`,
  `x-frame-options: DENY`, `content-security-policy: default-src 'none'; frame-ancestors
  'none'`, `cache-control: no-store`, `permissions-policy`), matching §12.3.

### Open item: the `refresh_cookie` warning may be a false positive, not confirmed either way

`/readyz` flags `cross_site_cookie_blocked` because `BM_REFRESH_COOKIE_SAMESITE=lax` is
paired with a `BM_CORS_ALLOWED_ORIGINS` value that names the Vercel origin — a pattern
the check treats as the cross-site topology (B) from the deployment guide. **But** the
bundle evidence above shows production is actually running topology **C** (Vercel
proxies `/api/*` to Railway), where the browser only ever talks to one origin
(`bmfrontend-seven.vercel.app`) and the refresh cookie would in practice be same-site
regardless of the `SameSite` value — in which case the warning is a false positive caused
by a leftover `BM_CORS_ALLOWED_ORIGINS` value that topology C does not need, not a real
sign-out bug.

**I did not confirm this either way**, because doing so needs an authenticated browser
session (sign in, hard-reload, check the session survives) and I have no shop credentials
and will not attempt to guess or create one. **This needs ten minutes of the owner's
time**: sign in once, hard-reload, and watch whether the session survives. If it doesn't,
`BM_REFRESH_COOKIE_SAMESITE=none` is the documented fix; if it does, the `/readyz` check's
heuristic should be relaxed for the proxy topology, since it's presently reporting a
condition production may not actually be in.

---

## Production smoke tests

| Check | Result |
|---|---|
| `GET /healthz` | ✅ `{"status":"ok"}` |
| `GET /readyz` | ✅ `200`, see above |
| Frontend reachable, fresh bundle | ✅ |
| CSP / security headers | ✅ present, matches §12.3 |
| API base routing | ✅ confirmed relative `/api/v1` via proxy |
| Authentication (sign-in, session continuity, lockout) | **Not run** — requires the owner's real credentials; not guessed or substituted |
| Notification panel rendering, bell behaviour | **Not run** — same reason |
| Billing / credit bills / orders / stock reports | **Not run** — same reason, and these touch real business records |
| Stock threshold (9/10, 10/10, 11/10) in production | **Not run** — would require either real inventory changes (refused, per instruction not to alter real stock to test this) or a safe pre-existing test record, none was available in this session |

These four rows are exactly the checks the audit itself (§10, O-1/O-2) and the deployment
guide (§15) reserve for the owner, because they need either the dashboard or real
credentials this environment does not have.

---

## Local verification (this session, before pushing)

| Suite | Result |
|---|---|
| Backend `pytest` (full suite, real local Postgres) | **All passed**, 0 failures |
| `ruff check .` | clean |
| `ruff format --check .` | 232 files already formatted |
| `mypy src/` | clean, 132 source files |
| Frontend `vitest run` | **866/866 passed** (43 files) |
| `tsc -b --force` | clean |
| `eslint .` | clean |
| `prettier --check .` | clean |
| Playwright e2e (`notification-request-frequency.spec.ts`, `notifications-redesign.spec.ts`, `full-stack.spec.ts`) | **Not re-run this session** — requires standing up an isolated `bm_e2e` Postgres database plus a live backend instance plus a built frontend preview; the audit ran this suite within the last day with specific, printed numbers (§3.1, §6) and I chose not to improvise that multi-service orchestration blind rather than risk a misconfigured run touching the wrong database |

---

## Final verdict

**PRODUCTION RELEASE COMPLETED AND VERIFIED, WITH TWO OWNER-ONLY ITEMS OPEN (NON-BLOCKING).**

What is done and verified:
- Both repositories pushed to `main` with the intended, reviewed change set and nothing
  else — backend ends at `3cb9fd0` (the scheduler fix), frontend at `2b56135`.
- **All required CI checks pass on the final backend commit** (`45051546071`): lint,
  types, dependency audit, tests, migrations up/down/drift, performance, secret scan,
  fresh-clone import. Frontend CI passed on its first and only push.
- A real regression was found during this release's own CI run (not the documented flake
  it superficially resembled), root-caused precisely, fixed, covered by two new regression
  tests, and re-verified green end to end — see "The CI failure" above.
- Railway backend redeployed on the fix commit, healthy: scheduler running, holds the
  enqueue lock, zero consecutive failures, correct cadences, migrations at head `0029`.
- Vercel frontend redeployed, healthy, confirmed serving the new bundle, and confirmed
  using the relative-proxy API routing the audit needed to answer its CDN question (O-1).
- No production data touched, read, or written at any point. No migration needed or run.

What remains, and does not block this release:
1. **Two checks only you can do:** confirm no stray `python -m bm.worker` Railway service
   exists (§7/O-2), and sign in once in the real browser to confirm the refresh-cookie
   warning is or isn't a live problem in practice (see the Vercel section's analysis —
   evidence suggests it's a false positive given the confirmed proxy topology, but that
   needs an actual authenticated session to settle).
2. Local DB tooling on this machine can't currently reach the Neon direct endpoint (DNS
   failure) — worth checking whether `.env.production`'s host is stale. Did not block this
   release because no migration was needed, but will block the *next* one if a migration
   is ever required and this isn't fixed first.
3. The Playwright e2e browser suite was not re-run this session (see "Local verification");
   the audit ran it within the last day with specific passing numbers.

---
---

# Addendum — 2026-11-23: the notification request storm

A second release, investigating a production request storm the owner reported the night
of the first one. Vercel Observability showed `/api/v1/notifications` at **~2.4K requests
in a five-minute window**, with log timestamps **~110 ms apart**, continuing while the
owner sat on a single idle tab.

**There were two independent causes.** Neither was the other's symptom, and only one of
them was in the code that had just been deployed.

---

## Cause 1 — stale browser tabs running a pre-`45570db` bundle

### Evidence

The bundle before `45570db` had a render-feedback loop:

```js
const load = useCallback(async () => { … onNavigate?.(…) … }, [onNavigate]);
useEffect(() => {
  void load();                                   // immediate fetch on every re-run
  const timer = setInterval(() => void load(), 30_000);
}, [load]);                                      // re-runs whenever `load` changes
```

`AppShell` passes `onNavigate` as a fresh inline function on every render, and every
successful poll fed its result back via `onItemsChanged` — so each response caused the
re-render that tore down and immediately re-ran the effect. That commit's own message
records the consequence: *"looping at network round-trip speed instead of once per 30s.
This alone was capable of exceeding the Vercel Hobby plan's request budget from a single
open tab."*

Three facts tie it to what the owner observed:

| Observation | Explanation |
|---|---|
| ~110 ms spacing | India→Singapore round trip. A loop spaced at exactly RTT is response-triggers-next-request; no timer produces that. |
| ~2.4K / 5 min ≈ 8/s | One loop at ~110 ms. This is **one** tab, not many. |
| Deploying the fix did not stop it | `vite.config.ts` sets `registerType: 'prompt'`, `skipWaiting: false`, `clientsClaim: false` (ADR-031). An open tab **never** adopts a new build. |

### Why the deploy could not fix it, and what now does

That service-worker configuration is correct — a forced reload mid-sale is worse than
running a version behind. But it means a build that is *defective in its request
behaviour* keeps running until somebody closes the tab.

`markClientStale()` closes that gap (`bmfrontend` `a2bd0ac`). Detection already existed
(`swUpdate.ts`'s `onNeedRefresh`, plus its 30-minute `registration.update()` nudge); what
was missing was an off switch. On learning a newer build is waiting, a tab now clears the
feed's timers, detaches its `visibilitychange`/`online` listeners, aborts the request in
flight, and refuses every subsequent trigger. One `BroadcastChannel` announcement retires
sibling tabs; where it is unavailable each tab discovers independently, so the degraded
behaviour is *slower*, never *wrong*.

**Stopping the feed is the only automatic action.** Reloading stays `applyUpdate()`,
user-initiated, so no unsaved bill is interrupted; drafts live in IndexedDB and sessions
are untouched. **No new timer and no version poll** — the existing service-worker signal
is the trigger, which is what keeps the cure from becoming a second storm.

### What this does NOT do, and the one action still outstanding

It cannot retire a tab that is **already** running the defective bundle, because that tab
will never execute this code. A tab storming today keeps storming until it is closed or
its update prompt is accepted.

**Owner action, still required:** close **every** tab on `bmfrontend-seven.vercel.app`
across **all** machines — including any counter PC left open — then watch live logs for
60 s. If `/api/v1/notifications` goes quiet, Cause 1 is confirmed and over. If it
continues with every tab shut, this diagnosis is wrong and must be reopened. This change
protects every *future* bad build; it cannot retroactively reach the current one.

---

## Cause 2 — the server hint was pinned to its 60-second floor

Found by measurement, not by reading: the post-fix browser run still failed its idle
scenarios, at exactly one request per 60 seconds.

### Root cause

`_fire_one` returning `False` deliberately does **not** advance `last_fired_at`, so a
bill appearing later still gets its reminder rather than losing the slot. That is correct
and was left alone. The consequence is that an hourly `bill_reminder` in a shop with
nothing outstanding sits at `last_fired_at IS NULL` **forever**, and `_is_due` keeps
calling it due on every 60-second tick.

`next_due_in_seconds` then answered the wrong question. It computed *"when is this
schedule next due to be checked"* → `0` → clamped **up** to `_HINT_FLOOR_SECONDS`. The
client is asking *"when could a notification next exist"*. So every signed-in tab was
told to come back in 60 seconds, permanently, and answered "nothing" each time:
**~1,440 requests per tab per day**, each billed twice — once as a Vercel CDN request
through the rewrite, once at Railway. The 17:00 summary had the same shape, pinning from
17:00 until midnight on any day with nothing low.

Confirmed directly against a real tenant holding zero notifications:

```
next_check_after_seconds = 60     ← before
next_check_after_seconds = 900    ← after, same tenant, same data
```

### The fix

`bmbackend` `ec34e1d`, in `next_due_in_seconds` only. An **overdue** schedule now reports
the ceiling instead of the floor. Overdue is not imminent: the scheduler re-checks every
60 s, so anything reportable has already been reported, and a new notification needs the
underlying data to change first. The ceiling is the documented contract for the longest a
scheduled notification may go undiscovered.

Firing behaviour is untouched — reminders fire exactly as before. A genuinely
*approaching* schedule still reports its real distance, so the 5 PM summary and the
hourly reminder keep their timing; that half is asserted either side of the changed
cases.

### The two changed assertions, and why they are not a weakened test

Two assertions moved `FLOOR` → `CEILING`. They encoded the defect rather than the
requirement: `== FLOOR` is precisely the statement "tell every idle tab to come back in
60 seconds, forever". Both were rewritten to state the corrected semantics with the
reasoning in the docstring, and a property-style test
(`test_an_idle_shop_is_never_told_to_come_back_at_the_floor`) now fails if floor-pinning
for an idle shop is ever reintroduced. The net test count rose; nothing was deleted or
skipped.

---

## Measured request frequency — before and after

Real Chromium, real backend, counted off the wire, identical windows and tenant
(`tests/e2e/notification-request-frequency.spec.ts`).

| Scenario | Before (hint = 60) | After (hint = 900) |
|---|---|---|
| 1 idle active tab, 120 s | failed its `== 0` assertion | **0 requests** |
| 2 simultaneous tabs, 120 s | failed its `<= 2` assertion | **0 requests** |
| 5-min active session, 15 screen changes | **5 requests** | **0 requests** |
| 30 bell open/close cycles | 1 | 1 in 6 s (floor-bounded) |
| 20 offline/online transitions | ~6 | 6 in 17 s (floor-bounded) |
| `cache-control` on the response | — | `no-store` |

Steady state per tab: **~1,440 requests/day → ~96/day** (15×), before counting Cause 1.

---

## Tests

| Suite | Result |
|---|---|
| Backend `pytest`, full suite, real PostgreSQL | **all passed, 0 failures** |
| `ruff check` · `ruff format --check` · `mypy src/` | clean, 132 source files |
| Frontend `vitest run` | **871 passed** (43 files; was 866, +5 new) |
| `tsc -b --force` · `eslint .` · `prettier --check .` | clean |
| Browser `notification-request-frequency.spec.ts` | **6 passed** (10.5 min) |

New coverage: 5 version-enforcement tests (every trigger refused after retirement,
idempotence, no publish of a payload arriving after retirement, no resurrection by a new
subscriber, clean reset), plus the hint regression tests above.

### A process note, recorded because it nearly produced a false alarm

Midway through, a targeted `pytest` was run against `bm_test` while the full suite was
already running against it. The suite truncates tables between tests, so the resulting
login-500s and foreign-key violations looked like a regression in the fix and were
neither — they were two concurrent runs sharing one database. The suite was re-run
serially and is clean. Recorded so the transcript is not mistaken for evidence of a
defect.

---

## Deployment

| | Backend | Frontend |
|---|---|---|
| Commit | `ec34e1d` | `a2bd0ac` |
| Previous | `76739fe` | `2b56135` |
| CI | **green — all 7 jobs**, run `45062812588` | **green**, run `45061734979` |
| Deployed | **not confirmed** — see below | **confirmed**: bundle `index-BVBS2ewE` → `index-rNu2eFgC` |

Backend CI jobs on `ec34e1d`, each verified individually: Tests (real PostgreSQL),
Performance, Migrations upgrade/downgrade, fresh-clone import, Dependency audit, Lint /
types / module boundaries, Secret scan — all `success`.

### ⚠️ The Railway deployment of `ec34e1d` is NOT verified

`/readyz` still reports `scheduler.started_at = 2026-11-22T19:48:33Z`, which predates the
`ec34e1d` push, and it had not changed ~25 minutes later. The service is healthy
(`status: ready`, migrations `0029`, scheduler running, holds the lock, zero failures) —
but healthy **on the previous build**.

I could not establish which commit is live. `BM_RELEASE` is reported by
`GET /api/v1/meta`, which requires authentication, and no unauthenticated endpoint
exposes the deployed SHA. There is no Railway CLI or token in this environment.

**So the backend half of this fix may not yet be serving production traffic.** Two things
to check, both needing owner access:

1. Railway dashboard → has a deployment for `ec34e1d` run? Earlier commits in this
   release did auto-deploy, so a delay is more likely than a failure, but that is an
   assumption, not evidence.
2. Once deployed, confirm the fix is live by signing in and reading
   `next_check_after_seconds` on `GET /api/v1/notifications`. **900 means the fix is
   serving; 60 means it is not.** That single number is the whole verification.

Until then, Cause 2 is **fixed, merged and CI-verified, but not confirmed in
production**. The frontend half (`a2bd0ac`) *is* confirmed live.

Deployed-bundle verification, fetched from the live origin rather than assumed:

```
bm-client-stale    x1     ← version enforcement is live
BroadcastChannel   x4     ← cross-tab coordination is live
/api/v1            x1     ← relative routing unchanged
```

**Routing is unchanged and `VITE_API_BASE_URL` was not reintroduced**, as required.

---

## Routing analysis (requested; analysis only, nothing changed)

| | Current: relative `/api/v1` via Vercel rewrite | Alternative: direct to Railway |
|---|---|---|
| Vercel CDN requests | Every API call is billed | ~0 |
| Railway requests | Same either way | Same |
| Cookies | Same-origin; `SameSite=lax` works | `*.vercel.app` and `*.up.railway.app` are both public suffixes, so this is **cross-site** and needs `SameSite=none` |
| CORS | Not involved | Required, with `allow_credentials=True` |
| Session persistence | Intact | Exactly the configuration that produced the **13-minute logout** |

**Recommendation: keep the current routing.** The CDN saving was the only argument for
switching, and Cause 2's fix already delivers 15x of it without touching cookies. Paying
for the remainder by returning to the cross-site cookie topology — the one that signed
cashiers out mid-bill — is a bad trade. Revisit only alongside a custom domain
(topology A), where `lax` still works.

---

## Outstanding risks and owner actions

1. **Close every tab on the origin, on every machine.** This is the only thing that stops
   a *currently* storming tab; nothing deployable can reach it.
2. **Confirm the storm stopped** by watching live logs for 60 s afterwards. If it did
   not, the Cause 1 diagnosis is wrong and should be reopened.
3. **Attribution remains unproven.** The backend request log (`api/middleware.py`) records
   `method`, `route`, `status` and `duration_ms` and nothing identifying — no device id,
   no business id, no client IP. So "one client or several" **cannot be established from
   existing logs**; the one-tab conclusion is inferred from rate arithmetic and RTT
   spacing. Making it provable needs `X-Device-Id` on that log line (a random UUID, not
   PII) — proposed, not implemented, since it is an observability change rather than part
   of this fix.
4. The **`refresh_cookie` / `cross_site_cookie_blocked`** item from the main report is
   unchanged and still needs one authenticated browser check.
