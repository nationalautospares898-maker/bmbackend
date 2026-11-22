# CDN Request Spike — Final Remediation and Production Verification

**Date:** 2026-11-22. Supersedes the open questions in
`CDN-REQUEST-GENERATOR-INVESTIGATION-2026-11-22.md`. Production database untouched
(still `0029 (head)`); no credential, role, environment variable or business row changed.

---

## 1. Phase 1 — re-verification, including a correction to my own earlier claim

Every load-bearing conclusion was re-checked against the actual source and the live
bundle rather than carried over.

| Re-checked | Result |
|---|---|
| Deployed bundle contained the `45570db` fix | **Confirmed** by extracting the minified region from the live bundle: ref indirection, **empty** dependency array, visibility gate, `Pw = 3e4` (30 s) — all present. |
| Every call site that can request `/api/v1/notifications` | **Exactly one** recurring caller (`NotificationBell.tsx`). The other three `notificationsApi` uses are click-driven mutations (acknowledge / snooze / dismiss). `AutoAlertBanner` issues no request of its own. |
| Which listeners could amplify a visibility storm | Three modules listen for `visibilitychange`: `renewal.ts` (acts only near token expiry), `scheduler.ts` (idle ticks never touch the network), and the bell (**fetches every time**). So in a storm only the bell generates traffic — consistent with the dashboard showing ~1 request on every other route. |
| Multi-tab multiplication | **My earlier framing was too pessimistic.** Polling is already gated on `document.visibilityState === 'visible'`, and only a tab actually in front of the cashier is visible, so background tabs cost nothing. Measured: 20 concurrent instances → 1.0 req/s, i.e. ~146 *visible* tabs would be needed to reach the observed rate. Multi-tab is ruled out as the cause and does not need cross-tab leader election (§3). |
| Service-worker stale-code trap | **Confirmed from the deployed `sw.js`**: an older worker serves its own precached `index.html` → its own old bundle. A plain `F5` cannot escape it; only the `SKIP_WAITING` message (the "Reload to update" button) or every tab closing can. |

### The correction that mattered

My previous session concluded that **only** the pre-fix code could produce ~7 req/s. That
was wrong, and re-testing is what found it. The deployed `45570db` code still had **no
throttle, no single-flight, and no duplicate suppression** on its `visibilitychange`
handler, and all of its rate-limiting state lived inside the component where a remount
reset it. Measured against the then-deployed code:

| Mechanism driven at 7 Hz | `45570db` (then live) |
|---|---|
| `visibilitychange` storm | **7.05 req/s** |
| Remount storm | **7.02 req/s** |
| Overlapping slow requests | unbounded (no in-flight guard) |

So the shipped code was *itself* capable of the incident rate. That is a confirmed defect
in code that was live in production — not a hypothesis — and it is what this change fixes.

### What is still *not* proven

**Which of the two mechanisms produced the specific 2.1K-requests-in-5-minutes traffic the
owner observed** — a stale tab running the pre-fix bundle, or the then-live bundle being
driven by repeated visibility events. Both reproduce the rate; both request the
byte-identical URL (verified by diffing `api/endpoints.ts` across commits); Vercel's route
view exposes no `X-Device-Id` and no tab identity, and `touch_device()` fires only on
login/refresh, so the database cannot attribute polls to a device either. **This fix closes
the second mechanism regardless, and makes the question moot for any future tab**, but the
historical attribution remains unproven and is not claimed.

---

## 2. Phase 2 — what was implemented

**New:** `bmfrontend/src/features/notifications/notificationsPoller.ts`.
**Changed:** `NotificationBell.tsx` (polling removed; it now subscribes and renders).

The three mechanisms are one omission: no single-flight and no minimum interval anywhere
that outlives a component instance. Both guards now live at **module scope**, where nothing
a component does can reset them:

- **Single-flight** — at most one `GET /notifications` in flight; a refresh requested while
  one is running joins it instead of starting a second.
