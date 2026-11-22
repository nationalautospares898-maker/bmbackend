# Which client is actually generating `/api/v1/notifications` traffic?

**Date:** 2026-11-22. **No production code was changed. Nothing was pushed or deployed. No
database write of any kind** — the only database access was a read-only, rolled-back
`SELECT` session (no `token_hash`, no business data, no full device ids). Both repository
working trees were clean before and after; this document is the only new file and is
uncommitted.

---

## 1. What is now PROVEN, from the production bytes themselves

### 1.1 The deployed bundle contains the FIXED implementation — not inferred, extracted

`https://bmfrontend-seven.vercel.app/` serves `/assets/index-CAY6ZSC1.js` (492,071 bytes).
The sourcemap is `403`, so the minified region was extracted directly. Verbatim from the
live production bundle, with the original names annotated:

```js
ce = K.useRef(i); ce.current = i;              // onNavigateRef = useRef(onNavigate)
const oe = K.useCallback(async () => {
    const we = await zc.list();                // notificationsApi.list()
    v(we.notifications); D(null);              // setItems / setError
    ... ce.current?.(Ne, ze.entity_id ?? void 0)   // reads onNavigate THROUGH the ref
}, []);                                        // <-- EMPTY DEPENDENCY ARRAY
K.useEffect(() => {
    const we = () => { document.visibilityState === "visible" && oe() };
    we();
    const ze = window.setInterval(we, Pw);     // Pw === 3e4 === 30_000ms (verified)
    document.addEventListener("visibilitychange", we);
    return () => { window.clearInterval(ze);
                   document.removeEventListener("visibilitychange", we) };
}, [oe]);
```

Every property of the fix is present in production: the ref indirection, the **empty**
dependency array, the visibility gate, the 30-second constant (`Pw = 3e4`, confirmed by
locating its declaration), and symmetric cleanup. `visibilitychange` occurs **6** times in
this bundle — the fixed signature (pre-fix was 4).

**Therefore: whatever is generating 7.3 req/s is not a client running the current bundle
behaving normally.** A client running this code produces 1 request on mount and 1 per 30s
(measured below: 0.05 req/s).

### 1.2 The service worker is a genuine stale-code trap — confirmed from the deployed `sw.js`

```js
e.precacheAndRoute([{url:"index.html",revision:"1e23b1cc…"}, {url:"assets/index-CAY6ZSC1.js"}, …]);
e.registerRoute(new e.NavigationRoute(e.createHandlerBoundToURL("/index.html"),
                                      {denylist:[/^\/api\//]}));
self.addEventListener("message", e => { e.data?.type === "SKIP_WAITING" && self.skipWaiting() });
```

An **older** service worker, still active in an open tab, serves **its own** precached
`index.html` for every navigation — which references **its own** old bundle hash, also in
its own precache. So a plain `F5` on such a tab is served entirely from the old cache and
never reaches Vercel for its JavaScript. The only escapes are the `SKIP_WAITING` message
(what the "Reload to update" banner sends) or every tab on the origin closing. This is
mechanism, not speculation — it is the deployed code.

Corroborating detail: `/assets/index-DJl4wgXl.js` (the previous bundle) now returns **1,345
bytes of HTML, not JavaScript** — `vercel.json`'s `/(.*) → /index.html` rewrite answers
missing assets with `200` + HTML. Old asset URLs are no longer served as code.

---

## 2. Measured: what can and cannot reach 7.3 req/s

Executed against the **current** component (not the old one), fake timers, 140 ms mocked
round trip, in a harness that mirrors `AppShell` exactly (inline non-memoised `onNavigate`,
stable `setLiveNotifications`). Temporary file, deleted afterward.

| Scenario | Measured | Verdict |
|---|---|---|
| **D.** One current-code tab, undisturbed, 60s | 3 requests = **0.05 req/s** | Correct. Cannot explain production. |
| **C.** 20 concurrent current-code instances, 60s | 60 requests = **1.00 req/s**; extrapolates to **~146 tabs** needed for 7.3 req/s | **Multi-tab RULED OUT** (see §3 for the independent confirmation) |
| **A.** Current code + `visibilitychange` dispatched 7×/s for 60s | 422 requests = **7.05 req/s** | **REPRODUCES the observed rate** |
| **B.** Current code + bell remounted 7×/s for 60s | 421 requests = **7.02 req/s** | **REPRODUCES the observed rate** |
| (prior session) Old pre-fix source, same harness | **7.0 req/s** | **REPRODUCES the observed rate** |

