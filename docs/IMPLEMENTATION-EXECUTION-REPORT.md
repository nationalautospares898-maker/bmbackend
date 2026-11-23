# Implementation Execution Report — Direct Railway API Routing

**Date:** 2026-11-23
**Scope:** Move bearer-authenticated API traffic off the Vercel CDN by routing it
directly to Railway, while keeping the three cookie-dependent auth routes same-origin.

---

## 1. Commits

| Repo | Commit | Branch | Contents |
|---|---|---|---|
| `bmfrontend` | `5c7255a40c19033e372246693f6509d01fc46f0b` | `main` | Routing policy in `client.ts` + 7 tests |
| `bmfrontend` | **`c302296`** | `main` | **Fix for a defect in `5c7255a` that CI caught — see §9** |
| `bmbackend` | `71cd52a13438d8f507b695968a62c0d7c55763db` | `main` | 3 CORS tests. **No application code changed.** |

Previous tips: `a2bd0ac` (frontend), `ceffeac` (backend). No force-push, no history
rewritten, no secrets committed (diffs scanned).

### CI

| Run | Result |
|---|---|
| `bmbackend` `71cd52a` (`45082323287`) | **Success.** The red X on "Performance (NFR-PERF)" is its `NFR-PERF latency measurements (recorded, not gating)` step, which carries `continue-on-error: true` by design — the workflow documents that absolute latency on a shared 2-core runner is recorded, not a release gate. The *blocking* step in that job ("Search equivalence and query plan") passed. |
| `bmfrontend` `5c7255a` (`45082341297`) | **Failed** — "Full stack against a real backend", 7/7 `counter-assignment` specs. A genuine defect, diagnosed and fixed in `c302296`. See §9. |
| `bmfrontend` `c302296` | See §9 |

---

## 2. The architecture, as deployed

```
Browser ──▶ VERCEL ──rewrite──▶ RAILWAY     POST /api/v1/auth/{login,refresh,logout}
                                            (bm_refresh cookie, SameSite=Lax intact)

Browser ─────────────────────▶ RAILWAY     everything else
                                            Authorization: Bearer, credentials:'omit'
                                            ★ notifications + reminders
```

The split follows the credential, and it matches how the application already worked:
`Cookie(` appears in the entire backend exactly twice (`auth.py:237` refresh, `:257`
logout), `deps.py:121–153` resolves every other request from the `Authorization` header
alone, and `REFRESH_COOKIE_PATH = "/api/v1/auth"` means the browser would not send
`bm_refresh` elsewhere *even same-origin*. Moving bearer endpoints is an authentication
no-op.

---

## 3. The CORS / `cross_site_cookie_blocked` question — resolved, no conflict

The owner observed `BM_CORS_ALLOWED_ORIGINS` correctly set *and* `/readyz` reporting
`cross_site_cookie_blocked`. These measure different things:

* **`BM_CORS_ALLOWED_ORIGINS`** governs whether cross-origin **bearer** requests are
  permitted.
* **`cross_site_cookie_blocked`** (`config.py::cross_site_refresh_cookie_origins`) is a
  **refresh-cookie** diagnostic. It flags "an allow-listed origin is cross-site to the
  API host while `SameSite=lax`" — and it has no knowledge of *how* the frontend reaches
  `/auth/refresh`.

Under this architecture the frontend reaches refresh/login/logout **same-origin through
the Vercel rewrite**, so the cookie is sent normally. The diagnostic is therefore a
**false positive for this topology**, firing on the narrow pattern (production + `lax` +
allow-listed provider-suffix origin). `lax` is correct and was left untouched; no
configuration was changed to silence a cosmetic warning.

### Proven live (read-only, production)

```
OPTIONS /api/v1/notifications        Origin: https://bmfrontend-seven.vercel.app
→ HTTP/1.1 200
  access-control-allow-origin:  https://bmfrontend-seven.vercel.app
  access-control-allow-methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
  access-control-allow-headers: Authorization, Content-Type, Idempotency-Key,
                                If-Match, X-Counter-Id, X-Device-Id, X-Request-Id
  access-control-max-age: 600

OPTIONS same, Origin: https://attacker.example.com
→ no access-control-allow-origin   (correctly refused)

GET /readyz                          Origin: https://bmfrontend-seven.vercel.app
→ access-control-expose-headers: X-Request-Id, Idempotent-Replay, X-Api-Version

GET /api/v1/notifications?limit=50   Origin: https://bmfrontend-seven.vercel.app
→ HTTP/1.1 401   access-control-allow-origin: https://bmfrontend-seven.vercel.app
                 Cache-Control: no-store
```

