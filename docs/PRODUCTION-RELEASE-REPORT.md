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