- **A floor between request starts** — `MIN_FETCH_INTERVAL_MS = 5_000`. Two fetches can
  never start closer together than this, whatever asks. The scheduled 30-second cadence is
  far above the floor and is therefore completely unaffected.
- Visibility gate, interval ownership, listener attach/detach and subscriber bookkeeping all
  moved into the same module, so the timer is started by the first subscriber and stopped by
  the last, and a response arriving after the last subscriber has gone is dropped rather
  than published (no device popup for an unmounted bell).

### Cross-tab leader election: considered and deliberately rejected

`scheduler.ts` establishes Web Locks as this application's cross-tab primitive, so it was
the obvious next step. It is the wrong one here: polling is already visibility-gated, so
background tabs cost nothing, and electing a single polling owner would let a **hidden** tab
hold the lock while the visible one showed nothing — trading a saving the visibility gate has
already banked for a bell that silently stops updating. The reasoning is recorded in the
module's own docstring, and the multi-tab case is measured in tests rather than assumed.

### Explicitly preserved

Device notifications (including the "don't fire for pre-existing alerts on first load"
rule), optimistic removal on dismiss/acknowledge/snooze, error handling, `onItemsChanged`
mirroring to `AutoAlertBanner`, the 30-second cadence, immediate refresh on becoming
visible, and the service-worker update protections of `ADR-031`. No billing, printing,
credit, stock, offline-sync or authentication file was touched.

---

## 3. Phase 3 — measured results

### The three-way comparison, in one run, using the real historical sources from git

| Implementation | Render-feedback loop | `visibilitychange` storm (7 Hz, 60 s) |
|---|---|---|
| v1 original (`735ffe7`) | **7.00 req/s** | **7.03 req/s** |
| v2 previous (`45570db`, was live) | 0.50 req/s — 1 request ✓ | **7.05 req/s** ✗ |
| **v3 final (this change)** | 0.50 req/s — 1 request ✓ | **0.22 req/s** ✓ |

v2 fixed the render loop and left the visibility storm open. v3 closes both — a **32×**
reduction in the worst case, with the normal cadence unchanged.

### The permanent test suite (`notificationsPoller.test.ts`, 15 tests)

Covers all twelve requested scenarios: one normal tab (1 request, then one per 30 s);
repeated and rapid visibility changes (bounded — 13 requests from 420 events, ceiling
asserted from the floor); 20 concurrent subscribers sharing one poller; a 420-iteration
remount storm (12 requests); overlapping slow requests (exactly one in flight, verified
against a 10-second response); failed requests (no early retry, last payload kept); network
recovery (next tick publishes); hidden→visible transitions (zero while hidden, immediate on
return); a storm of *failing* requests (still bounded — no retry loop); timer and listener
cleanup on unsubscribe; and an orphaned response being dropped. Service-worker update
behaviour is covered by `swUpdate.test.ts`; authentication refresh is `client.ts`'s concern
and is unaffected (the poller never triggers a refresh).

### Full gate results

| Gate | Result |
|---|---|
| Frontend suite | **814 / 814 passed** (40 files) — was 799; +15 poller tests |
| Existing `NotificationBell` tests | **20 / 20 passed, unchanged.** Only a `beforeEach` poller reset was added — the established pattern already used by `resetUpdateState` and `resetAuthState` for module-level state. No assertion weakened, skipped or disabled. |
| Backend suite | **1,073 / 1,073 passed** (exit 0) against a disposable local Postgres |
| `prettier --check` | clean |
| `eslint .` | clean (0 errors, 0 warnings) |
| `tsc -b --force` | clean |
| `npm run build` | clean |
| `npm audit --omit=dev` | **0 vulnerabilities** |

---

## 4. Phase 4 — deployment

- **Commit:** `ad9e6ec` on `main` (`bmfrontend`), 4 files, all under
  `src/features/notifications/`. Complete diff reviewed before pushing; no unrelated
  business logic present.
