# Phase 2 — Traceability Matrix

> **Current Phase 2 design disposition (2026-10-30):** [Design Completion Addendum](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) is the current cross-cutting decision record. It resolves superseded open labels and defines the Phase 3 implementation baseline. Historical closure reports/addenda remain unchanged; legal, physical and operational gates remain gates. It does not represent implementation or test completion.


| Field | Value |
|---|---|
| Status | **Living Phase 2 record** |
| Relationship to [`TRACEABILITY.md`](TRACEABILITY.md) | **That file remains the single requirement → endpoint → table → test matrix** and is not duplicated here. This file is an **overlay** adding the two dimensions Phase 1 did not have: **owner/technical decision** and **implementation milestone + release gate** |
| Purpose | Every significant requirement has a traceable path from the decision that authorised it to the milestone that builds it, the tests that verify it, and the gate that releases it |
| Implementation performed | **None.** Every test named is **planned** |

**How to read a row.** *Requirement* → *the decision that authorises it* → *what builds it* → *what
verifies it* → *what gate it must pass*. Where a requirement's **authorising decision is still open**,
the row says so, and the "must not build yet" column in
[`PHASE-2-IMPLEMENTATION-PLAN.md`](PHASE-2-IMPLEMENTATION-PLAN.md) governs.

Legend — **Class**: `OC` owner-confirmed · `DD` documented default · `TR` technical recommendation ·
`OPEN` open owner decision. **Gate**: `M` module-complete · `GL` go-live · `—` none.

---

## 1. Owner-confirmed workflows → implementation

