# Production Verification — Post-Push (This Session)

**Date:** 2026-11-22. Covers the two pushes made this session: `bmbackend` `55ea7f0..275571e`,
`bmfrontend` `45570db..9142e30`. All checks below are read-only (GET requests, GitHub's own
commit-status API). No credential, environment variable, or business data was touched.

## 1. GitHub-reported deployment status, per commit

```
gh api repos/nationalautospares898-maker/bmbackend/commits/275571e/status
  → state: success
    context: independent-integrity - bmbackend
    description: "Success - bmbackend-production.up.railway.app"

gh api repos/nationalautospares898-maker/bmfrontend/commits/9142e30/status
  → state: success
    context: Vercel
    description: "Deployment has completed"
```

Both platforms' own webhook-reported commit status is `success` for the exact commits pushed
this session — this is GitHub's record of each platform's own status, not an inference.

## 2. Backend — live, read-only checks against `https://bmbackend-production.up.railway.app`

| Check | Result | Reading |
|---|---|---|
| `GET /healthz` | `200 {"status":"ok"}` | Liveness probe (the one `Dockerfile`'s own `HEALTHCHECK` and Railway's container health check use) is healthy. Traffic is being served. |
| `GET /readyz` | `200`, `database: ok`, `migrations: {"expected_head":"0029","current_revision":"0028","status":"mismatch"}` | **Expected, not a defect.** The deployed code's migration folder now includes `0029`, but — exactly as instructed — the migration was not applied, so production's Alembic revision is still `0028`. `/readyz` correctly reports the mismatch; `/healthz` (what actually gates traffic) is unaffected, confirmed above. This will read `ok` again once, and only once, migration `0029` is explicitly applied. |
| `GET /readyz` → `refresh_cookie.status` | `cross_site_cookie_blocked` | **Pre-existing, unrelated to this session's push** — the same `BM_REFRESH_COOKIE_SAMESITE` gap already named in `PRODUCTION-DEPLOYMENT-GUIDE.md §10.4`, present before this session's changes and not touched by them. |
| `GET /readyz` → `alerting.state` | `disabled` | Pre-existing (OD-19 is still open, per `channel.py`'s own module docstring) — not a consequence of F2's fix, which only changed *when* a lock is held during delivery, not whether alerting is configured. |
| `GET /api/v1/notifications?limit=50` (through the Vercel rewrite) | `401 Unauthorized` | Unchanged from before this push — the endpoint is live, correctly routed, and rejects an unauthenticated request exactly as it did before. Proves routing end-to-end without needing a valid session. |

## 3. Frontend — live, read-only checks against `https://bmfrontend-seven.vercel.app`

| Check | Result | Reading |
|---|---|---|
| `GET /` headers | `Age: 63`, `Last-Modified` within the last two minutes of the push | A genuinely fresh deployment, not a cached response from before this session's push. |
| Served bundle | `assets/index-DJl4wgXl.js`, `Content-Length: 492006` | **Identical hash and byte count to the bundle already fingerprinted in `PRODUCTION-POLLING-VERIFICATION-2026-11-21.md`.** This is the expected outcome, not a verification failure: the only frontend change this push contained was removing five dependencies that no source file ever imported (confirmed by grep before committing), so Vite's content-addressed build hash correctly did not change. A different hash here would have meant something unexpected happened to the bundle; the same hash is the proof that nothing did. |

## 4. What this does and does not prove

**Proves:** both platforms built and deployed the exact commits pushed this session; the
backend is live and healthy; the frontend is serving a freshly-built, byte-verified bundle;
the one intentionally-unapplied piece of this session's backend change (migration `0029`) is
correctly reflected as not-yet-applied in the live readiness check, with no impact on actual
traffic.

**Does not prove (same limitation as every prior pass in this engagement, unchanged):** actual
request volume, cadence, or any live Vercel/Railway/Neon dashboard metric — no API token or
CLI credential for any of the three platforms exists in this environment. Nor does it include
an authenticated, in-browser functional check of billing, printing, or credit workflows — that
requires a browser-automation tool this environment does not have, same as
`PRODUCTION-POLLING-VERIFICATION-2026-11-21.md`'s own disclosed limitation. Nothing in this
session's changes (F2, F4, the already-approved index/dependency cleanup) touches any
billing-, stock-, or payment-related code path, which bounds the risk of that gap, but does
not close it.
