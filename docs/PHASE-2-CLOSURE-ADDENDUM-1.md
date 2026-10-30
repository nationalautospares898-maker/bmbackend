# Phase 2 Closure — Addendum 1: Owner Answer Set 4, Bill Evidence and Repository Identity

| Field | Value |
|---|---|
| Status | **Durable project record.** A dated addendum to [`PHASE-2-CLOSURE-REPORT.md`](PHASE-2-CLOSURE-REPORT.md) |
| Date | **2026-10-30** |
| Purpose | Integrate the owner's fourth answer set, reconcile every affected decision and document, verify repository identity, and re-evaluate Phase 2 closure |
| Supersedes | **No report.** The closure report and all Phase 1 reports/addenda remain **unedited and byte-identical**. Where a decision changed, the change is recorded here and in the authoritative documents |
| Verdict | # `PHASE 2 REMAINS CLOSED` |
| Implementation performed | **None.** No application code, migration, dependency manifest, CI workflow, executable test, Git operation or deployment |

---

## 1. The four owner answers, as received

| # | Owner input | Treated as |
|---|---|---|
| 1 | *"No need of invoice numbering."* | **New owner decision**, superseding OD-3's sequential-numbering preference — bounded by a new legal gate |
| 2 | The original bill workbook `ANAS_1234_20260606_231456.xlsx`, with its complete visible design described | **Reference evidence for content.** ⚠️ The file itself is **not in the workspace** — see §4 |
| 3 | Amounts must not have decimal places in `PRICE` or `GRAND TOTAL` | **Display/formatting requirement only.** Amends OD-23; does **not** touch OD-7 |
| 4 | Four primary actions: `Print Bill`, `Credit Bill`, `Save a Draft`, `Clear Draft` | **New owner decision**, re-answering OD-14 |

---

## 2. Decision reconciliation

Every owner decision was re-checked. **Only those the new evidence actually answers were changed.**

| OD | Previous status | New status | What changed |
|---|---|---|---|
| **OD-3** | 🟡 Sequential selected; gapless UNDECIDED → `BLOCKS-TESTING` | ✅ **RE-ANSWERED** → `NON-BLOCKING` | **No customer-visible invoice numbering.** Gapless question **moot**; `AC-1a` **withdrawn**; internal reference retained |
| **OD-5** | 🔴 Lower section `[UNSEEN]`; owner could not supply evidence | 🟡 **Lower section `[OWNER-DESCRIBED]`** → `BLOCKS-GO-LIVE` | Content now known (grand total, thank-you line, dealer credits, logos, **25 rows**). ⚠️ **Geometry, page setup, logo files and `QT`/`QTY` still unverified** |
| **OD-7** | 🟡 Principles selected; precision/method UNDECIDED → `BLOCKS-TESTING` | 🟡 **UNCHANGED** | The owner answered **display**, not **calculation**. **Deliberately not marked resolved** |
| **OD-14** | ✅ Settled — three labels | ✅ **RE-ANSWERED — four actions** | `Save / Credit Bill` → `Credit Bill`; `Keep for later` → `Save a Draft`; discard **promoted** to primary `Clear Draft` |
| **OD-22** | 🟡 Inclusion selected; placement unverified → `BLOCKS-TESTING` | 🟡 **PARTLY MOOT** → `BLOCKS-GO-LIVE` | Invoice-number half **withdrawn** by OD-3. Credit payment/balance block still selected, **credit-only**, placement still open — the described footer has no slot for it |
| **OD-23** | ✅ Settled — minimum exact decimals → `NON-BLOCKING` | 🟡 **AMENDED** → `BLOCKS-GO-LIVE` | `PRICE`/`GRAND TOTAL` print **no decimals**. 🔴 **Conflict recorded** for fractional amounts — not resolved by rounding |
| **OD-24** | 🟡 `ESTIMATE` preference; not a legal approval | 🟡 **UNCHANGED** | Preserved exactly, including its terminology boundary |
| **OD-25** | *(did not exist)* | 🔴 **NEW — OPEN** → `BLOCKS-GO-LIVE` | **Does law require a serially numbered tax invoice?** Created because OD-3's preference cannot settle the law |
| OD-1, OD-2, OD-4, OD-6, OD-8, OD-19 | Various | **UNCHANGED** | Nothing in the new evidence answers GST status, rates/HSN, legal identity, paper/printer, offline expectations or hosting/monitoring |
| OD-9…OD-13, OD-15…OD-18, OD-20, OD-21 | Safe on defaults / resolved | **UNCHANGED** | — |

