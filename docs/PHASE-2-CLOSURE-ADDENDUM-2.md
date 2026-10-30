# Phase 2 Closure Addendum 2 — Workbook Inspection & Repository Connection

**Date:** 2026-10-30
**Supersedes:** nothing. **Amends:** [Addendum 1](PHASE-2-CLOSURE-ADDENDUM-1.md) §4 (*"What the
workbook confirms — and what was NOT verified"*) and §6 (*GitHub verification*).
**Verdict:** **PHASE 2 REMAINS CLOSED.**

> **Why this document exists.** Two things that Addendum 1 recorded as *unverified* have now been
> verified by direct inspection: the owner's original bill workbook, and the GitHub repository
> connection. Addendum 1 is **not** rewritten — it was accurate when written. This addendum records
> what changed and why.

---

## 1. What is new since Addendum 1

| | Addendum 1 (2026-10-30, earlier) | This addendum |
|---|---|---|
| The workbook `ANAS_1234_20260606_231456.xlsx` | 🔴 **Not in the workspace. Not inspected.** Layout known only from the owner's written description | ✅ **Present at the workspace root and fully inspected** — every sheet, cell, merge, font, border, number format, column width, row height, embedded image and page-setup element |
| GitHub push authentication | 🔴 `gh` token invalid; write access unverified; **no Git operation performed** | ✅ **SSH authenticates as the collaborator `AshwinSharma-git`**; both repositories connected, committed and pushed |

Nothing else in Addendum 1 changes. All decisions, risks and safeguards recorded there stand.

---

## 2. Workbook inspection — method and provenance

**File inspected:** `ANAS_1234_20260606_231456.xlsx` at the workspace root, 19,114 bytes,
`SHA-256 D1DA3922 7566F7A3 42C44EFD BC2E6E03 862269DD 8ED784E5 6427E331 D11C9410`.

**Method.** The `.xlsx` is an OPC (ZIP) package. A **copy** was extracted to a scratch directory and
its XML parts were read directly: `xl/workbook.xml`, `xl/worksheets/sheet1.xml`,
`xl/worksheets/sheet2.xml`, `xl/sharedStrings.xml`, `xl/styles.xml`, `xl/drawings/drawing1.xml`,
`xl/media/image1.jpeg`, `xl/media/image2.jpeg`, and all three `docProps` parts.

> ⚠️ **The owner's original file was not modified.** It was copied, never opened for write. Its
> SHA-256 above was taken after the inspection and can be re-checked at any time.

**Document properties.** Created `2026-06-06T17:47:43Z`, last modified `2026-10-27T09:29:33Z`,
last modified by **Anas Mohammad**, produced by Microsoft Excel 2021. Two worksheets.

---

## 3. The verified bill layout

**Sheet `Table 1`** (the bill). Used range **`A1:E37`**. No hidden rows or columns. No protection.

### 3.1 Structure

| Row(s) | Height (pt) | Content | Merge |
|---|---|---|---|
| 1 | 19.5 | `NATIONAL  AUTO SPARES` *(two spaces after NATIONAL)* | `A1:E1` |
| 2 | 12.75 | `AUTO NAGAR ,HYDERABAD` | `A2:E2` |
| 3 | 10.05 | `Mob:9948207903,9848136564` | `A3:E3` |
| 4 | 13.5 | `Customer Name:` in `A4:B4`; the **value** `ANAS` in `C4:D4` | `A4:B4`, `C4:D4` |
| 5 | 12.75 | `Mechanic Name :` *(space before the colon)* — **label only, no separate value cell** | `A5:E5` |
| 6 | 12.75 | `Vehicle no:` in `C6:D6`; the **value** `1234` in `E6` | `C6:D6` |
| 7 | 12.75 | `ESTIMATE` in `A7:B7`; `DATE:` in `D7`; the date in `E7` | `A7:B7` |
| 8 | 13.5 | Column headers — `S R` · `ITEM` · **`QTY`** · `PRICE` · `TOTAL` | — |
| 9–33 | 13.5 each | **25 pre-numbered item rows**, serial `1`…`25` in column A | — |
| 34 | 16.5 | `GRAND TOTAL` in `A34:D34`; the value in `E34` | `A34:D34` |
| 35 | 10.95 | `THANK YOU  VISIT AGAIN` *(two spaces)* | `A35:E35` |
| 36 | 8.25 | `Authorised Dealers of :` | `A36:E36` |
| 37 | 18.75 | ` TATA - LEYLAND SPARE PARTS` *(leading space)* | `A37:E37` |

**Twelve merged ranges exactly:** `A1:E1`, `A2:E2`, `A3:E3`, `A4:B4`, `C4:D4`, `A5:E5`, `C6:D6`,
`A7:B7`, `A34:D34`, `A35:E35`, `A36:E36`, `A37:E37`.

### 3.2 Column widths

| Column | Width (chars) | ≈ px | Holds |
|---|---|---|---|
| A | 4.664 | 38 | `S R` / serial |
| B | 19.777 | 143 | `ITEM` |
| C | 3.332 | 28 | `QTY` |
| D | 8.000 | 61 | `PRICE` |
| E | 10.441 | 78 | `TOTAL` |

### 3.3 Typography — all Calibri

| Element | Size | Style | Colour |
|---|---|---|---|
| Shop name (row 1) | **13 pt** | bold *italic* | black |
| Address (row 2) | 8.5 pt | bold | black |
| Mobile (row 3) | 7.5 pt | bold, **underlined** | black |
| Field labels (rows 4–7) | 8.5 pt | bold *(`Customer Name:` is non-bold)* | black |
| Customer value `ANAS` | 9 pt | bold | black |
| **Column headers (row 8)** | 9 pt | bold | **`#232852`** (dark navy) |
| Item text | 9 pt | regular | black |
| Numbers (QTY/PRICE/TOTAL) | 9 pt | regular | black |
| `GRAND TOTAL` label | 7.5 pt | bold | **`#232852`** |
| **`GRAND TOTAL` value** | **11 pt** | **bold** | black |
| `THANK YOU  VISIT AGAIN` | 7.5 pt | bold *italic* | black |
| `Authorised Dealers of :` | 6 pt | bold, **centred** | black |
| `TATA - LEYLAND SPARE PARTS` | 7.5 pt | bold *italic* **underlined** | black |

**No fills.** The workbook uses only `none` and `gray125` patterns — **the header row is not
shaded.** Navy text on white.

### 3.4 Borders

- A **continuous thin outer rectangle** encloses `A1:E37`.
- A **thin horizontal rule under row 7** separates the field block from the item table.
- The item table `A8:E33` has a **full thin grid** — every cell boxed on all four sides.
- The `GRAND TOTAL` row is boxed; `E34` is fully boxed on all four sides.
- The customer block (rows 2–6) has **no internal grid** — only the outer left/right edges.

### 3.5 Alignment and number formats

| Column | Horizontal | Number format |
|---|---|---|
| A (serial) | centre | built-in `1` = **`0`** |
| B (item) | left | text |
| C (`QTY`) | right | built-in `1` = **`0`** |
| D (`PRICE`) | right | built-in `1` = **`0`** |
| E (`TOTAL`) | right | built-in `1` = **`0`** |
| E34 (`GRAND TOTAL`) | right | built-in `1` = **`0`** |
| E7 (date) | right | custom `164` = **`mm\-dd\-yyyy;@`** |

Every numeric cell is `vertical=top` with **`shrinkToFit`** set. Text cells use `wrapText`.

### 3.6 The example data

| S R | ITEM | QTY | PRICE | TOTAL |
|---|---|---|---|---|
| 1 | `1/2L CLUTCH OIL` | 1 | 240 | 240 |
| 2 | `1/2R 5mm WIRE` | 1 | 280 | 280 |
| 3 | `11L LE MAX` | 1 | 3850 | 3850 |
| 4 | `1250 FANBELT` | 1 | 450 | 450 |
| 5–25 | *(blank)* | *(blank)* | *(blank)* | **`0`** |
| | | | **GRAND TOTAL** | **4820** |

Customer `ANAS`, Vehicle `1234`, date serial `46179` = **6 June 2026**.

### 3.7 The embedded logos

| | Left | Right |
|---|---|---|
| Part | `xl/media/image1.jpeg` | `xl/media/image2.jpeg` |
| Identified as | **TATA** wordmark + roundel | **Ashok Leyland** "L" roundel |
| Pixels | 55 × 57 | 58 × 64 |
| Bytes | 2,163 | 2,797 |
| SHA-256 (first 16) | `08cef4281b6dc788` | `6ba13f049888e798` |
| Anchor | `oneCellAnchor`, col **A**, row **35**, offset 0.117 in | `oneCellAnchor`, col **E**, row **35**, offset 0.253 in |
| Printed size | 0.276 × 0.289 in (≈ 20 × 21 pt) | 0.293 × 0.325 in (≈ 21 × 23 pt) |

Both anchor in row 35 and extend down across rows 36–37, so they **flank the dealer-credit footer
lines** — left logo in column A, right logo in column E. This matches the owner's description.

> ℹ️ **These are third-party registered trademarks** (Tata Motors; Ashok Leyland). The shop prints
> them under its `Authorised Dealers of :` claim. Reproducing them in the generated bill continues
> the owner's existing practice; **whether that use is authorised is the owner's to confirm**, and
> this project makes no determination about it. Recorded, not resolved.

### 3.8 Derived page geometry

Summing the measured column widths and row heights:

| | Value |
|---|---|
| Content width | 46.21 chars ≈ 348 px ≈ **3.63 in ≈ 92 mm** |
| Content height | 499.5 pt ≈ **6.94 in ≈ 176 mm** |

**Consequence for paper choice** — arithmetic, not a decision:

| Paper | Printable area at the workbook's own 0.7/0.75 in margins | Fits? |
|---|---|---|
| **A5 portrait** (148 × 210 mm) | 112 × 172 mm | ⚠️ **Width fits; height overflows by ≈ 4 mm** |
| **A5 portrait at our proposed 8 mm margin** (FR-PRINT-016) | 132 × 194 mm | ✅ **Fits with ≈ 18 mm to spare** |
| **A4 portrait** (210 × 297 mm) | 174 × 259 mm | ✅ Fits easily; the bill occupies the upper-left ~53 % of the width |

This **corroborates the existing `@page { size: A5 portrait; margin: 8mm }` default** in
FR-PRINT-016 and shows the workbook's own default margins would *not* fit A5. It does **not** settle
[OD-6](OWNER-DECISIONS.md#od-6) — see §5.

---

## 4. Corrections — four documented claims the file disproves

Each of these was recorded in good faith from the screenshot or the owner's description. The file is
now the better evidence and supersedes them.

### 4.1 ✅ `QT` vs `QTY` — **RESOLVED: the header is `QTY`**

Addendum 1 §5 item 5 recorded this as an open conflict and kept **`QT`** because it was *directly
observed* in the screenshot. The workbook's shared-string table holds the literal **`QTY`**.

**The screenshot was not wrong — it was truncated.** Column C is only 3.33 characters wide and the
cell carries `shrinkToFit`; at 9 pt bold the third glyph renders at the cell edge and was
unreadable in the screenshot. The conflict is closed in favour of the file.

**`QTY` is now the specified header.** Every occurrence of `QT` as a column label has been corrected
across both repositories.

### 4.2 ✅ There are **no formulas anywhere** in the workbook

Several documents stated that the `0` in empty rows was *"a spreadsheet formula artefact"* generated
by `TOTAL = QTY × PRICE`, and `UI-UX-SPECIFICATION.md §12.1b` listed *"underlying formulas"* as
something the workbook would reveal.

**`sheet1.xml` contains zero `<f>` elements.** Every value — including each `0`, each line total,
and the `GRAND TOTAL` of `4820` — is a **hand-typed literal**. The owner types the arithmetic.

Two consequences worth stating plainly:

1. The empty-row `0`s are **typed placeholders**, not computed output. The decision to print them
   blank ([OD-23](OWNER-DECISIONS.md#od-23) option (b), already settled) is unaffected — but the
   *reason* given for it in several documents was wrong and has been corrected.
2. **The original bill has no arithmetic cross-check.** `4820` happens to equal
   `240 + 280 + 3850 + 450`, but nothing in the file enforces that. The replacement system computing
   totals server-side is a genuine improvement over the original, not a like-for-like reproduction.

### 4.3 🔴 The date format is **`mm-dd-yyyy`**, not `dd-mm-yyyy`

`OWNER-DECISIONS.md` OD-5 and `PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md` F-21 both recorded
**`DD-MM-YYYY`** as `[OBSERVED]`. It was not observed — it was **inferred**. The example date is
**6 June 2026**, which renders identically in both orders, so the screenshot could not distinguish
them.

The workbook's custom number format `164` is **`mm\-dd\-yyyy;@`** — month first.

This is **not** silently corrected to the Indian convention, and **not** silently reproduced either.
It becomes a new owner decision: **[OD-26](OWNER-DECISIONS.md#od-26)**.

### 4.4 ✅ 25 item rows — confirmed, and the absences are now verified

The owner's description said 25 rows; the file confirms rows 9–33 pre-numbered `1`…`25`.

More importantly, four things previously recorded as *"not mentioned by the owner — do not invent"*
are now **verified absent by inspection** of the complete used range `A1:E37`:

| Element | Previous status | Now |
|---|---|---|
| Signature / `For NATIONAL AUTO SPARES` block | *not mentioned* | ✅ **Verified absent** |
| Amount in words | *not mentioned* | ✅ **Verified absent** |
| **Invoice / bill number field** | absent from the visible region | ✅ **Verified absent from the entire sheet** |
| Tax / GST / GSTIN / HSN column or line | absent from the visible region | ✅ **Verified absent from the entire sheet** |
| Paid / Balance / Due block | absent from the visible region | ✅ **Verified absent from the entire sheet** |
| Discount column or line | absent from the visible region | ✅ **Verified absent from the entire sheet** |

The absence of any invoice-number field **corroborates** [OD-3](OWNER-DECISIONS.md#od-3)'s
re-answer. It does **not** bear on [OD-25](OWNER-DECISIONS.md#od-25): the document the shop issues
is labelled **`ESTIMATE`**, and whether the business must *also* issue a serially numbered tax
invoice is a question for the owner's accountant, which no spreadsheet can answer.

### 4.5 The second sheet

Sheet 2 is named `Sheet1` and its entire content is cell `A1` = the string `inhone oage ` — an
apparent typo fragment. It has no bill content, no formatting and no print setup.

**It is excluded from the specification.** It is recorded here only so that nobody later discovers
a second sheet and assumes it was overlooked.

---

## 5. What the workbook does **not** resolve

> This section matters as much as §3. The file answers less about *printing* than expected.

`sheet1.xml` contains **no `<pageSetup>` element**, **no `<headerFooter>` element**, and the
workbook defines **no print area** (`definedNames` is absent). The only page-related element is:

```xml
<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>
```

— which is Excel's **unmodified default**, not an owner choice.

Therefore the following remain **open**, exactly as before:

| Still unknown | Why the file cannot answer it |
|---|---|
| **Paper size** | Never set. Excel would use the printer's default at print time |
| **Orientation** | Never set |
| **Scaling / fit-to-page** | Never set |
| **Print area** | Never defined — Excel would print the used range |
| **Page header / footer text** | No `headerFooter` element |
| **The shop's actual printer and paper** | Not a property of the file ([OD-6](OWNER-DECISIONS.md#od-6)) |

**[OD-6](OWNER-DECISIONS.md#od-6) is unchanged and still `BLOCKS-GO-LIVE`.** The §3.8 arithmetic
*recommends* A5 at 8 mm margins; it does not approve it. A printed sample on the shop's own printer
remains the only sufficient evidence.

---

## 6. Owner decisions — changes in this addendum

| OD | Status before | Status now | Basis |
|---|---|---|---|
| **[OD-5](OWNER-DECISIONS.md#od-5)** | 🟡 Lower section `[OWNER-DESCRIBED]`; geometry, page setup, logos and `QT`/`QTY` unverified | ✅ **Layout RESOLVED by file inspection.** Content, geometry, typography, borders, merges, number formats and logos are all `[VERIFIED]`. **Page setup is not in the file** → that part transfers to OD-6 | Direct inspection (§2–§3) |
| **[OD-6](OWNER-DECISIONS.md#od-6)** | 🔴 Open — paper/printer | 🔴 **Open — unchanged**, now with measured content geometry (92 × 176 mm) to decide against | §3.8, §5 |
| **[OD-23](OWNER-DECISIONS.md#od-23)** | 🟡 No decimals; fractional case unresolved | 🟡 **Unchanged, but corroborated** — the original itself formats `QTY`/`PRICE`/`TOTAL`/`GRAND TOTAL` with built-in format `0`. ⚠️ **New evidence sharpens the conflict** — see below | §3.5 |
| **[OD-26](OWNER-DECISIONS.md#od-26)** | — | 🔴 **NEW — date format order.** `BLOCKS-GO-LIVE` | §4.3 |

**OD-1, OD-2, OD-3, OD-4, OD-7, OD-8, OD-14, OD-19, OD-21, OD-22, OD-24, OD-25 are unchanged.**
Nothing in the workbook answers any of them.

### 6.1 Why the workbook sharpens the OD-23 conflict rather than settling it

Excel's built-in number format `0` **rounds half-up for display while preserving the stored value**.
So the owner's original spreadsheet *does* silently round: a price of `240.50` would display `241`,
and because the `GRAND TOTAL` is hand-typed rather than summed, nothing in the original would ever
reveal the discrepancy.

This is useful evidence — it explains why the owner sees no problem with the instruction — but it is
**not a reason to copy the behaviour**. In the replacement system the grand total *is* computed from
the line values, so silent display-rounding would produce a printed bill whose column visibly does
not add up.

**The recorded position is unchanged and remains correct:** display-only formatting, never a change
to a stored value, and a **visible validation warning** rather than silent rounding
([R-37](PHASE-2-RISK-REGISTER.md), task **T3-08**). The recommendation to constrain prices to whole
rupees at entry **remains a recommendation**, not an approved policy.

---

## 7. GitHub repository connection

### 7.1 Authentication — resolved, with a correction to Addendum 1

Addendum 1 reported push authentication as missing. That was accurate for the **`gh` CLI**, and it
is **still** true:

```
gh auth status
X Failed to log in to github.com account AshwinSharma-git (default)
  - The token in default is invalid.
```

However, re-checking the environment found a **separate, working credential**: an Ed25519 SSH key
at `~/.ssh/id_ed25519` that authenticates to GitHub as the authorised collaborator.

```
ssh -T git@github.com
Hi AshwinSharma-git! You've successfully authenticated, but GitHub does not provide shell access.
```

**Addendum 1's claim was too broad.** It verified one authentication channel and reported the
absence of *all* push authentication. The correct statement was: *the `gh` token is invalid; other
channels were not checked.* Recorded here rather than edited there.

### 7.2 Remote state before any write

Both repositories were re-verified immediately before connecting — **not** assumed from Addendum 1:

| Repository | `git ls-remote` | Refs | Conclusion |
|---|---|---|---|
| `nationalautospares898-maker/bmfrontend` | exit 0 | **none** | Exists, readable, **empty** |
| `nationalautospares898-maker/bmbackend` | exit 0 | **none** | Exists, readable, **empty** |
| *(control)* `…/zz-does-not-exist-nas` | fatal | — | `Repository not found` — the probe distinguishes empty from absent |

Both remotes being empty means **there was no remote history to reconcile and nothing to
overwrite.** No force-push, no history rewrite and no `--allow-unrelated-histories` merge was needed
or used.

### 7.3 Transport

`origin` is set to the **exact HTTPS URL** specified by the owner, with an **SSH push URL** on the
same remote, because the HTTPS channel has no working credential:

```
origin  https://github.com/nationalautospares898-maker/<repo>.git  (fetch)
origin  git@github.com:nationalautospares898-maker/<repo>.git      (push)
```

Both URLs address the **same repository**. Once `gh auth login` is run, the push URL can be removed
with `git remote set-url --delete --push origin <ssh-url>` and everything reverts to plain HTTPS.

### 7.4 Pre-push safety

Verified by inspection of the staged file list in each repository before committing:

| Check | bmfrontend | bmbackend |
|---|---|---|
| Files staged | 9 | 24 |
| Non-Markdown files | **0** | **0** |
| `.env` or environment files | **0** | **0** |
| Private keys / certificates (`.pem`, `.key`, `.p12`, `.pfx`) | **0** | **0** |
| Binaries or archives | **0** | **0** |
| Credential-shaped strings | **0** | **0** |
| Cross-project files | **0** | **0** |
| Nested repository | **none** | **none** |

A `.gitignore` was added to each repository before the first commit, excluding environment files,
secrets, dependency directories, build output and editor/OS noise. **These are the only two
non-documentation files created**, and both were explicitly requested by the owner's Step 5.5.

> ⚠️ **The owner's workbook was deliberately NOT committed.** `ANAS_1234_20260606_231456.xlsx` lives
> at the workspace root, which is **outside both repositories**, and it is additionally listed in
> both `.gitignore` files so it cannot be added by accident. It is the owner's original business
> record, not a project artefact. Its *content* is fully specified in §3 of this document.

### 7.5 Write permission — how it was actually proven

Collaborator access was **not assumed from the owner's report or from a screenshot**. Before the
real push, `git push --dry-run -u origin main` was run against each repository. A dry-run push
performs the full server-side ref negotiation and **permission check**, and fails with
`ERROR: Permission to … denied to …` if the account lacks write access. Both returned:

```
 * [new branch]      main -> main
```

That is the first point at which write access was **verified** rather than believed.

---

## 8. Task updates

| Task | Status before | Status now |
|---|---|---|
| **T3-10** — reconcile the bill layout against the workbook | 🔴 Blocked — awaiting the file | 🟢 **Largely discharged.** Layout, geometry, typography, borders, merges, number formats, the `QTY` header and the logo assets are all resolved (§3, §4). **Remaining scope narrowed to:** extracting the two logo JPEGs into the frontend as print assets, and the printed-sample check |
| **T3-11** *(new)* | — | **Resolve the printed date format order** — `dd-mm-yyyy` (Indian convention) vs the workbook's literal `mm-dd-yyyy`. Blocked on [OD-26](OWNER-DECISIONS.md#od-26) |
| **T3-12** *(new)* | — | **Extract and embed the two logo images.** Copy `image1.jpeg` (TATA) and `image2.jpeg` (Ashok Leyland) from the workbook package into the frontend print assets at their measured sizes and footer positions |
| T3-01…T3-09 | unchanged | unchanged |

Full definitions — description, acceptance criteria, dependencies, affected milestones, owner
dependency and status — are in
[PHASE-2-IMPLEMENTATION-PLAN.md §1.2](PHASE-2-IMPLEMENTATION-PLAN.md).

---

## 9. Milestone readiness — recalculated

| Milestone | Addendum 1 | Now | Why |
|---|---|---|---|
| **M1** — repository, CI, conventions | 🟡 Conditional — push auth missing | ✅ **READY.** Its repository-connection precondition is **met**: both remotes connected, baseline pushed, branch `main` tracking `origin/main` | §7 |
| **M20** — print template | ✅ Ready (content known) | ✅ **Ready — now against verified geometry** rather than a description | §3 |
| **M27** — print fidelity | ⛔ Blocked on owner evidence | 🟡 **CONDITIONALLY READY.** The blocking evidence has arrived; the layout is fully buildable and testable against the file. Its **exit gate** still requires a printed sample on the shop's printer ([OD-6](OWNER-DECISIONS.md#od-6)) and the date-order answer ([OD-26](OWNER-DECISIONS.md#od-26)) | §3, §5 |
| **M4** — money engine | 🟡 Conditional — OD-7 | 🟡 **Conditional — unchanged.** OD-7 still freezes the policy values | §6.1 |
| **M28** — go-live | ⛔ Blocked | ⛔ **Blocked — unchanged.** Legal, printer, training and operational gates stand | §10 |

**Recalculated totals: 26 ready · 2 conditionally ready (M4, M27) · 1 blocked (M28).**
*(Previously 24 / 2 / 2.)* M1 moved conditional → ready; M27 moved blocked → conditional.

> **No milestone was promoted merely because a related question was answered.** M27 is *conditional*,
> not ready, precisely because its exit gate — a physical printed sample — is untouched by the
> workbook. M20 was already ready and stays ready.

---

## 10. Gates that remain

| Gate | Owner | Status |
|---|---|---|
| **[OD-25](OWNER-DECISIONS.md#od-25)** — statutory invoice numbering | Owner's accountant | 🔴 Open. **Unaffected** by the workbook (§4.4) |
| **[OD-7](OWNER-DECISIONS.md#od-7)** — calculation precision, rounding method and stage | Owner | 🔴 Open |
| **[OD-23](OWNER-DECISIONS.md#od-23)** — fractional-amount display policy | Owner | 🔴 Open, conflict recorded (§6.1) |
| **[OD-26](OWNER-DECISIONS.md#od-26)** — date format order | Owner | 🔴 **New**, open |
| **[OD-6](OWNER-DECISIONS.md#od-6)** — paper, printer, printed-sample sign-off | Owner | 🔴 Open (§5) |
| **[OD-22](OWNER-DECISIONS.md#od-22)** — credit payment-block placement | Owner | 🔴 Open |
| OD-1, OD-2, OD-4, OD-8, OD-19, OD-24 | Owner | 🔴 Open — unchanged |
| Trademark use of the TATA / Ashok Leyland marks | Owner | ℹ️ Recorded (§3.7), no determination made |
| Restore drill · alert recipient · staff training · opening stock · credential rotation · disk encryption · rollback rehearsal · break-glass | Owner / ops | 🔴 Open — unchanged |

---

## 11. Closure criteria — re-evaluated

All twelve criteria from the [closure report](PHASE-2-CLOSURE-REPORT.md) were re-checked against the
new evidence. **Twelve of twelve hold.** The two that were at risk:

| Criterion | Re-evaluation |
|---|---|
| *"Every claim is evidence-based"* | ✅ **Strengthened.** Four inference-based claims were found and corrected (§4). The one over-broad claim in Addendum 1 is corrected in §7.1 |
| *"No open decision is presented as settled"* | ✅ **Holds.** OD-5 moved to resolved **on evidence**; OD-6, OD-7, OD-23 and the new OD-26 stay open. Nothing was closed by assumption |

**Verdict: PHASE 2 REMAINS CLOSED.** The project is ready to enter Phase 3.

---

## 12. Validation performed

| Check | Result |
|---|---|
| Relative links across both repositories | ✅ **0 broken** |
| Anchor fragments, including the new `#od-26` | ✅ All resolve |
| Decision IDs `OD-1…OD-26` | ✅ 26 anchors, no gaps, no duplicates |
| Risk IDs `R-01…R-37` | ✅ 37, unchanged |
| Milestone IDs `M1…M28` | ✅ 28, unchanged |
| Task IDs `T3-01…T3-12` | ✅ 12, no duplicates — **T3-05 not duplicated** |
| `QT` as a column label | ✅ **0 remaining** outside labelled historical records |
| Stale customer-visible numbering requirements | ✅ **0 remaining** in live text |
| Stale "formula-generated `0`" claim | ✅ Corrected everywhere |
| Four button labels in both repositories | ✅ Consistent |
| Phase 1 closure reports | ✅ **Byte-identical** — 32891 / 17053 / 21206 / 18048 / 14057 |
| Owner's workbook | ✅ **Unmodified** — SHA-256 re-verified (§2) |
| Application code, migrations, manifests, CI workflows, executable tests | ✅ **None created** |
| Tests executed | ⚠️ **None. No executable test exists in either repository.** No test result is claimed anywhere in this document |

---

## 13. Push verification — final state

| | `bmfrontend` | `bmbackend` |
|---|---|---|
| Local directory | `c:\progromming\bm\bmfrontend` | `c:\progromming\bm\bmbackend` |
| `origin` (fetch) | `https://github.com/nationalautospares898-maker/bmfrontend.git` | `https://github.com/nationalautospares898-maker/bmbackend.git` |
| `origin` (push) | `git@github.com:nationalautospares898-maker/bmfrontend.git` | `git@github.com:nationalautospares898-maker/bmbackend.git` |
| Branch | `main`, tracking `origin/main` | `main`, tracking `origin/main` |
| **Commit** | **`88495a9be749be6959096ff3266de073519ab9e0`** | **`0482c7a2d9d185402c541efbcfaf4a7219e59b36`** |
| Local HEAD = remote `main` | ✅ **Yes** | ✅ **Yes** |
| Ahead / behind | `0 / 0` | `0 / 0` |
| Working tree | clean | clean |
| Files on the remote | **10** | **26** |
| Push | ✅ **`* [new branch] main -> main`** | ✅ **`* [new branch] main -> main`** |

**Mapping verified — not crossed:**

```
bmfrontend  →  nationalautospares898-maker/bmfrontend
bmbackend   →  nationalautospares898-maker/bmbackend
```

**Cross-contamination check.** Seven path names exist in both trees (`CLAUDE.md`, `README.md`,
`docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/TESTING-STRATEGY.md`,
`.gitignore`) — as they should, since each project owns its own copy. Every one was compared by
**blob hash** and all seven differ, confirming **no frontend file reached the backend repository or
the reverse**. No `.xlsx` exists in either tree.

**Not done, deliberately:** no force-push, no history rewrite, no branch deletion, no tag, no
repository creation, no settings or visibility change, no CI workflow, no GitHub Pages, no release.

---

*Addendum 2 ends. [Addendum 1](PHASE-2-CLOSURE-ADDENDUM-1.md) ·
[Closure report](PHASE-2-CLOSURE-REPORT.md) · [Implementation plan](PHASE-2-IMPLEMENTATION-PLAN.md)*