- **GitHub CI: all 7 required checks green** on `ad9e6ec` —
  `Lint, types, tests and build` ✓ (the check that was failing on `b9faf37`),
  `Full stack against a real backend` ✓, `Accessibility in a real browser` ✓,
  `End-to-end, offline and print` ✓, `Generated API types match the backend contract` ✓,
  `Dependency audit` ✓, `Secret scan` ✓. The three real-browser E2E jobs passing is the
  independent check that notification behaviour, billing and printing still work against a
  real backend — not just the unit suite.
- **Vercel:** deployment **success** for `ad9e6ec` (GitHub's record of Vercel's own status).
- **Railway:** untouched by this change and healthy — `/healthz` `{"status":"ok"}`,
  `/readyz` `"status":"ready"`.
- **Database:** `/readyz` reports `expected_head 0029`, `current_revision 0029`,
  `status: ok`. No migration was run and no row was written in this phase.

---

## 5. Phase 5 — production verification

**The new poller is confirmed live**, by fingerprinting the actual deployed bytes rather
than trusting a label. The live bundle is `assets/index-DiBCf9UF.js` (492,541 bytes), and
its module scope contains exactly the poller's shape:

```js
const lx = 3e4,          // POLL_INTERVAL_MS = 30_000
      ix = 5e3,          // MIN_FETCH_INTERVAL_MS = 5_000  ← the floor, now live
      to = new Set;      // listeners
let Xs = null,           // inFlight          ← single-flight, now live
    Qp = Number.NEGATIVE_INFINITY,  // lastStartedAt
    Js = null,           // timer
    Xc = !1;             // listenerAttached
```

`ix = 5e3` and the `inFlight` slot exist **only** in this change, so their presence proves
`ad9e6ec`'s code is being served. (The hash differs from the local build's
`index-BsxwKiMU.js` because Vercel inlines different `VITE_`-prefixed values — the same
21-byte-class difference documented earlier in this engagement, not different logic.)
`visibilitychange` still occurs 6 times, as expected.

### What remains unverified, and the exact remaining operator action

**Live Vercel request-rate telemetry is not reachable from this environment** — no Vercel
API token, CLI or dashboard access exists here, as disclosed throughout this engagement. I
therefore **cannot** present a before/after production request-rate measurement, and will
not substitute the synthetic 7.05 → 0.22 req/s figures for one. The before/after comparison
must be read from the dashboard by the owner.

**The one action only a human at the shop can perform** — and it has **not** happened:

> On the counter PC, with no bill in progress, either click **"Reload to update"** on the
> banner the app shows, or **fully quit the browser** (every window, not just the tab) and
> reopen it.

A plain `F5` is **not** sufficient — §1's service-worker finding is why. Until that is done,
any tab opened before this deployment keeps running its old JavaScript, and if such a tab is
the current traffic source the dashboard will not change. Newly opened tabs get the fixed
code immediately.

### What to look for afterwards

In Vercel → the `/api/v1/notifications` route: the rate should fall to roughly **2 requests
per minute per visible tab** (one per 30 s), from the observed ~400/min. Any `/assets/*`
request whose hash is **not** in the current manifest (`index-DiBCf9UF.js`,
`index-X8JUVui_.css`, `workbox-window.prod.es5-DtTICh4t.js`, and the current
`SyncPanel-*`/`StockScreen-*`/`OrdersScreen-*`/`DailyBills-*`/`CustomersScreen-*`/
`CreditInvoiceDetail-*`/`CanceledBills-*` chunks) is proof a client is still running an old
deployment.

---

## 6. Honest status

- **Confirmed defect in then-live code, fixed, deployed, and verified live:** the unthrottled
  visibility trigger, the component-scoped (resettable) rate state, and the missing
  single-flight guard. Measured 7.05 → 0.22 req/s worst case.
- **Not claimed:** that the specific historical traffic has stopped. That needs the operator
  action above plus a dashboard reading, neither of which this environment can perform or
  fabricate.
- **Unresolved, pre-existing, unrelated:** `/readyz`'s `refresh_cookie`
  `cross_site_cookie_blocked` warning (`BM_REFRESH_COOKIE_SAMESITE`, see
  `PRODUCTION-DEPLOYMENT-GUIDE §10.4`) — present before this work, untouched by it.