**No approval was invented.** No approval date, approver name, accounting sign-off or legal review is
recorded anywhere. The register's four-level status vocabulary is applied throughout.

### 2.1 The legal boundary on OD-3 — stated plainly

**The owner's preference does not make omitting an invoice number lawful.** GST law generally
requires a consecutive serial number on a *tax invoice*. This system issues documents titled
`ESTIMATE` under `tax.mode = estimate_only` and **cannot** emit `TAX INVOICE` until the `FR-TAX-003`
gate passes — so the requirement may not bite today, but **that is an accountant's determination.**

**This is why the internal reference was retained rather than deleted:** if
[OD-25](OWNER-DECISIONS.md#od-25) answers "required", the compliant mechanism already exists,
race-safe and tested, and enabling it is **configuration plus rendering** — not a redesign. **No
compliance claim is made anywhere.**

### 2.2 The OD-23 conflict — recorded, not resolved

A zero-decimal display **cannot** represent `240.50` without changing what the customer is told.

- **Stored values are never altered for display** — money stays exact decimal end to end.
- If a stored amount carries paise, zero-decimal rendering would **misstate the bill**: printed
  `PRICE` × `QTY` would not equal printed `TOTAL`, and the column would not sum to `GRAND TOTAL`.
- **Recommended (not adopted): constrain prices to whole rupees at entry** — the only option that
  honours the instruction literally *and* keeps the bill arithmetically truthful. It is a
  **business-rule change** requiring the owner's approval.
- **Until then:** keep exact stored values, display zero decimals for whole amounts, and **raise a
  visible validation warning rather than silently rounding** (task **T3-08**).

---

## 3. The four actions — reconciled against the approved safety rules

| Action | Approved behaviour — **preserved unchanged** |
|---|---|
| **`Print Bill`** | Cash sale. Authoritative backend finalisation **before** printing. Invoice + item snapshots + stock movement + payment commit **atomically**. Reprint/print failure **never** creates a second sale |
| **`Credit Bill`** | Credit sale finalised **immediately** through the **same** engine, `sale_type: credit`. Requires a **persisted `customer_id` + name**. Stock updated **exactly once**. Payment optional at save; collecting payment later **never** moves stock again. Outstanding derived from allocations |
| **`Save a Draft`** | Draft only — **no** finalisation, stock movement, payment, credit invoice **or** customer-facing number. `PUT /drafts/{id}` only |
| **`Clear Draft`** | Clears the **editable draft** after confirmation naming the line count. **Never** deletes or reverses a finalised sale. **Unavailable while an unresolved `pending_finalizations` row exists** — it must never destroy an idempotency key that may already have produced a sale |

**Nothing in the recovery design was weakened.** The 12-case state machine, `AC-REC-1…11`, the
canonical-fingerprint specification, pending-finalisation persistence, the two separate client
queues, and the offline refusal for **both** finalising actions are all intact. `Clear Draft` is
**additive**: it is a new primary affordance for an action that already existed (FR-BILL-012), now
with an explicit prohibition against touching the recovery state.

---

## 4. What the original workbook confirms — and what was NOT verified

> ### ⚠️ The workbook was not inspected
>
> A filesystem search of the entire workspace found **zero non-Markdown files** and **no `reference/`
> directory**. `ANAS_1234_20260606_231456.xlsx` is **not present**. Therefore **no sheet, cell,
> formula, merged range, column width, row height, border, font, number format, print setting, print
> area, hidden row/column or embedded image has been read.**
>
> What is held for the lower section is the **owner's written description** — substantially more than
> the earlier screenshot, because it covers the footer for the first time, but it is **testimony, not
> file evidence**, and the two are not interchangeable.

**Now known (content) — previously `[UNSEEN]`:** `GRAND TOTAL` row · `THANK YOU VISIT AGAIN` ·
`Authorised Dealers of:` · `TATA - LEYLAND SPARE PARTS` · logos · **25 pre-numbered item rows**
(supersedes "≥22, exact count unknown").

**Still genuinely missing — requires the file:**

1. Exact column widths, row heights, merged ranges, fonts and point sizes, number formats.
2. **Defined print area, paper size, margins, orientation, scaling** — what determines page fit.
3. Hidden rows/columns and the **second sheet's** purpose (the owner says it is probably not part of
   the printed bill; **not assumed**).