| Req | Requirement | Class | Decision | BE module / milestone | FE module / milestone | Entities | API | Key planned tests | Gate |
|---|---|---|---|---|---|---|---|---|---|
| FR-BILL-009 | **Four** primary actions `Print Bill` / `Credit Bill` / `Save a Draft` / `Clear Draft` | **OC** | [OD-14](OWNER-DECISIONS.md#od-14) **re-answered** | — | `BillActions` · **M17/M19** (T3-09) | — | — | `test_action_labels_and_sublabels`, `test_no_fifth_primary_action` | M |
| FR-BILL-013 | **`Save a Draft`** = draft only; no number, stock, invoice or payment | **OC** | OD-14 **re-answered**, ADR-006 | `sales` · **M8** | `BillActions` · **M17** | `draft_bills` | `PUT /drafts/{id}` only | `test_save_a_draft_no_finalize_call`, `test_draft_edit_creates_no_movements` | M |
| FR-BILL-012 | **`Clear Draft`** clears the editable draft only; **never** a finalised sale, and **blocked while a finalisation is unconfirmed** | **OC** | OD-14 **re-answered**, `P2-D-08` | — | `BillActions` · **M17/M18** (T3-09) | `draft_bills` | `DELETE /drafts/{id}` | `test_clear_draft_confirms_line_count`, `test_clear_draft_blocked_while_unconfirmed` | M |
| FR-SALE-020…028 | `Print Bill` validates → finalises → **then** prints; print failure is harmless | **OC** | OD-14, ADR-014, F-05 | `sales` · **M10** | `finalize.ts` · **M18** | `invoices`, `invoice_items`, `stock_movements`, `payments` | `POST /sales:finalize` | `happy-path-cash.spec.ts`, `print-failure.spec.ts`, `test_submit_disabled_in_flight` | M |
| FR-SALE-009, C-6 | Cash and credit use the **same** engine and endpoint | **OC** | OD-21, ADR-033 | `sales` · **M10** | `finalize.ts` · **M18/M19** | same | `POST /sales:finalize` | `test_cash_and_credit_structurally_identical`, `test_single_finalization_path` | M |
| FR-CREDIT-008, FR-CREDIT-020 | Credit finalises **immediately**, one request, no intermediate page | **OC** | [OD-21](OWNER-DECISIONS.md#od-21), **ADR-033 `accepted`** | `sales` · **M10** | `BillActions` · **M19** | `invoices.is_credit` | `POST /sales:finalize` (`credit`) | `test_credit_finalizes_in_one_request`, `test_no_credit_details_route`, structural: OpenAPI has no "prepare credit" endpoint | M |
| FR-CREDIT-009, FR-CUST-010 | Credit requires a persisted `customer_id` + name; **nothing else** | **OC** | OD-21 | `customers`, `sales` · **M7/M10** | `CustomerQuickCreate` · **M19** | `customers`, `ck_invoices__credit_customer` | `409 CREDIT_CUSTOMER_REQUIRED` | `test_credit_sale_name_only_customer`, `test_credit_without_customer_rejected_atomically` | M |
| FR-CREDIT-025 | Initial payment **optional**, not requested at save time | **OC** | OD-21 | `sales` · **M10** | `BillActions` · **M19** | `payments` (absent) | `payment` optional | `test_credit_without_payment_is_unpaid` | M |
| FR-CREDIT-012, FR-CREDIT-031 | Optional header values supplied are **always persisted** | **OC** | OD-21, PRD §6.9.1 | `sales` · **M10** | `finalize.ts` · **M19** | `invoices.*_snapshot` | finalise `header` | `test_optional_header_fields_persisted` | M |
| FR-CREDIT-026 | Confirmation states customer, outstanding, due date and **"Stock has been updated."**; never exposes the internal reference | **OC** | OD-3, OD-14, ADR-033 | — | confirmation card · **M19** | — | — | `test_credit_confirmation_states_stock_updated` | M |
| FR-CREDIT-029 | **No print dialog** at credit save; printable forever afterwards | **OC** | OD-21 | — | **M19/M20** | — | — | `test_credit_save_opens_no_print_dialog` | M |
| FR-CREDIT-010 | **Payment collection never moves stock** | **OC** | OD-21, F-06 | `credit` · **M11** | `RecordPaymentDialog` · **M21** | `payments`, `payment_allocations` | `POST /payments` | **`test_payment_creates_no_stock_movement`** (invariant **I-2**) | M |
| FR-CREDIT-003, FR-RPT-023 | **No paid/unpaid control anywhere**; status derived | **OC** | ADR-022, F-07 | `credit` · **M11** | all lists · **M21** | `invoices.payment_status` | **no route sets it** | `test_no_route_sets_payment_status` (OpenAPI scan), `test_no_paid_checkbox` (structural) | M |
| FR-RPT-028/029 | Credit Bills grouped by **stable `customer_id`**; grouping visual only; **no per-customer entity** | **OC** | OD-21, F-09 | `credit` · **M11** | `CreditBillsScreen` · **M21** | `invoices.customer_id` FK | `GET /credit/customers` | `test_credit_customers_aggregate`, `test_no_per_customer_table`, `test_grouping_survives_rename` | M |
| FR-RPT-031 | Settled invoices and zero-balance customers stay visible | **OC** | OD-21, F-10 | `credit` · **M11** | **M21** | — | `?include_settled=` | `test_settled_invoices_retained` | M |
| FR-CREDIT-030, FR-PRINT-021 | **One print layout** for cash and credit; label `ESTIMATE` for both | **OC** | OD-21, [OD-24](OWNER-DECISIONS.md#od-24) | `sales` print payload · **M12** | `InvoicePrintLayout` · **M20** | — | `print_payload` | `test_cash_and_credit_layout_identical`, `test_estimate_label_both_sale_types` | M + **GL (OD-24)** |
| FR-PRINT-017/018 | Zero decimals for `PRICE`, line `TOTAL`, and `GRAND TOTAL`; reject fractional monetary values; **empty rows blank** | **OC** + documented default | Design Completion Addendum §3 | — | `InvoicePrintLayout`, money validators · **M4/M20** | — | exact money strings | zero-decimal display and rejection tests; `test_empty_rows_blank_not_zero` | M |
| FR-PRINT-019/020 | No customer-visible number; credit-only `Paid / Balance / Due` block after `GRAND TOTAL` as a design default | OD-3 confirmed; OD-22 design default | Design Completion Addendum §4 | — | **M20/M27** | — | `print_payload` has no number; credit block from backend snapshots | no-number structural check; cash/credit block placement test | M + physical M27 acceptance |
| FR-OFFLINE-002 | **No offline finalisation — cash or credit**; no offline payment | **OC** | OD-21, ADR-011, F-17 | `sync` · **M14** | **M22** | — | **no sync type exists** | `test_no_finalize_sync_type` (registry scan, AC-OFF-8), `offline-finalize-refused.spec.ts`, `offline-credit-refused.spec.ts` | M + **GL (OD-8)** |
| FR-BILL-003 | On-screen labels **deliberately differ** from printed labels | **OC** | F-16, Addendum 4 | — | `BillItemsTable` · **M17** | — | — | `test_item_table_columns_exact` | M |

---

## 2. Gated engine requirements

| Req | Requirement | Class | Decision | Milestone | Tests | What is blocked |
|---|---|---|---|---|---|---|
| FR-SALE-004 | Invoice numbers allocated from a locked series row **inside** the transaction; **unique + sequential** | **OC** *(mechanism)* | ADR-009, **`P2-D-02`** accepted | **M9** | `test_numbering_unique_sequential_under_20_parallel`, `test_rollback_consumes_no_number`, `test_fy_rollover_creates_series_lazily` | Nothing — **buildable now** |
| NFR-DATA-005 | Internal sale reference is unique and sequential per (branch, series, FY); no customer-visible number or current gaplessness requirement | **OC** | OD-3 resolved for product scope; OD-25 legal contingency | **M9** | `property/test_numbering.py` (unique + sequential) | Implementation and tests only |
| AC-1 | Five-line cash sale with a **unique, sequential** number | **OC** | — | **M10** | `test_finalize_cash_sale`, `happy-path-cash.spec.ts` | Nothing |
| **AC-1a** | Withdrawn for current scope: no customer-visible invoice number requested | **Retired** | OD-3 re-answered; legal serial contingency under OD-25 | — | No gaplessness acceptance test for current scope | Reopens only on qualified legal advice + owner change |
| — | Financial-year series creation | **TR** | **`P2-D-09`** accepted · [Blueprint §8.2](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) | **M9** + **T3-02** | 8 tests incl. `test_fy_rollover_concurrent_first_sales`, `test_fy_boundary_timezone` | Nothing — **fully designed**; risk **R-15** mitigated |
| FR-SALE-003 | Server authoritative for all money; client total is a cross-check | **OC** | [OD-7](OWNER-DECISIONS.md#od-7) principles, ADR-032 | **M4/M10** | `test_total_mismatch_rejected`, `test_never_displays_own_total_as_final` | Nothing |
| NFR-DATA-002 | `total` = `subtotal − discount + tax + rounding_adjustment` | **OC** | — (**DB `CHECK`**) | **M10** | `invariants/test_invoice_totals.py`, `property/test_money_conservation.py` | Nothing |
| **NFR-DATA-002b** | `subtotal` = Σ `invoice_items.line_total` | **DD** | audit **CF-9** ✅ promoted into `PRD.md §7.4` | **M10** | `invariants/test_invoice_totals.py::subtotal_equals_line_sum` | Nothing — now defined and traced |
| — | Versioned whole-INR/no-rounding v1 policy default | **Design default complete** | Design Completion Addendum §3; changes require recorded owner/accountant decision before M4 exit | **M4/M10** | Exact precision, rejection and reconciliation tests | Implementation and tests only |
| NFR-DATA-009 | All money `NUMERIC`/`Decimal`/string, never float | **OC** | ADR-013 | **M2/M4** | `invariants/test_no_float_columns.py`, structural grep for `float(` | Nothing |

---

## 3. Core invariants → enforcement → test → gate

| ID | Invariant | Enforcement | Milestone | Planned test | Gate |
|---|---|---|---|---|---|
| **I-1** | A finalised sale deducts stock **exactly once** | Single transaction; `inventory` sole writer | M10 | `test_cash_sale_single_stock_movement`, `test_credit_sale_single_stock_movement` | **GL** |
| **I-2** | Payment collection creates **zero** stock movements | Distinct endpoints, no inventory path | M11 | `test_payment_creates_no_stock_movement` | **GL** |
| **I-3** | A failed sale leaves no partial effect **and no consumed number** | One transaction | M10 | `test_finalize_rollback_leaves_nothing` + `next_number` unchanged | **GL** |
| **I-4** | Invoice numbers unique in scope | `uq_invoices__series_sequence`, `uq_invoices__business_number` | M9/M10 | `concurrency/test_numbering.py` | **GL** |
| **I-5** | A repeated idempotent operation creates no second effect | `uq_idempotency__scope` inside the transaction | M10/M11/M14 | `test_finalize_idempotent_replay`, `test_duplicate_payment_once`, `sync/test_replay_batch_all_replayed` | **GL** |
| **I-6** | Drafts have no finalised-sale side effects | ADR-006; no inventory call | M8 | `test_draft_edit_creates_no_movements` | **GL** |
| **I-7** | `amount_paid` = Σ allocations; `payment_status` agrees | Same-transaction write + column grants | M11 | `invariants/test_payment_allocation.py` + nightly reconciliation | **GL** |
| **I-8** | `inventory_balances.quantity` = Σ movement deltas | Sole writer + nightly reconciliation | M6 | `invariants/test_stock_ledger.py`, `property/test_stock_conservation.py` | **GL** |
| **I-9** | No access outside authorisation scope | Token-derived scope; repository predicates | M3 | **Generated** `api/test_isolation.py` (**AC-8**), `api/test_permissions.py` | **GL** |
| **I-10** | Historical invoices retain their product/pricing snapshot | Immutable `invoice_items` | M10/M12 | `test_item_snapshot_survives_catalog_edit` | **GL** |
| **I-11** | Finalised financial rows never updated or deleted | **DB privileges** (ADR-015) | M2/M6/M10/M11 | `invariants/test_immutability.py` | **GL** |
| **I-12** | Cumulative returns ≤ sold quantity | **DB `CHECK`** `ck_invoice_items__returned` | M24 | `invariants/test_return_bounds.py` | — |
| NFR-DATA-007 | Negative stock only with an audited override | Service gate | M6/M10 | `property/test_no_negative_stock.py` | **GL** |
| NFR-DATA-010 | Every business-scoped row has `business_id` | `NOT NULL` | M2 | `invariants/test_tenant_columns.py` | **GL** |

---

## 4. Phase 2 decisions → what they change → verification

| Decision | Status | Changes | Milestone | Verified by |
|---|---|---|---|---|
| [`P2-D-01`](PHASE-2-DECISION-REGISTER.md#p2-d-01) | accepted | Artifact location + a pointer index in `bmfrontend` | — | Link validation; ADR-021 compliance |
| [`P2-D-02`](PHASE-2-DECISION-REGISTER.md#p2-d-02) | **accepted** · current scope resolved | Internal-only reference; no visible serial | M9 | Internal numbering and recovery tests; no gaplessness criterion |
| [`P2-D-03`](PHASE-2-DECISION-REGISTER.md#p2-d-03) | **proposed** · values remain OD-7 | `MoneyPolicy` object; bootstrap publishes it | M4, M17 | Property tests pass for **every** policy combination; no hardcoded rounding constant |
| [`P2-D-04`](PHASE-2-DECISION-REGISTER.md#p2-d-04) | **proposed** | States the source-of-truth hierarchy | — | Blueprint §1; linked from both `CLAUDE.md` |
| [`P2-D-05`](PHASE-2-DECISION-REGISTER.md#p2-d-05) | accepted | Import linter is a merge blocker | **M1** | A deliberate reverse import **fails CI** |
| [`P2-D-06`](PHASE-2-DECISION-REGISTER.md#p2-d-06) | accepted | Claim key before inventory locks | M10 | Duplicate submit returns `IDEMPOTENT_REQUEST_IN_PROGRESS` holding no locks |
| [`P2-D-07`](PHASE-2-DECISION-REGISTER.md#p2-d-07) | **proposed** | Normative fingerprint canonicalisation | M10, M18 | Shared cross-repo fixtures: equivalent payloads replay, non-equivalent conflict |
| [`P2-D-08`](PHASE-2-DECISION-REGISTER.md#p2-d-08) | **proposed** | Blocks edits while unconfirmed | M18 | Edit attempt while unconfirmed → blocked, row intact, key unchanged |
| [`P2-D-09`](PHASE-2-DECISION-REGISTER.md#p2-d-09) | **proposed** | Lazy FY series creation | M9 | Frozen-clock test crossing 1 April |
| [`P2-D-10`](PHASE-2-DECISION-REGISTER.md#p2-d-10) | accepted | Settings resolution order | M4, M14, M17 | `test_settings_defaults_and_audit`, `test_no_hardcoded_business_rule` |

---

## 5. Audit findings → correction → blocked work

All eleven are **RESOLVED** as of Phase 2 closure; full dispositions with validation methods in
[`PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md §6.1`](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md).

| Finding | Severity | Document(s) corrected | Task | Was blocking | Status |
|---|---|---|---|---|---|
| [CF-1](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md#cf-1) | **CRITICAL** | 11 files incl. `PRD.md` (+ new **AC-1a**), `OWNER-DECISIONS.md`, ADR-009, `DATA-MODEL.md`, `ARCHITECTURE.md`, `TESTING-STRATEGY.md`, `TRACEABILITY.md`, both READMEs, both `CLAUDE.md` | **T-03** ✅ | M9's acceptance criterion | ✅ **RESOLVED** |
| [CF-2](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md#cf-2) | HIGH | `TRACEABILITY.md §13A.3` → 101 / 93 / **194** | T-04 ✅ | Nothing | ✅ **RESOLVED** |
| [CF-3](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md#cf-3) | MEDIUM | `API-SPECIFICATION.md §4.1` + `§4.2` + new **`§4.2a`** (53 codes), `API-INTEGRATION.md §3.3/§4` | **T-01** ✅ | **M14, M22** — now unblocked | ✅ **RESOLVED** |
| [CF-4](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md#cf-4) | MEDIUM | `TRACEABILITY.md §5` (FR-CREDIT-001/004), FE `CLAUDE.md`, FE `README.md` | **T-02** ✅ | **M19** — now unblocked | ✅ **RESOLVED** |
| [CF-5](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md#cf-5) | MEDIUM | `OFFLINE-SYNCHRONIZATION.md §8`, `TRACEABILITY.md §3/§7`, `DECISIONS.md` ADR-027 stub | T-03 ✅ | Nothing | ✅ **RESOLVED** — rejected-option mentions preserved |
| [CF-6](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md#cf-6) | MEDIUM | `UI-UX-SPECIFICATION.md §12` preamble, FE `DECISIONS.md` | T-04 ✅ | Nothing | ✅ **RESOLVED** |
| [CF-7](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md#cf-7) | LOW | `bmbackend/README.md §6` | — | Nothing | ✅ **RESOLVED** (investigation pass) |
| [CF-8](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md#cf-8) | LOW | `OWNER-DECISIONS.md` summary → names **four** items | T-03 ✅ | Nothing | ✅ **RESOLVED** |
| [CF-9](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md#cf-9) | LOW | `PRD.md §7.4` (promoted), `DATA-MODEL.md §4`, `TRACEABILITY.md §12` | T-04 ✅ | Nothing | ✅ **RESOLVED** |
| [CF-10](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md#cf-10) | LOW | `DATA-MODEL.md` — tables moved to `§3.12`, tax → `§3.15` | T-04 ✅ | Nothing | ✅ **RESOLVED** |
| [CF-11](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md#cf-11) | LOW | `bmfrontend/README.md` anchor | — | Nothing | ✅ **RESOLVED** (investigation pass) |

---

## 6. Open owner decisions → blocked scope → milestone

| OD | Topic | Blocks implementation of | Blocks go-live of | Milestone |
|---|---|---|---|---|
| **[OD-3](OWNER-DECISIONS.md#od-3)** | Gapless numbering | **The policy statement + its acceptance test only** (`P2-D-02`) | — | M9 |
| **[OD-7](OWNER-DECISIONS.md#od-7)** | Rounding precision/method | **The policy values + expected-value assertions only** (`P2-D-03`) | — | M4 |
| **[OD-5](OWNER-DECISIONS.md#od-5)** | Bill's lower section | **The print template's bottom section** | Printed-sample sign-off | **M27** |
| **[OD-22](OWNER-DECISIONS.md#od-22)** | Number / payment-block placement | **Final placement** (both stay switchable) | Printed-sample sign-off | **M27** |
| [OD-1](OWNER-DECISIONS.md#od-1) | GST status | — | Tax configuration | M28 |
| [OD-2](OWNER-DECISIONS.md#od-2) | Rates / HSN / inclusivity | — | Tax configuration + per-product tax groups | M28 |
| [OD-4](OWNER-DECISIONS.md#od-4) | Legal shop identity | — | Invoice header content | M28 |
| [OD-6](OWNER-DECISIONS.md#od-6) | Paper and printer | — | Print verification on real hardware | M28 |
| [OD-8](OWNER-DECISIONS.md#od-8) | Offline expectation | — | Staff training | M28 |
| [OD-19](OWNER-DECISIONS.md#od-19) | Hosting + **named** alert recipient | — | Monitoring | M26/M28 |
| [OD-24](OWNER-DECISIONS.md#od-24) | Printed-document terminology | — | Document label approval | M28 |
| `OD-25` | Qualified determination on statutory serial-number requirements | Go-live | Owner/accountant | M28 |
| `OD-19-A` **new** | On-call expectation | — | Operations runbook completeness | M26 |

---

## 7. Acceptance criteria → milestone → gate

| AC | Criterion | Milestone | Gate |
|---|---|---|---|
| AC-1 | 5-line cash sale finalises and prints; stock down exactly once; number allocated | M10, M18, M20 | **GL** |
| AC-2 | Idempotent replay creates no second invoice or movement | M10 | **GL** |
| AC-3 | Two concurrent last-unit finalisations → one sale + one explicit error | M10 | **GL** |
| AC-4 | Credit sale with part payment reconciles across daily and credit reports | M11, M13 | **GL** |
| AC-5 | 20 drafts created, edited, switched, recovered after restart | M8, M17 | **GL** |
| AC-6 | Offline drafts sync with explicit outcomes; finalisation correctly refused | M14, M22 | **GL** |
| AC-7 | Every `NFR-DATA` invariant has a passing test, incl. randomised concurrency | M6, M10, M11 | **GL** |
| AC-8 | Cross-business read fails on every endpoint (generated matrix) | M3 | **GL** |
| AC-9 | Ambiguous import unapplicable without per-row decisions; double apply is a no-op | M23 | **GL** |
| AC-10 | Backup restore demonstrated; **restored DB passes all invariant tests** | M26 | **GL** |
| AC-11 | In `estimate_only`, nothing says "Tax Invoice"; the GST gate is demonstrated | M12, M18 | **GL** |
| AC-12 | Partial return adjusts stock, creates a correct credit note, reduces outstanding | M24 | — |
| AC-FE-1 | 5-line cash bill, keyboard only, under 60 s, by someone who has seen the screen once | M17, M18 | **GL** |
| AC-FE-2 | 20 bills survive a browser force-close with every field intact | M17 | **GL** |
| AC-FE-3 | Offline: bills editable, `Print Bill` disabled with the message, counter slip prints | M20, M22 | **GL** |
| AC-FE-4 | Dropped response → "Not yet confirmed", survives reload, one invoice on reconnect | M18 | **GL** |
| AC-FE-5 | Each documented 409 produces its specified UI | M18 | **GL** |
| AC-FE-6 | **Printed output matches the owner's Excel bill on the real printer — owner sign-off** | **M27** | **GL** |
| AC-FE-7 | Print fails/cancelled → invoice still exists and reprints; no second sale | M20 | **GL** |
| AC-FE-8 | axe clean on every screen; every billing task keyboard-completable | M25 | **GL** |
| AC-FE-9 | Offline + online edit → conflict UI; neither version lost | M22 | **GL** |
| AC-FE-10 | No token in storage, no "Tax Invoice" literal, no paid-status control | M16, M20, M21 | **GL** |
| AC-FE-11 | Decimal totals exact and matching the server to the paisa | M17 | **GL** |
| AC-FE-12 | Search 500 → typed query preserved + retry offered | M17 | **GL** |
| AC-OFF-1…8 | Sync replay, conflict, isolation, revocation, cursor reset, **no finalisation type** | M14, M22 | **GL** |

---

## 8. Release gates → evidence required

| Gate | Evidence | Depends on |
|---|---|---|
| Module complete | That module's unit/integration/API tests green; its invariants green; every error code it emits produced by a test; lint/type/import-linter clean; coverage thresholds met | — |
| PR-blocking suite | BE < 8 min; FE < 10 min incl. structural tests and the bundle-size gate | — |
| Release candidate | All invariant, concurrency and isolation suites green; migration up/down on a production-sized copy with invariants re-run after upgrade; a11y clean; print snapshots approved | — |
| **Print sign-off** | A printed bill on the shop's real printer and paper, compared side by side, **owner-signed** | **OD-5, OD-22, OD-6**, M27 |
| **Tax position** | `tax.mode` explicitly set; if `gst_enabled`, the FR-TAX-003 checklist passes; owner briefed that `ESTIMATE` is not a tax invoice | **OD-1, OD-2, OD-24** |
| **Business identity** | Legal name, address, contact entered from owner-supplied values — **no placeholders** | **OD-4** |
| **Restore drill** | Date, duration, restored timestamp and result recorded; **the invariant suite must pass on the restored data** | M26 |
| **Monitoring** | All 13 alerts live and reaching a **named** recipient; on-call expectation written down | **OD-19**, `OD-19-A` |
| **Staff training** | Reprint vs new sale; the not-confirmed state and counter slips; the oversell policy; **what offline does not do** | **OD-8** |
| **Operational** | Opening stock loaded via `opening` movements and **physically verified**; products/prices/aliases spot-checked; seeded credentials rotated; counters registered with `max_open_drafts` ≥ 20; devices registered with full-disk encryption; rollback rehearsed; break-glass stored outside the system | M28 |
| Security | No high/critical dependency vulnerabilities; secret scan clean | — |

---

## 9. Coverage statement

| Claim | Status |
|---|---|
| Every P0 requirement in both PRDs appears in [`TRACEABILITY.md`](TRACEABILITY.md) with a named planned test | ✅ **Verified in Phase 2** — all **194** (101 backend + 93 frontend) checked individually |
| `TRACEABILITY.md §13A.3`'s own tally | ❌ **Wrong** — states 88 frontend / 189 total. Audit **CF-2**, task **T-04** |
| Every owner-confirmed workflow traces to a milestone and a test | ✅ §1 above |
| Every core invariant traces to an enforcement mechanism, a test and a gate | ✅ §3 above |
| Every Phase 2 decision traces to what it changes and how it is verified | ✅ §4 above |
| Every audit finding traces to a correction task and the work it blocks | ✅ §5 above |
| Every open owner decision traces to its blocked scope and milestone | ✅ §6 above |
| Requirement IDs cited but undefined | **One**: `NFR-DATA-002b` (audit **CF-9**) — now traced here pending promotion to `PRD.md §7.4` |

> **These are planned tests. None has been written or executed.** Phase 1 and Phase 2 produced
> documentation only.

---

## 10. Related documents

[`TRACEABILITY.md`](TRACEABILITY.md) *(the single requirement → endpoint → table → test matrix)* ·
[`PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md`](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md) ·
[`PHASE-2-INVESTIGATION-REPORT.md`](PHASE-2-INVESTIGATION-REPORT.md) ·
[`PHASE-2-IMPLEMENTATION-BLUEPRINT.md`](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) ·
[`PHASE-2-IMPLEMENTATION-PLAN.md`](PHASE-2-IMPLEMENTATION-PLAN.md) ·
[`PHASE-2-DECISION-REGISTER.md`](PHASE-2-DECISION-REGISTER.md) ·
[`PHASE-2-RISK-REGISTER.md`](PHASE-2-RISK-REGISTER.md) ·
[`PHASE-2-OWNER-DECISIONS-REQUIRED.md`](PHASE-2-OWNER-DECISIONS-REQUIRED.md)
