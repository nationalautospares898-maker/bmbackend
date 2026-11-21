# CDN Request Spike — Forensic Report

**Date:** 2026-11-21. **No code changes were made in the production of this report.** Every
timestamp below is from GitHub's own record of Vercel's deployment status for the named commits —
not from Vercel's dashboard, which I have no access to — plus direct reading of the repository at
each relevant revision.

---

## 1. Executive summary

**The November 17–19 spike and the November 21 live-log burst have the same root cause, and it is
not a new or different bug than the one already found and fixed.** The defect is the render-
triggered polling loop in `NotificationBell.tsx` that this engagement already diagnosed and fixed
(commit `45570db`). What's new in this investigation, proven from the repository's own deployment
history rather than assumed:

- The buggy code was deployed to production on **2026-11-15 at 12:15:36 UTC** (commit `05008fd`) —
  two days *before* the observed November 17–19 spike, not after it. The spike's entire window
  falls inside the period the bug was live.
- The fix did not reach production until **2026-11-21 at 13:30:13 UTC** — nearly six days later.
- **Deploying a fix does not update JavaScript already running in an open browser tab.** Any tab
  that loaded the application at any point between those two timestamps, and has not been reloaded
  or closed since, is **still executing the old, buggy code right now**, completely unaffected by
  the successful production deployment. This application's own service-worker configuration
  (`skipWaiting: false`, update applied only on an explicit user click) means that tab will keep
  running the old code indefinitely until a human reloads it or the machine restarts.
- The observed cadence (every 0.1–0.2 seconds, HTTP 200, cache MISS) matches the originally-
  diagnosed loop's predicted speed almost exactly, and is fully explained by **a single** such tab —
  no fleet of phantom sessions or second bug is required to produce the observed volume.

**What remains unproven:** which specific device/tab is generating the current traffic (I have no
way to inspect that without platform or physical access), and whether the November 17–19 figure of
~1.44M requests is *entirely* attributable to this one mechanism versus partially ordinary traffic
— repository evidence proves the buggy code's presence throughout that window, not a request-by-
request accounting of it.

---

## 2. Evidence from the task's Vercel screenshots, as given

Taken at face value (I have no independent access to Vercel's dashboard or logs to verify these
directly):

