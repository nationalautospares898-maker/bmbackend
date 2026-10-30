# Phase 2 — Risk Register

> **Current Phase 2 design disposition (2026-10-30):** [Design Completion Addendum](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) is the current cross-cutting decision record. It resolves superseded open labels and defines the Phase 3 implementation baseline. Historical closure reports/addenda remain unchanged; legal, physical and operational gates remain gates. It does not represent implementation or test completion.


| Field | Value |
|---|---|
| Status | **Living Phase 2 record.** Reviewed at each milestone exit |
| Scope | Failure modes and risks across both repositories: financial, inventory, concurrency, security, operational, documentation and process |
| Purpose | For every risk: scenario, likelihood, impact, prevention, detection, recovery, test coverage, residual risk, and the decision it depends on |
| Implementation performed | **None.** Every test named here is **planned**; none has been written or executed |

## Scales

| Likelihood | Meaning | | Impact | Meaning |
|---|---|---|---|---|
| **High** | Expected during normal operation | | **Critical** | Money or stock becomes wrong, or records are lost/unauditable |
| **Medium** | Plausible within the first year | | **High** | A sale is blocked, a bill is wrong, or data is exposed |
| **Low** | Requires an unusual combination | | **Medium** | Degraded workflow; recoverable |
| | | | **Low** | Inconvenience |

**Severity** = likelihood × impact, judged for *this* system: one shop, ~5 users, ~500 invoices/day,
real money, no dedicated operations team.

---

## 1. Financial integrity risks

### R-01 · Duplicate invoice from a retried finalisation
**Severity: CRITICAL** · Likelihood **Medium** (network interruption at a counter is expected) · Impact **Critical**

| | |
|---|---|
| **Scenario** | A finalisation commits; the response is lost. The client retries. Without idempotency a second invoice, a second stock deduction and a second payment are created |
| **Affected** | B-09/M10, F-05/M18 |
| **Prevention** | `Idempotency-Key` **required** on `POST /sales:finalize`; the key row is inserted **inside** the business transaction so it is consumed only on commit; one key per user intent, **persisted before the first send**; a new key only when the user edits the bill |
| **Detection** | `Idempotent-Replay: true` header; finalisation outcome counters (`committed` vs `replayed`); `IDEMPOTENCY_KEY_REUSE` reported to error tracking as a client bug |
| **Recovery** | None needed — the retry returns the original invoice |
| **Test coverage** | `test_finalize_idempotent_replay`; `test_finalize_key_reuse_conflict`; `test_concurrent_same_key_one_invoice`; FE `test_idempotency_key_lifecycle`; E2E `lost-response-retry.spec.ts`; `P2-D-07` canonicalisation fixtures |
| **Residual** | Low. The remaining exposure is fingerprint mismatch (**R-02**) and the edit-while-unconfirmed path (**R-03**) |
| **Depends on** | `P2-D-06`, `P2-D-07` |

### R-02 · A legitimate retry rejected as key reuse
**Severity: HIGH** · Likelihood **Medium** · Impact **High**

| | |
|---|---|
| **Scenario** | The client reserialises the body between attempts — `"2500.0"` vs `"2500.00"`, different key order, an explicit `null` where a field was previously omitted. The fingerprint differs, the server returns `409 IDEMPOTENCY_KEY_REUSE`, and the sale fails at the counter in the exact path designed to make a lost response safe |
| **Affected** | M10, M18 |
| **Prevention** | **`P2-D-07`**: a normative canonicalisation rule — drop nulls, recursive key sort, money/quantity strings normalised to `Decimal` then re-rendered at declared scale, array order preserved, headers excluded |
| **Detection** | `IDEMPOTENCY_KEY_REUSE` rate; it is defined as a client bug, so any occurrence is a signal |
| **Recovery** | The user reloads the bill from the server and resubmits — but the original attempt's outcome must first be checked in Daily Bills |
| **Test coverage** | Shared cross-repository fixture list of equivalent / non-equivalent payload pairs in **both** suites |
| **Residual** | **Low.** Canonicalisation is now fully specified (Blueprint §5.2.3–5.2.4) with a worked `"2500.0"` vs `"2500.00"` table, an explicit include/exclude field list, and AC-IDEM-6 driving one shared fixture file from both suites. `P2-D-07` is **accepted**. Residual exposure is implementation fidelity, caught by AC-IDEM-6/7 |
| **Depends on** | `P2-D-07` |

### R-03 · Second invoice created by editing a bill while an attempt is unconfirmed
**Severity: CRITICAL** · Likelihood **Low–Medium** · Impact **Critical**

| | |
|---|---|
| **Scenario** | A finalisation is unresolved. The cashier edits the bill. `API-INTEGRATION.md §5`'s `onDraftEdited()` **deletes** the `pending_finalizations` row, discarding the only record of a key that may already have created an invoice. The next submit mints a new key → **two invoices for one sale** |
| **Affected** | M18, `OFFLINE-SYNCHRONIZATION.md §3.1` |
| **Prevention** | **`P2-D-08`**: block editing while an unresolved pending row exists (which invariant 4 already requires); clear the row **only on resolution** |
| **Detection** | Two invoices with the same lines, customer and minute; reconciliation of Daily Bills |
| **Recovery** | Cancel one invoice (within the window) → reversing movements + full credit note. The number is retained |
| **Test coverage** | *Edit attempt while unconfirmed → blocked, row intact, key unchanged* |
| **Residual** | **Low.** `P2-D-08` is **accepted** and fully designed (Blueprint §5.4): editing is blocked while unresolved, the row is cleared only on resolution, `Discard this attempt` gives the user a supervised exit, and §5.4.6 lists the forbidden transitions. AC-REC-4/7/9 verify it. The contradiction with `API-INTEGRATION.md §5` is now flagged in `../../bmfrontend/CLAUDE.md` so the illustrative snippet cannot be copied verbatim |
| **Depends on** | `P2-D-08` |

### R-04 · Partially finalised sale after a mid-transaction failure
**Severity: CRITICAL** · Likelihood **Low** · Impact **Critical**

