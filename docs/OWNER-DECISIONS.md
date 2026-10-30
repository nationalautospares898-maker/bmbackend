# Owner Decision Register

> ## Current disposition — 2026-10-30
>
> The [Phase 2 Design Completion Addendum](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) supersedes stale open/blocking labels for OD-3, OD-5, OD-7, OD-14, OD-21, OD-22, OD-23 and OD-26. It defines the design default and evidence boundary without claiming new owner or accountant approval. Genuine legal, owner-supplied, physical and operational gates are listed in [current required decisions](PHASE-2-OWNER-DECISIONS-REQUIRED.md). Historical text below is retained as decision history; use the addendum for current Phase 3 scope.

| Field | Value |
|---|---|
| Status | **Updated 2026-10-30 — the owner's workbook was supplied and inspected ([Addendum 2](PHASE-2-CLOSURE-ADDENDUM-2.md)).** **OD-5 is now RESOLVED on evidence**, and **OD-26** (date order) was raised by that inspection. Previously updated by the owner's fourth answer set.** Settled by owner decision: OD-14 (**re-answered — four actions**), OD-21, OD-23 (**amended**), **OD-3 (re-answered — no customer-visible numbering)**, **OD-5 (layout VERIFIED from the workbook)**. Partially settled: OD-7, OD-22, OD-24. New: **OD-25** (statutory numbering requirement), **OD-26** (printed date order). The rest open. **No item has received legal, tax or accounting approval.** |
| Authority | This is the **authoritative register** of unresolved business decisions for both repositories (ADR-021). `PRD.md §5` summarises it; this file carries the full analysis. |
| Last updated | 2026-10-30 |
| Audience | The business owner, and their accountant for the tax items |

> **Read the status on each item — do not assume.** As of 2026-10-30 the register holds three kinds
> of entry:
>
> | | Items |
> |---|---|
> | ✅ **Settled** | **OD-3** (no customer-visible invoice numbering), **OD-14** (four actions), OD-21 (credit workflow), **OD-5** (bill layout — *settled by file inspection, not by preference*) |
> | 🟡 **Partially settled** — a preference was given but part remains open | OD-7 (principles yes, precision/method no), OD-22 (credit payment block yes, placement no; invoice-number half withdrawn), **OD-23** (no decimals on `PRICE`/`GRAND TOTAL`; the fractional-amount case unresolved), OD-24 (wording yes, terminology approval no) |
> | 🔴 **Open** | everything else, plus **OD-25** (statutory numbering) and **OD-26** (printed date order) — each records a *recommended default*, which is an engineering proposal, **not** a decision the owner has made |