| Observation | What it rules in / out |
|---|---|
| Nov 17–19: ~1.44M requests | Matches the order of magnitude a continuously-looping tab would produce over ~2-3 days at the loop's predicted speed (see §7). |
| Nov 21 live log: `GET /api/v1/notifications` repeating | Points at the exact endpoint this engagement already traced to `NotificationBell.tsx`. |
| ~0.1–0.2s between entries | Matches the *un-fixed* loop's predicted cadence (network-round-trip-bound, not the intended 30s) almost exactly — see §7. **Rules out** the fixed code as the source: the fixed code cannot produce an interval shorter than 30s under any condition found in this investigation (§3). |
| HTTP 200 | **Proves** the requester holds a currently-valid access token. Rules out anonymous bots/scanners (which would get `401`) and rules out a failure-triggered retry loop (retries only follow a non-2xx response, and this client's retry floor is 1,000ms regardless — see §3). |
| Cache MISS | Expected and unavoidable — the backend sets `Cache-Control: no-store` on every API response; no API call can ever be a cache `HIT`. Not evidence of anything unusual on its own. |
| Path type "external" | Consistent with `vercel.json`'s `/api/:path* → Railway` rewrite rule — expected plumbing, not a finding. |
| Region `bom1` | Mumbai — consistent with routing for an India-based shop. Not a finding. |

---

## 3. Notification request call graph (current source, re-read in full this session)

**Every path that can call `GET /notifications`, traced exhaustively:**

1. `NotificationBell.tsx`'s own poll — the only recurring caller. One `useEffect`
   (`NotificationBell.tsx:162-180`), keyed on `[load]`. `load` (`:131-160`) is a `useCallback` with
   an **empty dependency array** — it reads the caller's `onNavigate` through a ref
   (`onNavigateRef`, `:128-129`), not as a dependency, specifically so it never changes identity.
   Because `load` cannot change identity, the effect's dependency array `[load]` cannot change
   either, so **the effect runs exactly once per mount**, regardless of how often `AppShell` (or
   anything else) re-renders. Inside the effect, `tick()` fires once immediately, then
   `window.setInterval(tick, 30_000)` — a genuine 30-second floor — plus a `visibilitychange`
   listener that also calls `tick()`, which itself only calls `load()` when
   `document.visibilityState === 'visible'`.
2. `AutoAlertBanner.tsx` — confirmed, again, to make **zero** API calls of its own (no `fetch`,
   no import from `@/api/`); it only renders the `items` prop `NotificationBell` passes up via
   `onItemsChanged`. One poll feeds both.
3. No other component, hook, or module in `bmfrontend/src` imports `notificationsApi.list`
   (re-confirmed by grep this session) — there is exactly one call site for the recurring poll.

**Can multiple instances/loops coexist, and are they cleaned up correctly?** In the *current*
source: no. `NotificationBell` is mounted exactly once, in `AppShell`'s header, for the life of a
signed-in session; the effect's cleanup function (`:176-179`) clears both the interval and the
listener on unmount; nothing re-mounts `AppShell` on a healthy session (the one documented
`onSessionLost` retry is explicitly limited to one attempt, guarded by a ref, specifically to
prevent a retry loop — `AppShell.tsx`'s `retriedAfterLoss`).

**Ruled out, with reasoning, per the task's explicit list:**
- *Multiple mounted notification components:* one instance exists in the current tree. Ruled out
  for the current code.
- *Duplicate effects / unstable dependencies:* this **was** the original defect (an unstable
  `onNavigate` dependency), already fixed in the committed source — `load` now has zero dependents
  that can change.
- *React Strict Mode:* double-effect-invocation is a **development-only** React behaviour; it does
  not exist in a production build. Confirmed as not applicable to deployed code.
- *Component remount loops:* none found; the one retry path is single-shot by design.
- *Automatic retries:* the client's retry logic (`api/client.ts`) only ever activates after a
  non-2xx response or a network failure, with a minimum backoff of 1,000ms — mechanically
  incapable of producing a 100–200ms cadence, and moot anyway since the observed responses are 200.
- *Authentication refresh loops:* `GET /notifications` returning 200 proves the token in use is
  valid at request time; a refresh loop would produce 401s or 403s, not a stream of 200s.
- *Multiple browser tabs (of the **current, fixed** code):* each would independently floor at
  30 seconds; even many such tabs interleaved would need an implausible number of concurrent tabs
  to approach a 0.1–0.2s aggregate cadence (see §7) — and "only two people use this application."
- *Service worker behaviour:* the service worker caches no `/api/` route and queues no requests
  (`vite.config.ts`'s `runtimeCaching: []`); it does not generate requests. It is, however, directly
  relevant to **why a fix doesn't reach an already-open tab** — see §4.
- *Recursive API interceptors:* no interceptor pattern exists in `api/client.ts` that calls itself;
  the only "shared" mechanism is the single-flight auth-refresh promise, which is not self-
  triggering.

**Conclusion: nothing in the currently-deployed source code can produce the observed cadence.** The
code that *could* — and did — produce exactly this cadence is the version that preceded `45570db`,
which is why §4 matters as much as it does.

---

## 4. Why a successful deployment does not stop an already-running tab

This is the mechanism that reconciles "the fix is confirmed deployed" with "the buggy pattern is
still observed live":

- `vite.config.ts`'s `VitePWA` config sets `registerType: 'prompt'`, `skipWaiting: false`,
  `clientsClaim: false` — **deliberately** (per the config's own comment, citing `ADR-031`): a
  build update must never force a reload mid-sale.
- `src/app/swUpdate.ts` / `main.tsx` wire this so that, even once a new service worker is detected,
  the application only sets a flag (`needsRefresh`) and shows `UpdateBanner` — a low-key, dismissible
  banner, not a modal — and **never** calls `applyUpdate()` automatically. A human must click
  "Reload to update."
- This application is a single-page app that never navigates to a new URL during normal billing
  use. The browser's/Workbox's update-check is conventionally tied to navigation events; an SPA
  tab that is opened once and left running for days may go a very long time — in the worst case,
  only as often as the browser's own background service-worker maintenance cycle checks (commonly
  on the order of once per day in Chromium) — before it even *notices* a new version exists, let
  alone before a person clicks the banner.
- **The practical consequence:** a tab that loaded `index.html` and the JavaScript bundle active
  at the time **continues executing that exact JavaScript, in memory, for as long as the tab stays
  open** — completely independent of what Vercel is currently serving to a *new* request for
  `index.html`. A server-side deployment changes what a *new* page load receives; it does nothing
  to a page load that already happened.
- This application's own documentation repeatedly describes its expected environment as "a counter
  PC that is never closed" — this is not a hypothetical edge case for this specific shop, it is the
  documented normal operating mode.

**This is not a code defect to fix reactively** — forcing an immediate reload on every deploy is
exactly the mid-sale-disruption risk `ADR-031` was written to avoid, and this investigation is not
proposing to override that design. It is the explanation for the observed discrepancy between "the
server-side fix is live" and "the buggy pattern is still being observed."

---

## 5. Root cause — proven vs. hypothesis, stated explicitly

**Proven, from repository and deployment-status evidence (not assumed):**
- The exact defect mechanism (unstable `useEffect` dependency causing an unbounded re-render-driven
  poll loop) — proven by reading the pre-fix source and the fix together.
- The buggy commit (`05008fd`) was live in production from **2026-11-15T12:15:36Z**.
- The fix (`45570db`) did not reach production until **2026-11-21T13:30:13Z**.
- The currently deployed source code (re-read in full this session) cannot reproduce the observed
  cadence under any mechanism investigated.
- A browser tab that loaded the application during the buggy window and has not been reloaded will
  continue running that exact code regardless of any later deployment — this follows from how
  browsers execute JavaScript and from this application's own, intentional service-worker update
  policy, not from guesswork.

**Strong hypothesis, not independently provable without platform/log access:**
- That the specific traffic shown in the Nov 21 live log is coming from exactly one such stale tab
  (versus, say, two). The *volume* is consistent with just one (§7); I cannot identify *which*
  device or confirm the count without server-side log inspection or physical access to the counter
  PC(s).
- That the Nov 17–19 figure of ~1.44M requests is *entirely* this mechanism and not partly ordinary
  traffic. The timeline proves the buggy code was live and capable of producing that volume
  throughout the window; it does not provide a request-by-request accounting.

**Explicitly not assumed, per this task's instruction:** that the Nov 21 fix resolved the Nov 17–19
spike's *underlying presence* on every device. The fix is correct and deployed; whether it has
actually taken effect on the specific device(s) that were open during the spike is a separate,
unverified question — see §9's recommended verification step.

---

## 6. Other endpoints capable of generating unusually high volume — re-audited this session

Re-checked against the same "could this coexist or compound" question. **No second contributor was
found.** Summary (full detail already recorded in this engagement's prior audits, re-verified here
by re-reading the current source, not re-asserted from memory alone):

| Endpoint | Re-check result |
|---|---|
| `sync.push` (outbox drain) | `scheduler.ts`'s `drainOutbox` returns before any `fetch` when the outbox is empty — confirmed again: idle ticks cost zero requests. |
| `sync.catalogDelta` | Delta-based, 30-minute ticker + once per sign-in. Bounded. |
| `auth.refresh` | Single-flight, cross-tab-locked; ~13-minute cadence. Returning 200s elsewhere in the app does not implicate this path. |
| `GET /orders?status=pending` | Once per sign-in per tab (a known, bounded, non-recurring duplication across tabs — documented in the prior pass as Finding F12 — not a loop). |
| `GET /products/search` | Keystroke-driven, debounced, aborts its own prior request. Cannot free-run. |

---

## 7. Expected vs. observed request rates

| Scenario | Rate |
|---|---|
| One tab, fixed code, visible | 1 request / 30s ≈ **0.033 req/s** |
| Two users, one tab each, fixed code | ≈ **0.067 req/s** (one request roughly every 15s, on average, interleaved) |
| Two users, two tabs each (4 total), fixed code | ≈ **0.133 req/s** (≈ every 7.5s) |
| **Observed:** every 0.1–0.2s | **5–10 req/s** |
| One tab running the **pre-fix** code (this engagement's original estimate, from the earlier audit pass: ~150–400ms per loop iteration, bounded only by network round-trip) | **2.5–6.7 req/s** |

**The observed 5–10 req/s is within, or immediately adjacent to, the range a single pre-fix tab was
already predicted to produce — before any live observation existed to compare against.** No
multiplication by "two users" or multiple tabs is needed to explain the magnitude; a single stale
tab suffices on its own. This is the strongest single piece of corroborating evidence in this
report: the original audit's theoretical prediction and this session's live observation agree to
within the same order of magnitude, derived completely independently.

---

## 8. Deployment timeline comparison

| Date/time (UTC) | Commit | Event | Notification-bell behaviour |
|---|---|---|---|
| 2026-11-15T12:15:36Z | `05008fd` | Deployed (`Deployment has completed`) | **Bug introduced**: `onItemsChanged` mirroring + unstable `onNavigate` dependency → unbounded render-loop |
| 2026-11-15T17:59:09Z | `31544e4` | Deployed (Prettier formatting only) | No logic change (confirmed by diff: whitespace/import-wrapping only) |
| *(no further commits touch this code until)* | | | |
| 2026-11-21T12:53:09Z | `45570db` (push) | Deploy **attempted**, failed: `"Account is blocked"` | Fix not yet live |
| 2026-11-21T13:30:13Z | `45570db` | Deployed (`Deployment has completed`) | **Bug fixed and live** for any *new* page load from this moment on |

**The entire November 17–19 window falls inside the 2026-11-15 → 2026-11-21 buggy-code period.**
This is a directly observed fact from GitHub's record of Vercel's deployment statuses, not an
inference.

---

## 9. Exact affected source files and line references

- `bmfrontend/src/features/notifications/NotificationBell.tsx` — the defect (pre-fix) and the fix
  (current), lines 115-180 (the `load` callback and its poll effect).
- `bmfrontend/src/app/AppShell.tsx` — the caller supplying the originally-unstable `onNavigate`/
  `onManageReminders` props, lines 474-481; and the `onItemsChanged={setLiveNotifications}` wiring
  that fed every poll result back into a state update, line 480.
- `bmfrontend/src/features/notifications/AutoAlertBanner.tsx` — confirmed, again, to make no
  independent API calls; only a consumer of the shared poll result.
- `bmfrontend/vite.config.ts` lines 99-133 — the service-worker configuration explaining why an
  already-open tab does not receive the fix without a manual reload.
- `bmfrontend/src/app/swUpdate.ts`, `src/main.tsx` — the update-prompt wiring confirming no
  automatic reload exists anywhere in this application.

---

## 10. Recommended minimal fixes — **not implemented, awaiting your approval**

1. **Immediate, operational, zero-code-risk:** reload (or close and reopen) every browser tab
   currently open on every counter PC and device that runs this application. This alone stops any
   stale, pre-fix instance from continuing to run, and is the only action that can actually be
   verified to stop the current live traffic — a server-side deployment cannot do this on its own,
   per §4.
2. **Verification-only, no code change:** after step 1, confirm in Vercel's own live logs that
   `GET /api/v1/notifications` entries return to a ~30-second cadence (or longer, accounting for
   `visibilitychange`-triggered extra polls) and that the request rate drops to the low end of §7's
   table.
3. **A defensive code improvement to *consider* for the future (not proposed for implementation
   now):** some applications mitigate exactly this class of "fix shipped, stale tab never updates"
   risk with a bounded, non-disruptive nudge — e.g., escalating the update banner's visibility after
   it's been waiting an unusually long time, or (more invasively, and a genuine trade-off against
   `ADR-031`'s own stated priority) force-applying an update after a very long idle period with no
   open bill. This is a product/UX decision with a real trade-off against the counter staff's
   current protection from a disruptive mid-sale reload, not something this report recommends
   unilaterally — flagging it for your consideration, not proposing it as a fix.

**No code was changed to produce this report.** Items 1–2 are operational; item 3 is explicitly
not implemented, per this task's instruction to wait for approval.

---

## 11. Verification and regression test plan (for when/if any change is approved)

- **To confirm the "stale tab" explanation (no code change needed):** reload the known counter-PC
  tab(s) now, and watch Vercel's live log for the request cadence to drop to ~30s-or-longer
  immediately afterward. A clean drop at the moment of reload would be strong, direct confirmation;
  continued rapid requests after every known tab has been reloaded would disprove this report's
  primary hypothesis and mean a different, as-yet-unfound source exists.
- **Regression coverage already in place, re-confirmed this session:** the four polling-discipline
  tests added when `45570db` was authored (`NotificationBell.test.tsx`) specifically assert that a
  re-render with a new `onNavigate` identity does not restart the poll, and that polling pauses
  when hidden and resumes on becoming visible — these are exactly the behaviours this report relies
  on being correct in the current source, and they were re-read (not just re-run) this session as
  part of confirming the current code's correctness.
- **If item 3 of §10 is ever approved:** would need new tests asserting the specific escalation/
  force-reload trigger condition, and — given it's a UX/safety trade-off — explicit sign-off that
  it does not fire mid-bill (the one thing `ADR-031` exists to prevent).

---

## 12. Remaining uncertainties

- Which specific device(s) are currently generating the Nov 21 traffic — unknown without platform
  log access or physical inspection of the counter PC(s).
- Whether the Nov 17–19 figure is *entirely* explained by this mechanism, or partly by ordinary
  traffic coinciding with it — the timeline proves the mechanism was live and capable throughout
  that window, not a request-by-request breakdown.
- Whether any device is *also* stuck on a version **older** than `05008fd` (i.e., opened before
  2026-11-15) — such a tab would not have the bug at all, but would also not have any other fix or
  feature shipped since; not investigated here as it's not relevant to this specific defect.
- Live Vercel/Railway/Neon telemetry remains entirely unavailable in this environment, as recorded
  throughout this engagement — every figure in this report not explicitly sourced from GitHub's
  deployment-status API or this session's own source-code reading is a derivation, not a
  measurement, and is labelled as such.