4. **The logo image files**, which must be embedded.
5. 🔴 **`QT` vs `QTY`** — the inspected screenshot shows **`QT`**; the owner's description says
   **`QTY`**. The specification keeps **`QT`** (directly observed) and flags the discrepancy.
   **Not resolved by guessing** (task **T3-10**).
6. The owner's description includes **no signature block and no amount-in-words**. Earlier drafts
   listed both as `[UNSEEN]`. **They must not be invented.**

**➡️ To close OD-5 and T3-10:** place the workbook in the workspace (e.g.
`reference/owner-original-bill.xlsx`) together with the logo image files. **The owner's original file
must not be modified** — a normalised specification is kept separately in
`../../bmfrontend/docs/UI-UX-SPECIFICATION.md §12`.

---

## 5. New tasks

| ID | Task | Milestone(s) | Status |
|---|---|---|---|
| **T3-06** | Remove the customer-visible invoice number end to end | M12, M20 | Open |
| **T3-07** | Update credit confirmation and payment-history references | M19, M21 | Open |
| **T3-08** | No-decimal display without corrupting precision | M20 | Open — **gated on OD-23's fractional conflict** |
| **T3-09** | Implement the four primary actions and `Clear Draft` safety rules | M17, M18, M19 | Open |
| **T3-10** | Reconcile the full bill layout against the workbook; resolve `QT`/`QTY`; embed logos | M20, M27 | **Blocked — awaiting the file** |

Full fields in [`PHASE-2-IMPLEMENTATION-PLAN.md §1.2`](PHASE-2-IMPLEMENTATION-PLAN.md).

**T3-05 verified and unchanged** — still a low-priority frontend specification/test cleanup due
before **M19/M25** exit, not blocking either start, still dependent on completed T-02 and settled
OD-21/ADR-033. **T3-09 partly overlaps it** (both touch the modal allow-list), so T3-09 lands first
and T3-05 verifies the residue.

---

## 6. GitHub repository verification

| Check | Method | Result |
|---|---|---|
| Remote identity | Owner-supplied URLs | `https://github.com/nationalautospares898-maker/bmfrontend.git` · `https://github.com/nationalautospares898-maker/bmbackend.git` |
| Remotes exist and are readable | `git ls-remote --exit-code` (read-only, unauthenticated) | ✅ **Both exist and are publicly readable** |
| Remote contents | same | ✅ **Both are completely EMPTY** — zero refs (exit code 2 = "no matching refs"). Consistent with the Quick-setup screenshots |
| Probe method validity | Control probe against a deliberately bogus repository name | ✅ Returned `Repository not found`, proving the probe distinguishes "empty" from "absent" |
| Local folders | `find` over the workspace | ✅ Documentation only — **0 non-Markdown files**, **0 `.git` directories** |
| **Push authentication** | `gh auth status` | 🔴 **FAILED.** *"The token in default is invalid"* for account **`AshwinSharma-git`** |
| **Write permission** | — | 🔴 **UNKNOWN.** The authenticated account (`AshwinSharma-git`) is **not** the repository owner (`nationalautospares898-maker`). Even after re-authentication, write access must be confirmed |

### 6.1 What the owner must authorise

**No Git operation was performed** — no `init`, no commit, no remote configuration, no push. Because
both remotes are **empty**, there is no remote history to reconcile (requirement 10 is moot), and
**nothing will be overwritten**.

To proceed, **one** of:

1. **Re-authenticate the GitHub CLI** on this machine for an account with **write access** to
   `nationalautospares898-maker`: run `gh auth login -h github.com` interactively. *(The owner runs
   this; a token must never be pasted into chat.)*
2. **Grant the existing account access** — add `AshwinSharma-git` as a collaborator with write
   permission on both repositories, then re-authenticate.