**This is a correction to the previous session's conclusion.** It is not true that only the
old code can produce this rate. The current code has **no throttle, dedupe, or single-flight
guard on its `visibilitychange` handler** — every such event fires an immediate request,
and overlapping requests are permitted. That is a real latent defect in shipped code,
independent of whichever mechanism is active in production right now.

---

## 3. What the production evidence rules out

From the owner's Vercel dashboard (5-minute window): `/api/v1/notifications` ≈ 2.2K
requests; **every other API route ≈ 1 request**; all `200`; gaps of 0.1–0.2s.

| Candidate | Status | Evidence |
|---|---|---|
| ~146 browser tabs | **RULED OUT** | Each tab's sign-in fetches catalog + pending orders, and each fresh load performs its own refresh rotation. 146 tabs would show ~146 requests on `/sync/catalog` and `/orders`; the dashboard shows **1 each**. Independently, the database shows nothing like that many sessions (§4). |
| Unauthenticated bot / scanner / uptime monitor | **RULED OUT** | Responses are `200`. The endpoint returns `401` without a valid access token (verified by direct request). A `200` proves a genuine authenticated session. |
| Auth-refresh loop | **RULED OUT** | A refresh loop produces `401`/`/auth/refresh` volume. `/auth/refresh` ≈ 1 in the window, consistent with the normal ~13-minute proactive renewal. |
| `AppShell` remount loop | **RULED OUT** | An `AppShell` remount refetches catalog and orders. Those routes show ≈ 1 request. |
| Backend feedback loop (the endpoint creating its own work) | **RULED OUT** | `notifications.active_for()` is a pure `SELECT`; a `GET` writes no notification and no outbox row. The table holds **3** rows total (1 `active`), which also explains the ~251-byte average response. |
| Service worker caching `/api/` | **RULED OUT** | `runtimeCaching: []`, and the navigation route carries `denylist:[/^\/api\//]` — confirmed in the deployed `sw.js` above. |
| Duplicate call sites | **RULED OUT** | `notificationsApi.list` has exactly one caller in the whole frontend (`NotificationBell.tsx`); `AutoAlertBanner` makes no request of its own. |

**Two candidates survive, and both reproduce the rate:**
- **(i)** a tab running the **old pre-fix bundle**, held in place by the service-worker trap of §1.2;
- **(ii)** a tab running the **current bundle** being driven by a `visibilitychange` storm (§2A).

---

## 4. Database session forensics (read-only) — what it did and did not settle

`devices` carries `label`, `user_agent`, `last_seen_at`; `refresh_tokens` carries
`device_id`, `issued_at`, `consumed_at`. Findings:

- **Two devices are currently active**, both labelled "Counter PC": one with 82 lifetime
  tokens (12 issued in the last hour), one with 337 lifetime tokens since 2026-11-17 (4 in
  the last hour). Eight further devices last appeared between 2026-11-08 and 2026-11-16.
  One is a `Python-urllib/3.11` entry explicitly labelled as a prior verification artifact
  (safe to revoke; not a traffic source — 1 token, last seen 2026-11-08).
- **A refresh-rotation anomaly worth recording separately:** the most recently active device
  shows **11 token rotations within ~23 seconds** (20:59:37→21:00:00Z), and a similar burst
  around 19:31Z, against an otherwise normal ~13-minute cadence (one gap measured at 781s).
  A chain of successful rotations like that is the signature of **several app instances
  loading at once** (a fresh load has no in-memory token, so the cross-tab lock serialises
  but cannot dedupe it) — i.e. evidence that multiple tabs/windows **do** get opened
  together on that machine, even though multi-tab is far too small to explain 7.3 req/s.
- **Why this cannot name the culprit:** `touch_device()` is called **only on login and
  refresh** (`domain/auth/service.py:665`), never on a normal authenticated request. So
  `last_seen_at` tracks refreshes, not notification polls. **The database structurally
  cannot attribute the notification traffic to a device.**

---

## 5. The evidence gap, stated precisely

The two surviving candidates are **indistinguishable in every log this environment can
reach**, and I will not guess between them:

- Both request the **identical URL**. Verified by diffing `api/endpoints.ts` between the
  pre-fix commit and `HEAD`: `list(limit = 50)` → `GET /notifications?limit=50` in both.
  No query-string or header difference exists to tell them apart in Vercel's logs.