| | |
|---|---|
| **Scenario** | A failure between the invoice insert and the stock movements leaves an invoice with no stock deduction, or a consumed invoice number with no invoice |
| **Prevention** | Finalisation is **one** transaction (FR-SALE-001). Participants (`InventoryService`, `CreditService`) never commit. **No HTTP, file I/O or job execution inside the transaction** — side effects go to `outbox_events` |
| **Detection** | `ck_invoices__total`; NFR-DATA-001 reconciliation; `invoice_series.next_number` drift |
| **Recovery** | None needed — the rollback is complete |
| **Test coverage** | `test_finalize_rollback_leaves_nothing`, with an **injected failure after the invoice insert**, asserting no invoice, no movement, no payment **and `next_number` unchanged** |
| **Residual** | Very low |

### R-05 · Derived `amount_paid` / `payment_status` drifting from allocations
**Severity: HIGH** · Likelihood **Low** · Impact **High** (wrong outstanding balances — directly BO-3)

| | |
|---|---|
| **Prevention** | Written **only** in the same transaction as the allocation rows; **column-level grants** limit which invoice columns the app may update at all; no endpoint sets them; **no "mark as paid" endpoint exists** |
| **Detection** | `invariants/test_payment_allocation.py`; **nightly reconciliation that alerts rather than repairing** |
| **Recovery** | Find the bug. Drift means a defect, not a value to patch — auto-healing would hide the cause |
| **Test coverage** | `amount_paid` equals the allocation sum across part payment, several payments, over-payment, credit-note allocation, cancellation, return **and after a reversal**; two concurrent allocations to one invoice → no lost update |
| **Residual** | Low. Accepted consciously in ADR-022 as the price of a fast outstanding query |

### R-06 · Money arithmetic silently wrong via floating point
**Severity: CRITICAL** · Likelihood **Low** · Impact **Critical**

| | |
|---|---|
| **Prevention** | `Decimal` / `NUMERIC` / JSON **strings** end to end (ADR-013); lint forbids `float` in `domain/`; client arithmetic confined to `lib/money.ts` with `decimal.js-light`; `parseFloat`/`Number()`/`+`/`-`/`*` on money elsewhere is a **lint error** |
| **Detection** | `client_computed_total` cross-check → `422 TOTAL_MISMATCH`; schema scale validation → `422` rather than a silent round |
| **Recovery** | — |
| **Test coverage** | `invariants/test_no_float_columns.py` (inspects `information_schema`); structural grep for `float(` in `domain/`; `lib/money.ts` at **100% branch**; property tests both sides; `0.1 + 0.2` display case (AC-FE-11) |
| **Residual** | Very low — four independent controls |

### R-07 · Rounding policy chosen by default rather than by decision
**Severity: HIGH** · Likelihood **Medium** · Impact **High** (a wrong policy invalidates the money suites in both repositories)

| | |
|---|---|
| **Scenario** | `ARCHITECTURE.md §6.5` states `ROUND_HALF_UP` at line level as though settled; `DATA-MODEL.md §3.1` defaults `sales.rounding_mode = line_half_up`. Phase 3 hardcodes it, tests freeze around it, and the documented default quietly becomes the decision OD-7 was supposed to make |
| **Prevention** | **`P2-D-03`**: the policy is an explicit object resolved from settings and published by bootstrap; property tests pass for **every** policy combination; `ARCHITECTURE.md §6.5` reworded to present the default as a configurable value |
| **Detection** | Review: any hardcoded rounding constant outside the policy resolver |
| **Recovery** | A policy change affects **newly computed** values only — issued invoices keep their snapshots and are never recomputed, so **no migration on live financial data** |
| **Test coverage** | Property tests parameterised over all policy combinations; structural test that no business-rule constant is hardcoded where bootstrap provides it |
| **Residual** | **Low.** `P2-D-03` is **accepted**; the policy schema, validation rules and four anti-divergence controls are specified (Blueprint §7.1–7.4), and `ARCHITECTURE.md §6.5`'s wording is scheduled for correction with M4. **OD-7's values remain unapproved** — which is the point: the engine cannot silently adopt them |
| **Depends on** | `P2-D-03`, **OD-7** |

### R-08 · Printed bill that does not visibly add up
**Severity: MEDIUM** · Likelihood **Medium** (if OD-7 selects sum-then-round) · Impact **Medium** (trust at the counter)