The 401 is the correct answer for an unauthenticated call; the presence of
`access-control-allow-origin` alongside it proves the browser is permitted to make and
read the request. **Direct bearer-authenticated browser requests are permitted.**

`Idempotent-Replay` being exposed is load-bearing and easy to miss: `client.ts` reads it
to distinguish a replayed finalisation from a new sale. Cross-origin it would read
`null` without this header, every replay would look like a fresh sale, and the
duplicate-bill protection would be defeated while `curl` and all server-side tests kept
passing. It is now pinned by a test.

---

## 4. Deployment verification

### Vercel — confirmed live

| Evidence | Result |
|---|---|
| Bundle before | `index-rNu2eFgC.js`, CSP `connect-src 'self' http://localhost:8000` |
| Bundle after | **`index-BI9la4Qm.js`**, CSP `connect-src 'self' http://localhost:8000 https://bmbackend-production.up.railway.app` |
| Variable picked up at build | ✅ proven by the CSP change — `vite.config.ts`'s plugin derives `connect-src` from `VITE_API_BASE_URL` |
| Deterministic build check | The deployed hash **`index-BI9la4Qm.js` exactly matches** the local production build made with the same variable — independent confirmation Vercel built with the intended value |

Deployed-bundle contents (fetched from the live origin):

```
bmbackend-production.up.railway.app/api/v1   ×1   ← direct base baked in
"/auth/login","/auth/refresh","/auth/logout"      ← exact cookie-route set
"omit" / "include"                                ← both credential modes
bm-client-stale                                   ← stale-client guard preserved
```

### Railway — healthy, unchanged

`/readyz`: `status: ready`, migrations `0029` = head, scheduler running, holds the
enqueue lock, `consecutive_failures: 0`, `last_error: null`. **No backend application
code was deployed** (the commit is tests only), so no behavioural change was expected or
observed.

**No database migration was required or performed.** This change touches HTTP routing
only. Production remains at `0029`.

---

## 5. Real-browser production evidence

Live Chromium against `https://bmfrontend-seven.vercel.app`, read-only — page load only,
no sign-in, no mutation, no business data touched.

| Request | Destination | Expected | Result |
|---|---|---|---|
| `POST /api/v1/auth/refresh` | **VERCEL** | Vercel (cookie route) | ✅ |
| `POST /api/v1/auth/unattended` | **RAILWAY** | Railway (bearer-only) | ✅ |
| Notification requests to **Vercel** | **0** | 0 | ✅ |

Console: one pre-existing benign CSP `frame-ancestors` meta warning; 401 on refresh
(not signed in) and 403 on `unattended` (the `BM_UNATTENDED_ACCESS=false` control working
as intended). No CORS errors, no CSP violations.

> **A measurement correction, recorded rather than hidden.** The capture script's summary
> line read "auth routes leaking to RAILWAY: 2", which is a bug in *the script*, not the
> architecture: it matched any path containing `/auth/`, and `/auth/unattended` is
> deliberately direct because it is bearer-only. The three cookie routes
> (login/refresh/logout) all went to Vercel, which is the property that matters.

---

## 6. Test results

| Suite | Result |
|---|---|
| Frontend `vitest run` | **878 passed** (43 files; was 871, +7 routing tests) |
| `tsc -b --force` · `eslint .` · `prettier --check .` | clean |
| Production build with the variable set | ✅ builds; base baked in; CSP auto-extended |
| Backend full suite (real PostgreSQL) | **clean** — see §8 on the first run |
| `ruff check` · `ruff format --check` · `mypy src/` | clean, 132 source files |
| Backend `tests/api/test_operational.py` | **19 passed** (incl. 3 new CORS tests) |

Static audit: `fetch`/XHR/axios/WebSocket appear **nowhere** outside `client.ts`; no
runtime hardcoded `/api/v1` remains (only generated type definitions); the service
worker references **no** `/api` route (`runtimeCaching: []`,
`navigateFallbackDenylist: [/^\/api\//]`).

---

## 7. What is proven, and what is not

**Proven:**
* Direct bearer requests are permitted by production CORS (live preflight + 401 with
  allow-origin).
* Hostile origins are refused.
* The deployed bundle routes notifications/reminders to Railway and the three cookie
  routes to Vercel.
* On a real production page load, the refresh route went to Vercel and a bearer route
  went to Railway.
* Zero notification requests reached Vercel in the observed session.

**Not proven, and not claimed:**
* **An authenticated production notification request has not been observed.** The bell
  only mounts after sign-in, and I have no shop credentials and will not guess or create
  any. The routing decision is proven from the deployed bundle, CORS, and direct
  reachability — but the final wire observation of an authenticated
  `GET /api/v1/notifications` going to Railway requires one signed-in session.
  **This is the single remaining acceptance item, and it takes about two minutes:** sign
  in, open DevTools → Network, filter `notifications`, confirm the Domain column reads
  `bmbackend-production.up.railway.app` and never the Vercel host.
