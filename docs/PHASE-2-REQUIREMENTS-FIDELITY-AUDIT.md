# Phase 2 — Requirements Fidelity Audit

| Field | Value |
|---|---|
| Status | **Living Phase 2 record.** Maintained in this repository, not a snapshot |
| Created | Phase 2 investigation pass · **dispositions finalised at Phase 2 closure (§6.1)** |
| Purpose | Verify that the Phase 1 documentation accurately represents the owner's recorded decisions — and classify every significant requirement by the strength of its evidence |
| Scope | Both repositories (`bmbackend`, `bmfrontend`) — 28 documents at the investigation pass, 38 after the Phase 2 artifacts were added |
| Implementation performed | **None.** This file is documentation only |
| Authority | Subordinate to [`OWNER-DECISIONS.md`](OWNER-DECISIONS.md) for business decisions and [`API-SPECIFICATION.md`](API-SPECIFICATION.md) for the contract. Where this audit finds those documents inconsistent, it **records the conflict** — it does not resolve it by fiat |

> ## Evidence limitation — stated up front
>
> **No transcript of the owner conversations is available to this audit.** The only evidence of what
> the owner decided is what the repository documents record. Therefore:
>
> - This audit can verify **internal consistency**, **classification honesty**, and whether a
>   document **claims more certainty than its own cited source supports**.
> - It **cannot independently verify** that an item marked `SELECTED PREFERENCE` was in fact selected
>   by the owner. Those are taken at face value, because they are the project's own primary record.
> - Where this audit says a decision is **owner-confirmed**, that means *"the authoritative register
>   records it as owner-confirmed"* — not *"this audit re-verified it with the owner."*
>
> **Phase 1 criterion 14 ("no fabricated approval") is re-tested here and still passes:** no approval
> date, approver name, accounting sign-off or legal review appears anywhere in either repository.
> Every settled item is attributed to an owner *preference* or *specification*, never to a sign-off.

---

## 1. Source inventory

All 28 pre-existing files were read in full. **No file was modified during the investigation pass**; the corrections in §6.1 were applied in the later closure pass.

### 1.1 Authoritative documents