3. **Use a credential helper / SSH key** already authorised for that account.

Then confirm explicitly that the first push is authorised. The intended, reviewable sequence is:

```text
per repository (bmbackend, bmfrontend):
  git init -b main
  git remote add origin <the matching URL>     # verified per folder, never crossed
  <add a .gitignore covering .env, secrets, build output>
  git add -A && git status                      # INSPECT the staged list before committing
  git commit -m "docs: Phase 1 + Phase 2 documentation baseline"
  git push -u origin main                       # requires the authorisation above
```

**Safeguards that will apply:** the remote URL is verified **per folder** so frontend can never point
at the backend repository; the staged file list is **inspected for secrets before** committing (the
workspace currently contains **no** `.env`, credential or binary file at all); **no** `--force`, no
history rewrite, no branch deletion; and **no new repository is created**.

---

## 7. Phase 2 closure re-evaluation

| Closure criterion | Still met? | Note |
|---|---|---|
| 1 Fidelity findings resolved | ✅ | CF-1…CF-11 unaffected. **CF-1's subject is now moot** — OD-3's re-answer removes the customer-visible serial entirely, which *strengthens* the correction |
| 2 Gapless misclassification removed | ✅ | Reinforced: gaplessness is now moot, not merely unapproved |
| 3 Sale-finalisation recovery designed | ✅ | Unchanged; `Clear Draft` adds an explicit prohibition against disturbing it |
| 4 FY series initialisation designed | ✅ | Unchanged — the series now allocates an internal reference |
| 5 Idempotency + fingerprinting | ✅ | Unchanged. Idempotency keys are **not** invoice numbers and were never affected |
| 6 Money policy coherent while OD-7 open | ✅ | Unchanged. The display amendment does not touch the policy object |
| 7 Critical/high risks addressed | ✅ | R-14 **fully closed**; **R-36** added (statutory numbering); R-33 narrowed |
| 8 Blueprint and milestones reflect corrections | ✅ | Readiness **recalculated**, not carried over: **24 ready / 2 conditional / 2 blocked** |
| 9 Owner register accurate, no fabricated approvals | ✅ | Three supersessions recorded explicitly; OD-25 added; nothing invented |
| 10 Traceability and consistency validated | ✅ | §9 below |
| 11 Documentation complete and linked | ✅ | This addendum linked from both repositories' AI context |
| 12 Unknowns bounded and cannot cause an unapproved policy to ship | ✅ | OD-23's conflict, OD-25 and T3-10 each carry an explicit gate; `Must not build yet` rows name what is forbidden |

**All 12 criteria still met. Phase 2 remains CLOSED.**

---

## 8. Risk register changes