| | |
|---|---|
| **Scenario** | Under "full precision per line, round only the total", printed line totals do not sum to the printed grand total. A customer adds the column, gets a different number, and queries it; staff cannot explain it |
| **Prevention** | The recommended policy is **line-then-sum**, the only option where the bill always adds up. If OD-7 selects otherwise, `rounding_adjustment` must be **visible** on the bill |
| **Detection** | Pre-go-live manual bill review with the owner |
| **Recovery** | Change the policy; existing invoices keep their snapshots |
| **Test coverage** | Property test: Σ printed line totals + rounding adjustment **exactly** equals the printed grand total |
| **Residual** | Medium — depends on OD-7. **If whole-rupee rounding is ever enabled, the adjustment must be printed.** ✅ *Updated 2026-10-30: the bill's bottom is now verified, so a position for that line can be specified — but it changes the owner's layout and therefore needs approval alongside [OD-6](OWNER-DECISIONS.md#od-6).* This coupling must not be forgotten |
| **Depends on** | **OD-7**, **OD-5** |

---

## 2. Inventory integrity risks

### R-09 · Overselling the last unit from two counters
**Severity: CRITICAL** · Likelihood **High** (the design explicitly expects concurrent counters) · Impact **Critical**

| | |
|---|---|
| **Prevention** | `SELECT … FOR UPDATE` on `inventory_balances` in **ascending `product_id`** order inside the finalisation transaction; stock verified **after** the lock; negative balances only via an audited override |
| **Detection** | NFR-DATA-007 reconciliation report; oversell-override alert at > 5/day |
| **Recovery** | A reasoned adjustment movement |
| **Test coverage** | `concurrency/test_two_counters_last_unit` (one sale + one `INSUFFICIENT_STOCK available: 0`); `test_ten_sales_three_stock` (exactly 3 succeed); `property/test_no_negative_without_override` |
| **Residual** | Very low — serialisation is database-enforced, so it holds across multiple application instances |

### R-10 · Balance and ledger diverging
**Severity: CRITICAL** · Likelihood **Low** · Impact **Critical** (BO-2 depends on a trustworthy stock figure)

| | |
|---|---|
| **Prevention** | `inventory` is the **sole writer**; everything routes through `InventoryService.apply_movements`; `stock_movements` granted `INSERT, SELECT` only |
| **Detection** | **Nightly reconciliation recomputing every balance from the ledger**; discrepancy alert is **Critical** |
| **Recovery** | Reconciliation **alerts and requires an authorised adjustment** — it never silently rewrites a balance, because drift means a bug to find |
| **Test coverage** | `test_balance_equals_ledger_sum`; `property/test_stock_conservation.py`; **a test that patches `apply_movements` and runs the whole API suite to prove no other write path exists** |
| **Residual** | Low |

### R-11 · Deadlock between concurrent multi-product sales
**Severity: HIGH** · Likelihood **Medium** without discipline · Impact **High** (a counter freezes mid-sale)

| | |
|---|---|
| **Prevention** | A **fixed** lock order on every path: `inventory_balances` (ascending `product_id`) → `invoice_series` → `customers` → `invoices`. `lock_timeout` = 3 s so a pathological wait fails fast |
| **Detection** | Postgres deadlock log; lock-wait metric; `lock_timeout` error rate |
| **Recovery** | The client retries the **same** idempotency key |
| **Test coverage** | **Deadlock probe: 50 random parallel multi-product sales → zero deadlocks**; lock-timeout test asserts a fast retryable failure, not a hang |
| **Residual** | Low. **The lock order is a correctness requirement, not a style preference** — any new write path must adopt it |

### R-12 · A draft silently reserving or deducting stock
**Severity: HIGH** · Likelihood **Low** · Impact **High**

| | |
|---|---|
| **Prevention** | ADR-006 / FR-DRAFT-005: drafts have **zero** inventory effect. `Keep for later` maps to `PUT /drafts/{id}` **only** |
| **Detection** | Movement count before/after a draft operation |
| **Recovery** | A reasoned adjustment |
| **Test coverage** | `test_draft_edit_creates_no_movements`; FE `test_keep_for_later_no_finalize_call`; 50 rapid alternating draft updates finalise nothing |
| **Residual** | Very low |

### R-13 · Payment collection deducting stock again
**Severity: CRITICAL** · Likelihood **Low** · Impact **Critical**

| | |
|---|---|
| **Scenario** | `POST /payments` is wired to the sale path, or a "collect payment" flow reuses finalisation, deducting stock a second time for an already-sold item |
| **Prevention** | FR-CREDIT-010: sale finalisation and payment collection are **distinct financial events with distinct endpoints**. `POST /payments` writes payment + allocation rows only |
| **Detection** | Reconciliation; movement count per invoice |
| **Recovery** | Reversing movements + a payment reversal |
| **Test coverage** | **`test_payment_creates_no_stock_movement`** — movement count before/after; invariant **I-2** |
| **Residual** | Very low — 7 consistent assertions across the documentation and an explicit test |

---

## 3. Numbering and identity risks

### R-14 · Gapless numbering shipped as an invariant the owner never agreed to
**Severity: HIGH** · Likelihood **High** (it is the current documented state) · Impact **High** (a statutory claim the project cannot support)

| | |
|---|---|
| **Scenario** | FR-SALE-004, NFR-DATA-005, AC-1 and ADR-009 all assert gaplessness as settled, with an invariant test and a go-live acceptance criterion, while OD-3 records it **UNDECIDED — APPROVAL REQUIRED**. The system then presents a statutory-sounding guarantee that no owner or accountant approved |
| **Prevention** | **Task T-03** reclassifies the gapless clause as conditional on OD-3; **`P2-D-02`** re-scopes the blockage so the module can still be built |
| **Detection** | Audit **CF-1** found it; re-check at every milestone exit |
| **Recovery** | Documentation correction. **No code change** — the gapless implementation satisfies either answer |
| **Test coverage** | The gap-detection test's status changes from *required* to *informational* if gaps are acceptable |
| **Residual** | **Closed.** Phase 2 closure corrected all eleven locations: `PRD.md` (FR-SALE-004, NFR-DATA-005, AC-1 + new conditional AC-1a), `DATA-MODEL.md §4`, `DECISIONS.md` (ADR-009 title, index, decision and rationale), `ARCHITECTURE.md §5.4`, `TESTING-STRATEGY.md §5`, `TRACEABILITY.md` (§3, §12, §13), `OWNER-DECISIONS.md` (OD-3 blocking level, OD-22's assertion, the summary table), `bmbackend/README.md`, `bmbackend/CLAUDE.md` and `bmfrontend/docs/DECISIONS.md`. Residual risk is **recurrence**, covered by R-32 |
| **Depends on** | **OD-3**, `OD-3-A`, `P2-D-02` |

### R-15 · First sale of a new financial year failing
**Severity: HIGH** · Likelihood **Medium** (it happens on a known date) · Impact **High** (billing stops)

| | |
|---|---|
| **Scenario** | No `invoice_series` row exists for the new financial year and nothing specifies who creates it. On 1 April the first sale fails |
| **Prevention** | **`P2-D-09`**: lazy creation inside the finalisation transaction — `INSERT … ON CONFLICT DO NOTHING` then `SELECT … FOR UPDATE` |
| **Detection** | Finalisation failure alert; the frozen-clock boundary test |
| **Recovery** | Manual series creation via `PUT /admin/invoice-series/{id}` — the outage the prevention exists to avoid |
| **Test coverage** | **Frozen-clock test crossing 1 April → a new series row is created lazily and allocates `00001`** |
| **Residual** | **Low.** `P2-D-09` is **accepted** and fully designed (Blueprint §8.2): lazy in-transaction creation, four options evaluated, the concurrent-first-sale race proved safe by `uq_invoice_series__scope` + `ON CONFLICT DO NOTHING` + the existing row lock, template/kind inheritance specified, 8 named tests. **New dependency surfaced:** the timezone gap (T3-02) — without it the boundary would be evaluated in UTC |
| **Depends on** | `P2-D-09` |

### R-16 · Per-counter series applied as a performance fix, silently creating permanent gaps
**Severity: MEDIUM** · Likelihood **Low** · Impact **High** if gapless turns out to be required

| | |
|---|---|
| **Prevention** | The documented remedy for series contention is per-counter series — **but it guarantees permanent gaps.** It must not be applied before OD-3 is answered. `DEPLOYMENT.md §9` already lists "confirm the numbering answer to Q-TAX-3 still holds" as its prerequisite |
| **Detection** | Review of any series configuration change |
| **Recovery** | **None — gaps already issued cannot be closed.** Renumbering issued documents is falsifying records |
| **Test coverage** | n/a (a configuration control, not code) |
| **Residual** | Low, but **irreversible if it occurs**. Measured capacity is ~4 orders of magnitude above requirement, so contention should never arise |
| **Depends on** | **OD-3** |

### R-17 · Customer identity breaking on rename or duplicate creation
**Severity: MEDIUM** · Likelihood **Medium** (duplicates are expected at a counter) · Impact **Medium**

| | |
|---|---|
| **Prevention** | `customer_id` is **immutable and permanent**; every invoice, payment and grouping association keys on it, never on the name string; duplicates detected on normalised phone and name and **surfaced for the user to choose** — the system never silently merges; a referenced customer is never hard-deleted |
| **Detection** | `CUSTOMER_DUPLICATE_SUSPECTED` warning; `uq_customer_phones__business_phone` |
| **Recovery** | Explicit, permissioned, audited merge (P1, FR-CUST-005) that repoints invoices and payments and keeps a redirect |
| **Test coverage** | `test_rename_preserves_invoice_links`; `test_grouping_survives_rename`; `test_duplicate_candidates_no_automerge`; `test_customer_deactivate_not_delete` |
| **Residual** | Medium until merge ships (P1): two records for one real customer split their outstanding balance across two groups. **No automatic merge policy is proposed** |

---

## 4. Offline and synchronisation risks

### R-18 · A sale finalised or queued offline
**Severity: CRITICAL** · Likelihood **Low** (structurally prevented) · Impact **Critical** (overselling + duplicate or gapped numbering)

| | |
|---|---|
| **Prevention** | Four independent structural controls: the sync registry has **exactly three** operation types; `pending_finalizations` can only be created **while online**; it is **never drained through `/sync/operations`**; and **both** `Print Bill` and `Save / Credit Bill` are refused offline (invariants 2 and 2a) |
| **Detection** | **Structural test over the operation registry (AC-OFF-8)** |
| **Recovery** | n/a |
| **Test coverage** | `test_no_finalize_sync_type`; `offline-finalize-refused.spec.ts`; `offline-credit-refused.spec.ts` |
| **Residual** | Very low. The risk is a **future change**, which is why ADR-010 keeps the two queues separate and ADR-011 requires owner approval to add a financial operation type |

### R-19 · Staff believing they can bill offline
**Severity: HIGH** · Likelihood **High** without training · Impact **High** (the first outage is experienced as a system failure)

| | |
|---|---|
| **Scenario** | "Cloud-first with offline capability" (C-1) is read as "the shop can keep selling". During an outage a customer cannot be handed a numbered invoice — only a counter slip |
| **Prevention** | Explicit scope in FR-OFFLINE-001/002; an unmistakable UI state; the counter slip is clearly watermarked `COUNTER SLIP — NOT AN INVOICE` with no number; **staff training is a go-live gate** |
| **Detection** | A real 10-minute offline drill during a shift before go-live |
| **Recovery** | Training |
| **Test coverage** | `offline-drafts.spec.ts`; `counter-slip.spec.ts`; manual drill (both manual checklists) |
| **Residual** | Medium until **OD-8** is explicitly accepted and training is done. This is an **expectation** risk, not a software defect — and it is the single biggest gap between the phrase "offline capability" and what is being built |
| **Depends on** | **OD-8** |

### R-20 · A required error code that the client can never handle
**Severity: MEDIUM** · Likelihood **High** (it is the current state) · Impact **Medium**

| | |
|---|---|
| **Scenario** | `PRECONDITION_REQUIRED` (428), `SKIPPED_AFTER_EARLIER_FAILURE`, `CORRUPT_VERSION` and `DRAFT_ALREADY_CANCELLED` are required by the specs but absent from the registry declared "complete for v1". The client's `ErrorCode` union is **generated** from that registry and must be handled in an exhaustive `switch`, so a compliant client cannot handle them — an unhandled runtime branch in the offline path |
| **Prevention** | **Task T-01**: add the four codes to `§4.2`, add `428` to `§4.1`, mirror into `API-INTEGRATION.md §4`. Codes are additive-only, so this is non-breaking |
| **Detection** | Audit **CF-3**; the structural test "every `ErrorCode` member is handled and exercised" |
| **Recovery** | Documentation correction before M14/M22 |
| **Test coverage** | `test_all_error_codes_handled`; the sync rejection tests that need `DRAFT_ALREADY_CANCELLED` |
| **Residual** | **Closed.** All four codes are registered in `API-SPECIFICATION.md §4.2` (now **53 codes**) with a dedicated `§4.2a` giving each one's status, emitter, semantics and client behaviour, and all four are mirrored into `API-INTEGRATION.md §4`. The registry's self-description and the frontend's generated-union note now agree. **M14 and M22 are no longer blocked** |

### R-21 · Silent data loss on a conflict or a rejected operation
**Severity: HIGH** · Likelihood **Low** · Impact **High**

| | |
|---|---|
| **Prevention** | **No silent merge and no silent discard.** Version check + explicit user choice (keep mine / keep theirs / compare); `conflicted` and `rejected` stay visible until the user acts; local edit vs remote delete → `rejected` with an offer to **recreate as a new bill** so lines are not lost; the client **never** deletes local drafts in response to an auth or sync error |
| **Detection** | Sync panel; conflict-rate metric (> 5% is a warning) |
| **Recovery** | The user chooses; nothing is lost either way |
| **Test coverage** | `sync/test_conflict_returns_both_states`; `DraftConflictDialog.test.tsx`; AC-FE-9 |
| **Residual** | Low. **Automatic line-level merge was correctly rejected** — a plausible-looking wrong bill is worse than a visible conflict, because nobody checks |

### R-22 · Unsynced drafts lost with a device
**Severity: MEDIUM** · Likelihood **Low** · Impact **Medium**

| | |
|---|---|
| **Prevention** | Drafts sync on **every change while online** (debounced 500 ms), so the exposure equals the offline window; device revocation **retains** local data in case access is restored |
| **Detection** | Oldest `client_created_at` in a sync batch reveals how long a device was offline |
| **Recovery** | **Accepted by design — unsynced drafts were never sales** (E-11) |
| **Test coverage** | `restart-recovery.spec.ts`; `test_revoked_keeps_local_data` |
| **Residual** | Low and consciously accepted |

---

## 5. Security risks

Full control-by-control analysis is in
[`PHASE-2-INVESTIGATION-REPORT.md §9`](PHASE-2-INVESTIGATION-REPORT.md). The risks that remain
**after** the designed controls:

### R-23 · An authorised insider acting within their permissions
**Severity: MEDIUM** · Likelihood **Medium** · Impact **High**

| | |
|---|---|
| **Scenario** | A manager cancels a genuine sale within the 24-hour window, or a permitted user adjusts stock down with a plausible reason. Both are **within** their granted permissions |
| **Prevention** | Not preventable by design — this is the limit of a permission system. Mitigated by least privilege (`discount.cashier_max_percent = 0`, cancellation restricted to manager/owner, `credit.writeoff` owner-only) and **mandatory reasons** |
| **Detection** | **Audit log with before/after on 13 sensitive actions**; oversell overrides > 5/day alert; discount report; reconciliation |
| **Recovery** | Reversal workflows; the audit trail identifies the actor, device, counter and reason |
| **Test coverage** | `test_sensitive_change_records_before_after`; `test_oversell_audited`; `test_payment_permission_denied_audited` |
| **Residual** | **Medium, and irreducible.** The control is *attribution and review*, not prevention — and it only works if the owner actually reads the reports. **That is an operational dependency, not a software one** |

### R-24 · An alert that reaches nobody
**Severity: HIGH** · Likelihood **Medium** · Impact **High**

| | |
|---|---|
| **Scenario** | A reconciliation discrepancy (stock and ledger disagree) or a backup failure fires at 9 p.m. and no one is defined to receive or act on it |
| **Prevention** | **OD-19** requires a **named** recipient before go-live; `DEPLOYMENT.md §8.1` requires every alert to reach a real person |
| **Detection** | Pre-go-live alert test firing each alert to the named recipient |
| **Recovery** | — |
| **Test coverage** | Manual pre-go-live verification |
| **Residual** | **Medium — and partly unspecified.** No document defines the **on-call expectation** (response time, business hours vs 24/7). For a one-shop system "best effort, owner's phone" is a legitimate answer, but it must be *written down* so a 9 p.m. critical alert has a defined expectation. **New gap found in Phase 2** |
| **Depends on** | **OD-19** |

### R-25 · Unencrypted IndexedDB on a stolen counter machine
**Severity: MEDIUM** · Likelihood **Low** · Impact **Medium**

| | |
|---|---|
| **Prevention** | Bounded cache (`sync.catalog_cache_max_rows` = 5000); **`cost_price` never cached, regardless of permission**; remote device revocation; 15-minute access-token life; **full-disk encryption on counter machines as a stated operational requirement** and a go-live checklist item |
| **Detection** | `last_seen_at`; revocation audit |
| **Recovery** | Revoke the device; the refresh family is invalidated and sync is refused |
| **Residual** | **Medium and accepted.** No browser primitive makes local storage genuinely safe. Exposure is the cached catalog subset, stock figures and open drafts — no cost prices, no tokens, no payment instrument data |

### R-26 · A 15-minute window after device revocation
**Severity: LOW** · Likelihood **Low** · Impact **Medium**

| | |
|---|---|
| **Prevention** | Access tokens are not individually revocable; the 15-minute lifetime is the bound. Device revocation stops **refresh** immediately |
| **Residual** | **Low and accepted**, with the trade stated: token introspection on every request would add a database round trip to the hot path. **Documented so nobody assumes instant cutoff** |

### R-27 · Backup that cannot be restored
**Severity: CRITICAL** · Likelihood **Medium** (untested backups commonly fail) · Impact **Critical**

| | |
|---|---|
| **Prevention** | Daily snapshots + PITR + a **weekly off-provider encrypted dump** (protects against account-level loss, not just instance failure) |
| **Detection** | **Quarterly restore drill, and before first go-live.** The proof is **running the invariant suite against the restored database** — not that the restore completed |
| **Recovery** | Restore into a new instance, repoint, verify invariants |
| **Test coverage** | **AC-10**; recorded with date, duration, restored timestamp and result |
| **Residual** | Low. **An untested backup is treated as no backup**, and a failed or skipped drill blocks the next release |

---

## 6. Operational and process risks

### R-28 · Rate limiting silently multiplied by adding a replica
**Severity: MEDIUM** · Likelihood **Low** · Impact **Medium**

| | |
|---|---|
| **Prevention** | The in-process limiter is correct only at one replica. `DEPLOYMENT.md §9` records the prerequisite: **replace the limiter with a shared store before adding a replica** |
| **Residual** | Low — the prerequisite is written down, which is what stops it being discovered later |

### R-29 · A half-migrated fleet serving writes
**Severity: HIGH** · Likelihood **Low** · Impact **Critical**

| | |
|---|---|
| **Prevention** | Migrations are an **explicit deploy step, never on application start**; `/readyz` fails while the code's expected head differs from the database head, so such a replica **does not receive traffic**; expand→migrate→contract on live financial tables |
| **Detection** | `/readyz`; `alembic current` check in the deploy procedure |
| **Recovery** | Roll back the app and leave the schema — always the first option, possible only because of expand/contract |
| **Test coverage** | `/readyz` head-mismatch test; up/down per revision on a production-sized copy with invariants re-run after upgrade |
| **Residual** | Very low |

### R-30 · Module boundaries eroding
**Severity: MEDIUM** · Likelihood **Medium** without enforcement · Impact **Medium** (long-term maintainability)

| | |
|---|---|
| **Prevention** | **`P2-D-05`**: the import linter is a **merge blocker from M1**, before there is code to untangle. Five rules, including **exclusive table ownership** |
| **Detection** | CI |
| **Test coverage** | A deliberate reverse import must fail CI in M1 |
| **Residual** | Low |

### R-31 · Phase 3 rebuilding the rejected credit-details design
**Severity: MEDIUM** · Likelihood **Medium** · Impact **Medium** (wasted work + a workflow the owner explicitly rejected)

| | |
|---|---|
| **Scenario** | `TRACEABILITY.md §5` — the document most likely used as a work list — still names **`CreditDetailsForm`** and "Payment field in the credit form" for FR-CREDIT-001/004, and the modal allow-list still reserves a modal for "credit details" |
| **Prevention** | **Task T-02**, scheduled **before M19** |
| **Detection** | Audit **CF-4** |
| **Recovery** | Documentation correction; if built, deletion |
| **Test coverage** | `test_no_credit_details_route`; `test_credit_finalizes_in_one_request`; `test_modal_allowlist` must **not** whitelist "credit details" |
| **Residual** | **Closed.** `TRACEABILITY.md §5` FR-CREDIT-001 now describes the approved one-request flow with inline customer selection and names `test_no_credit_details_route`; FR-CREDIT-004 states payment is optional and not requested at save time. Surviving mentions of the rejected design are explicitly labelled as rejected in `../../bmfrontend/CLAUDE.md` and `bmfrontend/README.md`. The remaining modal-list and test-plan cleanup is tracked as T3-05 and must complete before M19/M25 exit; it does not block starting either milestone or reopen R-31. |

### R-32 · A documented default mistaken for an owner decision
**Severity: MEDIUM** · Likelihood **Medium** · Impact **Medium–High**

| | |
|---|---|
| **Scenario** | The pattern already present in CF-1 (gapless) and R-07 (rounding), and visible in `bmbackend/README.md §6`'s self-contradiction about approval status (CF-7). A default is restated enough times that it reads as settled |
| **Prevention** | `OWNER-DECISIONS.md`'s **four-level status vocabulary** (`SELECTED PREFERENCE` / `DOCUMENTED PROPOSAL` / `APPROVAL REQUIRED` / `UNVERIFIED REFERENCE DETAIL`), applied rigorously in the register itself; the fidelity audit's classification table; both `CLAUDE.md` files forbid presenting a default as an owner decision |
| **Detection** | **Re-run the fidelity audit at each milestone exit** |
| **Recovery** | Reclassification |
| **Residual** | Medium — this is a **recurring documentation risk**, not a one-time defect. The register's discipline is good; the leakage happens downstream in the PRD, ADRs and tests |

### R-33 · The print layout's unseen bottom being invented
**Severity: HIGH** · Likelihood **Medium** (pressure to ship a complete-looking bill) · Impact **High** (the grand total is the most-read number on the page)

| | |
|---|---|
| **Prevention** | ✅ **Largely discharged 2026-10-30 — the workbook was inspected, so the layout is evidence rather than inference.** `[VERIFIED]` / `[PROPOSED]` classification retained; **M27 remains conditionally ready, not ready**; every `[PROPOSED]` element must still be switchable off **without altering the layout**; a signature block, amount in words, tax line and invoice number are **verified absent** and must not be added. ⚠️ **Residual exposure moves to page setup** — paper, orientation and scaling were never set in the file ([OD-6](OWNER-DECISIONS.md#od-6)) — **and to the date order** ([OD-26](OWNER-DECISIONS.md#od-26)) |
| **Detection** | Review of the print layout against `UI-UX-SPECIFICATION.md §12.2a` |
| **Recovery** | Remove the invented content; obtain real evidence |
| **Test coverage** | Print snapshots cover only the observed top section; the printed-sample sign-off is a **go-live gate** |
| **Residual** | Medium while OD-5 is unresolvable. **The owner has confirmed they cannot currently supply further evidence**, so this risk persists indefinitely rather than resolving on a schedule |
| **Depends on** | **OD-5**, **OD-22** |

### R-34 · Go-live on an unconfirmed tax position
**Severity: HIGH** · Likelihood **Low** · Impact **High** (legal/compliance exposure)

| | |
|---|---|
| **Prevention** | `tax.mode = estimate_only` by default; documents titled `ESTIMATE`; `totals.tax` is `null` **not** `"0.00"` so no misleading zero-tax row renders; the literal `"Tax Invoice"` must not exist in the frontend source; `POST /admin/tax/enable-gst` returns a **precise checklist** until every prerequisite is met; a startup warning while in `estimate_only`; **the owner must be briefed that `ESTIMATE` documents are not tax invoices** (go-live checklist) |
| **Detection** | **AC-11**: no response anywhere contains "Tax Invoice" in estimate mode — asserted by scanning serialised responses across the API suite |
| **Recovery** | Enabling GST later is **configuration and data entry, not a migration** on live financial data |
| **Residual** | Low from a software standpoint. **The business exposure is real and is OD-1/OD-2/OD-24's to resolve**; an `ESTIMATE` does not support input-tax credit for business customers |
| **Depends on** | **OD-1**, **OD-2**, **OD-24** |

### R-35 · No import source and no real catalog data
**Severity: MEDIUM** · Likelihood **High** (it is the current state) · Impact **Medium**

| | |
|---|---|
| **Scenario** | No sample product list, supplier PDF or customer ledger has been provided. Import parsing cannot be validated against real data, and go-live requires the shop's catalog, prices, aliases and opening stock to be loaded |
| **Prevention** | Import is specified as a pipeline with **mandatory human review**, never a fixed column mapping — the only honest design without a sample. **Do not claim a dataset is available** |
| **Detection** | Go-live checklist: opening stock loaded via `opening` movements and **physically verified**; products/prices/aliases spot-checked by the owner |
| **Recovery** | Reasoned adjustment movements |
| **Residual** | Medium. **The real data migration is the go-live data load, not a Phase 3 feature** — it should be scoped early rather than discovered at go-live, especially the per-product tax-group entry that OD-2 would require |
| **Depends on** | **OD-2** (if GST is enabled, every active product needs a tax group) |

---

### R-36 · Statutory numbering exposure — documents issued without a legally required serial number
**Severity: HIGH** · Likelihood **Low–Medium** · Impact **High** *(new — created by OD-3's re-answer)*

| | |
|---|---|
| **Scenario** | The owner instructed *"No need of invoice numbering."* If law requires a consecutive serial number on documents the shop hands customers, the bills are non-compliant |
| **Affected** | M9, M12, M20; go-live |
| **Prevention** | The **internal** in-transaction reference is **retained** (OD-3), so a compliant path exists as configuration plus rendering rather than a redesign; `tax.mode = estimate_only` by default; the system **cannot** emit `TAX INVOICE` until the `FR-TAX-003` gate passes; **no compliance claim is made anywhere** |
| **Detection** | Accountant review under **[OD-25](OWNER-DECISIONS.md#od-25)** |
| **Recovery** | Enable rendering of the existing reference. **The gapless question then revives** and must be answered before any tax invoice is issued |
| **Test coverage** | `test_no_invoice_number_in_print_payload`; FE structural `test_no_invoice_number_rendered`; `test_enable_gst_gate_checklist` |
| **Residual** | **Medium and irreducible by engineering.** This is a legal determination. The design keeps the cost of reversal low; it cannot remove the exposure |
| **Depends on** | **[OD-25](OWNER-DECISIONS.md#od-25)** — `BLOCKS-GO-LIVE` |

### R-37 · Zero-decimal display misstating a fractional amount
**Severity: HIGH** · Likelihood **Low–Medium** · Impact **High** *(new — created by the OD-23 amendment)*

| | |
|---|---|
| **Scenario** | The owner requires `PRICE` and `GRAND TOTAL` to print with no decimals. If a stored amount carries paise, zero-decimal rendering **misstates the bill**: printed `PRICE` × `QTY` will not equal printed `TOTAL`, and the column will not sum to `GRAND TOTAL` |
| **Affected** | M20 (**T3-08**) |
| **Prevention** | **Stored values are never rounded or truncated for display.** A fractional amount reaching a zero-decimal field raises a **visible validation warning** instead of being silently rounded. Recommended (not adopted): **constrain prices to whole rupees at entry** |
| **Detection** | The validation warning; a property test asserting formatting never mutates the stored decimal |
| **Recovery** | Resolve [OD-23](OWNER-DECISIONS.md#od-23)'s conflict — whole-rupee entry, print the paise, or print rounded **with the rounding visible** |
| **Test coverage** | `test_print_amount_formatting`, `test_format_roundtrip_preserves_value`, plus a new case for the fractional path |
| **Residual** | **Medium until the owner chooses.** The system will refuse to misstate a bill silently, which may mean refusing to print until the amount is whole |
| **Depends on** | Design default in the 2026-10-30 addendum §3; implementation enforces integer-INR validation with no rounding |

---

## 7.1 Current dispositions — 2026-10-30

| Risk | Current design status | Remaining gate |
|---|---|---|
| R-07 | Money policy fully specified as a versioned whole-INR/no-rounding default | Owner/accountant change must be recorded before M4 exit |
| R-14 | No current customer-visible numbering or gapless requirement | Reopens only if qualified advice under OD-25 requires visible serials |
| R-36 | Mitigation and contingency documented; legal status unknown | Qualified legal/accounting determination before go-live |
| R-37 | Fractional values rejected before finalization and by print validation | Phase 3 M4/M20 implementation and acceptance tests |
| R-19, R-24, R-25, R-27, R-33, R-34, R-35 | Design controls specified | Training/alerts/device encryption/restore/physical print/tax/catalog gates as applicable |

The remaining risks retain the mitigations and acceptance evidence in their existing entries. A documented design mitigation is not evidence of an implemented control.

## 7. Summary

| Severity | Count | IDs |
|---|---|---|
| **CRITICAL** | **9** | R-01, R-03, R-04, R-06, R-09, R-10, R-13, R-18, R-27 |
| **HIGH** | **15** | R-02, R-05, R-07, R-11, R-12, R-14, R-15, R-19, R-21, R-24, R-29, R-33, R-34, **R-36**, **R-37** |
| **MEDIUM** | **12** | R-08, R-16, R-17, R-20, R-22, R-23, R-25, R-28, R-30, R-31, R-32, R-35 |
| **LOW** | **1** | R-26 |
| **Total** | **37** | R-01…R-37 |

> **R-36** (statutory numbering) and **R-37** (zero-decimal display vs fractional amounts) were added
> by the owner's fourth answer set — see
> [`PHASE-2-CLOSURE-ADDENDUM-1.md §8`](PHASE-2-CLOSURE-ADDENDUM-1.md). Both are **go-live gates**,
> neither blocks a Phase 3 milestone from starting.

### Closure review of every CRITICAL and HIGH risk

Each was re-checked against four questions: is the mitigation **complete**, is it **in the
blueprint**, does the **plan** include the work, and do **acceptance criteria** exist?

| Risk | Mitigation complete? | In blueprint | In plan | Acceptance criteria | Status at closure |
|---|---|---|---|---|---|
| R-01 duplicate invoice | ✅ | §5.2, §5.4 | M10, M18 | AC-2, AC-IDEM-1/3, AC-REC-1 | **Mitigated** |
| R-02 retry rejected as reuse | ✅ **closed in this pass** | §5.2.3–5.2.4 | M10, M18 | AC-IDEM-6/7/9 | **Mitigated** |
| R-03 second invoice via edit | ✅ **closed in this pass** | **§5.4** (12 cases) | M18, M22 | AC-REC-4/7/9 | **Mitigated** |
| R-04 partial finalisation | ✅ | §5.1 | M10 | `test_finalize_rollback_leaves_nothing` | **Mitigated** |
| R-05 `amount_paid` drift | ✅ | §5.3, §11 | M11 | NFR-DATA-003/004 invariants | **Mitigated** (ADR-022 residual accepted) |
| R-06 float money | ✅ | §6.3, §7 | M4 | `test_no_float_columns`, 100% branch `money.ts` | **Mitigated** |
| R-07 rounding by default | ✅ **mechanism closed** | **§7.1–7.4** | M4 | property tests over **all** policies | **Mitigated; OD-7 values still unapproved** |
| R-09 overselling | ✅ | §5.1 | M6, M10 | AC-3, `test_two_counters_last_unit` | **Mitigated** |
| R-10 balance/ledger divergence | ✅ | §4.1, §11 | M6 | NFR-DATA-001 + patch-and-run-suite test | **Mitigated** |
| R-11 deadlock | ✅ | §5.1 property 4 | M10 | 50-way deadlock probe | **Mitigated** |
| R-12 draft touching stock | ✅ | §3.3 ownership | M8 | `test_draft_edit_creates_no_movements` | **Mitigated** |
| R-13 payment deducting stock | ✅ | §5.3 | M11 | **I-2** `test_payment_creates_no_stock_movement` | **Mitigated** |
| R-14 unapproved gapless invariant | ✅ **closed in this pass** | §8, §8.1 | M9 + T-03 done | AC-1 / **AC-1a** split | **Closed** — recurrence covered by R-32 |
| R-15 FY series creation | ✅ **closed in this pass** | **§8.2** | M9, **T3-02** | 8 named tests | **Mitigated** |
| R-18 offline finalisation | ✅ | §9, §5.4 case 1 | M14, M22 | AC-OFF-8 structural | **Mitigated** |
| R-19 staff expect offline billing | ⚠️ **training, not software** | §10 counter slip | M28 | Manual drill | **Accepted — go-live gate [OD-8](OWNER-DECISIONS.md#od-8)** |
| R-20 unhandleable error codes | ✅ **closed in this pass** | §6.2 | M14, M22 unblocked | `test_all_error_codes_handled` | **Closed** |
| R-21 silent conflict data loss | ✅ | §9 | M22 | AC-FE-9 | **Mitigated** |
| R-24 alert reaching nobody | ⚠️ **partly** | §14 | M26 | Manual alert test | **Open — needs [OD-19](OWNER-DECISIONS.md#od-19) + `OD-19-A`** |
| R-27 unrestorable backup | ✅ | §14 | M26 | **AC-10** restore drill | **Mitigated — go-live gate** |
| R-29 half-migrated fleet | ✅ | §12 | M1, M2 | `/readyz` head-mismatch test | **Mitigated** |
| R-31 rebuilding rejected credit UI | ✅ **closed in this pass** | — | M19 unblocked | `test_no_credit_details_route` | **Closed** |
| R-33 inventing the unseen bill bottom | Mitigated by workbook evidence and explicit design | Addendum §4 | **M27 design-ready** | Physical print sign-off | Owner acceptance remains Phase 3 gate |
| R-34 go-live on unconfirmed tax | ✅ software; ⚠️ business | §11 | M28 | **AC-11** | **Accepted — go-live gates OD-1/2/24** |

### Historical open-risk snapshot at closure — current deltas are in §7.1

| Risk | Severity | What it needs | Who | Blocks |
|---|---|---|---|---|
| **R-24** on-call expectation undefined | HIGH | **[OD-19](OWNER-DECISIONS.md#od-19)** names a recipient; **`OD-19-A`** sets the response expectation; then a written runbook | Owner, then engineering | **Go-live only** (M26 runbook completeness) |
| **R-19** staff expect offline billing | HIGH | **[OD-8](OWNER-DECISIONS.md#od-8)** acceptance + staff training | Owner | **Go-live only** |
| **R-33** unseen bill bottom | HIGH | **[OD-5](OWNER-DECISIONS.md#od-5)** evidence, then **[OD-22](OWNER-DECISIONS.md#od-22)** placement on a reviewed sample | Owner | **M27 + go-live** |
| **R-34** unconfirmed tax position | HIGH | **OD-1**, **OD-2**, **OD-24** | Owner + accountant | **Go-live only** |
| **R-07** rounding policy values | HIGH *(mechanism mitigated)* | **[OD-7](OWNER-DECISIONS.md#od-7)** selects precision, method, stage, whole-rupee | Accountant | **M4's policy freeze only** |
| **R-14** gapless customer-visible series | Retired for current scope | No customer-visible numbering; contingency only if OD-25 requires it | Qualified accountant + owner | Go-live contingency only |
| **R-16** per-counter series creating permanent gaps | MEDIUM | Nothing to build — a **standing prohibition** until OD-3 is answered | — | — |
| **R-23** insider within permissions | MEDIUM | **Irreducible.** The control is attribution + owner review, not prevention | Owner (process) | — |
| **R-25** unencrypted IndexedDB | MEDIUM | Full-disk encryption on counter machines | Owner (go-live checklist) | **Go-live only** |
| **R-32** default mistaken for a decision | MEDIUM | **Recurring** — re-run the fidelity audit at each milestone exit | Engineering | — |
| **R-35** no import source / real catalog | MEDIUM | The shop's catalog, prices, aliases and opening stock at go-live data load | Owner | **Go-live only** |

**No CRITICAL risk is unmitigated.** Of the HIGH risks, none blocks a Phase 3 milestone other than
**R-33 → M27**; the rest gate **go-live** or a single acceptance criterion.

### Accepted and irreducible

R-23 (insider acting within permissions — the control is attribution, not prevention), R-25
(unencrypted IndexedDB), R-26 (15-minute token window), R-22 (unsynced drafts lost with a device),
and R-16's irreversibility if per-counter series are ever applied before OD-3 is answered.

**Honest position: this system will not be free of risk.** The financial and inventory invariants are
defended by database constraints, database privileges and real-concurrency tests, which is the
strongest practical arrangement at this scale. What remains is an authorised insider acting within
their permissions, an unencrypted browser store on a physical machine, and several owner decisions
whose absence is recorded rather than guessed.

---

## 8. Related documents

[`PHASE-2-INVESTIGATION-REPORT.md`](PHASE-2-INVESTIGATION-REPORT.md) ·
[`PHASE-2-DECISION-REGISTER.md`](PHASE-2-DECISION-REGISTER.md) ·
[`PHASE-2-IMPLEMENTATION-PLAN.md`](PHASE-2-IMPLEMENTATION-PLAN.md) ·
[`PHASE-2-OWNER-DECISIONS-REQUIRED.md`](PHASE-2-OWNER-DECISIONS-REQUIRED.md) ·
[`SECURITY.md`](SECURITY.md) · [`DEPLOYMENT.md`](DEPLOYMENT.md) ·
[`TESTING-STRATEGY.md`](TESTING-STRATEGY.md)