* Billing, stock, credit-bill and offline workflows were **not** exercised in production,
  for the same reason — they require authentication and would create real business
  records. They are covered by the local suites and unchanged by this routing change.

---

## 8. Honest notes

* **One backend full-suite run failed** on `test_movements_read_oldest_first_with_a_running_balance`.
  It passed 12/12 in isolation and the next full run was clean. The backend commit is
  **tests only** — no source changed — so it cannot be causal. Same family as the
  documented `test_stock_movement_report.py` full-suite-only ordering flake
  (`NOTIFICATION-PRODUCTION-READINESS-AUDIT.md §5`).
* **Backend rate limiting remains unimplemented** (mandate item 11 of the stale-client
  requirement). `RAILWAY-ENVIRONMENT-VARIABLES.md §4` records that no HTTP rate limiter
  exists. Adding one means new middleware on every request of a live billing system and
  needs its own design and testing; shipping it alongside a routing change would risk the
  billing disruption the mandate forbids. **Recommended as the next separate change.**
* **Stale-client detection latency is up to ~30 minutes**, bounded by
  `UPDATE_CHECK_INTERVAL_MS` in `swUpdate.ts`. Clients detect independently per browser
  and per computer; `BroadcastChannel` only accelerates it within one profile.
  Disconnected clients are not stopped instantly and this report does not claim they are.

---

## 9. Rollback

**Delete `VITE_API_BASE_URL` in Vercel → redeploy the frontend.**

`DIRECT_BASE` collapses to the relative base, the `/api/:path*` rewrite is still present
(never removed), and the refresh cookie was never altered — so **nobody is signed out, no
session is lost, and no backend change is needed.** One deploy cycle, no data risk.

Backend rollback is a redeploy of the previous Railway build; the commit is tests only,
so there is nothing behavioural to undo. No migration to reverse.

---

## 9. The defect CI caught in `5c7255a`, and its fix

Recorded in full because it is the most instructive part of this release: the first
commit shipped a real bug, CI caught it, and the production arrangement was never
affected.

### What broke

All seven `tests/e2e/counter-assignment.spec.ts` specs failed in the "Full stack against
a real backend" job, each timing out on `getByLabel('Item, row 1')` — the billing grid
never appeared, because sign-in never completed.

### Root cause

The routing policy sends the three cookie-bearing auth routes to a **relative** base, so
that in production they stay same-origin and the `SameSite=Lax` refresh cookie is
delivered through `vercel.json`'s rewrite. **That default silently assumes a proxy
exists.**

It does in production, and under `BM_DEV_API_PROXY`. It does **not** in the full-stack
acceptance topology: CI sets `BM_E2E_API_URL: http://localhost:8000`, and
`playwright.config.ts` serves the built app from `vite preview` on its own port with
nothing in between. The relative `/api/v1/auth/login` therefore resolved to the *preview
server*, returned 404, and every spec failed at sign-in.

The design was right; the implementation over-generalised "relative" into "always
correct".

### Fix (`c302296`)

`VITE_AUTH_BASE_URL` makes the auth base explicit and overridable, defaulting to the
relative path. **Production leaves it unset**, so the production arrangement is
byte-for-byte unchanged and the cookie keeps its same-origin guarantee.
`playwright.config.ts` sets it alongside `VITE_API_BASE_URL` so the suite's auth reaches
the same backend its bearer calls do. Added to `vite-env.d.ts` and to the structural
allow-list (`rules.test.ts`), which exists to stop an un-reviewed `VITE_` variable
appearing in a public bundle — the new one is a public hostname, never a secret.

### Verified by reproducing the CI topology locally

Absolute API URL, no proxy, built app on a preview port:

| Spec | Result |
|---|---|
| `counter-assignment.spec.ts` | **7 passed** (was 7 failed) |
| `full-stack.spec.ts` + `back-office-screens.spec.ts` | **15 passed, 2 skipped** |

The full-stack run covers the business-integrity properties directly: a cash bill
finalises and **deducts stock exactly once**, a credit bill finalises and recording a
payment **does not deduct stock again**, a server-saved draft **survives a reload**, and
finalisation is **refused offline with nothing created**.

### One trap worth recording

While reproducing this I first pointed the suite at `127.0.0.1:8013` while the page was
on `localhost:4273`, and the reload spec failed. That is not a code fault:
**`localhost` and `127.0.0.1` are different hosts for `SameSite` purposes, while ports
are not.** Mixing them drops the refresh cookie on reload. CI uses `localhost` for both.
The distinction is the same one that governs the whole architecture, in miniature.