| Document | Authority over | Notes |
|---|---|---|
| [`OWNER-DECISIONS.md`](OWNER-DECISIONS.md) | **All business decisions** (OD-1…**OD-25**) | Declared authoritative by ADR-021 and by `PRD.md §5` |
| [`PRD.md`](PRD.md) | Backend business rules, requirement IDs, priorities, AC-1…12, release gates | `§5` is a *summary* of the OD register; the register governs |
| [`API-SPECIFICATION.md`](API-SPECIFICATION.md) | The HTTP contract, error-code registry, idempotency contract | Explicitly wins over `../../bmfrontend/docs/API-INTEGRATION.md` |
| [`DATA-MODEL.md`](DATA-MODEL.md) | Schema, constraints, indexes, invariant enforcement; **§3.14** canonical identifier vocabularies | |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Modules, layering, transaction boundaries, lock order, idempotency mechanism | |
| [`SECURITY.md`](SECURITY.md) | Threat model, authz enforcement points, DB privileges, upload rules | |
| [`OFFLINE-SYNCHRONIZATION.md`](OFFLINE-SYNCHRONIZATION.md) | The sync protocol and the three-states distinction | Explicitly authoritative for the frontend |
| [`TRACEABILITY.md`](TRACEABILITY.md) | **The single** requirement → endpoint → table → test matrix for both repos | |
| [`TESTING-STRATEGY.md`](TESTING-STRATEGY.md) | Backend test layers, invariant/concurrency suites, gates | |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Environments, migrations, backups, go-live checklist `§11.2` | |
| [`DECISIONS.md`](DECISIONS.md) | ADR-001…022 + index of 023…033 | Global ADR numbering |
| [`PHASE-1-EXIT-CRITERIA.md`](PHASE-1-EXIT-CRITERIA.md) | The 19 criteria and their **living** evaluation | Supersedes the closure reports as the maintained record |
| `../../bmfrontend/docs/PRD.md` | Client requirements; **defines** `FR-BILL-*`, `FR-CREDIT-02x`, `FR-RPT-02x`, `FR-PRINT-01x/02x`, `FR-OFFLINE-014/015`, `FR-NAV-*`, `FR-ITEM-*`, `FR-UI-PERM-*`, AC-FE-* | |
| `../../bmfrontend/docs/UI-UX-SPECIFICATION.md` | Screens; **§12** the printed bill and its `[VERIFIED]`/`[PROPOSED]` classification *(read from the owner's workbook, 2026-10-30)* | |
| `../../bmfrontend/docs/DECISIONS.md` | ADR-023…033 | ADR-033 is the only `accepted` ADR |
| `../../bmfrontend/docs/ARCHITECTURE.md`, `API-INTEGRATION.md`, `TESTING-STRATEGY.md` | Client structure, consumption of the contract, client test layers | Subordinate to backend contract docs |
| `bmbackend/CLAUDE.md`, `bmfrontend/CLAUDE.md` | AI-session **rules** (not specifications) | Must agree with the specs |

### 1.2 Historical / non-authoritative

| File | Status |
|---|---|
| `../../PHASE-1-CLOSURE-REPORT.md` + `ADDENDUM`, `-2`, `-3`, `-4` | **Immutable point-in-time snapshots.** Not edited by this audit. Addendum 4 carries the current Phase 1 verdict; earlier verdicts superseded, findings stand. ADR-021 classes all five as *disposable evidence* |
| `../../README.md` (workspace root) | **Explicitly non-authoritative** by its own header and ADR-021 — links only |
| `bmbackend/README.md`, `bmfrontend/README.md` | Orientation. Informational; defer to `docs/` |

### 1.3 Verified repository state

| Check | Result |
|---|---|
| Non-Markdown files anywhere under `bm/` | **0** |
| `.git` directories | **0** — neither repository is initialised |
| Application source, manifests, migrations, tests | **None exist** |
| Markdown files | **28** at the investigation pass (**38** after the Phase 2 artifacts) |

**Consequence for this audit:** every statement about the system is a *specification*, never an
observation of behaviour. Nothing in either repository has been built, run or tested.

---

## 2. Classification method

| Class | Meaning | May Phase 3 build on it? |
|---|---|---|
| **OWNER-CONFIRMED** | The authoritative register records an explicit owner decision or specification | Yes, within what the decision actually covers |
| **DOCUMENTED DEFAULT** | A default recorded in project documentation; an engineering proposal, not an owner decision | Yes, but it must stay changeable and must not be described as approved |
| **TECHNICAL RECOMMENDATION** | Proposed by engineering (typically an ADR with status `proposed`); no owner input needed | Yes — these are engineering's call |
| **INFERENCE** | An interpretation drawn from available evidence | Only with the inference labelled |
| **OPEN OWNER DECISION** | A business decision still requiring explicit owner/accountant approval | No, for the part that is open |
| **CONFLICT** | Two or more authoritative sources disagree | No, until recorded and resolved |
| **UNSUPPORTED CLAIM** | A document presents something as confirmed without sufficient evidence | No |

**Rules applied.** An inference, default or recommendation is never promoted to OWNER-CONFIRMED.
Absence of objection is never treated as approval. A settled decision is reopened only on the four
grounds in §5.

---

## 3. Requirement and decision register

### 3.1 Owner-confirmed business decisions

| ID | Requirement / decision | Source | Class | Evidence & rationale | Affects | Consistent? | Follow-up |
|---|---|---|---|---|---|---|---|
| **F-01** ⚠️ **SUPERSEDED — see [Addendum 1](PHASE-2-CLOSURE-ADDENDUM-1.md)** | *(As audited)* Three billing action labels. **The owner has since specified FOUR**: `Print Bill`, `Credit Bill`, `Save a Draft`, `Clear Draft` | [OD-14](OWNER-DECISIONS.md#od-14) "✅ SETTLED"; FR-BILL-009, FR-BILL-013 | **OWNER-CONFIRMED** | Register records owner selection and says "Do not rename these buttons"; downgraded to `NON-BLOCKING`; UI acceptance strings may be frozen | FE billing, UI acceptance tests | **No** — see [CF-5](#cf-5) | Remove residual `Complete & Print` |
| **F-02** | `Save / Credit Bill` finalises a credit sale immediately through the same engine as cash; no mandatory credit-details step | [OD-21](OWNER-DECISIONS.md#od-21) "✅ CONFIRMED"; ADR-033 (**`accepted`**); FR-CREDIT-008, FR-CREDIT-020 | **OWNER-CONFIRMED** | The only `accepted` ADR in either repo, attributed to direct owner specification. 9 consistent assertions found | BE sales/credit, FE billing | **No** — see [CF-4](#cf-4) | Remove residual `CreditDetailsForm` references |
| **F-03** | Credit requires a persisted `customer_id` + non-empty `name`; **nothing else** is mandatory; supplied optional values are always persisted | FR-CUST-010, FR-CUST-011, FR-CREDIT-009, FR-CREDIT-012, FR-CREDIT-021/031; PRD §6.9.1 field table | **OWNER-CONFIRMED** (derived directly from OD-21's "no field becomes mandatory merely because the sale is on credit") | Field-by-field classification table is authoritative for both repos; `ck_invoices__credit_customer` enforces it in the DB | BE customers/sales, FE billing | **Yes** | — |
| **F-04** | Initial payment is **optional** on a credit sale and is not requested at save time | FR-CREDIT-025, FR-CREDIT-004; API-SPEC §3.3 (`payment` optional for `credit`) | **OWNER-CONFIRMED** | OD-21 item 6 and ADR-033 consequences | BE sales, FE billing | **Mostly** — see [CF-4](#cf-4) item (c) | Fix stale "part payment preview" test requirement |
| **F-05** | Printing is **not** sale finalisation; print failure/cancel/reprint never creates a second sale, number or movement | FR-PRINT-003, FR-SALE-022/023/024, E-8; ADR-014 | **OWNER-CONFIRMED** (cash) + **OWNER-CONFIRMED** (credit: no print at save, FR-CREDIT-029) | Reprint is a `GET`; no finalisation route is reachable from it. 7 consistent assertions | BE invoices, FE print | **Yes** | — |
| **F-06** | Payment collection never moves stock; it writes payment + allocation rows only | FR-CREDIT-010; API-SPEC §3.7; ADR-022 | **OWNER-CONFIRMED** | 7 consistent assertions; `POST /payments` has no inventory path | BE credit/inventory | **Yes** | — |
| **F-07** | No standalone Paid/Unpaid control anywhere; `payment_status` and `amount_paid` are derived | FR-CREDIT-003, FR-RPT-023; ADR-022; AC-FE-10 | **OWNER-CONFIRMED** (register frames it as the failure the owner is escaping) | 12 prohibitions, 0 toggles proposed; column grants restrict who may write the cache | BE credit, FE all lists | **Yes** | — |
| **F-08** ⚠️ **RENAMED — now `Save a Draft`; semantics unchanged** | *(As audited)* `Keep for later` creates/updates a draft only — no number, no stock, no invoice, no payment | FR-BILL-013, FR-DRAFT-005; OD-14 semantics table; ADR-006 | **OWNER-CONFIRMED** | Maps to `PUT /drafts/{id}` only; drafts never reserve stock | BE sales, FE billing | **Yes** | — |
| **F-09** | Credit invoices are grouped by stable `customer_id`; grouping is **visual only**; no per-customer table or directory | FR-RPT-028/029, FR-CREDIT-011; DATA-MODEL §3.9 "Deliberately rejected" | **OWNER-CONFIRMED** (OD-21 item 5 obligates the view) + **TECHNICAL RECOMMENDATION** (that it needs no new entity) | 7 assertions; satisfied by an indexed FK plus one aggregate endpoint | BE credit/reports, FE Credit Bills | **Yes** | — |
| **F-10** | Settled invoices remain accessible in history; a zero-balance customer still exists | FR-RPT-031; `include_settled` flag | **OWNER-CONFIRMED** | Nothing is deleted; `payment_status='paid'` distinguishes | BE credit, FE Credit Bills | **Yes** | — |
| **F-11** | Cash and credit print the **same** layout/template | FR-CREDIT-030, FR-PRINT-021; OD-24 rejection of option (c) | **OWNER-CONFIRMED** | Print payload is identical in shape; differs only in `payment.*` | FE print, BE print payload | **Yes** | — |
| **F-12** ⚠️ **SUPERSEDED — see [Addendum 1](PHASE-2-CLOSURE-ADDENDUM-1.md)** | *(As audited)* Amount display: minimum decimals that represent the value exactly; never pad, never truncate; no separators, no currency symbol. **The owner has since amended this: `PRICE` and `GRAND TOTAL` print NO decimals** | [OD-23](OWNER-DECISIONS.md#od-23) "✅ SELECTED PREFERENCE (b)"; FR-PRINT-017 | **OWNER-CONFIRMED** | Explicit worked table (`240.00`→`240`, `240.50`, `240.05`); downgraded to `NON-BLOCKING` | FE print/money format | **Yes** | — |
| **F-13** | Unused printed item rows render **blank**; the original's `0` is not reproduced *(verified 2026-10-30: those `0`s are hand-typed literals, not formula output)* | OD-23 item 3; FR-PRINT-018 | **OWNER-CONFIRMED** | | FE print | **Yes** | — |
| **F-14** ⚠️ **PARTLY SUPERSEDED — see [Addendum 1](PHASE-2-CLOSURE-ADDENDUM-1.md)** | *(As audited)* The printed bill **does** carry an invoice number; credit bills **do** carry payment/balance info; cash bills do **not**. **The invoice-number half is WITHDRAWN** by OD-3's re-answer; the credit payment block stands | [OD-22](OWNER-DECISIONS.md#od-22) "✅ SELECTED PREFERENCE (a)"; FR-PRINT-019/020 | **OWNER-CONFIRMED** *(inclusion only)* | Register is explicit that *whether* is settled and *where* is not; cash-bill payment block explicitly **NOT SELECTED** | FE print | **Yes** | Placement remains [OD-22](OWNER-DECISIONS.md#od-22) |
| **F-15** | Printed document label is `ESTIMATE` for cash **and** credit | [OD-24](OWNER-DECISIONS.md#od-24) "✅ SELECTED PREFERENCE (a)"; FR-PRINT-021 | **OWNER-CONFIRMED** *(wording preference)* — **not** a tax/legal approval | Register states this explicitly and keeps `BLOCKS-GO-LIVE` | FE print, BE `document_title` | **Yes** | OD-24 approval outstanding |
| **F-16** | On-screen table labels deliberately differ from printed labels; **do not align one to the other** | FR-BILL-003 (amended in Addendum 4); UI-UX §12.2; both `CLAUDE.md` | **OWNER-CONFIRMED** (print labels observed) + **TECHNICAL RECOMMENDATION** (keeping screen labels distinct) | The screen is not a spreadsheet replica; the bill is | FE billing + print | **Yes** | — |
| **F-17** | Offline finalisation is refused for **both** cash and credit; no offline queue for sales or payments | FR-OFFLINE-002; OFFLINE-SYNC §3.1 invariants 2, 2a, 2b; ADR-011; ADR-033 option 3 "rejected outright" | **OWNER-CONFIRMED** *(that credit is refused like cash — a consequence of OD-21)* + **OPEN OWNER DECISION** *(that the owner accepts the resulting shop limitation — [OD-8](OWNER-DECISIONS.md#od-8))* | Registry has exactly 3 operation types; a structural test asserts the absence | BE sync, FE offline | **Yes** | OD-8 is a go-live/training gate |
| **F-18** | `pending_finalizations` exists only for **uncertain outcomes of requests started online**, and retries reuse the **original** idempotency key | OFFLINE-SYNC §3.1 states 1/2/3 + §8; ADR-010; FR-SALE-026/027 | **TECHNICAL RECOMMENDATION** (hardened by OD-21's consequences) | The three-state distinction is the strongest part of the Phase 1 design; invariant 4 forbids treating uncertainty as permission to create a second sale | BE idempotency, FE queues | **Yes** | — |

### 3.2 Partially settled — the four Phase 2 module gates

| ID | What is settled | What is open | Class of the open part | Blocking reality |
|---|---|---|---|---|
| **F-19** / [OD-3](OWNER-DECISIONS.md#od-3) ⚠️ **SUPERSEDED — see [Addendum 1](PHASE-2-CLOSURE-ADDENDUM-1.md)** | *(As audited)* A **sequential** series, in-transaction allocation from a locked `invoice_series` row. **The owner has since withdrawn customer-visible invoice numbering entirely**; the reference is internal-only and the gapless question is **moot** | ~~Whether numbering must be **gapless**~~ — **moot**; replaced by **[OD-25](OWNER-DECISIONS.md#od-25)** (statutory requirement) | **OPEN OWNER DECISION** (statutory/accounting) | Register says `BLOCKS-IMPLEMENTATION`. **This audit challenges the blocking scope — see [CF-1](#cf-1) and `PHASE-2-DECISION-REGISTER.md` P2-D-02** |
| **F-20** / [OD-7](OWNER-DECISIONS.md#od-7) | Three binding principles: a documented policy must exist; the **backend is authoritative**; the client displays without changing meaning | Precision, rounding method, rounding stage, whole-rupee rounding, domain exceptions | **OPEN OWNER DECISION** (accounting) | Register says `BLOCKS-IMPLEMENTATION` (both repos). **Audit challenges the scope — see P2-D-03** |
| **F-21** / [OD-5](OWNER-DECISIONS.md#od-5) | ✅ **Entire bill `[VERIFIED]` 2026-10-30 by direct inspection of the workbook**: used range `A1:E37`, 12 merges, all column widths and row heights, Calibri typography with point sizes and the navy `#232852` headers, borders, number formats (built-in `0`), columns `S R · ITEM · QTY · PRICE · TOTAL`, **25** item rows, `GRAND TOTAL` row 34, the three footer lines, and both logo images. Signature block, amount in words, tax lines, paid/balance block and any invoice number are **verified absent** | 🔴 **Page setup — never set in the file**: no `pageSetup` element, no print area, no paper size, no orientation, no scaling. 🔴 **Date order**: the file says `mm-dd-yyyy`; the project had assumed `dd-mm-yyyy` | **LAYOUT: EVIDENCED.** Three earlier claims were **inferences, now corrected**: the `QT` header (actually **`QTY`**), the `DD-MM-YYYY` order, and the "formula-generated" zeros (the sheet has **no formulas**) | ✅ **No longer blocking.** Print-fidelity sign-off remains a go-live gate under [OD-6](OWNER-DECISIONS.md#od-6); date order under [OD-26](OWNER-DECISIONS.md#od-26) |
| **F-22** / [OD-22](OWNER-DECISIONS.md#od-22) | Inclusion of invoice number (all bills) and payment/balance block (credit only) | **Placement** of both | **UNVERIFIED REFERENCE DETAIL**, coupled to OD-5 | `BLOCKS-IMPLEMENTATION` for final placement; both elements must stay switchable without breaking the layout |

### 3.3 Open owner decisions — go-live gates

| ID | Topic | Class | Register's recommended default | Blocking level |
|---|---|---|---|---|
| **F-23** / [OD-1](OWNER-DECISIONS.md#od-1) | GST registration status / GSTIN | **OPEN OWNER DECISION** | Remain `estimate_only` | BLOCKS-GO-LIVE |
| **F-24** / [OD-2](OWNER-DECISIONS.md#od-2) | Tax rates, HSN, inclusive vs exclusive | **OPEN OWNER DECISION** | Tax-exclusive, **no rates seeded** | BLOCKS-GO-LIVE (only if OD-1 = registered) |
| **F-25** / [OD-4](OWNER-DECISIONS.md#od-4) | Legal shop identity for the header | **OPEN OWNER DECISION** | Fields stay empty; **print nothing** where empty; gate blocks release | BLOCKS-GO-LIVE |
| **F-26** / [OD-6](OWNER-DECISIONS.md#od-6) | Paper size and printer | **OPEN OWNER DECISION** | A5 portrait, 8 mm margin, switchable to A4 | BLOCKS-GO-LIVE |
| **F-27** / [OD-8](OWNER-DECISIONS.md#od-8) | Acceptance that offline = draft capture, not invoice issuing | **OPEN OWNER DECISION** | Option (a), the designed scope | BLOCKS-GO-LIVE (staff training) |
| **F-28** / [OD-19](OWNER-DECISIONS.md#od-19) | Hosting, **named** alert recipient, budget | **OPEN OWNER DECISION** | Managed platform, nearest region, named recipient | BLOCKS-GO-LIVE |
| **F-29** / [OD-24](OWNER-DECISIONS.md#od-24) | Final printed-document terminology | **OPEN OWNER DECISION** (wording preference already given) | Retain `ESTIMATE` | BLOCKS-GO-LIVE |

### 3.4 Documented defaults — safe, must stay changeable

Every item below is **NON-BLOCKING** in the register and is a `settings` row, not a hardcoded rule
(FR-ADMIN-004). Phase 3 may build on each, but **must not describe any as owner-approved**.

| ID | Default | Register | Setting key |
|---|---|---|---|
| F-30 | Drafts never reserve stock | [OD-9](OWNER-DECISIONS.md#od-9) / ADR-006 | — (design) |
| F-31 | Cancellation: manager + owner, 24 h window, reason mandatory | [OD-10](OWNER-DECISIONS.md#od-10) | `sales.cancel_window_hours = 24` |
| F-32 | Returns accepted, 30-day window, damaged → scrap | [OD-11](OWNER-DECISIONS.md#od-11) | `returns.window_days = 30` |
| F-33 | Credit terms per customer, default 30 days; limits **warn-only** | [OD-12](OWNER-DECISIONS.md#od-12) | `credit.default_terms_days = 30`, `credit.enforce_limit = false` |
| F-34 | Cashier discount cap 0% | [OD-13](OWNER-DECISIONS.md#od-13) | `discount.cashier_max_percent = 0` |
| F-35 | No archived PDF per invoice; regenerate on demand | [OD-15](OWNER-DECISIONS.md#od-15) | — (FR-PRINT-005 deferred) |
| F-36 | Reprints marked `DUPLICATE COPY` | [OD-16](OWNER-DECISIONS.md#od-16) | `print.mark_reprints_duplicate = true` |
| F-37 | Username + password, 30-day trusted device, optional PIN switch | [OD-17](OWNER-DECISIONS.md#od-17) | — |
| F-38 | One branch, 1–3 counters at launch; branch transfers out of MVP | [OD-18](OWNER-DECISIONS.md#od-18) | — |
| F-39 | Chrome/Edge, 1366×768 minimum, keyboard + mouse | [OD-20](OWNER-DECISIONS.md#od-20) / Q-FE-1/2/4 | — |
| F-40 | English only, strings externalised | Q-FE-5 | — |
| F-41 | Bill tab label = number + customer name | Q-FE-6 | — |
| F-42 | Credit sale needs no phone number (warn only) | Q-BIZ-5 | — |
| F-43 | Oversell permitted only with `inventory.oversell.override` + reason ≥ 10 chars | C-3 (**owner-confirmed**), FR-SALE-008 | `inventory.allow_oversell_override = true` |

> **F-43 note.** The *prevention* of overselling is OWNER-CONFIRMED (C-3). The *override mechanism's
> parameters* (who holds the permission, the 10-character minimum) are DOCUMENTED DEFAULTS.

### 3.5 Technical recommendations — engineering's own call, no owner input needed

ADR-001…005, 007, 008, 010, 012, 013, 016…019, 021, 022 (backend) and ADR-023…026, 028…032
(frontend) are **TECHNICAL RECOMMENDATION**, status `proposed`. This audit **endorses all of them
unchanged**; rationale is reviewed in `PHASE-2-INVESTIGATION-REPORT.md §4`. None is reopened.

ADR-006, 009, 011, 014, 015, 020, 027 carry "owner approval needed" and are **represented in the OD
register rather than duplicated** (OD-9, OD-3, OD-8, OD-5/6/15, OD-10, OD-1/2, OD-14 respectively).

---

## 4. Conflicts, unsupported claims and defects

Eleven findings. Each has an ID, a severity, the exact disagreeing sources, and the correction
required. **None is corrected by this audit** — corrections to authoritative documents are proposed
in `PHASE-2-DECISION-REGISTER.md` and listed in §6 below.

<a id="cf-1"></a>
### CF-1 — **CRITICAL** · "Gapless" is asserted as a settled requirement and a data invariant while OD-3 records it undecided

**The disagreement.**

| Source | Says |
|---|---|
| [OD-3](OWNER-DECISIONS.md#od-3) | *"**Gapless** … 🔴 **UNDECIDED — APPROVAL REQUIRED**"*; sequential ≠ gapless; `BLOCKS-IMPLEMENTATION` |
| `PRD.md` **FR-SALE-004** (P0) | *"…so committed numbers **are gapless** and a rolled-back attempt consumes no number"* |
| `PRD.md` **NFR-DATA-005** | *"Invoice numbers are unique **and gapless** per (branch, series, financial year)"* — a data invariant |
| `PRD.md` **AC-1** (go-live AC) | *"…and a **gapless** invoice number"* |
| `DECISIONS.md` **ADR-009** | Titled *"**Gapless** invoice numbers from a locked series row"*; **Decision: Option 1** (gapless) |
| `DATA-MODEL.md §4` | NFR-DATA-005 enforcement: unique constraint + in-transaction allocation + **gap-detection test** |
| `TESTING-STRATEGY.md §5` | Property: *"committed numbers are unique **and gapless**"* |
| `TRACEABILITY.md §3, §12` | `test_numbering_gapless_under_20_parallel`, `property/test_numbering_unique_gapless` |
| [OD-22](OWNER-DECISIONS.md#od-22) *(own text)* | *"The system allocates a **gapless** invoice number to every sale"* |
| `bmbackend/README.md` rule 5 | *"…so committed numbers are **gapless**"* |

So the requirement baseline, the invariant set, the acceptance criteria, the test plan, the
traceability matrix **and the OD register's own OD-22 entry** all treat gapless as settled, while
OD-3 — the authoritative register — records it as an undecided statutory question that blocks the
module.

**Classification.** The gapless property as currently written is an **UNSUPPORTED CLAIM** at the
requirement level (FR-SALE-004, NFR-DATA-005, AC-1 present it as confirmed), and a
**TECHNICAL RECOMMENDATION** at the design level (ADR-009, status `proposed`).

**Why this matters.** NFR-DATA-005 is a *financial data invariant* with a named invariant test and a
go-live acceptance criterion. Shipping an invariant the owner never agreed to is exactly the
confusion the register's four-level status vocabulary exists to prevent. Conversely, if gapless
*is* the intended design, OD-3 overstates the blockage.

**Material engineering finding — the asymmetry OD-3 misses.** OD-3 justifies
`BLOCKS-IMPLEMENTATION` with *"the allocation code and its concurrency tests differ materially
between gapless and gap-tolerant designs, so building one and switching later wastes the work."*
That reasoning is **only valid in one direction**:

- A **gapless** implementation (locked `invoice_series` row, allocated inside the transaction)
  **also satisfies** a gap-tolerant requirement. Gaplessness is strictly stronger.
- A **gap-tolerant** implementation (`SEQUENCE`) does **not** satisfy a gapless requirement.

Therefore building the gapless design now is **never wasted work**, whichever way OD-3 is answered.
What OD-3 genuinely blocks is **freezing NFR-DATA-005 and AC-1 as stated**, not writing the module.

**Correction required.**
1. Reclassify the *gapless* clause of **NFR-DATA-005** and **AC-1** as conditional on OD-3, or
   restate them as "unique and sequential, with gaplessness as the implemented default pending
   OD-3".
2. Amend **FR-SALE-004** so it states the mechanism (in-transaction allocation from a locked series
   row) as the requirement and gaplessness as its *consequence under the current default*.
3. Correct **OD-22**'s "Already confirmed" paragraph, which asserts gapless as fact.
4. Re-scope OD-3's blocking level from "the numbering module" to "the numbering **policy
   statement and its acceptance test**". **Requires owner acknowledgement** — proposed as
   `P2-D-02`, with the owner-facing question in `PHASE-2-OWNER-DECISIONS-REQUIRED.md`.

<a id="cf-2"></a>
### CF-2 — **HIGH** · The authoritative traceability matrix's P0 tally contradicts the exit-criteria evaluation

| Source | Backend P0 | Frontend P0 | Total |
|---|---|---|---|
| `TRACEABILITY.md §13A.3` | 101 | **88** | **189** |
| `PHASE-1-EXIT-CRITERIA.md` criterion 12 | 101 | **93** | **194** |
| `PHASE-1-CLOSURE-REPORT-ADDENDUM-4.md §2, §5` | 101 | 93 | 194 |
| **Counted in this audit** | **101** | **93** | **194** |

Criterion 12 cites `TRACEABILITY.md §13A` as its evidence, so the evidence document and the
evaluation that cites it disagree by 5 frontend requirements. Addendum 4 records correcting this
figure *once already* ("initially stated 195 P0 … the verified figures are 194"), but the matrix's
own `§13A.3` table was not updated with it.

**Substantive coverage is nevertheless intact.** This audit checked all 194 P0 IDs individually:
**every one appears in `TRACEABILITY.md`.** The "0 untraced" claim is true; only the self-reported
tally is wrong. This is a documentation defect, not a coverage gap — and it must not be reported as
one.

**Correction required.** Update `TRACEABILITY.md §13A.3` to 101 / 93 / 194 and note the recount.

<a id="cf-3"></a>
### CF-3 — **MEDIUM** · Four error codes are used normatively but absent from the registry declared "complete for v1"

The registry in `API-SPECIFICATION.md §4.2` contains exactly **49** codes (verified by count,
matching Addendum 4's "49/49"). These four are used in normative text but are **not in it**:

| Code | Used in | Required client behaviour |
|---|---|---|
| `PRECONDITION_REQUIRED` (**428**) | `API-SPECIFICATION.md §6`; `../../bmfrontend/docs/API-INTEGRATION.md §6` | "Client bug: report it and refetch" |
| `SKIPPED_AFTER_EARLIER_FAILURE` | `OFFLINE-SYNCHRONIZATION.md §4.3` | Retry after the user resolves the conflict |
| `CORRUPT_VERSION` | `OFFLINE-SYNCHRONIZATION.md §5.2` | Treated as `rejected`, logged |
| `DRAFT_ALREADY_CANCELLED` | `OFFLINE-SYNCHRONIZATION.md §5.4` | Offer "recreate as a new bill" so lines are not lost |

Additionally, **428 is absent from the status-code table in `§4.1`**.

**Why it matters concretely.** The frontend's `ErrorCode` union is *generated* from this registry
(ADR-029), feature code must handle it in an **exhaustive `switch`**, and a structural test asserts
every union member is handled and exercised (`../../bmfrontend/docs/TESTING-STRATEGY.md §8` rule 6).
A code that is required by the sync spec but missing from the registry can therefore **never be
handled** by compliant client code. This is a contract defect that would surface in Phase 3 as an
unhandled runtime branch in the offline path.

**Correction required.** Add the four codes to `§4.2`, add `428` to `§4.1`, and mirror into
`API-INTEGRATION.md §4`. Codes are additive-only, so this is a non-breaking change (§8).

<a id="cf-4"></a>
### CF-4 — **MEDIUM** · Residual references to the superseded credit-details design

ADR-033 and OD-21 state the mandatory credit-details page is *"removed from the documentation, not
merely deprecated"* and *"must not be reinstated."* Four residues remain:

| # | Location | Residue |
|---|---|---|
| (a) | `TRACEABILITY.md §5`, FR-CREDIT-001 | Frontend behaviour column reads **"`CreditDetailsForm` then the same submit"** — names a component from the removed design, in the authoritative matrix |
| (b) | `TRACEABILITY.md §5`, FR-CREDIT-004 | **"Payment field in the credit form"** — the credit form no longer exists |
| (c) | `../../bmfrontend/docs/TESTING-STRATEGY.md §4.5` | **"Terms prefill the due date; both editable. Part payment updates the outstanding preview live"** and "submits … with the payment inline" — contradicts FR-CREDIT-025 (payment not requested at save time) and FR-CREDIT-029 (no dialog). Same section elsewhere correctly asserts "no intermediate dialog is rendered" |
| (d) | `../../bmfrontend/docs/PRD.md` FR-NAV-007 and `UI-UX-SPECIFICATION.md §1` principle 6 | Modals reserved for *"**credit details**, conflict resolution, destructive confirmation, supervisor authorisation"* — reserves a modal for a step that no longer exists |

**Risk.** A Phase 3 session reading the traceability matrix for FR-CREDIT-001 would build
`CreditDetailsForm` — precisely the design the owner rejected. The matrix is the document most
likely to be used as a work list.

**Correction required.** Update (a)–(d) to the OD-21/ADR-033 flow; remove "credit details" from the
modal allow-list (the `test_modal_allowlist` structural test must not whitelist it).

<a id="cf-5"></a>
### CF-5 — **MEDIUM** · Residual `Complete & Print` label after OD-14 fixed the three labels

| Location | Residue |
|---|---|
| `OFFLINE-SYNCHRONIZATION.md §8` | Flow diagram opens with `user presses "Complete & Print"` — the refusal path for the **cash** action, which OD-14 fixed as `Print Bill` |
| `TRACEABILITY.md §3` FR-SALE-001 | Frontend behaviour: **"`Complete & Print` → one request"** |
| `TRACEABILITY.md §7` FR-OFFLINE-002 | **"`Complete & Print` refuses with the documented message"** |
| `DECISIONS.md` §Stubs, ADR-027 row | Titled *"A single `Complete & Print` action instead of two ambiguous buttons"* — **this is rejected option 2, not ADR-027's decision.** ADR-027 decided option 1: *keep both buttons*. The index entry misstates the ADR it indexes |

`Complete & Print` was the *considered-and-rejected* single-action design. OD-14 is SETTLED and says
UI acceptance strings may be frozen against the three labels — so these strings would make three
documents disagree with the frozen contract.

**Correction required.** Replace with `Print Bill` in the three flow/matrix locations; correct the
ADR-027 stub title to match its actual decision.

<a id="cf-6"></a>
### CF-6 — **MEDIUM** · The print specification still describes itself as a reconstruction from a written description

`../../bmfrontend/docs/UI-UX-SPECIFICATION.md §12` preamble:

> *"It is reconstructed from the owner's written description; **the real file was not available in
> Phase 1** and this must be checked against it before sign-off (`PRD.md §4.1`, Q-PRINT-1)."*

This is contradicted by the **same document's own top banner** and `§12.1`, which state the layout is
*transcribed from an inspected screenshot* and that the earlier reconstruction was *wrong in
specifics*. The stale sentence also cites `Q-PRINT-1`, which OD-5/OD-6 supersede.

The identical stale claim appears in `../../bmfrontend/docs/DECISIONS.md` ("Unresolved decisions"):
*"The print layout in `docs/UI-UX-SPECIFICATION.md §12` is a reconstruction and must be verified
against it."*

**Why it matters.** It understates the evidence actually held (the top section is `[OBSERVED]`) while
also obscuring the real remaining gap (the bottom section is `[UNSEEN]`). It could invite a future
session to "re-reconstruct" observed, evidenced detail.

**Correction required.** Replace both passages with the accurate partial-verification statement and
point at `§12.1b` / OD-5 / OD-22.

<a id="cf-7"></a>
### CF-7 — **LOW** · `bmbackend/README.md §6` contradicts itself on approval status

Line 119–120: *"**Nothing in that register has been approved.**"* — then the same section lists
*"**Settled 2026-10-29** … OD-21 credit workflow · OD-23 amount display · OD-14 action labels"*.

`OWNER-DECISIONS.md`'s header is the accurate formulation: three items settled by owner decision,
five partially settled, **and no item has received legal/tax/accounting approval**. The README
collapses "no legal approval" into "no approval of any kind".

**Correction required.** Adopt the register's wording.

<a id="cf-8"></a>
### CF-8 — **LOW** · `OWNER-DECISIONS.md` summary miscounts and misattributes the blocking set

Immediately after the summary table:

> *"**Two items** block implementation of a specific module (OD-3, OD-5, OD-7) plus one that blocks
> UI acceptance (OD-14). Everything else can be built now."*

Three defects in one sentence: it says "two" and names **three**; it **omits OD-22**, which the same
table marks `BLOCKS-IMPLEMENTATION`; and it says **OD-14** blocks UI acceptance when OD-14's own
entry records it as **SETTLED / NON-BLOCKING**.

The correct set is **OD-3, OD-5, OD-7, OD-22** — which is what `PHASE-1-EXIT-CRITERIA.md §4.1`,
Addendum 4 and both READMEs say.

**Correction required.** Fix the sentence to name four items and drop OD-14.

<a id="cf-9"></a>
### CF-9 — **LOW** · `NFR-DATA-002b` is an orphan invariant

`DATA-MODEL.md §4` defines **NFR-DATA-002b** (*"`invoices.subtotal` = Σ `invoice_items.line_total`"*).
It is **not** in `PRD.md §7.4`'s NFR-DATA list (which runs 001–010) and **not** in
`TRACEABILITY.md §12`'s invariant→test table. It is the only requirement-shaped ID in either
repository that is cited but never defined in a PRD.

It is a *genuine and important* invariant — `ck_invoices__total` covers the composition of `total`
from `subtotal`, but nothing covers `subtotal` against the line items, so without 002b a bill could
satisfy the DB `CHECK` while disagreeing with its own lines.

**Correction required.** Promote it into `PRD.md §7.4` and add a `TRACEABILITY.md §12` row with a
named test (`invariants/test_invoice_totals.py::subtotal_equals_line_sum`).

<a id="cf-10"></a>
### CF-10 — **LOW** · `DATA-MODEL.md` section ordering and table misplacement

`§3.14` (canonical identifiers) is printed **before** `§3.13` (tax), and the `jobs`,
`outbox_events` and `notifications` tables appear **nested under the `§3.14` heading** rather than
under `§3.12` (infrastructure tables) where they belong. Navigational defect in the authoritative
schema document; cross-references to "§3.14" for the oversell vocabulary still resolve.

**Correction required.** Reorder and move the three tables under `§3.12`.

<a id="cf-11"></a>
### CF-11 — **LOW** · Malformed link in `bmfrontend/README.md`

The OD-21 link in `bmfrontend/README.md §7` carried the `#od-21` fragment in its link **text** but
not in its **target**, so it landed at the top of a 1159-line register instead of at OD-21. (All
link targets in the workspace resolve; only this anchor was lost.)

**Correction required.** Move the `#od-21` fragment into the target. ✅ **Applied during Phase 2** —
see §6.

---

## 5. Settled decisions — preservation check

No settled decision was reopened by this audit. The four permitted grounds for revisiting were
tested against every OWNER-CONFIRMED item in §3.1:

| Ground | Triggered? |
|---|---|
| Conflicts with another authoritative requirement | **Only CF-1** — and the conflict is about the *classification* of gaplessness, not about any owner decision. OD-3's content is preserved |
| New evidence makes it technically unsafe or impossible | **No** |
| Violates a critical integrity, security or correctness invariant | **No.** F-01…F-18 are all integrity-*strengthening* |
| Material ambiguity prevents implementation | **No** for F-01…F-18. The genuine ambiguities are the already-recorded open parts of F-19…F-22 |

**Specifically preserved unchanged:** the three action labels (F-01); immediate credit finalisation
with one engine (F-02); minimum customer identity (F-03); optional credit payment (F-04); printing is
not finalisation (F-05); payments never move stock (F-06); no paid toggle (F-07); draft-only
semantics (F-08); `customer_id` grouping with no per-customer entity (F-09); shared print template
(F-11); the OD-23 display rule (F-12/F-13); inclusion-but-not-placement for OD-22 (F-14);
`ESTIMATE` for both types (F-15); and the offline refusal covering both sale actions (F-17).

---

## 6. Proposed corrections — summary

> **Two-pass history, recorded honestly.** In the **investigation pass** no authoritative document was
> modified: all eleven corrections were *recorded as proposals* (tasks T-01…T-04), and only CF-7 and
> CF-11 — both in non-authoritative README files already being updated as AI-context — were applied.
> In the **closure pass** every remaining correction was verified afresh and **applied** to the
> authoritative documents. §6.1 below is the final disposition record; the per-file log is in
> [`PHASE-2-CLOSURE-REPORT.md`](PHASE-2-CLOSURE-REPORT.md) §3.
>
> Each finding's **original problem statement and evidence are preserved verbatim above** — they are
> the justification for the correction and must stay readable, so §4 was not rewritten to pretend the
> defects never existed.

### 6.1 Final dispositions — all 11 findings ✅ **RESOLVED**

Every finding was re-verified before correction (not assumed from the earlier report), corrected in
**every** affected authoritative occurrence, then re-verified by the stated method.

| ID | Original problem | Files corrected | Validation method | Status |
|---|---|---|---|---|
| **CF-1** | Gapless numbering asserted as a confirmed P0 requirement, data invariant and go-live criterion in 11 places while OD-3 records it UNDECIDED | `PRD.md` (FR-SALE-004, NFR-DATA-005, **new AC-1a**), `DATA-MODEL.md §4`, `DECISIONS.md` (ADR-009 title + index + decision + rationale; ADR-027 stub), `ARCHITECTURE.md §5.4`, `TESTING-STRATEGY.md §5`, `TRACEABILITY.md` (§3, §12, §13), `OWNER-DECISIONS.md` (OD-3 blocking level + OD-22 assertion + summary), `bmbackend/README.md`, `bmbackend/CLAUDE.md`, `bmfrontend/docs/DECISIONS.md` | `grep -i 'gapless\|gap-free'` across all 38 files; every surviving hit is OD-3's own question/options, a labelled cross-reference, or a correction note — **no assertion remains** | ✅ **RESOLVED** |
| **CF-2** | Matrix tally said 88 frontend / 189 total; exit criteria said 93 / 194 | `TRACEABILITY.md §13A.3` | Recounted from `^\| FR-… \| P0 \|` rows: **101 + 93 = 194**; all 194 IDs checked individually against the matrix — every one present | ✅ **RESOLVED** |
| **CF-3** | 4 codes required by the sync spec were absent from a registry declaring itself complete; `428` absent from the status table | `API-SPECIFICATION.md §4.1` + `§4.2` + **new `§4.2a`** (status, emitter, semantics, client behaviour per code); `bmfrontend/docs/API-INTEGRATION.md §3.3, §4` | Registry recount = **53**; all 4 present in both the backend registry and the frontend §4 table | ✅ **RESOLVED** |
| **CF-4** | `TRACEABILITY.md §5` named `CreditDetailsForm` / "Payment field in the credit form" — the design ADR-033 forbids reinstating | `TRACEABILITY.md §5` (FR-CREDIT-001, FR-CREDIT-004); warnings updated in `bmfrontend/CLAUDE.md`, `bmfrontend/README.md` | `grep CreditDetailsForm`: no occurrence remains that could read as an instruction; surviving mentions are **explicitly labelled rejected** per the closure brief | ✅ **RESOLVED** |
| **CF-5** | Residual `Complete & Print` after OD-14 froze the labels; ADR-027 stub stated the **rejected** option as the decision | `OFFLINE-SYNCHRONIZATION.md §8` flow, `TRACEABILITY.md §3` + `§7`, `DECISIONS.md` §Stubs ADR-027 row | `grep 'Complete & Print'`: 4 hits remain, **all** in rejected-option lists (OD-14 option (b), frontend ADR-027 options 2) — **preserved deliberately** so the rejected design stays explicable | ✅ **RESOLVED** |
| **CF-6** | Print spec called itself "reconstructed from the owner's written description", contradicting its own banner and §12.1 | `bmfrontend/docs/UI-UX-SPECIFICATION.md §12` preamble; `bmfrontend/docs/DECISIONS.md` "Also outstanding" | `grep 'reconstructed from the owner'`: only the audit's own quotation of the defect remains | ✅ **RESOLVED** |
| **CF-7** | `bmbackend/README.md §6` said "Nothing in that register has been approved" then listed three settled items | `bmbackend/README.md §6` | Re-read: now matches the register's own four-level wording | ✅ **RESOLVED** *(applied in the investigation pass)* |
| **CF-8** | Summary said "Two items" then named three, omitted OD-22, and wrongly listed OD-14 as blocking | `OWNER-DECISIONS.md` summary | Re-read: names **four** (OD-3, OD-5, OD-7, OD-22), drops OD-14, and records the re-scoping | ✅ **RESOLVED** |
| **CF-9** | `NFR-DATA-002b` defined only in `DATA-MODEL.md §4` — absent from `PRD.md §7.4` and untraced | `PRD.md §7.4` (promoted with rationale), `DATA-MODEL.md §4` (back-reference), `TRACEABILITY.md §12` (traced row) | ID cross-check: **0** cited-but-undefined `FR-*`/`NFR-*` IDs remain across all 38 files | ✅ **RESOLVED** |
| **CF-10** | `§3.14` printed before `§3.13`; `jobs`/`outbox_events`/`notifications` nested under `§3.14` | `DATA-MODEL.md` — three tables moved into `§3.12`, tax renumbered `§3.15` | Section order now monotonic: 3.9 → 3.10 → 3.11 → 3.12 → 3.14 → 3.15. **`§3.14` still denotes canonical identifiers**, so all existing cross-references stay valid | ✅ **RESOLVED** |
| **CF-11** | OD-21 link carried its anchor in the text, not the target | `bmfrontend/README.md` | Anchor validator: all fragments resolve | ✅ **RESOLVED** *(applied in the investigation pass)* |

**Owner input still outstanding on CF-1 — but it no longer blocks anything.** `OD-3-A` asks the owner
to acknowledge that work proceeds on gap-free-as-default. Because the misclassification is corrected
and the in-transaction design satisfies either answer to OD-3, the acknowledgement is now a courtesy
confirmation rather than a gate — see
[`PHASE-2-OWNER-DECISIONS-REQUIRED.md`](PHASE-2-OWNER-DECISIONS-REQUIRED.md) item 2a.

**No finding was closed on the basis of a partial fix.** For each, the whole corpus was re-scanned
rather than only the location named in the original finding.

---

## 7. Audit verdict

| Question | Answer |
|---|---|
| Does the documentation accurately represent the recorded owner decisions? | **Substantially yes.** All 18 owner-confirmed items in §3.1 are recorded accurately and consistently in the register, and the four-level status vocabulary is applied rigorously in `OWNER-DECISIONS.md` itself |
| Are owner-confirmed decisions distinguishable from recommendations? | **Yes — after closure.** CF-1 was the significant failure: a documented default had been promoted to a P0 requirement, a data invariant and a go-live acceptance criterion. **Corrected in every affected document** (§6.1). The recurrence risk is tracked as R-32 and re-checked at each milestone exit |
| Any fabricated approval, date, approver or sign-off? | **None found.** Re-tested across all 28 files. Criterion 14 still passes |
| Any settled decision silently changed? | **None found** |
| Is P0 traceability complete? | **Yes — all 194 P0 IDs are traced**, verified ID-by-ID. The matrix's tally was wrong (CF-2) and is **now corrected** to 101 / 93 / 194 |
| Do links, anchors and IDs resolve? | **Yes.** 207/207 link targets resolve; 24/24 OD anchors and all ADR anchors exist; of 319 cited `FR-*`/`NFR-*` IDs, exactly one (`NFR-DATA-002b`, CF-9) lacks a PRD definition |
| Is the documentation sufficient to plan Phase 3 against? | **Yes.** All eleven corrections are applied, so **no milestone is blocked on documentation**. The remaining owner decisions are bounded in `PHASE-2-OWNER-DECISIONS-REQUIRED.md` and each has an explicit approval gate |

**Honest summary.** The Phase 1 documentation set is of unusually high quality: the safety
invariants are consistent across 28 documents, the offline three-state model is rigorous, and the
register's separation of *preference* from *approval* is maintained with discipline. The defects
found were real but narrow — one requirement-classification error with genuine consequences (CF-1),
one contract-registry omission that would break a client rule (CF-3), one stale work-list entry that
could rebuild a rejected design (CF-4), and eight hygiene items. **All eleven are now corrected.**
**No owner decision was misrepresented, and no approval was invented** — in either pass.

**What this audit still cannot do.** It verified internal consistency, classification honesty and
cross-document agreement. It **cannot** confirm that an item recorded as `SELECTED PREFERENCE` was in
fact selected by the owner, because no conversation transcript exists. That limitation is unchanged
by closure and is stated at the top of this document.

---

## 8. Related documents

[`PHASE-2-INVESTIGATION-REPORT.md`](PHASE-2-INVESTIGATION-REPORT.md) ·
[`PHASE-2-DECISION-REGISTER.md`](PHASE-2-DECISION-REGISTER.md) ·
[`PHASE-2-OWNER-DECISIONS-REQUIRED.md`](PHASE-2-OWNER-DECISIONS-REQUIRED.md) ·
[`PHASE-2-TRACEABILITY-MATRIX.md`](PHASE-2-TRACEABILITY-MATRIX.md) ·
[`OWNER-DECISIONS.md`](OWNER-DECISIONS.md) ·
[`PHASE-1-EXIT-CRITERIA.md`](PHASE-1-EXIT-CRITERIA.md)