- Both produce notification-only traffic with every other route near zero.
- Both yield `200` from one authenticated session.
- Vercel's route view shows path, method, status, cache, region, and User-Agent — **not
  `X-Device-Id`**, and **not tab identity**. I am not going to infer a device from a shared
  `Mozilla/5.0 (Windows NT 10.0…)` string; four enrolled devices share that User-Agent.

### The three ways to close it — all safe, none requiring a deploy

**(a) The hidden-tab test — the decisive discriminator, 60 seconds, no code change.**
The pre-fix code has **no** visibility gate; the current code polls **only** while
`visibilityState === 'visible'`. So, on the counter PC, minimise the browser window (or
switch to a different application) for 60 seconds and watch Vercel's live log:
- **traffic continues** → the generator is running **old code** → candidate (i), and the
  fix is operator-side (§6);
- **traffic stops, then resumes on restore** → the generator is running **current code**
  driven by a visibility storm → candidate (ii), and the fix is the code change in §6.

This disturbs nothing: minimising a window does not interrupt a sale, and the app's own
design already treats hidden tabs as a normal state.

**(b) Read `X-Device-Id` off a notification request.** Expand a single
`/api/v1/notifications` entry in Vercel's log detail and look for the `X-Device-Id` request
header (the client sends it on every request, `api/client.ts`). Its first 8 characters can be
matched against the device list in §4 to name the exact machine. If Vercel's plan does not
expose request headers, this is unavailable — say so rather than guessing.

**(c) Stale-asset detection from the logs alone.** Any `/assets/*` request whose hash is not
in the **current** manifest is proof of a client still running an old deployment. The current
valid set, read from the deployed `sw.js`:
`index-CAY6ZSC1.js`, `index-X8JUVui_.css`, `workbox-window.prod.es5-DtTICh4t.js`,
`SyncPanel-C-4H29Cl.js`, `StockScreen-DhmAK6V1.js`, `OrdersScreen-CtoXhHOx.js`,
`DailyBills-BFz4fpYN.js`, `CustomersScreen-DCqveYro.js`, `CreditInvoiceDetail-B48aXKR5.js`,
`CanceledBills-DnRXD4PD.js`. Anything else under `/assets/` = a stale client.

---

## 6. Smallest safe corrective approach

**Do not deploy anything until test (a) above has selected between the two candidates.**
Writing code for the wrong one is exactly the speculative-fix cycle to avoid.

**If (a) shows traffic continues while hidden → candidate (i), stale old-code tab.**
No code fix can reach already-running JavaScript. The minimal action is operator-side and
must respect `ADR-031` (never force a reload mid-sale): with no bill in progress, either
click **"Reload to update"** on the banner the app already shows, or **fully quit the browser**
(every window, not just the tab) and reopen. A plain `F5` is **not** sufficient — §1.2 is why.

**If (a) shows traffic stops while hidden → candidate (ii), visibility storm on current code.**
The minimal, behaviour-preserving fix is to make the bell's visibility trigger idempotent
rather than to change the polling design: in `NotificationBell.tsx`, (1) ignore a
`visibilitychange`-driven refresh if one is already in flight (a single-flight ref), and
(2) ignore it if a successful poll completed within the last few seconds. Both are local to
the one effect, preserve the 30-second cadence, preserve immediate refresh-on-focus, and add
no infrastructure. Roughly ten lines, with tests mirroring §2A.

**Worth fixing regardless of (a)'s outcome** — recorded here, not implemented, because it is
real but is not established as the current production cause: the unthrottled
`visibilitychange` handler and the absence of single-flight on the poll (§2) mean the shipped
code *can* be driven far above its intended rate. The §6-(ii) change is the fix for that
latent defect whether or not it is today's culprit.

**Not recommended:** any server-side or edge rate limit as a CDN remedy. Vercel counts a
request when it reaches the edge, independent of the status code returned — so a limiter
cannot reduce the metric in question (established with sources in the previous report).

---

## 7. Verdict

**ROOT CAUSE NOT YET PROVEN — ONE 60-SECOND TEST AWAY.** Narrowed from eight candidates to
two, with six ruled out on production evidence. The deployed bundle is proven to contain the
fix; the previous session's claim that only the old code could produce this rate is **proven
wrong** — the current code can be driven to 7.05 req/s through an unthrottled
`visibilitychange` path, which is a genuine defect found this session. Which of the two
surviving mechanisms is live in production cannot be determined from any telemetry reachable
here, and the hidden-tab test in §5(a) separates them definitively in one minute without
touching code, data, or deployments.
