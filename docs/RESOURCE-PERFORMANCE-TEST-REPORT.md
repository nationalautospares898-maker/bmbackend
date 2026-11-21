# Resource Performance Test Report (this pass)

**Date:** 2026-11-21. Since no new code change was made this pass (see
`RESOURCE-OPTIMIZATION-IMPLEMENTATION-REPORT.md`), this report's job is to **confirm the prior
pass's results still hold** — nothing silently regressed between the two passes — plus the new,
live-production evidence gathered this session.

## Commands actually run this session, and their actual results

| Command | Result |
|---|---|
| `git status --porcelain` (both repos) | Identical to the state at the end of the prior pass — no drift. |
| `npx vitest run` (`bmfrontend`) | **39 test files, 795 tests — all passed.** Re-run fresh this session (not merely cited from before). |
| `curl -D - https://bmfrontend-seven.vercel.app/assets/index-DJl4wgXl.js` (×2) | `X-Vercel-Cache: HIT` both times, `Age` increasing between calls — confirms edge caching is live and working for the hashed bundle. |
| `curl -D - https://bmfrontend-seven.vercel.app/` | `X-Vercel-Cache: HIT`. |
| `curl -D - https://bmfrontend-seven.vercel.app/sw.js` | `X-Vercel-Cache: HIT`. |
| `curl -D - https://bmfrontend-seven.vercel.app/api/v1/notifications?limit=50` | `401 Unauthorized`, `Cache-Control: no-store`, `X-Vercel-Cache: MISS` — confirms the API proxy path is never cached, by design, consistently with the backend's own header policy. |
| `gh api repos/.../commits/45570db.../status` | `state: success`, `"Deployment has completed"`, `2026-11-21T13:30:13Z` — confirms the fix is live (re-check of a status that was `failure` at the time of the original push). |

**Backend suite (`ruff`, `mypy`, `lint-imports`, `pytest`, migration round-trip, `alembic check`):
not re-run this session.** The backend repository's `git status` is byte-identical to its state at
the end of the prior pass (confirmed above), which already ran and recorded all of these commands
with their actual results (1,071 tests collected, 0 failures; see the prior pass's
`REGRESSION-AND-PERFORMANCE-TEST-REPORT.md` for the exact transcript). Re-running an unchanged
codebase against an unchanged local database would reproduce the same result at the cost of several
minutes without checking anything new; the `git status` comparison is the evidence that nothing
invalidates the prior run.

## Before/after measurements

**Database (Finding F1, carried forward, not re-measured this session):** the prior pass's
`EXPLAIN (ANALYZE, BUFFERS)` comparison — `Seq Scan`/9.253ms/1,312 buffers without the index vs.
`Bitmap Heap Scan`/1.387ms/192 buffers with it, on an 80,000-row synthetic dataset with a realistic
0.3%-active distribution — stands unchanged. No new measurement was needed because no new database
change was made.

**Frontend bundle size:** unchanged from the prior pass's measurement (`492.03 kB` main bundle,
byte-identical before/after the dependency cleanup) — not re-measured since no dependency or build
configuration changed in this pass.

**CDN cache behaviour:** newly measured this session (not available in the prior pass, which had
no live-production access confirmed working at the time) — see the curl results above. This is the
first actual before/after-style evidence in this engagement of Vercel's edge genuinely absorbing
static-asset traffic (`HIT`) while API traffic structurally cannot be absorbed (`MISS`, always).

## Limitations

- No Vercel/Railway/Neon dashboard or API telemetry exists in this environment — see
  `PRODUCTION-RESOURCE-CONSUMPTION-BASELINE.md` for the complete account. Nothing in this report
  substitutes an estimate for a measurement without saying so explicitly.
- No browser-automation tool exists in this environment, so Finding F12 (cross-tab duplicate
  startup fetches) could not be reproduced in an actual multi-tab browser session — it is derived
  from reading the trigger code, not observed traffic.
- The backend's `-m slow` 100,000-product load-test suite was not re-run this session (nor the
  prior one) — no finding in either pass touches the query paths it covers, and seeding it costs
  several minutes for no new information.
