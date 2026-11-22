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
| Commit | `2ccd56a0a673d4767cb5560b9d05533882595012` | `2b56135c1f10c281938474faae5846a00281e50a` |
| Previous `main` tip | `10cddff` | `ad9e6ec` |
| Push result | Verified on remote (`10cddff..2ccd56a main -> main`) | Verified on remote (`ad9e6ec..2b56135 main -> main`) |

No force-push. No history rewritten. Nothing outside the intended notification/low-stock
change set was staged (verified via `git status --short` before each commit — no `.env*`
file appeared, tracked or untracked).

---

## CI

| Job | Backend run (`45044925068`) | Frontend run (`45045019928`) |
|---|---|---|
| Lint / types / module boundaries | ✅ pass | ✅ pass (tsc -b, eslint, prettier) |
| Dependency audit | ✅ pass | — |
| **Tests** | ❌ **failed** (see below) | ✅ 866/866 pass |
| Migrations upgrade/downgrade/drift | ✅ pass | — |
| Performance (NFR-PERF, 100k catalogue) | ✅ pass | — |
| Secret scan | ✅ pass | — |
| Fresh clone importable | ✅ pass | — |
| **Overall** | ❌ 7/8 checks green | ✅ all green |

Run URLs:
- Backend: `https://github.com/nationalautospares898-maker/bmbackend/actions/runs/45044925068`
- Frontend: `https://github.com/nationalautospares898-maker/bmfrontend/actions/runs/45045019928`

### The one CI failure

The `pytest` step inside **Tests (real PostgreSQL)** failed. I could not pull the raw job
log — the Actions log-download endpoint returned `403 Must have admin rights to
Repository`, and the `gh` CLI's stored token is expired in this environment (`gh auth
status` reports "The token in default is invalid") — so I cannot name the exact assertion
that failed from here.

What I can establish instead:
- Every other job in the same run passed, including `alembic check` (schema drift) and
  the full lint/type suite.
- I ran the **identical** command (`pytest` against a real local PostgreSQL, same code)
  immediately before pushing: **all tests passed, no failures**, alongside a clean
  `ruff check`, `ruff format --check`, and `mypy src/`.
- `docs/NOTIFICATION-PRODUCTION-READINESS-AUDIT.md §5` already documents a **pre-existing,
  unreproducible** flake in `tests/api/test_stock_movement_report.py`, measured at ~25%
  failure in full-suite runs and 0% in 27 targeted/isolated reruns, attributed to a
  transaction-ordering timing interaction unrelated to this change set. The audit
  explicitly classifies it **BLOCKED — NOT VERIFIED, does not block deployment**, and the
  test itself was instrumented this release specifically so its next failure prints
  diagnostic evidence instead of a bare list mismatch.

This profile — one flaky test module, everything else green, not reproducible locally —
matches that documented flake. **I am not certifying that as fact**, because I have not
read the actual failing assertion in this run. A re-run of the failed job (or `gh auth
login` followed by pulling the log) will confirm it in under ten minutes; I was not able
to do either from this session.

**This finding is carried forward, not hidden**, per the standing instruction: if this
specific failure turns out to be something other than the known flake, it must be treated
as a real regression and fixed before this release is considered fully verified.

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

**RELEASE PARTIALLY COMPLETED — ONE BLOCKER TO CONFIRM, TWO OWNER-ONLY ITEMS REMAIN.**

What is done and verified:
- Both repositories pushed to `main` with the intended, reviewed change set and nothing
  else.
- Railway backend redeployed, healthy: scheduler running, correct cadences, zero
  failures, migrations at head.
- Vercel frontend redeployed, healthy, confirmed serving the new bundle, and confirmed
  using the relative-proxy API routing the audit needed to answer its CDN question.
- No production data touched. No migration needed or run.

What is not yet closed:
1. **The backend CI test job failed on this exact commit.** Strong circumstantial
   evidence points to the already-documented, non-blocking `test_stock_movement_report.py`
   flake, but I could not read the actual log to confirm it, because of a missing
   repo-admin / expired `gh` token in this environment. **Action needed:** re-run the
   failed job (one click, since you're already on that GitHub page) or restore `gh auth`
   so the next session can pull it directly. If it passes on retry, this is closed. If it
   fails again with a *different* assertion, that is a real regression in this release and
   must be fixed before calling this done.
2. **Two checks only you can do:** confirm no stray `python -m bm.worker` Railway service
   exists (§7/O-2), and sign in once in the real browser to confirm the refresh-cookie
   warning above is or isn't a live problem.
3. Local DB tooling on this machine can't currently reach the Neon direct endpoint (DNS
   failure) — worth checking whether `.env.production`'s host is stale, independent of
   this release.

Neither of these blocks what's already live — the scheduler and migrations are
demonstrably healthy in production right now — but I'm not calling this fully verified
while a CI job on the pushed commit is red and unexplained.