> ### ⚠️ Fourth owner answer set — what changed
>
> The owner supplied four answers. **Three re-answer decisions previously recorded as settled**, so the
> supersession is recorded explicitly rather than silently:
>
> | Answer | Effect |
> |---|---|
> | *"No need of invoice numbering."* | **OD-3 re-answered.** Customer-visible invoice numbering is withdrawn. The gapless question becomes **moot** for current scope. A new go-live gate **OD-25** captures the statutory question the owner's preference cannot settle |
> | The original bill workbook `ANAS_1234_20260606_231456.xlsx`, with its layout described | **OD-5 advanced** then, and **RESOLVED on 2026-10-30** when the file itself was supplied and inspected. ⚠️ The workbook sets **no page setup at all**, so paper, orientation and scaling transfer to [OD-6](#od-6) |
> | *Amounts must not have decimal places in `PRICE` or `GRAND TOTAL`* | **OD-23 amended.** A display rule only. It **conflicts** with any fractional amount; the conflict is recorded, not resolved by rounding |
> | Four actions: `Print Bill`, `Credit Bill`, `Save a Draft`, `Clear Draft` | **OD-14 re-answered.** `Save / Credit Bill` → `Credit Bill`; `Keep for later` → `Save a Draft`; discard is promoted to a **primary** action `Clear Draft` |
>
> **No approved safety behaviour changed.** Atomic finalisation, stock-exactly-once, idempotency,
> the recovery state machine, credit customer identity, payment-never-moves-stock, draft/finalised
> separation and the offline refusal are all preserved unchanged.
>
> **No item in this register has received legal, tax or accounting approval, and no approval date,
> approver name or sign-off has been recorded anywhere — because none has been given.** A selected
> preference settles what the owner wants; it does not settle whether it is lawful or correct for
> their tax status. Where a default would be visible to a customer or to the tax authorities, it
> remains deliberately conservative.
>
> **No business fact in this file was invented.** Where a value is unknown (GSTIN, legal name, paper
> size, tax rates), it is recorded as unknown and the system is built to refuse to guess.

---

## How to use this register

Each item records the seven fields required for a decision to be actionable:

1. **Question** — the exact thing that must be decided.
2. **Already confirmed** — what is settled, so the owner is not re-asked.
3. **Options** — the realistic choices.
4. **Consequence of each** — what changes in the software and in the shop.
5. **Recommended default + rationale** — what happens if no answer arrives.
6. **Blocking level** — `BLOCKS-IMPLEMENTATION` / `BLOCKS-TESTING` / `BLOCKS-GO-LIVE` / `NON-BLOCKING`.
7. **Approver** — who must sign off.

### Blocking-level definitions

| Level | Meaning |
|---|---|
| `BLOCKS-IMPLEMENTATION` | Phase 2 coding of the affected module cannot start; the answer changes the design, not just a value |
| `BLOCKS-TESTING` | Code can be written, but its acceptance test cannot be finalised |
| `BLOCKS-GO-LIVE` | Build and test freely; **production use is unsafe or non-compliant** without the answer |
| `NON-BLOCKING` | The default is safe indefinitely; confirm when convenient |

### Status vocabulary — a preference is not an approval

Added 2026-10-29 with the owner-decision update. **These four states are distinct and must never be
collapsed into each other.** Most of the risk in this register is someone reading "the owner told us
what they want" as "this is signed off".

| Status | Meaning | May implementation rely on it? |
|---|---|---|
| **SELECTED PREFERENCE** | The owner has stated a direction. It is a real input and documentation follows it — but it is **not** a legal, accounting or fidelity approval | Yes, for the part the preference actually covers |
| **DOCUMENTED PROPOSAL** | A layout or technical detail this project suggests. **Not approved.** Must stay switchable/reversible | No — build it so it can be withdrawn |
| **APPROVAL REQUIRED** | Cannot be treated as settled by preference alone; needs the owner, their accountant, or a printed sample | No |
| **UNVERIFIED REFERENCE DETAIL** | A fact that **cannot be established** from the evidence held. Must never be invented | No |

A single decision can hold several of these at once. **OD-23** is now the clearest case: *no decimals
on `PRICE` and `GRAND TOTAL`* is a **selected preference**, while *what to do when an amount actually
carries paise* is **APPROVAL REQUIRED** — so the item is partly settled and still gated.
*(OD-22 was the previous example; its invoice-number half was withdrawn by OD-3's re-answer.)*

**No approval date, approver name, accounting sign-off or legal review has been recorded anywhere in
this register, because none has been given.**

### Summary

| ID | Topic | Blocking level | Approver |
|---|---|---|---|
| [OD-1](#od-1) | GST registration status | **BLOCKS-GO-LIVE** | Owner + accountant |
| [OD-2](#od-2) | Tax rates, HSN codes, inclusive/exclusive | **BLOCKS-GO-LIVE** | Owner + accountant |
| [OD-3](#od-3) | Invoice numbering | ✅ **RE-ANSWERED — no customer-visible invoice numbering.** The gapless question is **MOOT** for the current scope; an **internal-only** sale reference is retained. → `NON-BLOCKING` for the numbering module; statutory question moved to **[OD-25](#od-25)** | Owner *(granted)*; accountant for OD-25 |
| [OD-4](#od-4) | Legal shop identity for the invoice header | **BLOCKS-GO-LIVE** | Owner |
| [OD-5](#od-5) | **The actual Excel invoice reference** | ✅ **RESOLVED 2026-10-30 — the workbook was supplied and inspected directly.** Layout, geometry, typography, borders, merges, number formats and both logos are `[VERIFIED]`; `QTY` confirmed. ⚠️ The file defines **no page setup**, so paper/orientation/scaling transfer to [OD-6](#od-6), and the date order became [OD-26](#od-26) | — |
| [OD-6](#od-6) | Paper size and printer | **BLOCKS-GO-LIVE** | Owner |
| [OD-7](#od-7) | Money precision and rounding | 🟡 **Principles selected; precision & method UNDECIDED** → **BLOCKS-TESTING** (the policy *values* + expected-value assertions; the engine is buildable parameterised by a policy object — P2-D-03) | Owner + accountant |
| [OD-8](#od-8) | Offline expectation — drafts vs billing | **BLOCKS-GO-LIVE** (staff training) | Owner |
| [OD-9](#od-9) | Draft stock reservation | NON-BLOCKING | Owner |
| [OD-10](#od-10) | Who may cancel an invoice, and for how long | NON-BLOCKING | Owner |
| [OD-11](#od-11) | Returns accepted, and within what window | NON-BLOCKING | Owner |
| [OD-12](#od-12) | Credit terms and credit limits | NON-BLOCKING | Owner |
| [OD-13](#od-13) | Cashier discount authority | NON-BLOCKING | Owner |
| [OD-14](#od-14) | Billing action labels | ✅ **RE-ANSWERED — four actions**: `Print Bill` · `Credit Bill` · `Save a Draft` · `Clear Draft` | — |
| [OD-15](#od-15) | Archived PDF copies of invoices | NON-BLOCKING | Owner + accountant |
| [OD-16](#od-16) | Reprint marked "DUPLICATE COPY" | NON-BLOCKING | Owner |
| [OD-17](#od-17) | Login style for counter staff | NON-BLOCKING | Owner |
| [OD-18](#od-18) | Number of counters and branches at launch | NON-BLOCKING | Owner |
| [OD-19](#od-19) | Hosting, alert recipient, budget | **BLOCKS-GO-LIVE** | Owner |
| [OD-20](#od-20) | Counter browser and screen size | NON-BLOCKING | Owner |
| [OD-21](#od-21) | **Credit workflow — ✅ CONFIRMED 2026-10-29** | **RESOLVED** | — |
| [OD-22](#od-22) | Invoice number & payment block on the printed bill | 🟡 **PARTLY MOOT** — the invoice-number half is withdrawn by OD-3's re-answer. The credit payment/balance block is still selected; the owner's described footer has no slot for it → placement open | Owner |
| [OD-23](#od-23) | Amount formatting and empty-row zeros on the bill | 🟡 **AMENDED — `PRICE` and `GRAND TOTAL` print with no decimal places.** Conflicts with fractional amounts; see the recorded conflict → `BLOCKS-GO-LIVE` until the fractional case is resolved | Owner |
| [OD-24](#od-24) | Printed document label — `ESTIMATE` retained | 🟡 **Preference selected; not a legal approval** → still **BLOCKS-GO-LIVE** | Owner + accountant |
| [OD-25](#od-25) | **Does law require a serially numbered tax invoice?** | 🔴 **OPEN — new, created by OD-3's re-answer** → **BLOCKS-GO-LIVE** | Owner + accountant |
| [OD-26](#od-26) | **Printed date order — `dd-mm-yyyy` vs the workbook's `mm-dd-yyyy`** | 🔴 **OPEN — new, raised by the 2026-10-30 workbook inspection.** The previously `[OBSERVED]` `DD-MM-YYYY` was an inference → **BLOCKS-GO-LIVE** | Owner |

**No owner decision blocks the start of any implementation module.** OD-3 is answered (no
customer-visible numbering); **OD-5's layout is now VERIFIED from the workbook itself**; OD-14 is
re-answered. **OD-7** still gates the money module's expected-value assertions; **OD-23**'s
fractional-amount conflict, **OD-22**'s credit-block placement, **OD-6**'s paper/printer choice and
**OD-26**'s date order gate print sign-off; and **OD-25** (statutory numbering) is a go-live gate.

> *Corrected in Phase 2 closure. This sentence previously said "two items", then named three; it
> omitted OD-22 (which this table marks `BLOCKS-IMPLEMENTATION`); and it listed OD-14 as blocking UI
> acceptance although OD-14's own entry records it **SETTLED / NON-BLOCKING**. Audit finding CF-8.*
>
> **Phase 2 re-scoped two of the four** — see `PHASE-2-DECISION-REGISTER.md`:
> **OD-3** (P2-D-02) gates the numbering **policy statement and its release criterion**, not the
> module, because the in-transaction design satisfies either answer. **OD-7** (P2-D-03) gates the
> money **policy values and expected-value assertions**, not the engine, which is built
> parameterised by a policy object. **OD-5** and **OD-22** still block the print template's
> **bottom section** and the **final placement** of the number / payment block respectively.

---

<a id="od-1"></a>
## OD-1 — Is the business GST-registered?

*Supersedes Q-TAX-1. Related: FR-TAX-001…004, ADR-020.*

**Question.** Is National Auto Spares registered for GST? If yes, what is the GSTIN, and what is the
registered legal name and state?

**Already confirmed.** Tax requirements are *not* confirmed (C-4). The system currently ships in
`estimate_only` mode and prints documents titled `ESTIMATE`.

**Options.**
- **(a) Not registered / below threshold.** Stay in `estimate_only` permanently.
- **(b) Registered.** Supply GSTIN, legal name, state code; enable `gst_enabled` after the
  `FR-TAX-003` gate passes.
- **(c) Composition scheme.** A distinct case: no tax collected on the invoice, but a specific
  declaration must appear on the bill.

**Consequences.**
- (a) No change needed. Documents remain estimates. **The shop cannot issue a tax invoice**, and
  business customers who need input credit will be unable to claim it.
- (b) Requires OD-2 (rates/HSN) and OD-3 (series) before the gate will open. Every active product
  needs a tax group. Document title becomes `TAX INVOICE`.
- (c) Needs its own declaration text, which the owner's accountant must supply verbatim. **Do not
  draft this text in-house.**

**Recommended default.** Remain `estimate_only`. Rationale: an unconfigured document labelled a tax
invoice is a legal problem; an estimate is merely a limitation. The schema already supports GST, so
switching later is configuration and data entry, not a migration on live financial data.

**Blocking level.** `BLOCKS-GO-LIVE` — the shop may run on estimates, but the owner must consciously
accept that, not discover it.

**Approver.** Owner, advised by their accountant.

---

<a id="od-2"></a>
## OD-2 — Tax rates, HSN codes, and inclusive vs exclusive pricing

*Supersedes Q-TAX-2. Related: FR-TAX-002, FR-TAX-003.*

**Question.** If GST applies: which rate applies to which product group, what HSN code does each
group carry, and are the shop's shelf prices tax-inclusive or tax-exclusive?

**Already confirmed.** Nothing. No rate, HSN or inclusivity assumption exists anywhere in the system
by design.

**Options.**
- **(a) Tax-exclusive.** Prices entered net; tax added as a separate line.
- **(b) Tax-inclusive.** Prices entered gross; tax back-calculated and shown for information.

**Consequences.**
- (a) Printed total is higher than the shelf price, which surprises walk-in customers unless staff
  quote gross. Simpler arithmetic and the conventional B2B format.
- (b) Printed total matches what the customer was quoted. Requires back-calculation, which produces
  rounding differences that must be shown consistently.
- Either way: **every active product needs a tax group before `gst_enabled` can be switched on.**
  With a large catalog this is a significant data-entry task that should be scoped early, not
  discovered at go-live.

**Recommended default.** Tax-exclusive, with no rates seeded. Rationale: it is the conventional
format for a parts business selling to transport companies, and it keeps the arithmetic auditable.
**Not applied unless OD-1 is answered (b).**

**Blocking level.** `BLOCKS-GO-LIVE` (only if OD-1 = registered).

**Approver.** Owner's accountant; owner confirms the pricing convention staff actually use.

---

<a id="od-3"></a>
## OD-3 — Invoice series format, financial year, and whether numbering must be gapless

*Supersedes Q-TAX-3. Related: FR-SALE-004, NFR-DATA-005, ADR-009, ADR-011.*

**Question.** What format must the invoice number take, does it reset each financial year, and is
strictly consecutive (gapless) numbering a legal requirement for this business?

**Already confirmed.** Financial year starts in April by default (`financial_year_start_month = 4`,
changeable). Invoice numbers are server-allocated and unique.

**Options.**
- **(a) Gapless per branch per financial year.** Number allocated inside the sale transaction, so a
  failed sale consumes no number.
- **(b) Gaps permitted.** Use a database sequence — faster, no lock contention, but a rolled-back
  sale permanently burns a number.
- **(c) Gapless per counter.** Each counter has its own series (`NAS/2026-27/C1/00001`).

**Consequences.**
- (a) **Currently implemented in the design.** Finalisations on one series serialise at a single-row
  lock — microseconds at this shop's volume. Satisfies the strictest reading of consecutive
  numbering rules.
- (b) Audit questions about missing numbers. Only acceptable if the accountant confirms gaps are fine.
- (c) Removes all lock contention, but **each counter's unused numbers are permanent gaps**, so this
  is incompatible with a gapless requirement.
- **Critical interaction:** this answer constrains OD-8. Offline sale finalisation (if ever wanted)
  requires pre-allocated per-counter number blocks, which **guarantees gaps**. If gapless is
  mandatory, offline billing is permanently off the table — not merely deferred.

### ✅ RE-ANSWERED BY THE OWNER — customer-visible invoice numbering is NOT required

**Owner's instruction, verbatim: _"No need of invoice numbering."_**

This **supersedes** the 2026-10-29 selection of a sequential invoice-number series (preserved below
for the record). The supersession is explicit, not silent.

#### What is withdrawn

- **No customer-visible invoice number.** Nothing is printed on the bill as an invoice or bill
  number, and **no visible numbering scheme may be invented to replace it**.
- **The gapless-versus-gap-tolerant question is MOOT** for the current scope. Gaplessness was only
  ever meaningful for a *customer-visible, statutorily-serial* number. `AC-1a` is **withdrawn** and
  the gap-detection test ceases to be a criterion.

#### What is retained — four different things, deliberately distinguished

| Concept | Customer-visible? | Retained? | Why |
|---|---|---|---|
| **Invoice number** (rendered string on the bill) | Yes | ❌ **Withdrawn** | The owner does not want it |
| **Internal sale reference** — `invoices.sequence_number`, allocated in-transaction per (business, branch, series, FY) | **No** | ✅ **Retained, internal-only** | Auditability, reconciliation, stable ordering, and the OD-25 contingency |
| **Idempotency key** | No | ✅ **Retained unchanged** | Duplicate-sale prevention (FR-SALE-002) |
| **Database primary key** `invoices.id` (uuid) | No | ✅ **Retained unchanged** | Referential integrity |

**Why the internal reference is retained rather than deleted.** It already exists, is race-safe, is
tested, and costs nothing at this scale. It gives audit and reconciliation a short human-readable
handle, it backs the case-12 recovery lookup (Blueprint §5.4.5), and — decisively — **if
[OD-25](#od-25) establishes a statutory numbering requirement, the compliant mechanism is already
there.** Deleting it would only have to be rebuilt. **It is never rendered to a customer.**

#### ⚠️ Legal boundary — this preference cannot settle the law

**The owner's preference does not establish that omitting an invoice number is lawful.** GST law
generally requires a consecutive serial number on a *tax invoice*. This system currently issues
documents titled `ESTIMATE` in `estimate_only` mode ([OD-1](#od-1), [OD-24](#od-24)), where that
requirement may not bite — but **that is an accountant's determination, not ours**. The question is
recorded as **[OD-25](#od-25)** and is a **go-live gate**. **Do not claim compliance.**

**Blocking level.** **`NON-BLOCKING`** for the numbering module — the question that gated it is
answered. Statutory exposure moves to OD-25 (`BLOCKS-GO-LIVE`).

**Approver.** Owner — **granted** for the product decision. Accountant still required for OD-25.

---

### Historical record — the superseded 2026-10-29 selection

*Retained unchanged below so the decision history stays auditable. **It no longer governs.***

### 🟡 SELECTED DIRECTION (2026-10-29) — partial — ⚠️ SUPERSEDED

The owner selected **a sequential invoice-number series**.

### ⚠️ The gapless question is explicitly NOT decided

"Sequential" and "gapless" are **different properties**, and only the first was selected:

| Property | Meaning | Status |
|---|---|---|
| **Sequential** | Numbers are issued in increasing order from a series | ✅ **SELECTED PREFERENCE** |
| **Gapless** | **Every** number in the sequence corresponds to a committed invoice — a failed or rolled-back sale leaves no hole | 🔴 **UNDECIDED — APPROVAL REQUIRED** |

A sequence can be perfectly sequential and still contain gaps (a database `SEQUENCE` behaves exactly
that way: `…411, 412, 414…` after one rollback). Whether those gaps are acceptable is a
**statutory/accounting question**, not a preference — it requires explicit owner and accountant
approval.

### What is preserved unchanged

The existing numbering specification stands as written and **must not be silently altered**:

- Format `NAS/{financial_year}/{sequence:05d}` — the prefix `NAS` remains a **placeholder pending
  [OD-4](#od-4)**, not a confirmed business value.
- Scoping by **(business, branch, series, financial year)** — `DATA-MODEL.md §3.8`.
- Financial-year rollover driven by `businesses.financial_year_start_month`.
- In-transaction allocation from a locked `invoice_series` row (ADR-009, FR-SALE-004).

**No numbering architecture was investigated, altered or implemented in this update.** The design
remains as documented; only the decision status changed.

### Interaction that must not be forgotten

The gapless answer **constrains [OD-8](#od-8)**: per-counter number blocks (which offline
finalisation would require) guarantee permanent gaps. If gapless is ultimately mandatory, offline
billing becomes permanently impossible rather than merely deferred. **Answer OD-3 and OD-8
together.**

**Blocking level.** **Re-scoped in Phase 2 closure (P2-D-02) — now `BLOCKS-TESTING`, not
`BLOCKS-IMPLEMENTATION`.**

This item previously read `BLOCKS-IMPLEMENTATION` for the whole numbering module, on the stated
grounds that *"the allocation code and its concurrency tests differ materially between gapless and
gap-tolerant designs, so building one and switching later wastes the work."* **That reasoning is
directionally asymmetric and the conclusion was too broad:**

| Built | Satisfies a **gapless** requirement? | Satisfies a **gap-tolerant** requirement? |
|---|---|---|
| In-transaction locked counter row — option (a) | ✅ | ✅ |
| `SEQUENCE` — option (b) | ❌ | ✅ |
| Per-counter series — option (c) | ❌ | ✅ |

Gaplessness is **strictly stronger**, so option (a) satisfies either answer and building it now is
never wasted. What this decision genuinely gates is **whether gaplessness may be stated as a
business invariant and a release criterion** (NFR-DATA-005, AC-1a) and whether the gap-detection
test is a gate or merely informational.

**Still binding regardless of the answer:** the per-counter-series contention remedy
(option (c), `counters.invoice_series_id`) **must not be applied before this item is answered** — it
guarantees permanent gaps, and gaps once issued **cannot be closed**, because renumbering issued
documents is falsifying records.

**Approver.** Owner's accountant (whether gapless is legally required), owner (format preference,
plus the `OD-3-A` acknowledgement that work proceeds on gap-free-as-default meanwhile).

---

<a id="od-4"></a>
## OD-4 — Legal shop identity for the invoice header

*Supersedes Q-LEGAL-1. Related: FR-ADMIN-001, FR-PRINT-001.*

**Question.** What exactly should print at the top of the bill — legal entity name, trading name,
full address, phone number(s), email, and any registration numbers?

**Already confirmed.** The trading heading is **NATIONAL AUTO SPARES** (stated by the owner and used
as the display name). Everything else is unknown.

**Options.**
- **(a) Owner supplies the exact text**, entered into business settings.
- **(b) Ship with the fields empty.** The bill prints the heading only.

**Consequences.**
- (a) The bill looks correct from day one.
- (b) The bill is missing address and phone, which customers expect and which may be legally
  required. The go-live gate blocks production until this is filled, so (b) is a temporary state,
  never a shipped one.
- The system **prints nothing where a field is empty** — never a placeholder like "Address line 1"
  (FR-PRINT-001). An unfilled field is visibly absent rather than wrong.

**Recommended default.** Fields stay empty and the go-live gate blocks release. Rationale: inventing
a plausible address would be worse than an obviously incomplete one.

**Blocking level.** `BLOCKS-GO-LIVE`.

**Approver.** Owner.

---

<a id="od-5"></a>
## OD-5 — The actual Excel invoice reference ⚠️

*Supersedes Q-PRINT-1 (layout half). Related: C-9, FR-PRINT-013, ADR-014, ADR-028.*

**Question.** Can the owner provide the actual Excel bill file (or a clear photograph/scan of a
printed bill)?

**Already confirmed.** A **screenshot of the owner's Excel bill, inspected 2026-10-29.** It
evidences the top section: heading `NATIONAL AUTO SPARES` (bold italic, centred, merged A:E),
`AUTO NAGAR ,HYDERABAD`, `Mob:9948207903,9848136564`, the `Customer Name:` / `Mechanic Name :` /
`Vehicle no:` block, the `ESTIMATE` label left-aligned on the `DATE:` row, `DD-MM-YYYY` dates, and
the column headers **`S R · ITEM · QT · PRICE · TOTAL`** over pre-numbered rows whose empty entries
show `0` in `TOTAL`. Amounts appear as plain integers with no decimals, separators or currency
symbol. **This supersedes the earlier written-description reconstruction, which had the column
labels and the `ESTIMATE` placement wrong.**

**⚠️ STATUS CHANGED 2026-10-29 — PARTIALLY RESOLVED.**

The owner supplied a **screenshot of the actual Excel bill**, which **was inspected directly**. The
top section is now evidenced and `../../bmfrontend/docs/UI-UX-SPECIFICATION.md §12.1` is a
transcription rather than a reconstruction. The earlier reconstruction was **wrong in specifics** —
notably the column labels (`S R · ITEM · QT · PRICE · TOTAL`, not `S.No · Item · Qty · Price ·
Total`) and the placement of `ESTIMATE` (left-aligned on the DATE row, not a centred banner).

**What remains unresolved:** the screenshot ends at worksheet row 30, inside the pre-numbered empty
rows, so **the entire bottom section is unseen** — grand total, signature area, footer text, the
defined print area, page size, margins and scaling (`§12.1b`).

**What was actually checked (2026-10-29, re-verified during this update).** The **original `.xlsx`
workbook is NOT available**: zero non-Markdown files exist anywhere under `bm/`, and a filename
search for the `ANA…` workbook visible in the screenshot's title bar found nothing. **Only a
screenshot was inspected; the workbook was not.**

**What is still needed — one of:**
- **(a) The original `.xlsx` workbook** (preferred). Resolves the bottom section *and* the print
  area, page size, margins, scaling and formulas in one step.
- **(b) A second screenshot scrolled to the bottom of the print area.** Resolves the grand total,
  signature and footer, but **not** page setup.
- **(c) A photo of a printed bill on the shop's actual paper.** Resolves layout *and* real-world page
  fit, which is ultimately what matters.

**Consequences.**
- (a) or (c) closes this item entirely. (b) closes most of it and leaves page setup to [OD-6](#od-6).
- Proceeding on the screenshot alone means the top of the bill will be right and the bottom is a
  guess — the grand total is the most-read number on the page, so guessing it is not acceptable for
  a production bill.

**Recommended default.** Build the print template now for the **`[OBSERVED]`** top section, leave the
bottom section unimplemented behind a clear gap, and request (a). Rationale: most of the layout can
proceed immediately, and the remaining unknown is small, well-defined and listed in `§12.1b`.

**Blocking level.** `BLOCKS-IMPLEMENTATION` for the **bottom section** of the print template only.
The header, field block and item table can now be built from evidence. Full sign-off still requires a
printed sample on the shop's printer and paper.

**Approver.** Owner.

### 🟡 ADVANCED BY THE OWNER — the complete bill layout is now described; the file is not yet in the workspace

The owner states that the original workbook **`ANAS_1234_20260606_231456.xlsx`** exists and has been
supplied, and describes its complete visible design. **This supersedes the 2026-10-29 position that
no further evidence could be provided** (preserved below).

#### ⚠️ What was actually verified, and what was not

| | |
|---|---|
| **The workbook was NOT inspected** | A filesystem search of the whole workspace found **zero non-Markdown files** and **no `reference/` directory**. The file is **not present**, so no sheet, cell, formula, merge, column width, font, number format, print area or embedded image has been read |
| **What IS now held** | The owner's **written description** of the complete layout — which is substantially more than the earlier screenshot, because it covers the **lower section** for the first time |

**This is owner testimony, not file inspection, and the two are not interchangeable.** The
distinction is kept because `[OBSERVED]` in `../../bmfrontend/docs/UI-UX-SPECIFICATION.md §12.2a`
has always meant *directly evidenced*.

#### Now described by the owner — the lower section, previously `[UNSEEN]`

| Element | Status |
|---|---|
| `GRAND TOTAL` row | **`[OWNER-DESCRIBED]`** — exists; exact row, label placement and alignment not verified |
| `THANK YOU VISIT AGAIN` footer line | **`[OWNER-DESCRIBED]`** |
| `Authorised Dealers of:` + `TATA - LEYLAND SPARE PARTS` | **`[OWNER-DESCRIBED]`** |
| Supplied logos | **`[OWNER-DESCRIBED]`** — images exist; **the actual image files are not in the workspace** |
| **25 pre-numbered item rows** | **`[OWNER-DESCRIBED]`** — supersedes the earlier "≥22, exact count unknown" |
| Item columns `S R · ITEM · QTY · PRICE · TOTAL` | **`[OBSERVED]`** previously — note the owner now writes **`QTY`** where the screenshot showed **`QT`**; see the conflict below |
| Header, address, mobile, Customer/Mechanic/Vehicle/`ESTIMATE`/`DATE` block | **`[OBSERVED]`** — unchanged |

#### 🔴 Two details still genuinely missing — they require the file

1. **Exact geometry and page setup** — column widths, row heights, merged ranges, fonts and point
   sizes, borders, number formats, **defined print area, paper size, margins, orientation and
   scaling**, hidden rows/columns, and the second sheet's purpose. A written description cannot
   supply these, and they are what determines whether the bill fits the page.
2. **The logo image files**, which must be embedded in the print template.

#### 🔴 Recorded conflict — `QT` versus `QTY`

The inspected screenshot showed the column header **`QT`**; the owner's written description says
**`QTY`**. Both cannot be reproduced. **Not resolved by guessing** — the specification keeps
**`QT`** (the directly-observed value) and flags the discrepancy for confirmation from the workbook
or a printed sample. Recorded as Phase 3 task **T3-10**.

**Blocking level.** **`BLOCKS-GO-LIVE`** — downgraded from `BLOCKS-IMPLEMENTATION`. The bill's
**content** is now sufficiently described to build the full template, header to footer. **Print
fidelity sign-off still requires the workbook (or a printed sample) plus [OD-6](#od-6).**

**➡️ Still needed from the owner:** place `ANAS_1234_20260606_231456.xlsx` in the workspace (for
example at `reference/owner-original-bill.xlsx`) together with the logo image files, so the geometry,
page setup and `QT`/`QTY` question can be settled from evidence.

**Approver.** Owner.

---

### ✅ RESOLVED 2026-10-30 — the workbook was supplied and **inspected directly**

**The file arrived.** `ANAS_1234_20260606_231456.xlsx` is present at the workspace root
(19,114 bytes, SHA-256 `D1DA3922…D11C9410`) and **every part of it has been read**: both worksheets,
all cell values, the shared-string table, the full style table, all merged ranges, column widths,
row heights, borders, number formats, the drawing anchors and both embedded images.

**The owner's original file was not modified** — a copy was extracted and inspected; the SHA-256
above was taken afterwards and can be re-checked.

**The complete verified layout is recorded in
[PHASE-2-CLOSURE-ADDENDUM-2.md §3](PHASE-2-CLOSURE-ADDENDUM-2.md#3-the-verified-bill-layout)** and
transcribed into `../../bmfrontend/docs/UI-UX-SPECIFICATION.md §12.1`. It is no longer a
transcription from a screenshot, nor a reconstruction from a description — it is **read from the
file**. The evidence class `[VERIFIED]` now applies to the layout.

#### What the inspection settled

| | |
|---|---|
| **Structure** | Used range `A1:E37`; **12** merged ranges; **25** pre-numbered item rows (9–33); `GRAND TOTAL` row 34; three footer rows 35–37 |
| **Geometry** | All five column widths and all 37 row heights measured. Content block ≈ **92 × 176 mm** |
| **Typography** | Calibri throughout; every element's point size, weight, italic/underline and colour recorded — including the headers' navy `#232852` |
| **Borders** | Outer rectangle around `A1:E37`; rule under row 7; full grid on `A8:E33` |
| **Number formats** | `QTY`, `PRICE`, `TOTAL` and `GRAND TOTAL` all use Excel built-in format **`0`** — zero decimal places |
| **Logos** | Two JPEGs extracted and identified: **TATA** (55×57 px) in column A and **Ashok Leyland** (58×64 px) in column E, both anchored in row 35 beside the dealer-credit lines |
| **Verified absent** | Invoice-number field · tax/GST/GSTIN/HSN · signature block · amount in words · paid/balance/due block · discount line — **none exists anywhere in the sheet** |

#### 🔴 `QT` versus `QTY` — **CLOSED in favour of `QTY`**

The conflict recorded above is resolved. The workbook's shared-string table holds the literal
**`QTY`**. The screenshot was not misread — **column C is 3.33 characters wide with `shrinkToFit`
set**, so at 9 pt bold the third glyph fell at the cell edge and was unreadable.

**The specification now says `QTY`.** The earlier instruction to keep `QT` is withdrawn.

#### ⚠️ Three corrections the file forced

1. **There are no formulas in the workbook — none.** Every value, including each empty-row `0` and
   the `GRAND TOTAL` of `4820`, is a **hand-typed literal**. The claim that the `0`s were a
   "formula artefact" was an inference and was wrong. *(The decision to print empty rows blank —
   [OD-23](#od-23) option (b) — is unaffected; only its stated rationale was wrong.)*
2. **The original bill has no arithmetic cross-check**, since the grand total is typed rather than
   summed.
3. **The date format is `mm-dd-yyyy`, not `dd-mm-yyyy`** — see **[OD-26](#od-26)**, raised by this
   inspection.

#### 🔴 What the workbook does **not** resolve

The sheet contains **no `<pageSetup>` element, no `<headerFooter>` element and no defined print
area**. Its `pageMargins` are Excel's untouched defaults (0.7/0.75 in). Therefore **paper size,
orientation, scaling and print area were never set by the owner** and cannot be read from the file.

**That part of this decision transfers to [OD-6](#od-6), which is unchanged and still open.**
Measured arithmetic shows the content would **overflow A5 by ≈ 4 mm at the workbook's own margins**
but fits comfortably at the 8 mm margin already specified in FR-PRINT-016 — a *recommendation*
supported by measurement, **not** an approval.

**Blocking level.** ✅ **Layout: RESOLVED — no longer blocking.** Print-fidelity sign-off on a
printed sample remains a go-live gate under **[OD-6](#od-6)**, and the date order under
**[OD-26](#od-26)**.

**Approver.** Owner — for OD-6 and OD-26 only. The layout itself needed evidence, not approval, and
now has it.

---

### Historical record — the 2026-10-29 position

*Retained so the history stays auditable. **Superseded by the entry above.***

### 🔴 OWNER RESPONSE 2026-10-29 — further evidence cannot be supplied at this time — ⚠️ SUPERSEDED

The owner has confirmed they **cannot currently provide** the original Excel workbook, a
bottom-scrolled screenshot, or a photograph of a printed bill.

**Consequences, recorded plainly:**

- The supplied screenshot remains the **only** source for the bill layout, and covers only the
  **upper portion**.
- The **lower section stays unverified**. The grand total, signature/footer area, complete row
  count, print area, paper size, margins, scaling and page setup are **`[UNSEEN]`** and
  **must not be invented, inferred, or described as verified**.
- The evidence classifications **`[OBSERVED]` / `[UNSEEN]` / `[PROPOSED]`** in
  `../../bmfrontend/docs/UI-UX-SPECIFICATION.md §12.2a` are **retained exactly as they stand**.
- **No substitute file was searched for and no unseen content was inferred** during this update.
- **OD-5 is NOT resolved**, and is not downgraded. It stays open until sufficient reference evidence
  is supplied and reviewed.

**This blocks more than itself.** [OD-22](#od-22)'s placement question cannot be answered without
the lower section, so the two are now blocked by the same missing evidence.

**Blocking level.** **`BLOCKS-IMPLEMENTATION`** for the **bottom section of the print template** —
unchanged and currently un-advanceable. The header, field block and item table remain buildable from
observed evidence.

**➡️ Still outstanding for the owner, whenever it becomes possible:** the original `ANA….xlsx`, a
screenshot scrolled to the bottom of the bill, or a photo of a printed bill.

---

<a id="od-6"></a>
## OD-6 — Paper size and printer

*Supersedes Q-PRINT-1 (hardware half). Related: FR-PRINT-016, NFR-FE-COMP-001.*

**Question.** What printer is at the counter, and what paper will bills print on?

**Already confirmed.** Nothing. The design assumes a plain laser/inkjet printer driven by the
browser print dialog (ADR-014).

**Options.** (a) A5 plain paper · (b) A4 plain paper · (c) pre-printed stationery ·
(d) thermal roll (80 mm).

**Consequences.**
- (a) Default in the spec; economical, familiar for parts bills.
- (b) Same template, more whitespace, fewer filler rows needed.
- (c) Changes the template substantially — the heading and borders are already on the paper, so the
  digital layout must *not* reprint them. Needs the actual stationery to align against.
- (d) **A different template, not a variation.** Narrow single-column layout; the `S.No | Item | Qty
  | Price | Total` table does not fit at 80 mm as specified. Treat as a separate deliverable.

**Recommended default.** A5 portrait, plain paper, `@page { size: A5 portrait; margin: 8mm }`,
switchable to A4 by configuration. Rationale: matches a typical counter setup and the A4 fallback is
free.

**Blocking level.** `BLOCKS-GO-LIVE` — print fidelity must be checked on the real device
(`TESTING-STRATEGY.md §9`).

**Approver.** Owner.

---

<a id="od-7"></a>
## OD-7 — Money rounding rule

*Supersedes Q-MONEY-1. Related: FR-SALE-003, NFR-DATA-002, ADR-032.*

**Question.** Round each line to 2 decimals and sum, or sum and round once? And should the grand
total be rounded to the nearest rupee?

**Already confirmed.** All amounts are exact decimals (`NUMERIC`), never floating point. Any
whole-rupee rounding is recorded explicitly as `invoices.rounding_adjustment` so the arithmetic
always reconciles.

**Options.**
- **(a) Round each line half-up to 2 dp, then sum.** Line totals on the printed bill add up exactly
  to the stated total.
- **(b) Keep full precision per line, round only the grand total.** The printed line totals may not
  visibly sum to the total — customers notice and query this.
- **(c) Either of the above, plus round the grand total to the nearest rupee**, with the difference
  shown as a rounding line.

**Consequences.**
- (a) **What the design implements.** Predictable, explainable at the counter, and matches how a
  hand-written bill works.
- (b) Marginally more "accurate" in aggregate but produces bills that appear to add up wrongly.
- (c) Avoids handling coins. The rounding adjustment must be visible on the bill, or the total looks
  arbitrary.

### 🟡 SELECTED DIRECTION (2026-10-29) — principles only

The owner selected these **principles**, which are now binding:

1. **The system must have a documented decimal-precision and rounding policy.** It may not be
   implicit, emergent or per-module.
2. **The backend is the authoritative calculator** for every monetary value.
3. **The frontend and the printed bill display backend-calculated amounts without independently
   changing their meaning** — no client-side re-rounding, no re-derivation, no restatement.

These confirm and harden the existing architecture (FR-SALE-003, ADR-032, ADR-013). The client's
running total stays a clearly-labelled **preview**, cross-checked by `client_computed_total` and
surfaced as `TOTAL_MISMATCH` on disagreement rather than silently reconciled.

### ⚠️ What remains undecided — APPROVAL REQUIRED

| Open question | Status |
|---|---|
| Decimal **precision** for amounts (2 dp? more for unit prices?) | 🔴 **UNDECIDED** |
| **Rounding method** (half-up, half-even, truncate) | 🔴 **UNDECIDED** |
| **Where** rounding applies (per line, then sum? or on the grand total?) | 🔴 **UNDECIDED** |
| Whole-rupee rounding of the invoice total | 🔴 **UNDECIDED** |
| Domain-specific exceptions (e.g. oil sold by part-litre) | 🔴 **UNDECIDED** |

**No specific rounding algorithm is invented or adopted by this update, and no accounting approval is
claimed.** The existing documented *default* (round each line half-up to 2 dp, then sum;
whole-rupee rounding off) remains exactly that — a **documented proposal** awaiting the
owner/accountant, not a decision.

### Relationship to OD-23

[OD-23](#od-23) settled **display formatting** only — show the minimum decimals that represent a
value exactly. That is a rendering rule and **cannot substitute for this policy**. OD-23 renders
whatever OD-7 produces; it never changes it. An amount is never rounded to make a bill look tidier.

**Blocking level.** **Re-scoped in Phase 2 closure (P2-D-03) — now `BLOCKS-TESTING`, not
`BLOCKS-IMPLEMENTATION`.**

The original concern stands and is the reason this item exists: the rounding rule is the one piece of
logic duplicated on both sides (client preview and server authority), and fixing it late would
invalidate the money suites in both repositories. **Phase 2 removes that exposure structurally
rather than by waiting:** the rule becomes an explicit `MoneyPolicy` object resolved from `settings`
and published through `GET /sync/bootstrap` — which already carries "rounding" among the
client-relevant settings (`API-SPECIFICATION.md §3.12`) and which the client is already forbidden to
hardcode (`../../bmfrontend/CLAUDE.md §5` rule 15). Both sides then **consume one published policy**
instead of holding two constants that can silently diverge.

Consequently the engine, its property tests (run across **all** policy combinations) and
`lib/money.ts` are buildable now, and what remains blocked is **(i)** selecting the production policy
values and **(ii)** freezing the expected-value assertions in the two money acceptance suites.

**A later change of policy affects newly computed values only** — issued invoices keep their
snapshots and are never recomputed (FR-SALE-005, ADR-015), so no migration on live financial data is
implied. `invoices.rounding_adjustment` already exists, so enabling whole-rupee rounding later needs
no schema change.

**Approver.** Owner's accountant (precision and method); owner (coin-handling preference).
**No policy value is adopted by this re-scoping.**

---

<a id="od-8"></a>
## OD-8 — The offline expectation: draft safety is not offline billing ⚠️

*Supersedes the "offline expectation gap". Related: C-1, C-3, FR-OFFLINE-001/002/013, ADR-011.*

**Question.** Does the owner accept that during an internet outage staff can keep **capturing** bills
but cannot **issue** a numbered invoice until the connection returns?

**Already confirmed.** Cloud-first with offline capability (C-1). No overselling (C-3). Auditable
invoices and stock history (C-7). **These three cannot all hold if billing continues offline** — two
disconnected counters cannot both check the last unit of stock, and neither can allocate a
server-controlled invoice number.

**Options.**
- **(a) Offline drafts only (the designed scope).** Create, edit and switch bills offline; print an
  unnumbered, watermarked counter slip; finalise when back online.
- **(b) Full offline billing.** Requires per-counter stock allocation **and** per-counter invoice
  number blocks.
- **(c) Offline billing with no stock checking.** "Fix it afterwards."

**Consequences.**
- (a) No work is lost and nothing is re-typed. **But the customer cannot be handed a numbered
  invoice during the outage** — only a counter slip, with the real bill issued afterwards, dated at
  finalisation time. Staff must understand this, which is why training is a go-live gate.
- (b) Stock becomes fragmented and partly invisible between counters; allocations must expire and
  reconcile; **invoice numbering gains permanent gaps**, which may conflict with OD-3. A substantial
  additional design, not a feature toggle.
- (c) **Rejected.** Abandons C-3 and produces negative stock, duplicate numbers and unreconcilable
  books — it trades a visible inconvenience for invisible corruption.

**Recommended default.** (a). Rationale: it preserves every confirmed requirement while removing the
real pain (lost work and re-typing). The limitation is honest and visible rather than a silent
correctness risk.

**Note on the invoice date.** Under (a), a bill captured during an outage is **dated when it is
finalised**, not when it was typed. If that distinction matters for the shop's books, say so — it is
a further question, not covered by this default.

**Blocking level.** `BLOCKS-GO-LIVE` — implementation proceeds on (a), but the owner must accept the
trade-off and staff must be trained, or the first outage will be experienced as a system failure.

**Approver.** Owner.

---

<a id="od-9"></a>
## OD-9 — Should an open draft reserve stock?

*Related: FR-DRAFT-005, ADR-006.*

**Question.** When a part is added to an open bill, should that stock be held against other counters?

**Already confirmed.** At least 20 drafts open per counter (C-5). No overselling (C-3).

**Options.** (a) No reservation — stock checked only at finalisation · (b) Reserve on add, release on
removal or timeout · (c) No reservation, but *show* "N in other open bills".

**Consequences.**
- (a) **What the design implements.** A draft may fail at finalisation with a clear
  `INSUFFICIENT_STOCK` message naming the shortfall. Matches physical reality: the part is picked off
  the shelf, and a draft is a note in progress.
- (b) With 20 drafts per counter across several counters, large amounts of stock become invisible to
  everyone else, and every abandoned draft strands inventory until a timeout expires. Requires a
  whole reservation-expiry subsystem.
- (c) Useful visibility with no locking. A modest UI addition.

**Recommended default.** (a), with (c) as a later enhancement if staff ask for it.

**Blocking level.** `NON-BLOCKING`.

**Approver.** Owner — it determines what staff see when two counters want the same part.

---

<a id="od-10"></a>
## OD-10 — Who may cancel an invoice, and for how long after issue?

*Supersedes Q-BIZ-8. Related: FR-SALE-011, ADR-015.*

**Question.** Who can cancel a finalised bill, and within what time?

**Already confirmed.** Issued invoices are **immutable**; cancellation reverses stock and issues a
full credit note, keeps the invoice number, and is audited. There is no edit path, ever.

**Options.** (a) Manager and owner, 24-hour window · (b) Owner only, any time · (c) Cashier, same
shift only.

**Consequences.**
- (a) **Default.** Covers same-day mistakes without a bottleneck.
- (b) Maximum control; the owner is interrupted for routine errors.
- (c) Fast, but the staff member who made the error can erase the evidence — weakens the main
  internal control (threat T-1).

**Recommended default.** (a) `billing.sale.cancel` held by manager and owner,
`sales.cancel_window_hours = 24`, reason mandatory. After the window, corrections are credit notes.

**Blocking level.** `NON-BLOCKING` — a settings value and a role assignment.

**Approver.** Owner.

---

<a id="od-11"></a>
## OD-11 — Are returns accepted, and within what window?

*Supersedes Q-BIZ-2. Related: FR-RET-001…005.*

**Question.** Does the shop accept returns? Within how many days? Are damaged parts restocked?

**Already confirmed.** Returns reference specific invoice items; cumulative returns can never exceed
the quantity sold; a credit note is issued at the original snapshot price.

**Options.** (a) 30 days, manager permission · (b) No returns · (c) Longer window, or per-supplier
rules.

**Consequences.**
- (a) **Default.** Covers wrong-part purchases, the common case in parts retail.
- (b) The returns module is unnecessary — a scope reduction worth knowing early.
- (c) Needs rules the system does not currently model.

**Recommended default.** (a) `returns.window_days = 30`, `returns.create` for manager and owner;
damaged items route to a scrap movement rather than sellable stock.

**Blocking level.** `NON-BLOCKING` (returns are P1, after go-live).

**Approver.** Owner.

---

<a id="od-12"></a>
## OD-12 — Credit terms and credit limits

*Supersedes Q-BIZ-3. Related: FR-CREDIT-005/006.*

**Question.** Standard payment terms for credit customers? Should the system enforce a credit limit?

**Already confirmed.** Outstanding balance is always derived from recorded payments and allocations —
never a manually editable field.

**Options.** (a) Per-customer terms, default 30 days, limits optional and warn-only · (b) One fixed
term for everyone · (c) Enforce hard credit limits that block a sale.

**Consequences.**
- (a) **Default.** Flexible; the owner can set a limit per customer where it matters.
- (b) Simpler, but transport customers commonly negotiate individually.
- (c) Blocks a sale at the counter in front of the customer. Powerful but needs an override path and
  a clear staff script, or it will be bypassed.

**Recommended default.** (a) `credit.default_terms_days = 30`, `credit.enforce_limit = false`
(warn only). Rationale: a warning informs without creating a counter confrontation on day one.

**Blocking level.** `NON-BLOCKING`.

**Approver.** Owner.

---

<a id="od-13"></a>
## OD-13 — May a cashier give discounts?

*Supersedes Q-BIZ-4. Related: FR-SALE-014, `discount.cashier_max_percent`.*

**Question.** Can counter staff discount a line or a bill? Up to what percentage?

**Already confirmed.** Every discount is recorded with before/after values and the acting user.
Price overrides require a separate permission.

**Options.** (a) No cashier discount (cap 0%) · (b) A capped allowance, e.g. 5% · (c) Unlimited.

**Consequences.**
- (a) **Default.** Every discount goes through a manager, fully attributable.
- (b) Faster for routine haggling; raises the margin-leakage risk the audit log is there to detect.
- (c) Not recommended for a cash counter.

**Recommended default.** (a) `discount.cashier_max_percent = 0`, raised deliberately once the owner
sees the discount report. Rationale: starting at zero and loosening is easy; the reverse is a
conversation with staff.

**Blocking level.** `NON-BLOCKING`.

**Approver.** Owner.

---

<a id="od-14"></a>
## OD-14 — Billing button wording

*Related: FR-BILL-009, FR-BILL-013, FR-CREDIT-026, ADR-027.*

**Question.** Are these the right words for the three billing actions?

| Action | Proposed label | Proposed sub-label |
|---|---|---|
| Cash sale | `Print Bill` | "Complete cash sale and print" |
| Credit sale | `Save / Credit Bill` | "Record as credit — payment due later" |
| Draft only | `Keep for later` | "Saves this bill as a draft — nothing has been sold" |

> **Updated 2026-10-29 by [OD-21](#od-21)/ADR-033.** The credit action no longer has a second
> confirm button (`Finalise Credit Bill`) because the intermediate dialog is gone. The wording load
> now falls on the **confirmation card** shown after finalisation, which must state the invoice
> number, the outstanding amount and — critically — **"Stock has been updated."** so nobody mistakes
> a completed credit sale for a saved draft.

**Already confirmed.** The owner's workflow has two prominent actions, `Print Bill` and
`Save / Credit Bill` (C-9 context, §3.5 of the original brief).

**Options.**
- **(a) Keep both owner-familiar buttons, disambiguate with sub-labels, add a quiet third draft
  action.**
- **(b) One `Complete & Print` button with a cash/credit choice inside.**
- **(c) Keep the original two labels unchanged.**

**Consequences.**
- (a) **Default.** Preserves the owner's mental model; removes the ambiguity in the word "save".
  Three affordances instead of two.
- (b) Arguably cleaner, but discards the familiar shape and adds a step to the most frequent action.
- (c) **Real risk:** a staff member presses "Save", believes nothing happened, and stock is already
  gone. This is the specific failure the wording exists to prevent.

### ✅ RE-ANSWERED BY THE OWNER — four primary actions

The owner now specifies **exactly these four**, and no others:

| # | Label — **final** | Effect |
|---|---|---|
| 1 | **`Print Bill`** | Finalises a **cash** sale through the authoritative backend, updates stock **exactly once**, then opens printing. Printing is **not** finalisation |
| 2 | **`Credit Bill`** | Finalises a **credit** sale **immediately** through the **same** engine, updates stock **exactly once**, records the opening outstanding. Requires a persisted `customer_id` + name. No print dialog opens |
| 3 | **`Save a Draft`** | Saves an editable draft **only** — no finalisation, no stock movement, no payment, no credit invoice, no customer-facing number |
| 4 | **`Clear Draft`** | Clears the **current editable draft** after confirmation. **Never** deletes or reverses a finalised sale, and **never** discards an uncertain finalisation attempt or its idempotency key |

#### Mapping from the superseded labels

| Previous (2026-10-29) | Now | Note |
|---|---|---|
| `Print Bill` | **`Print Bill`** | Unchanged |
| `Save / Credit Bill` | **`Credit Bill`** | **Label change only.** The approved OD-21 / ADR-033 behaviour is preserved exactly |
| `Keep for later` | **`Save a Draft`** | **Label change only.** FR-BILL-013's draft-only semantics are preserved exactly |
| *(discard — a secondary affordance)* | **`Clear Draft`** | **Promoted to a primary action.** Previously `Discard` (`UI-UX-SPECIFICATION.md §4.6`) / FR-BILL-012 |

**The ambiguity OD-14 originally existed to remove is now removed by the labels themselves:**
`Credit Bill` no longer contains the word "save", and `Save a Draft` says *draft* explicitly — so the
two can no longer be confused. The confirmation card's *"Stock has been updated."* safeguard
(FR-CREDIT-026) is **retained**, because a completed credit sale must still be unmistakable.

#### `Clear Draft` safety rules — load-bearing

1. Requires a confirmation stating **how many lines will be lost** (FR-BILL-012).
2. Operates on the **editable draft only**. A finalised sale is immutable (ADR-015) and is corrected
   by cancellation, return or credit note — **never** by clearing a draft.
3. **Must be unavailable, and must clear nothing, while that bill holds an unresolved
   `pending_finalizations` row.** In that state the bill is read-only and the only exits are
   `Retry now`, `Print counter slip`, `Check Daily Bills`, or the supervised `Discard this attempt`
   (Blueprint §5.4.4). **Clearing must never destroy an idempotency key that may already have
   produced a sale.**

**Do not invent a fifth primary action.**

**Blocking level.** **`NON-BLOCKING`** — the four labels are a fixed contract that UI acceptance
tests may assert against.

**Approver.** Owner — **granted**.

---

### Historical record — the superseded three-label decision

*Retained so the history stays auditable. **It no longer governs.***

### ✅ SETTLED (2026-10-29) — existing labels retained — ⚠️ SUPERSEDED

The owner selected: **keep the three action labels exactly as documented.**

| Action | Label — **final** |
|---|---|
| Cash sale | **`Print Bill`** |
| Credit sale | **`Save / Credit Bill`** |
| Draft only | **`Keep for later`** |

**Do not rename these buttons or redesign the action vocabulary.** The labels are now a fixed
contract that UI acceptance tests may assert against.

### Confirmed semantics — unchanged by this wording decision

| Action | Behaviour |
|---|---|
| `Print Bill` | Finalises a **cash** sale, updates stock **exactly once**, and opens printing **only after** successful finalisation (FR-SALE-020…023) |
| `Save / Credit Bill` | Finalises a **credit** sale **immediately**, updates stock **exactly once**, saves the invoice under the selected customer's Credit Bills grouping. **No separate mandatory credit-details page** ([OD-21](#od-21), ADR-033) |
| `Keep for later` | Saves a **draft only** — no invoice number, no stock change, no payment implication (FR-BILL-013) |

**This wording decision changes no behaviour.** The approved credit workflow from OD-21 stands
exactly as documented.

The sub-labels and the confirmation-card wording remain **documented proposals** — in particular the
credit confirmation must still state the invoice number, the outstanding amount and
*"Stock has been updated."* (FR-CREDIT-026), which is the safeguard that replaced the removed dialog.

**Blocking level.** **`NON-BLOCKING`** — downgraded from `BLOCKS-TESTING`. The three primary labels
are fixed, so UI acceptance strings for them can be frozen.

**Approver.** Owner — **granted** for the action labels.

---

<a id="od-15"></a>
## OD-15 — Must a PDF copy of every invoice be archived?

*Related: FR-PRINT-005, ADR-014.*

**Question.** Does the business need a stored electronic copy of each bill, beyond the database record?

**Already confirmed.** Every invoice is stored as immutable line-item snapshots and re-renders
deterministically, so a past bill can always be reproduced exactly without an archived file.

**Options.** (a) No archived file — regenerate on demand · (b) Generate and store a PDF per invoice ·
(c) Generate PDFs on demand for emailing only.

**Consequences.**
- (a) **Default.** No storage cost, no extra infrastructure. Reprint already reproduces the document.
- (b) Needs a server-side rendering component, storage, retention and backup of those files. Worth it
  only if an auditor insists on files rather than records.
- (c) A middle path if the owner wants to email bills later.

**Recommended default.** (a). Rationale: the immutable snapshot is a stronger record than a PDF,
and server PDF generation is deferred (FR-PRINT-005) rather than refused.

**Blocking level.** `NON-BLOCKING`.

**Approver.** Owner, advised by their accountant.

---

<a id="od-16"></a>
## OD-16 — Should reprints be marked "DUPLICATE COPY"?

*Supersedes Q-BIZ-6. Related: FR-PRINT-004, `print.mark_reprints_duplicate`.*

**Question.** When a bill is printed a second time, should the copy be visibly marked?

**Already confirmed.** Reprinting is a read-only re-render and can never create a second sale. Print
attempts are logged with user, device and copy number.

**Options.** (a) Mark reprints `DUPLICATE COPY` · (b) Print reprints identically to the original.

**Consequences.**
- (a) **Default.** Prevents a reprint being presented as a second original — common statutory
  practice and a sensible internal control.
- (b) Cleaner-looking, but two identical "originals" can circulate.

**Recommended default.** (a) `print.mark_reprints_duplicate = true`.

**Blocking level.** `NON-BLOCKING`.

**Approver.** Owner.

---

<a id="od-17"></a>
## OD-17 — Login style for counter staff

*Supersedes Q-BIZ-9. Related: FR-AUTH-001/005.*

**Question.** How do staff sign in at a shared counter machine?

**Already confirmed.** Every financial record stores the actual acting user. Devices are registered
and remotely revocable.

**Options.** (a) Username + password, 30-day trusted device, optional PIN quick-switch between staff ·
(b) Password every time · (c) One shared counter login.

**Consequences.**
- (a) **Default.** Fast shift changes while keeping each sale attributable to a real person.
- (b) Secure but slow; staff will write passwords down or stay logged in as one person.
- (c) **Destroys attribution** — "who sold this?" becomes unanswerable, and the audit log loses most
  of its value.

**Recommended default.** (a).

**Blocking level.** `NON-BLOCKING` (PIN switch is P1).

**Approver.** Owner.

---

<a id="od-18"></a>
## OD-18 — How many counters and branches at launch?

*Supersedes Q-BIZ-1, Q-OPS-1 (scale half). Related: FR-ADMIN-002/003, FR-INV-010.*

**Question.** How many billing counters run at once, and is there more than one shop location?

**Already confirmed.** Multiple staff and counters must work simultaneously (C-2). Design target: 3
counters, 5 concurrent users. The schema is branch-aware from day one.

**Options.** (a) One branch, 1–3 counters · (b) One branch, more counters · (c) Multiple branches.

**Consequences.**
- (a) **Default.** Matches the design target with large headroom.
- (b) Fine; watch the invoice-series lock, whose documented remedy is per-counter series (OD-3(c)) —
  which interacts with the gapless requirement.
- (c) **Needs the branch-transfer module, which is explicitly out of MVP scope.** Stock received at
  one branch cannot be sold at another until that is built; until then, mis-received stock is
  corrected with two reasoned adjustments.

**Recommended default.** (a) one branch at launch.

**Blocking level.** `NON-BLOCKING` for (a)/(b); (c) blocks go-live for the second branch only.

**Approver.** Owner.

---

<a id="od-19"></a>
## OD-19 — Hosting, alert recipient, and budget

*Supersedes Q-OPS-1…6. Related: NFR-REL-001/002, NFR-OPS-003.*

**Question.** Where is this hosted, who receives a production alert at 9 p.m., and what monthly
infrastructure cost is acceptable?

**Already confirmed.** Single-region deployment, one API replica, managed PostgreSQL, 99.5% monthly
availability target, RPO ≤ 5 minutes via point-in-time recovery.

**Options.** (a) Managed platform in the nearest region, alerts to owner + developer ·
(b) Self-hosted on a shop machine · (c) Multi-region.

**Consequences.**
- (a) **Default.** Low cost, managed backups. Accepts brief restart outages — during which counters
  keep capturing drafts.
- (b) No monthly fee, but backups, patching and uptime become the shop's problem, and remote access
  from the owner's phone stops working.
- (c) Unnecessary at this scale and materially more expensive. **The 99.5% target does not imply hot
  standby, and nothing in the design pretends otherwise.**

**Recommended default.** (a), with a **named** alert recipient before go-live — an alert that reaches
nobody is not monitoring.

**Blocking level.** `BLOCKS-GO-LIVE`.

**Approver.** Owner.

---

<a id="od-20"></a>
## OD-20 — Counter browser and screen size

*Supersedes Q-FE-1, Q-FE-2, Q-FE-4. Related: NFR-FE-COMP-001/002.*

**Question.** Which browser runs on the counter machines, what screen resolution, and is any counter
a touchscreen?

**Already confirmed.** Desktop-first design. Print fidelity is guaranteed on Chrome and Edge only.

**Options.** (a) Chrome/Edge, 1366×768 minimum · (b) Firefox or Safari · (c) A touchscreen counter.

**Consequences.**
- (a) **Default.** Print verified here; layout optimised for 1920×1080 and usable to 1280×720.
- (b) Functional, but **print output is not guaranteed to match** — the bill is the one thing that
  must look right.
- (c) Hit targets are already ≥ 40 px so touch is not excluded, but keyboard-first billing (the
  speed requirement) assumes a physical keyboard. A touch-only counter would be materially slower.

**Recommended default.** (a).

**Blocking level.** `NON-BLOCKING`, but it determines where print verification happens (OD-6).

**Approver.** Owner.

---

<a id="od-21"></a>
## OD-21 — Credit billing workflow ✅ **CONFIRMED BY THE OWNER 2026-10-29**

*Status: **RESOLVED — this is now a confirmed requirement, not an open question.** Related:
FR-CREDIT-020…031, FR-CUST-010, FR-RPT-028…033, ADR-033 (`accepted`).*

**What the owner decided.** `Save / Credit Bill` **finalises the credit sale immediately**:

1. No navigation to the old credit-details page; no mandatory additional-information form.
2. Validate the selected customer and the genuinely mandatory sale fields only.
3. Finalise through the **same** backend engine as a cash sale, with credit semantics.
4. Atomically record invoice, invoice number, items, stock movements and the opening outstanding
   balance. **Stock changes immediately.**
5. Store the invoice against that customer and show it in the customer-wise Credit Bills area.
6. **No printing required at save time**; the invoice stays viewable and printable afterwards.
7. The printed credit bill uses the **same format as the cash bill**.
8. Additional customer fields remain **available but optional**.

**What this supersedes.** The Phase 1 design in which `Save / Credit Bill` opened a mandatory
credit-details dialog before finalising. That flow is **removed from the documentation**, not merely
deprecated.

**What it does *not* change.** Every safety property is retained: one finalisation engine, atomic
stock + invoice + number, idempotency, server-side money, derived payment status, immutable issued
records, and the **offline refusal** — a credit sale is a sale, so it cannot be finalised or queued
offline (`OFFLINE-SYNCHRONIZATION.md §3.1` invariant 2a).

**Blocking level.** `RESOLVED`. It unblocked credit implementation and *added* the obligation to
build the customer-wise Credit Bills view.

**Approver.** Owner — **granted**.

---

<a id="od-22"></a>
## OD-22 — Invoice number and payment block on the printed bill

*New, arising from inspecting the owner's reference screenshot. Related: FR-PRINT-010, FR-CREDIT-030,
`UI-UX-SPECIFICATION.md §12.2a`.*

**Question.** The owner's bill shows **no invoice-number field and no payment/outstanding block**.
The system allocates a **unique, sequential** invoice number to every sale, and credit bills need to
show what is owed. Where should these appear, and should they appear at all?

> *Corrected in Phase 2 closure: this paragraph previously read "gapless invoice number", asserting
> as established fact a property that [OD-3](#od-3) records as **undecided**. Audit finding CF-1.*

**Already confirmed.** The reference screenshot (top section) was inspected: heading, address,
mobile, Customer Name, Mechanic Name, Vehicle no, ESTIMATE, DATE, and the
`S R · ITEM · QTY · PRICE · TOTAL` table. **Neither an invoice number nor a payment block is
present.** The bottom of the bill was not visible, so neither can be ruled out there.

**Options.**
- **(a) Add the invoice number top-right, and a `Paid / Balance / Due` block below the total for
  credit bills only.** Both switchable off.
- **(b) Print neither.** Keep the bill visually identical to today's.
- **(c) Invoice number only; no payment block.**

**Consequences.**
- (a) The customer can quote a bill number, and a credit customer sees what they owe — both
  genuinely useful. Risk: the bill stops looking like the familiar one, and placement is a guess
  until the bottom section is seen.
- (b) Maximum fidelity, but staff cannot reference a bill by number on paper, and a credit customer
  leaves with no record of their balance. Internally the number still exists.
- (c) A middle path if the owner considers balances private.

### 🟡 RECONCILED — the invoice-number half is withdrawn; the credit payment block remains open

Two of the owner's later answers change this item:

| Half of OD-22 | Status now |
|---|---|
| **Print an invoice number** | ❌ **WITHDRAWN.** [OD-3](#od-3) is re-answered — *"No need of invoice numbering."* There is no customer-visible number to place, so the placement question **disappears** rather than being answered. `FR-PRINT-019` is retired |
| **Print payment/balance information on credit bills** | ✅ **Still a SELECTED PREFERENCE** — unchanged. 🟡 **Placement still open**, and now harder: the owner's described footer (`GRAND TOTAL`, `THANK YOU VISIT AGAIN`, dealer credits, logos) contains **no slot for a `Paid / Balance / Due` block** |

**Consequence.** The payment block remains a `[PROPOSED]` element that must stay **switchable off
without altering the layout**, exactly as before. It is **credit-only** — the preference has never
covered cash bills and still does not. Where it sits relative to `GRAND TOTAL` and the footer needs
the owner's confirmation on a printed sample.

**Blocking level.** **`BLOCKS-GO-LIVE`** — downgraded from `BLOCKS-IMPLEMENTATION`. The template is
buildable with the block rendered in a switchable position; only sign-off is gated.

**Approver.** Owner — inclusion granted; **placement still requires a reviewed printed sample**.

---

### Historical record — the 2026-10-29 selection

*Retained so the history stays auditable. The invoice-number half **no longer applies**.*

### ✅ SELECTED PREFERENCE (2026-10-29) — option (a) — ⚠️ PARTLY SUPERSEDED

The owner selected:

1. **Include an invoice number on the printed bill.**
2. **Include payment and balance information for credit bills.**

### What that settles, and what it does not

| Aspect | Status |
|---|---|
| *Whether* to print an invoice number | ✅ **SELECTED PREFERENCE** — yes |
| *Whether* credit bills show payment/balance information | ✅ **SELECTED PREFERENCE** — yes |
| **Where** the invoice number goes | 🔴 **UNVERIFIED REFERENCE DETAIL** — the reference shows no invoice number anywhere, and the bill's lower section is unseen ([OD-5](#od-5)) |
| **Where** the payment/balance block goes | 🔴 **UNVERIFIED REFERENCE DETAIL** — same reason |
| Whether payment/balance appears on **cash** bills | 🟡 **NOT SELECTED.** The preference covers credit bills only. **Do not assume it applies to cash bills** — keep it credit-only until the owner says otherwise |
| Exact wording of the payment block's labels | 🟡 **DOCUMENTED PROPOSAL** (`Paid / Balance / Due`) |

### Binding rules for the print template

- The payment and balance figures must **accurately represent the recorded payments and the
  outstanding balance** from the authoritative invoice and allocation records (FR-CREDIT-002). The
  printed bill reports what the backend holds; it never computes, rounds or restates it
  (FR-PRINT-010, ADR-032).
- Both elements stay classified **`[PROPOSED]`** in
  `../../bmfrontend/docs/UI-UX-SPECIFICATION.md §12.2a` and must remain **switchable off without
  breaking the layout**. A selected preference to *include* something is not an approval of *how it
  looks*.
- New print elements must stay **visually distinguishable** from `[OBSERVED]` elements in the
  specification, so a future reader can always tell what came from the owner's sheet and what this
  project added.
- **Do not invent a layout for the unseen lower section.** Any placement proposal for the payment
  block is provisional and must be marked as such.
- Existing recommended placements already in the documentation are **preserved as proposals**, not
  promoted to approved.

**Blocking level.** **`BLOCKS-IMPLEMENTATION`** for the print template — unchanged. The *content*
question is answered; the *placement* question is not, and placement is coupled to
[OD-5](#od-5), which the owner cannot currently advance. The header, field block and item table can
still be built from observed evidence.

**Approver.** Owner — **inclusion granted; placement still requires a reviewed sample.**

---

<a id="od-23"></a>
## OD-23 — Amount formatting and the zero-filled empty rows

*New, from the reference screenshot. Related: `UI-UX-SPECIFICATION.md §12.2`, [OD-7](#od-7).*

**Question.** The reference prints amounts as plain integers (`240`, `3850`) with no decimals, no
thousands separators and no currency symbol, and shows **`0` in `TOTAL` on every empty
pre-numbered row**. Should the system reproduce both behaviours?

**Already confirmed.** Internally all money is exact decimal (`NUMERIC`) regardless of display —
this question is **presentation only** and does not affect stored values or arithmetic.

**Options.**
- **(a) Reproduce the reference exactly** — integers, no separators, zeros on empty rows.
- **(b) Reproduce amount style, but leave empty rows blank** instead of printing `0`.
- **(c) Modernise** — two decimals and Indian grouping (`3,850.00`), empty rows blank.

**Consequences.**
- (a) Maximum familiarity. But a column of `0`s is an artefact of a spreadsheet formula, not a
  deliberate design, and a part priced at `240.50` could not be shown correctly.
- (b) Keeps the familiar number style while dropping a likely-unintentional artefact.
- (c) Clearer for larger amounts and necessary if any price has paise, but visibly different from
  today's bill.

### 🟡 AMENDED BY THE OWNER — no decimal places in `PRICE` or `GRAND TOTAL`

**Owner's instruction: amounts must not have decimal places in `PRICE` or `GRAND TOTAL`.**

This **amends** the 2026-10-29 minimum-exact-decimals rule (preserved below). Where the two agree —
a whole amount prints as `240`, never `240.00` — nothing changes. **They disagree only when an
amount actually carries paise.**

#### What is settled

| Column | Rule | Status |
|---|---|---|
| `PRICE` | **No decimal places** | ✅ **SELECTED PREFERENCE** |
| `GRAND TOTAL` | **No decimal places** | ✅ **SELECTED PREFERENCE** |
| `TOTAL` (line total) | **Not named by the owner.** The original bill shows integers here | 🟡 **INFERENCE** — apply the same rule, but it is an inference, not an instruction |
| Unused item rows | Print **blank**, never `0` | ✅ Unchanged from 2026-10-29 |

#### 🔴 RECORDED CONFLICT — the fractional-amount case is NOT resolved

**A zero-decimal display cannot represent `240.50` without changing what the customer is told.**
This is a genuine, unresolved conflict and **it is not resolved by rounding**:

- **Stored values are never altered for display.** Money stays exact decimal end to end — `NUMERIC`
  in Postgres, `Decimal` in Python, strings over JSON (ADR-013, NFR-DATA-009). **An amount is never
  rounded, truncated or re-scaled to satisfy a display rule.**
- If a stored amount carries paise, rendering it with zero decimals would **misstate the bill**: the
  printed `PRICE` × `QTY` would not equal the printed `TOTAL`, and the column would not sum to the
  printed `GRAND TOTAL`.

**Options for the owner (none adopted):**

| Option | Consequence |
|---|---|
| **(a) Constrain prices to whole rupees at entry** | The conflict disappears at source and every printed bill adds up. **Requires the owner to accept that no part-rupee price can be entered** — a business-rule change, not a display change |
| **(b) Print the paise when they exist** | The bill always adds up, but the instruction is not followed literally in that case |
| **(c) Print rounded, with the rounding shown** | Needs a visible rounding line; the owner's described footer has no slot for one |

**Recommended: (a)** — the only option that both honours the instruction literally and keeps the bill
arithmetically truthful. **It is a recommendation, not a decision**, and it interacts with
[OD-7](#od-7): if whole-rupee prices are adopted, OD-7's whole-rupee-rounding question largely
answers itself.

**Until the owner chooses, the system must** keep exact stored values, display zero decimals for
whole amounts, and **surface a validation warning rather than silently rounding** if a fractional
amount reaches a zero-decimal field.

#### Relationship to OD-7 — unchanged

This remains a **display/presentation** decision. It does **not** settle calculation precision,
rounding method, rounding stage or fractional-rupee treatment — those are **[OD-7](#od-7)** and
remain open.

**Blocking level.** **`BLOCKS-GO-LIVE`** — upgraded from `NON-BLOCKING`. A bill that may misstate a
price cannot be handed to a customer. The *whole-amount* case is implementable now.

**Approver.** Owner — granted for whole amounts; **the fractional case requires an answer**.

---

### Historical record — the superseded 2026-10-29 display rule

*Retained so the history stays auditable. The whole-amount part still governs; the
minimum-exact-decimals part is amended for `PRICE` and `GRAND TOTAL`.*

### ✅ SELECTED PREFERENCE (2026-10-29) — option (b) — ⚠️ AMENDED

The owner selected:

1. **Whole amounts print without decimal places** when the value is an exact whole amount
   (`240`, not `240.00`).
2. **Decimal places appear when needed** to represent the amount accurately (`240.50`).
3. **Unused item rows print blank** — the reference's `0`s are **not** reproduced. *(Corrected
   2026-10-30: those `0`s are **hand-typed literals**, not formula output — the workbook has no
   formulas at all. The decision is unchanged; only its stated rationale was wrong.)*

### The rule this creates

> **Display precision is chosen per value: show the minimum decimals that represent the amount
> exactly. Never pad, never truncate.**

| Stored value | Printed as |
|---|---|
| `240.00` | `240` |
| `3850.00` | `3850` |
| `240.50` | `240.50` |
| `240.05` | `240.05` |
| empty row | *(blank — no `0`)* |

### Display formatting ≠ calculation precision — the critical separation

This preference governs **presentation only**. It must never change a stored or computed value:

- Monetary values remain exact decimals end to end — `NUMERIC` in PostgreSQL, `Decimal` in Python,
  strings over JSON (ADR-013, NFR-DATA-009).
- **An amount is never rounded to make the printed bill look cleaner.** If a value carries paise, the
  bill shows them.
- Formatting happens at the rendering boundary, from the backend-supplied value, and changes nothing
  about its meaning (FR-PRINT-010).
- **The accounting precision and rounding policy remain [OD-7](#od-7)** and are unaffected by this
  display rule. If OD-7 later establishes, say, whole-rupee invoice rounding, that is an *arithmetic*
  decision producing a different stored value — which this display rule would then render faithfully.

**Blocking level.** **`NON-BLOCKING`** — downgraded from `BLOCKS-IMPLEMENTATION`. The display rule
is fully specified and can be implemented as written. It no longer blocks the print template.

**Approver.** Owner — **granted** for display formatting. *(Calculation precision is OD-7 and
remains open.)*

---

<a id="od-24"></a>
## OD-24 — The printed document label

*New, from the reference screenshot. Related: FR-TAX-001, FR-PRINT-002, [OD-1](#od-1), ADR-020.*

**Question.** The reference bill is labelled **`ESTIMATE`**. Is that the intended label for every
printed sale, including credit sales — or is a different word wanted?

**Already confirmed.** The screenshot shows the literal text `ESTIMATE`, left-aligned in column A on
the same row as `DATE`. The system already defaults `tax.mode = estimate_only`, which independently
produces the title `ESTIMATE`. **These agree today** — the reference and the system default match.

**Options.**
- **(a) Keep `ESTIMATE` for all printed sales** (matches both the reference and the current mode).
- **(b) Use a different word** — e.g. `BILL`, `CASH BILL`, `INVOICE`.
- **(c) Vary by sale type** — e.g. `ESTIMATE` for cash, `CREDIT BILL` for credit.

**Consequences.**
- (a) Zero change; printed bills look exactly as they do now. Note an `ESTIMATE` is not a tax invoice
  and does not support input-tax credit for business customers.
- (b) **Must not be chosen casually.** `INVOICE` or `TAX INVOICE` carries statutory meaning; the
  system will not print `TAX INVOICE` at all until the [OD-1](#od-1)/[OD-2](#od-2) gate passes
  (FR-TAX-003/004).
- (c) Rejected as a default — a differing label would make credit bills look like a different
  document, against the owner's instruction that both use the same format (FR-CREDIT-030). The
  sale type is already conveyed by the payment information.

### ✅ SELECTED PREFERENCE (2026-10-29) — option (a)

The owner selected: **retain `ESTIMATE` for both cash and credit documents.**

This is consistent with two independent things that already agree:

| Source | Says |
|---|---|
| The reference screenshot | **`ESTIMATE`** — directly observed, left-aligned in column A on the `DATE` row (`[OBSERVED]`) |
| The system's default `tax.mode = estimate_only` | Produces the title `ESTIMATE` (FR-TAX-001) |
| **The owner's preference** | **`ESTIMATE`** for both workflows |

So no change to behaviour is required — the preference confirms the existing state.

### ⚠️ What this preference is NOT

**It is not legal, tax or go-live approval.** A preference about wording cannot settle whether the
document the shop hands a customer is the correct instrument for its tax status. Specifically:

- An `ESTIMATE` is **not a tax invoice** and does not support input-tax credit for business
  customers. If the shop is or becomes GST-registered ([OD-1](#od-1)), continuing to issue estimates
  may be inadequate — that is an accountant's call, not a wording preference.
- **`BLOCKS-GO-LIVE` therefore remains active.** It is cleared only when the appropriate owner —
  advised by their accountant — confirms the final printed-document terminology in light of the
  resolved tax status.

### Guardrails retained

- `BILL`, `INVOICE` and `TAX INVOICE` are **not** introduced as replacements. No substitute label is
  used anywhere.
- The title is rendered from `print_payload.document_title` only; the client never hardcodes it.
- The literal `"Tax Invoice"` **must not appear in the frontend source** (FR-PRINT-011, AC-FE-10),
  and the system cannot emit it until the `FR-TAX-003` gate passes.
- Cash and credit use the **same** label — a differing label would make credit bills look like a
  different document, against FR-CREDIT-030.

**Blocking level.** **`BLOCKS-GO-LIVE`** — unchanged. The preference is recorded; the compliance
question is untouched by it.

**Approver.** Owner, advised by their accountant — **wording preference supplied; terminology
approval still outstanding.**

---

<a id="od-25"></a>
## OD-25 — Does the law require a serially numbered tax invoice? 🔴 **NEW**

*Created by [OD-3](#od-3)'s re-answer. Related: FR-TAX-001…004, [OD-1](#od-1), [OD-2](#od-2),
[OD-24](#od-24), ADR-020.*

**Question.** The owner has instructed that **no customer-visible invoice numbering** is needed. Does
any law applicable to National Auto Spares nevertheless require a **consecutive serial number** on
the documents it hands customers — and if so, from what point?

**Already confirmed.** The owner does not want visible numbering (OD-3). The system currently issues
documents titled `ESTIMATE` with `tax.mode = estimate_only`, computes no tax, and **cannot** emit
`TAX INVOICE` until the `FR-TAX-003` gate passes. An **internal, non-printed** sale reference is
retained and is already race-safe and auditable.

**Why this is a separate decision and not part of OD-3.** OD-3 settles **what the owner wants**. It
cannot settle **what the law requires** — a preference is not a compliance determination. Recording
them as one item would let a product preference read as legal clearance, which the register's
four-level status vocabulary exists to prevent.

**Options.**
- **(a) Not required** — because the shop is unregistered/below threshold and issues estimates, not
  tax invoices. Current design is compliant as built.
- **(b) Required once GST-registered.** Visible serial numbering must be enabled **before** any
  `TAX INVOICE` is issued.
- **(c) Required even for estimates** under some rule applicable to the shop.

**Consequences.**
- (a) No change. The internal reference stays internal.
- (b) **The mechanism already exists** — OD-3 deliberately retained the internal in-transaction
  allocation for exactly this contingency. Enabling it is **configuration plus rendering**, not a
  redesign: expose `sequence_number` through `print_payload` and render it. **The gapless question
  then revives** and must be answered before issuing tax invoices.
- (c) Same mechanism, enabled immediately; `ESTIMATE` documents would carry a serial number.

**Recommended default.** Keep numbering **internal-only and unprinted**, as the owner instructed, and
**treat this as a go-live gate**. Rationale: it honours the owner's decision now, costs nothing, and
preserves a compliant path if the accountant answers (b) or (c). **No compliance claim is made.**

**Blocking level.** **`BLOCKS-GO-LIVE`**. Build and test freely. **Production use without an answer
carries unquantified statutory exposure**, and this project is not qualified to quantify it.

**Approver.** Owner, advised by their accountant. **Answer it together with
[OD-1](#od-1)/[OD-2](#od-2)** — the GST answer largely determines this one.

---

<a id="od-26"></a>
## OD-26 — Which date order should the printed bill use? 🔴 **NEW**

*Raised 2026-10-30 by direct inspection of the owner's workbook. Related: OD-5, FR-PRINT-013,
T3-11.*

**Question.** The printed bill shows a date. Should it read **day-month-year** (`06-06-2026` meaning
6 June) or **month-day-year** (`06-06-2026` meaning 6 June — the same, in this one example)?

**Why this is being asked at all.** It looks like a non-question, and that is exactly the trap.

Until now the project recorded **`DD-MM-YYYY`** as an `[OBSERVED]` fact, carried from the owner's
screenshot into `PRD.md`, the fidelity audit and the UI specification. **It was never observed.** It
was *inferred* — because the only example date in the bill is **6 June 2026**, which renders
identically in both orders and therefore distinguishes nothing.

Inspecting the actual file settles what the screenshot could not. Cell `E7` carries custom number
format **`164`**, whose format code is:

```
mm\-dd\-yyyy;@
```

**Month first.** The owner's real spreadsheet is formatted US-style.

**Why it matters.** On 6 June nothing goes wrong. On **7 August** the two orders produce `07-08-2026`
and `08-07-2026` — and a customer, a mechanic or the owner reading a credit bill months later cannot
tell which was meant. Ageing disputes on credit accounts turn on exactly this.

**Options.**

- **(a) `DD-MM-YYYY`** — the Indian convention, and what every other document in this project
  currently assumes. Diverges from the original spreadsheet.
- **(b) `MM-DD-YYYY`** — reproduces the workbook exactly. Likely to be misread by customers in
  India, and inconsistent with how dates appear everywhere else in the application.
- **(c) `DD-MMM-YYYY`** (`06-Jun-2026`) — unambiguous to every reader regardless of convention.
  Three characters wider; column E is 10.44 characters, so it fits.

**Recommended default — (a) `DD-MM-YYYY`**, on the assumption that the `mm-dd-yyyy` format is an
**accident of Excel's locale rather than an intention**. The shop is in Hyderabad and the rest of
the bill is written for Indian readers. **(c) is the safer engineering answer** and is offered in
case the owner wants the ambiguity gone permanently.

> ⚠️ **This is a recommendation, not a decision.** The system will not silently "correct" the
> owner's format, and will not blindly copy it either. Until this is answered, the specification
> records the measured fact (`mm-dd-yyyy` in the original) alongside the recommended default, and
> **the printed date format is not frozen**.

**Consequences.** Low implementation cost under any answer — it is one format string in the print
payload. The cost of getting it **wrong silently** is a bill whose date means something other than
what the reader thinks, which is why it is recorded rather than assumed.

**Blocking level.** **`BLOCKS-GO-LIVE`**. The print template can be built now against the
recommended default. The answer is required before a real bill is handed to a customer, and belongs
with the printed-sample sign-off under [OD-6](#od-6).

**Approver.** Owner.

---

## Items deliberately **not** in this register

These were resolved by engineering and need no owner input. They are recorded as ADRs in
`DECISIONS.md` with full rationale: language and framework choices (ADR-002, ADR-023), database and
search technology (ADR-005), job queue (ADR-004), locking strategy (ADR-008), serialisation
conventions (ADR-012, ADR-013), test infrastructure (ADR-017), migration process (ADR-016),
repository layout (ADR-021), and state management (ADR-024, ADR-025).

Two ADRs marked "owner approval needed" are **represented here rather than duplicated**: ADR-006 is
OD-9, ADR-011 is OD-8, ADR-014 is OD-5 + OD-6 + OD-15, ADR-015 is OD-10, ADR-020 is OD-1 + OD-2,
ADR-027 is OD-14, and ADR-009 is OD-3.

---

## Related documents

`PRD.md §4` (confirmed requirements) · `PRD.md §5` (summary of this register) ·
`DECISIONS.md` (ADR records) · `PRD.md §14` and `DEPLOYMENT.md §11.2` (go-live gates) ·
`../../bmfrontend/docs/PRD.md §4` (frontend-specific questions)