| Risk | Change |
|---|---|
| **R-14** gapless invariant unapproved | ✅ **Fully closed.** The subject no longer exists |
| **R-16** per-counter series creating permanent gaps | ⬇️ **Reduced** to "re-check [OD-25](OWNER-DECISIONS.md#od-25) before enabling" — gaps are harmless for an internal reference |
| **R-33** inventing the unseen bill bottom | ⬇️ **Narrowed.** Content is described; the remaining exposure is **geometry, page setup, logos and `QT`/`QTY`** — and inventing *those* is still forbidden |
| **R-36** *(new)* **Statutory numbering exposure** | 🔴 **HIGH.** The shop may issue documents without a serial number that law requires. *Prevention:* internal mechanism retained so a compliant path exists; `estimate_only` default; no compliance claim made. *Detection:* accountant review. *Gate:* **[OD-25](OWNER-DECISIONS.md#od-25)**, `BLOCKS-GO-LIVE` |
| **R-37** *(new)* **Zero-decimal display misstating a fractional amount** | 🔴 **HIGH.** *Prevention:* exact stored values; **visible validation warning instead of silent rounding** (T3-08); recommended whole-rupee entry constraint. *Gate:* **[OD-23](OWNER-DECISIONS.md#od-23)** fractional conflict, `BLOCKS-GO-LIVE` |

---

## 9. Validation performed

Every check below was **actually executed** in this pass.

| Check | Result |
|---|---|
| Workbook presence | 🔴 **Absent** — 0 non-Markdown files, no `reference/` directory. **Not inspected, and not claimed to be** |
| GitHub remotes | ✅ Both exist, readable, **empty** (verified with a control probe) |
| GitHub push auth | 🔴 **Invalid token** for `AshwinSharma-git`; account is not the repository owner |
| Relative links | ✅ All resolve |
| Anchors | ✅ All resolve, including the new `#od-25` |
| OD anchors defined | ✅ **25** |
| Stale `Save / Credit Bill` / `Keep for later` in current docs | ✅ Replaced in the authoritative specifications; remaining occurrences are **labelled supersession records** or **immutable Phase 1 history** |
| Stale customer-visible invoice-number requirements | ✅ Retired in `PRD.md`, `DATA-MODEL.md`, `API-SPECIFICATION.md`, `ARCHITECTURE.md`, `TESTING-STRATEGY.md`, `TRACEABILITY.md`, frontend `PRD.md`, `UI-UX-SPECIFICATION.md` |
| Stale decimal-display assumptions | ✅ Amended in `OWNER-DECISIONS.md`, frontend `PRD.md` FR-PRINT-017, `UI-UX-SPECIFICATION.md §12.2` |
| Outdated credit-details form/modal language | ✅ Removed from `FR-NAV-007` and `UI-UX-SPECIFICATION.md §1`; residue tracked as **T3-05** |
| T3-05 still present and scoped | ✅ Verified in `PHASE-2-IMPLEMENTATION-PLAN.md §1.1`, with M19/M25 exit-gate wording intact |
| Phase 1 closure reports byte-identical | ✅ **5/5 unchanged** — 32891 / 17053 / 21206 / 18048 / 14057 bytes |
| Executable tests | **None exist and none were run.** Every test named is **planned** |
| Application code / migrations / manifests / CI workflows | **None created** |

---

## 10. What remains open

**Owner / accountant:**
[OD-25](OWNER-DECISIONS.md#od-25) statutory numbering *(new)* ·
[OD-23](OWNER-DECISIONS.md#od-23) fractional-amount conflict *(new)* ·
[OD-7](OWNER-DECISIONS.md#od-7) calculation precision and rounding ·
[OD-1](OWNER-DECISIONS.md#od-1) GST status · [OD-2](OWNER-DECISIONS.md#od-2) rates/HSN ·
[OD-4](OWNER-DECISIONS.md#od-4) legal identity · [OD-6](OWNER-DECISIONS.md#od-6) paper/printer ·
[OD-8](OWNER-DECISIONS.md#od-8) offline acceptance ·
[OD-19](OWNER-DECISIONS.md#od-19) hosting + named alert recipient (+ `OD-19-A` on-call expectation) ·
[OD-24](OWNER-DECISIONS.md#od-24) terminology approval ·
[OD-22](OWNER-DECISIONS.md#od-22) credit payment-block placement.

**Files / environment:** the workbook `ANAS_1234_20260606_231456.xlsx` **and** the logo images;
**working GitHub push authentication** with write access to `nationalautospares898-maker`.

**Operational (unchanged):** demonstrated restore · monitoring to a named person · printed-sample
sign-off · staff training · opening stock loaded and physically verified · seeded credentials
rotated · full-disk encryption on counter machines · rollback rehearsed · break-glass stored outside
the system.

---

## 11. Related documents

[`PHASE-2-CLOSURE-REPORT.md`](PHASE-2-CLOSURE-REPORT.md) *(unedited)* ·
[`OWNER-DECISIONS.md`](OWNER-DECISIONS.md) *(authoritative)* ·
[`PHASE-2-IMPLEMENTATION-PLAN.md`](PHASE-2-IMPLEMENTATION-PLAN.md) ·
[`PHASE-2-IMPLEMENTATION-BLUEPRINT.md`](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) ·
[`PHASE-2-RISK-REGISTER.md`](PHASE-2-RISK-REGISTER.md) ·
[`PHASE-2-DECISION-REGISTER.md`](PHASE-2-DECISION-REGISTER.md) ·
[`PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md`](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md) ·
[`PHASE-2-TRACEABILITY-MATRIX.md`](PHASE-2-TRACEABILITY-MATRIX.md) ·
[`PHASE-1-EXIT-CRITERIA.md`](PHASE-1-EXIT-CRITERIA.md)
