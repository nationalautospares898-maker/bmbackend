# Phase 2 Design Completion Addendum — 2026-10-30

**Status:** Phase 2 design is complete for transition to Phase 3 implementation. This dated addendum updates the living requirements, design and planning records by reference; it does not rewrite the Phase 1 closure reports, the Phase 2 closure report, or either earlier Phase 2 closure addendum. It records owner instructions supplied directly for this work, engineering defaults, and external gates separately. It does not assert legal compliance or physical printer acceptance.

## 1. Decision status and authority

The user-supplied owner-approved product requirements for this completion are: no customer-visible invoice number; retain an internal sale reference; preserve the original National Auto Spares bill layout; render `PRICE` and `GRAND TOTAL` without decimal places; and provide exactly four primary actions: `Print Bill`, `Credit Bill`, `Save a Draft`, and `Clear Draft`. The settled sale, credit, draft, payment, inventory atomicity, idempotency, recovery, and offline rules remain in force. This addendum treats these as requirements, without adding approval to historical records.

Decision labels: **confirmed requirement** means directly supplied by the owner; **design default** means a complete engineering proposal with rationale and measurable acceptance criteria, not an attributed owner approval; **external gate** means evidence or authority unavailable to this design pass. Phase 3 may implement confirmed requirements and the documented defaults. A default may be changed before its milestone exit through a recorded decision and updated tests. External gates block the release activity they govern, not unrelated Phase 3 design or implementation.

## 2. Resolved owner decisions and remaining external gates

| Item | Phase 2 design disposition | Phase 3 / external boundary |
|---|---|---|
| OD-3 / OD-25 numbering | No customer-visible number in any print or customer-facing list/confirmation. Keep UUID and in-transaction internal sale reference for audit, support, reconciliation and idempotency recovery. Remove gaplessness as a current business requirement. | OD-25 remains a hard pre-go-live legal/accounting gate. Do not issue a document represented as a compliant tax invoice until qualified advice confirms the applicable numbering, tax and document rules. If a visible statutory serial is required, obtain owner authorization and update the product requirement before enabling it. No legal conclusion is made here. |
| OD-7 / OD-23 money | Use the complete v1 policy in §3 below as the Phase 3 design default. OD-23's zero-decimal display requirement is confirmed. The related line-total display follows the workbook's zero-decimal format. | Values and fractional-input restrictions are explicitly a design default, not an owner/accountant approval. Record any owner/accountant change before M4 exit; do not silently change policy. |
| OD-26 date | Match workbook format `MM-DD-YYYY`, with a single configurable format source. API/storage use an unambiguous date value; the bill mirrors the workbook's visible order. | M27's sample must confirm that owner/staff interpret the order correctly. A requested format change is a documented product revision. |
| OD-22 credit block | For credit bills only, place a compact `Paid / Balance / Due` block immediately after `GRAND TOTAL` and before `THANK YOU  VISIT AGAIN`; it is absent on cash bills. This is a proposed placement that uses the only natural adjoining space without moving the item table or logos. | M27 compares the rendered sample with the reference and records owner acceptance. The block remains configuration-switchable until accepted. |
| OD-5 reference bill | The supplied workbook is the authoritative layout reference. Preserve all observed fields, geometry, styling, empty-row treatment and logos as specified by the workbook inspection. | The workbook does not configure print page setup. Its original bytes remain unchanged. |
| OD-6 printer and paper | Engineering default: browser printing on plain A5 portrait, `@page` margin 8 mm, 100% scale, fit one page, preserve aspect/column proportions; provide A4 portrait as a selectable fallback. The 92 mm × 176 mm measured content fits within A5's 132 mm × 194 mm area at those margins. | Actual printer, driver, paper and physical sample are Phase 3 M27/M28 acceptance inputs. Thermal and pre-printed stock require a separately approved template. |
| OD-14 actions / OD-21 credit | Four labels and semantics in §4 are binding. Credit finalizes immediately through the same atomic endpoint as cash, requires a persisted customer, does not require payment, and does not open a print dialog. | These flows are implemented and tested in Phase 3. No fifth primary action or mandatory credit details flow. |
| OD-1/2/4/8/19/24 and operational approvals | Keep safe technical defaults: tax remains `estimate_only`; no invented business identity or tax values; offline finalization remains prohibited; existing alerting, hosting and document terminology gates stay visible. | Owner/accountant, hosting recipient/budget, staff training and business identity remain release gates. Do not fabricate approvals. |

## 3. Complete v1 money policy (OD-7 + OD-23)

This is the default for implementation and test design. It separates the five concerns that prior records mixed together.

1. **Input validation.** Accept decimal strings only; reject exponent notation, non-finite values, negative amounts where prohibited, excess precision, and unknown fields. Unit price, monetary discounts, payments, and every finalized monetary amount must be an exact whole number of INR for the v1 bill format. Quantities use the unit's configured scale (integer units remain integer); the server accepts a sale line only when its post-discount, pre-tax extension is an exact whole INR. Reject fractional monetary inputs/results with a typed validation error before finalization. Never coerce, round or truncate an input to make it fit the print format. The tax-disabled v1 flow computes no tax; tax enablement remains separately gated.
2. **Calculation precision.** Use decimal arithmetic only (`Decimal` / exact decimal library), never binary floating point. Preserve declared quantity precision. Multiply quantity by unit price and apply permitted discounts in exact decimal arithmetic. Under the v1 default, a line whose final monetary result is fractional INR is rejected; no implicit rounding occurs. Invoice totals equal the exact sum of accepted whole-INR line totals plus any explicitly permitted whole-INR adjustments. Payment allocation and outstanding balance use exact decimal arithmetic and must reconcile exactly to the invoice amount.
3. **Stored values.** Keep monetary columns as fixed-point `NUMERIC(14,2)` for schema consistency and future compatibility, but v1 issued-sale, payment, allocation and credit amounts are constrained by service validation to integral INR (`value = trunc(value)`). Persist immutable snapshots of inputs, results, currency (`INR`) and policy version. Do not rewrite historical records when settings change. A schema migration is not authorized by this design addendum.
4. **Rounding.** No line, invoice, payment, allocation or balance rounding is performed in v1; the rounding adjustment is exactly zero. This is a deliberate policy choice that satisfies the zero-decimal bill requirement without concealing paise. If the owner/accountant later authorizes paise or whole-rupee rounding, specify the mode, stage and visible reconciliation line first, version the policy, and add migration only if the chosen data model requires it. Never enable whole-rupee rounding implicitly.
5. **Display.** Render `PRICE`, line `TOTAL`, `GRAND TOTAL`, and the credit `Paid / Balance / Due` amounts as ungrouped integer strings with no decimal places or currency symbol, matching workbook number format `0`. Empty item rows remain blank (the workbook's literal zeros are not repeated, per settled OD-23). The backend is authoritative; the frontend formats only validated integral values and never rounds. A non-integral value reaching the print renderer is a blocking error and must prevent handing out a misleading bill.

**Why this default:** it is the only v1 rule that simultaneously honors the owner's zero-decimal requirement, preserves exact values, and avoids displaying a rounded figure as though it were exact. It is intentionally conservative about fractional prices/results. Acceptance for M4: input/property tests cover whole INR, valid fractional quantities producing whole-INR extensions, rejected fractional prices/discounts/line results/payments, exact reconciliation, immutable policy snapshots, no floating point, and formatting that round-trips without changing the value. Owner/accountant changes require a written replacement policy before the M4 exit gate; this addendum does not claim their approval.

## 4. Bill, actions, and print profile

### Bill and date

Use the observed `Table 1` range A1:E37: title and contact rows; customer/mechanic/vehicle/date block; `ESTIMATE`; `S R | ITEM | QTY | PRICE | TOTAL`; 25 item rows; `GRAND TOTAL`; thank-you and dealer-credit footer; TATA left and Ashok Leyland right logos. Match the source's column proportions 4.664:19.777:3.332:8:10.441, measured row heights, Calibri typography, navy `#232852` headings, borders, merges, alignments, white background, and footer positions. Do not add a visible invoice number, tax/discount line, signature, or amount-in-words. Keep unused rows blank. Format dates `MM-DD-YYYY` based on workbook cell E7's `mm\-dd\-yyyy;@` number format. Use ISO `YYYY-MM-DD` in API contracts and store a date/timestamp with explicit business timezone; only the print formatter emits the workbook order.

### Proposed browser print profile

`@page { size: A5 portrait; margin: 8mm; }`; one page; 100% scale; no browser headers/footers; print content approximately 92 mm wide × 176 mm high; preserve measured aspect and column ratios; no forced page breaks. Ensure screen preview is not used as print output. Keep an A4 portrait profile selectable without changing field order or typography. Physical validation must include default-browser print dialog, the intended shop printer/driver, a representative 25-row bill, one long item name, cash and credit examples, both logos, no clipping, readable text, and signed owner acceptance. If A5 cannot be produced reliably, record the measured failure and obtain the owner's paper choice before changing the default.

### Credit information placement

On credit bills only, render `Paid`, `Balance`, and `Due` in one compact block directly below the `GRAND TOTAL` row and before `THANK YOU  VISIT AGAIN`. Derive all values from the finalized invoice and payment allocations; do not calculate balances in the client or print template. The block must fit in the A5 vertical allowance (about 18 mm remaining at the proposed margin); if it does not, fail the print acceptance check instead of overlapping the footer. Cash bills omit this block. It stays switchable off until reviewed and signed at M27.

### Four actions and invariant mapping

| Primary action | Contract |
|---|---|
| `Print Bill` | Online only; atomically finalize cash sale, stock movement and payment once; open print only after confirmed success. Print failure never rolls back the sale; reprint never finalizes. |
| `Credit Bill` | Online only; immediately finalize through the same idempotent sale endpoint; require a persisted customer; payment optional; atomically record invoice, stock and opening balance; no print dialog. |
| `Save a Draft` | Persist editable draft only; no invoice, payment, credit balance or stock movement; safe draft outbox only while offline. |
| `Clear Draft` | Confirm and clear the current editable draft only; disabled while finalization is unconfirmed; never removes a finalized sale or loses an idempotency key. |

No fifth primary action. Preserve the settled no-offline-finalization rule, safe draft sync queue, lost-response arbitration, immutable invoices, exact idempotency fingerprint, and stock/payment atomicity.

## 5. Full remaining-work classification

### Owner decisions

| Decision group | Classification now | Status meaning |
|---|---|---|
| OD-1, OD-2, OD-25 tax/document legality | (c) external qualified owner/accountant gate | No statutory or tax conclusion; tax invoice issuance and production remain blocked until evidence is recorded. |
| OD-3 numbering preference | Resolved by confirmed product requirement | No visible numbering; internal reference retained. Its old gapless branch is historical and no longer gates M9. |
| OD-4 business identity | (c) owner-supplied fact | Never invent legal name, address or phone. |
| OD-5 bill reference | Resolved from the original workbook | Workbook inspected; historical claim that it was unavailable is superseded for current design. |
| OD-6 paper/printer | (b) A5 profile default; physical details are (c) | Design may start; printer/sample/sign-off remain M27/M28 acceptance. |
| OD-7/OD-23 money | (b) complete default in §3; zero-decimal display is confirmed | Not owner/accountant approval. Replacement policy must be written before M4 exit if changed. |
| OD-8 offline expectations | Technical behaviour is resolved: drafts only, no sale finalization | Owner acknowledgement/training remains a go-live gate; preserve the existing exact offline rules. |
| OD-9 through OD-13, OD-15 through OD-18, OD-20 | Existing documented defaults reviewed; retain unless contradicted by explicit requirements | Defaults are engineering assumptions; validate any safety-sensitive operational value in M28. |
| OD-14 / OD-21 four actions and credit flow | Resolved by explicit owner requirements | Implement exactly as §4. |
| OD-19 / OD-19-A hosting and alerts | (c) owner/operations inputs, with technical default already documented | Named alert recipient, budget, escalation expectation and runbook remain M26/M28 gates. |
| OD-22 credit block | (b) placement default in §4 | Sample acceptance is external at M27; no invoice-number placement exists. |
| OD-24 document label | Existing `ESTIMATE` safety default retained | Owner/accountant terminology confirmation remains go-live gate. |
| OD-26 date | Resolved as workbook-faithful `MM-DD-YYYY` | Test non-ambiguous date (e.g. 08-07-2026 for Aug 7) and sign off sample. |

### P2-D decisions

P2-D-01 through P2-D-10 were inspected. Retain accepted technical choices for documentation ownership, the money policy object, source hierarchy, import boundary linting, idempotency claim ordering/canonicalization, uncertain-finalization edit lock, lazy FY series, and settings resolution. Amend P2-D-02's scope: there is no current customer-visible series or gapless requirement, so OD-3 acknowledgement and gapless acceptance tests are retired for current scope; internal reference allocation remains as specified. P2-D-03 remains accepted as the mechanism and now uses §3 values as a documented design default. Acceptance labels mean engineering design status only, never implementation completion or owner approval.

### Risks R-01–R-37

Retain every risk ID and its existing mitigation/control design. Reclassify R-07 as **design default specified; accountant/owner policy change remains a pre-M4-exit decision**, not a Phase 2 gap. Retire R-14 as a current customer-visible gapless-number risk; preserve it as a contingency only if OD-25 later requires visible serials. R-36 is an external legal go-live gate and must remain open until qualified advice is recorded. R-37 is mitigated by §3 input constraints and render-blocking validation; test at M4/M20. R-19, R-24, R-25, R-27, R-33, R-34 and R-35 retain their operational, security, restore, print, compliance and data-load gates. The other risks remain controlled by their existing architecture, tests and milestone exit criteria. Do not describe a designed control as implemented or tested.

### Phase 3 tasks T3-01–T3-12

All twelve are reviewed and fully specified for implementation; their completion status remains **not implemented**. Preserve T3-01 through T3-07 and T3-09 through T3-12 acceptance criteria. Amend T3-08 to implement §3: exact integer-INR validation, no rounding, no-decimal display, and blocking on an invalid fractional value (not a non-blocking warning or silent display). T3-10 uses the complete workbook layout and profile in §4; M27 physical comparison is an acceptance milestone. T3-11 uses `MM-DD-YYYY` from E7. T3-12 embeds the two workbook logos at measured size and footer positions; trademark authorization remains the owner's responsibility. M27 is implementation-ready; its physical print/owner sign-off is a Phase 3 acceptance gate.

### Milestones M1–M28

All 28 milestones now have a complete design baseline, identified dependencies and acceptance evidence; this means **design-ready to enter Phase 3**, not built, passed, or ready for production. M1–M26 retain their existing scope and sequence, with M4 using §3's policy default. M27's template is no longer blocked on missing workbook, placement or date decisions: implement the proposed A5 profile and credit block; complete physical printer/sample sign-off during Phase 3. M28 remains a production gate, not a Phase 2 design blocker: legal/tax approvals, owner facts, named operations, training, data load, security and restore evidence must be collected before go-live. Phase 3 must not claim M27/M28 acceptance before evidence exists.

## 6. Cross-document changes required

This addendum is the dated cross-cutting source for the disposition above. The following living records must point to it and adopt the applicable normative detail: `OWNER-DECISIONS.md`, `PRD.md`, `DATA-MODEL.md`, `API-SPECIFICATION.md`, `ARCHITECTURE.md`, `OFFLINE-SYNCHRONIZATION.md`, `TESTING-STRATEGY.md`, `PHASE-2-DECISION-REGISTER.md`, `PHASE-2-OWNER-DECISIONS-REQUIRED.md`, `PHASE-2-RISK-REGISTER.md`, `PHASE-2-IMPLEMENTATION-BLUEPRINT.md`, `PHASE-2-IMPLEMENTATION-PLAN.md`, `PHASE-2-TRACEABILITY-MATRIX.md`, `TRACEABILITY.md`, both frontend PRD/API/UI/architecture/testing/decision files, both READMEs, and both `CLAUDE.md` files. Existing closure reports remain immutable historical snapshots.

## 7. Phase boundary and acceptance

Phase 2 design completion means no avoidable engineering decision remains unanswered for the agreed v1 scope. Phase 3 implements and tests these contracts without adding schema migrations in this documentation pass. It includes the physical print comparison, offline/recovery drills, backup restore, monitoring, deployment rehearsal and all milestone evidence. Legal compliance, owner-supplied business identity and operational approvals remain external gates; no document issued by the system may be represented as legally compliant until those gates are cleared. The workbook's original SHA-256 observed for this pass is `D1DA39227566F7A342C44EFDBC2E6E03862269DD8ED784E56427E331D11C9410` (19,114 bytes); this addendum made no workbook writes.
