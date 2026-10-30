# Backend PRD — National Auto Spares Shop Management System

> **Current Phase 2 design disposition (2026-10-30):** [Design Completion Addendum](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) is the current cross-cutting decision record. It resolves superseded open labels and defines the Phase 3 implementation baseline. Historical closure reports/addenda remain unchanged; legal, physical and operational gates remain gates. It does not represent implementation or test completion.


| Field | Value |
|---|---|
| Repository | `bmbackend` (independent Git repository) |
| Document owner | Backend engineering |
| Status | **Phase 1 draft — awaiting owner review** |
| Last updated | 2026-10-29 |
| Companion document | `bmfrontend/docs/PRD.md` (UI/UX and client behaviour) |

> **Scope of this document.** This PRD defines what the **backend** must do: business rules,
> data integrity, API behaviour, transaction boundaries, permissions, synchronisation and
> operational reliability. UI wording, screen layout and interaction design are owned by the
> frontend PRD. Where an end-to-end behaviour spans both sides, **the same requirement ID is
> used in both documents** — see `docs/TRACEABILITY.md`.

---

## 1. Product vision and business objectives

### 1.1 Vision

Replace the shop's manual, Excel-based billing and stock tracking with a reliable digital
system that is fast enough for a busy counter, accurate enough to be the financial source of
truth, and simple enough for staff with little computer experience.

### 1.2 Business objectives

| ID | Objective | How we will know it worked |
|---|---|---|
| BO-1 | Eliminate manual bill writing | 100% of sales recorded through the system within 30 days of go-live |
| BO-2 | Know true stock position | Physical count vs. system variance under 2% of SKUs per monthly cycle count |
| BO-3 | Stop credit leakage | Every outstanding rupee attributable to a customer, invoice and date; aged receivables report available daily |
| BO-4 | Faster billing than paper | Median time from first keystroke to printed bill under 60 seconds for a 5-line bill |
| BO-5 | Survive a bad internet day | Counter staff can keep capturing bills during an outage and lose no draft work |
| BO-6 | Auditable history | Any invoice, payment or stock change can be traced to a user, device, timestamp and reason |

### 1.3 Non-objectives for v1

Accounting ledger replacement, e-way bills, e-invoicing (IRP) integration, payroll,
multi-currency, online storefront, customer-facing app, barcode label printing.

---

## 2. Business context and current manual workflow

Derived from the owner's description and the supplied Excel invoice reference. **Items marked
`(unconfirmed)` are assumptions that require owner confirmation — see §5.**

### 2.1 Current state

1. Customer (often a mechanic, driver or transport business) arrives at the counter and asks
   for parts, by part name, vehicle, or by showing an old part.
2. Staff locate the part physically, recall or look up the price `(unconfirmed: how prices are
   currently decided)`.
3. Staff write or type a bill in an Excel template. **Confirmed by the owner's reference screenshot
   (inspected 2026-10-29):** heading `NATIONAL AUTO SPARES`, address `AUTO NAGAR ,HYDERABAD`,
   `Mob:9948207903,9848136564`, then `Customer Name:`, `Mechanic Name :`, `Vehicle no:`, an
   `ESTIMATE` label and `DATE:` in `DD-MM-YYYY`, over a pre-numbered line table with columns
   **`S R | ITEM | QTY | PRICE | TOTAL`**. Unused rows are pre-numbered and show `0` in `TOTAL`
   (a hand-typed literal — the sheet has no formulas). The sheet's bottom section was not visible, so how the grand
   total is presented remains unknown ([OD-5](OWNER-DECISIONS.md#od-5)).
4. The bill is printed and handed over.
5. Cash sales are collected immediately. Credit sales are recorded informally and chased later
   `(unconfirmed: current credit tracking method)`.
6. Stock is known by memory and physical inspection. Replenishment is triggered by noticing an
   empty shelf.
7. Supplier invoices arrive as PDFs or spreadsheets and are entered manually, or not at all.

### 2.2 Pain points this system targets

- No reliable stock figure, so parts are over- or under-ordered.
- Credit balances live in people's heads and paper notebooks.
- No history: "what did this customer buy in March?" is unanswerable.
- Two staff cannot bill at once from the same sheet.
- No protection against selling stock that is already gone.

### 2.3 Physical and operational constraints

- `(unconfirmed)` Number of counters at launch. Design target: **3 counters, 5 concurrent users.**
- `(unconfirmed)` Printer model and paper size. Design target: **A5 and A4 on a plain laser/inkjet
  printer via the browser print dialog.** Thermal roll support is a documented future option.
- `(unconfirmed)` Internet reliability. Design assumption: **broadband with multi-minute outages
  several times per week**, which is what the offline design is sized for.

---

## 3. User personas and permissions

### 3.1 Personas

| Persona | Computer skill | Primary need | Time at screen |
|---|---|---|---|
| **Owner** | Moderate | Totals, credit, margins, control over prices and overrides | Minutes/day, often on phone |
| **Counter staff / cashier** | Low | Bill fast without mistakes, never lose a bill | All day |
| **Stock keeper** | Low | Receive supplier stock, correct counts, see low stock | An hour/day |
| **Accountant (external)** | High | Export sales, tax and receivables for books | Monthly |
| **Auditor** | High | Read-only verification of history | Rare |

### 3.2 Roles and permission model

Roles are **bundles of named permissions**, not hardcoded behaviour. Permissions are checked in
the service layer, never only in the UI.

| Permission | Owner | Manager | Cashier | Stock keeper | Accountant | Auditor |
|---|---|---|---|---|---|---|
| `billing.draft.manage` | ✓ | ✓ | ✓ | – | – | – |
| `billing.sale.finalize` | ✓ | ✓ | ✓ | – | – | – |
| `billing.price.override` | ✓ | ✓ | – | – | – | – |
| `billing.discount.apply` | ✓ | ✓ | limited¹ | – | – | – |
| `billing.sale.cancel` | ✓ | ✓ | – | – | – | – |
| `billing.draft.takeover` | ✓ | ✓ | – | – | – | – |
| `inventory.oversell.override` | ✓ | ✓ | – | – | – | – |
| `inventory.adjust` | ✓ | ✓ | – | ✓ | – | – |
| `inventory.receive` | ✓ | ✓ | – | ✓ | – | – |
| `catalog.read` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `catalog.write` | ✓ | ✓ | – | ✓ | – | – |
| `catalog.cost.read` | ✓ | ✓ | – | ✓ | ✓ | ✓ |
| `customer.manage` | ✓ | ✓ | ✓ | – | – | – |
| `credit.read` | ✓ | ✓ | ✓ | – | ✓ | ✓ |
| `credit.payment.record` | ✓ | ✓ | ✓ | – | ✓ | – |
| `credit.writeoff` | ✓ | – | – | – | – | – |
| `returns.create` | ✓ | ✓ | – | – | – | – |
| `imports.manage` | ✓ | ✓ | – | ✓ | – | – |
| `reports.sales.read` | ✓ | ✓ | own² | – | ✓ | ✓ |
| `reports.financial.read` | ✓ | ✓ | – | – | ✓ | ✓ |
| `admin.users.manage` | ✓ | – | – | – | – | – |
| `admin.settings.manage` | ✓ | – | – | – | – | – |
| `admin.devices.manage` | ✓ | ✓ | – | – | – | – |
| `audit.read` | ✓ | ✓ | – | – | ✓ | ✓ |

¹ Cashier discount is capped by `settings.discount.cashier_max_percent`, default **0%** until the
owner sets it. ² Cashier sees only their own counter/shift totals.

**Rules.**
- Every role is scoped to a `business_id` and a set of `branch_id`s via a membership row.
- No request may read or write data outside the caller's business. Branch scoping is enforced on
  every inventory, sale and report query.
- `Auditor` is read-only at the API level: all write endpoints reject the role.

---

## 4. Confirmed requirements

These were stated by the owner and are treated as **fixed inputs** to the design.

| # | Confirmed decision |
|---|---|
| C-1 | Cloud-first deployment with offline capability |
| C-2 | Multiple staff and multiple billing counters work simultaneously |
| C-3 | Overselling is prevented by default; an explicit, authorised, audited override exists |
| C-4 | Tax and statutory invoice requirements are **not yet confirmed** |
| C-5 | At least 20 active draft bills per counter, capacity configurable |
| C-6 | Cash and credit sales use the same sales + inventory transaction engine |
| C-7 | Finalised invoices, payments and stock movements have auditable history |
| C-8 | Catalog, stock, invoice history, customers and credit balances live in a central database |
| C-9 | The printed bill closely reproduces the essential layout/columns of the Excel invoice |
| C-10 | Frontend and backend are separate Git repositories in one parent workspace |

---

## 5. Assumptions and unresolved questions

> **The authoritative register is [`OWNER-DECISIONS.md`](OWNER-DECISIONS.md)** — it carries, for each
> item, the exact question, what is already confirmed, the options, the consequence of each, the
> recommended default with rationale, the blocking level, and who must approve. This section is a
> **summary with cross-references**; where the two differ, `OWNER-DECISIONS.md` governs.
>
> **Check each item's status — three are settled, five are partly settled, the rest are open.**
> Settled 2026-10-29: **OD-14** (action labels), **OD-21** (credit workflow), **OD-23** (amount
> display). Partly settled: **OD-3**, **OD-5**, **OD-7**, **OD-22**, **OD-24**. For every other
> item a "recommended default" is an engineering proposal that keeps implementation unblocked, not
> a decision the owner has made.
>
> **No item has received legal, tax or accounting approval**, and no approval date or approver is
> recorded anywhere — because none has been given.

### 5.1 Decisions that block work

Blocking levels are defined in `OWNER-DECISIONS.md`. **Updated 2026-10-29** after the owner-decision
update: four items block the start of implementation; the rest block production use, which is a
different and later gate.

| ID | Register | Question | Status / recommended default | Blocking level |
|---|---|---|---|---|
| Q-TAX-3 | [OD-3](OWNER-DECISIONS.md#od-3) | Invoice numbering | ✅ **RE-ANSWERED: no customer-visible invoice numbering.** An internal-only sale reference is retained for audit and reconciliation. Gapless question **moot** | **NON-BLOCKING** |
| — | [OD-25](OWNER-DECISIONS.md#od-25) | **Does law require a serially numbered tax invoice?** | 🔴 **OPEN — new.** Internal mechanism retained so a compliant path exists if required. **No compliance claim is made** | **BLOCKS-GO-LIVE** |
| Q-PRINT-1a | [OD-5](OWNER-DECISIONS.md#od-5) | **The bill's lower section** | ✅ **RESOLVED 2026-10-30 — the workbook was supplied and inspected.** Layout, geometry, typography, borders, merges, number formats and both logos are **verified**; the header is **`QTY`**. ⚠️ The file sets **no page setup**, so paper/orientation/scaling transfer to [OD-6](OWNER-DECISIONS.md#od-6) and the date order to [OD-26](OWNER-DECISIONS.md#od-26) | **RESOLVED** |
| Q-MONEY-1 | [OD-7](OWNER-DECISIONS.md#od-7) | Decimal precision and rounding policy | 🟡 **Principles selected** (documented policy required; backend authoritative; client displays without changing meaning). **Precision, rounding method and exceptions undecided**; the prior default remains a proposal | **BLOCKS-IMPLEMENTATION** (money module, both repos) |
| Q-PRINT-22 | [OD-22](OWNER-DECISIONS.md#od-22) | Invoice number and credit payment/balance on the bill | 🟡 **Invoice-number half WITHDRAWN** by OD-3's re-answer. Credit payment/balance block still selected, **credit-only**, placement open — the described footer has no slot for it | **BLOCKS-GO-LIVE** (placement sign-off) |
| ~~Q-FE-WORD~~ | [OD-14](OWNER-DECISIONS.md#od-14) | Billing action labels | ✅ **RE-ANSWERED — four actions**: `Print Bill` / `Credit Bill` / `Save a Draft` / `Clear Draft`. UI acceptance strings may be frozen | **NON-BLOCKING** |
| Q-TAX-1 | [OD-1](OWNER-DECISIONS.md#od-1) | Is the business GST-registered? If yes, GSTIN? | Ship in `estimate_only`: documents titled **ESTIMATE**, no tax lines, never called a tax invoice | **BLOCKS-GO-LIVE** |
| Q-TAX-2 | [OD-2](OWNER-DECISIONS.md#od-2) | If GST applies: inclusive or exclusive pricing? Which rates/HSN per group? | Tax-exclusive; no rates seeded; every active product needs a tax group before `gst_enabled` opens | **BLOCKS-GO-LIVE** |
| Q-LEGAL-1 | [OD-4](OWNER-DECISIONS.md#od-4) | Exact legal shop name, address, phone, registration numbers for the header | Do **not** invent. Fields stay empty, nothing is printed in their place, and the go-live gate blocks release | **BLOCKS-GO-LIVE** |
| Q-PRINT-1b | [OD-6](OWNER-DECISIONS.md#od-6) | Paper size and printer | A5 portrait, plain paper, browser print, switchable to A4 | **BLOCKS-GO-LIVE** |
| Q-OFFLINE-1 | [OD-8](OWNER-DECISIONS.md#od-8) | Does the owner accept that offline means draft capture, **not** invoice issuing? | Offline drafts only; unnumbered counter slip during an outage; staff training is a go-live gate | **BLOCKS-GO-LIVE** |
| Q-OPS-1 | [OD-19](OWNER-DECISIONS.md#od-19) | Hosting, named alert recipient, budget | Managed platform, nearest region, alerts to a **named** person | **BLOCKS-GO-LIVE** |

### 5.2 Non-blocking questions — defaults are safe, confirm before go-live

| ID | Register | Question | Recommended default |
|---|---|---|---|
| Q-BIZ-1 | [OD-18](OWNER-DECISIONS.md#od-18) | Number of counters and branches at launch | One branch, 1–3 counters; schema is branch-aware from day one. A second **branch** needs the transfer module, which is out of MVP scope |
| Q-BIZ-2 | [OD-11](OWNER-DECISIONS.md#od-11) | Are returns accepted? Within how many days? | Yes, 30-day window, manager permission; damaged items scrap rather than restock |
| Q-BIZ-3 | [OD-12](OWNER-DECISIONS.md#od-12) | Credit terms — fixed net days, or per customer? | Per-customer `credit_terms_days`, default 30; `credit.enforce_limit = false` (warn only) |
| Q-BIZ-4 | [OD-13](OWNER-DECISIONS.md#od-13) | Can a cashier give discounts? | No — `discount.cashier_max_percent = 0` until the owner raises it |
| Q-BIZ-5 | — | Must a credit sale always have a phone number? | No, but warn; name + vehicle number required |
| Q-BIZ-6 | [OD-16](OWNER-DECISIONS.md#od-16) | Should reprints be marked "DUPLICATE"? | Yes — `print.mark_reprints_duplicate = true` |
| Q-BIZ-7 | [OD-9](OWNER-DECISIONS.md#od-9) | Is negative stock ever acceptable? Should drafts reserve stock? | Negative only via the audited oversell override; drafts **never** reserve stock |
| Q-BIZ-8 | [OD-10](OWNER-DECISIONS.md#od-10) | Who may cancel a finalised invoice, and for how long after? | Manager and owner, within 24 h, reason mandatory; later corrections use credit notes |
| Q-BIZ-9 | [OD-17](OWNER-DECISIONS.md#od-17) | Login style for counter staff | Username + password, 30-day trusted device, optional PIN quick-switch |
| Q-OPS-2 | [OD-19](OWNER-DECISIONS.md#od-19) | Acceptable data-loss window (RPO) | 5 minutes (point-in-time recovery enabled) |
| Q-ARCHIVE-1 | [OD-15](OWNER-DECISIONS.md#od-15) | Must a PDF copy of each invoice be archived? | No — the immutable snapshot re-renders deterministically; server PDF is deferred |
| Q-FE-BROWSER | [OD-20](OWNER-DECISIONS.md#od-20) | Counter browser, screen size, touchscreen? | Chrome/Edge, 1366×768 minimum, keyboard + mouse |

Hosting and alerting (`Q-OPS-1`) is in §5.1 because an unmonitored production system is a go-live
blocker, not a preference.

### 5.3 Explicit assumptions baked into this design

| ID | Assumption | If wrong |
|---|---|---|
| A-1 | Catalog under ~100,000 SKUs | Search strategy still holds; revisit index choice above 1M |
| A-2 | Under ~500 invoices/day | Single Postgres instance is ample; revisit partitioning above ~5M sale rows |
| A-3 | Staff share a counter device rather than having personal laptops | Device-bound sessions and PIN switching are the right model |
| A-4 | Prices are set by the shop, not by a supplier feed | No external price sync module needed in v1 |
| A-5 | One physical shop location at launch | Branch transfers deferred to a later phase |

### 5.4 Information referenced but **not** available to this phase

- **Bill reference — partially resolved 2026-10-29.** The owner supplied a **screenshot** of the
  actual Excel bill and it **was inspected**. `bmfrontend/docs/UI-UX-SPECIFICATION.md §12.1` is now
  transcribed from it: heading `NATIONAL AUTO SPARES`, address `AUTO NAGAR ,HYDERABAD`, contact
  `Mob:9948207903,9848136564`, the Customer / Mechanic / Vehicle / DATE block, the `ESTIMATE` label
  (left-aligned on the DATE row), and the column headers **`S R · ITEM · QTY · PRICE · TOTAL`**.
  The earlier reconstruction's column labels and `ESTIMATE` placement were **wrong and are
  superseded**. **The original `.xlsx` was NOT available** and the screenshot stops partway down, so
  the grand total, signature area, footer and page setup remain unseen ([OD-5](OWNER-DECISIONS.md#od-5)).
- No sample product list, supplier PDF or customer ledger was provided. Import parsing rules are
  therefore specified as a pipeline with human review, not as a fixed column mapping.

---

## 6. Functional requirements

Priority: **P0** = required for go-live, **P1** = required within 3 months, **P2** = later.

### 6.1 Authentication, users and devices (`FR-AUTH`)

| ID | P | Requirement |
|---|---|---|
| FR-AUTH-001 | P0 | Users authenticate with username + password (Argon2id hashed). On success the server issues a short-lived access token (15 min) and a rotating refresh token bound to a registered `device_id`. |
| FR-AUTH-002 | P0 | Refresh tokens rotate on every use. Re-use of a consumed refresh token revokes the whole token family and writes an audit event. |
| FR-AUTH-003 | P0 | Every API call resolves to `(user_id, business_id, branch_id, counter_id, device_id, permissions)`. Requests lacking a valid branch scope are rejected. |
| FR-AUTH-004 | P0 | Devices are registered and listable. An owner/manager can revoke a device; revoked devices are rejected on all endpoints with `DEVICE_REVOKED` and cannot sync. |
| FR-AUTH-005 | P1 | Multiple staff may share one device: a signed-in device can quick-switch users with a per-user PIN (rate-limited, 5 attempts then password required). Each sale records the actual acting user. |
| FR-AUTH-006 | P0 | Failed logins are rate-limited per username and per IP; lockout is time-based and audited. |
| FR-AUTH-007 | P0 | Passwords can be reset by the owner; forced change on first login for seeded accounts. |

### 6.2 Business, branch and counter administration (`FR-ADMIN`)

| ID | P | Requirement |
|---|---|---|
| FR-ADMIN-001 | P0 | One business record holds legal name, display name, address, contact, and (if confirmed) tax identifiers. These populate the invoice header; none are invented by the system. |
| FR-ADMIN-002 | P0 | Branches belong to a business. Inventory, invoice series and reports are branch-scoped. |
| FR-ADMIN-003 | P0 | Counters belong to a branch and own draft bills. Counter capacity (`max_open_drafts`, default 20, minimum 20) is configurable per counter. |
| FR-ADMIN-004 | P0 | Settings are stored as typed, audited key/value rows at business and branch level, with documented defaults. No business rule is hardcoded that the owner is expected to change. |
| FR-ADMIN-005 | P1 | Owner can create/disable users and assign role + branch memberships. |

### 6.3 Product catalog and search (`FR-CAT`)

| ID | P | Requirement |
|---|---|---|
| FR-CAT-001 | P0 | Products have: name, optional SKU/part code, brand, category, unit of measure, default selling price, optional cost, optional HSN code, active flag. |
| FR-CAT-002 | P0 | A product may have many **aliases** (alternate names, old part numbers, local names, OEM/aftermarket cross references). Aliases are searchable and uniqueness-checked per business. |
| FR-CAT-003 | P0 | Search endpoint returns matches from the **first typed character**, ranked: exact SKU → SKU prefix → name prefix → alias prefix → fuzzy/trigram contains. |
| FR-CAT-004 | P0 | Search results include product id, display name, SKU, brand, unit, default selling price and **current branch stock**. Cost price is included only if the caller holds `catalog.cost.read`. |
| FR-CAT-005 | P0 | Search is server-side and paginated (default 20, max 50). The client must never download the full catalog to autocomplete. |
| FR-CAT-006 | P0 | Search must tolerate case, accents, extra spaces, and common separators in part numbers (`JK-123`, `jk 123`, `jk123` all match). |
| FR-CAT-007 | P1 | Barcode/scanner input is accepted by the same search endpoint via an exact-barcode lookup path; `barcode` is a distinct indexed field. |
| FR-CAT-008 | P0 | SKU is unique per business when present. Creating a duplicate SKU is rejected with the conflicting product identified. |
| FR-CAT-009 | P1 | Products can be deactivated but never hard-deleted once referenced by a sale, purchase or stock movement. |
| FR-CAT-010 | P2 | Price history is retained with effective dates; an invoice always references its own snapshot, not the history table. |

### 6.4 Draft bills (`FR-DRAFT`)

| ID | P | Requirement |
|---|---|---|
| FR-DRAFT-001 | P0 | A draft bill is server-persisted, identified by a **client-generated UUID**, and owned by `(business, branch, counter)`. The client id is the server id — no id remapping. |
| FR-DRAFT-002 | P0 | At least 20 drafts may be open per counter simultaneously. Exceeding `max_open_drafts` returns `DRAFT_LIMIT_REACHED`; it never silently discards a draft. |
| FR-DRAFT-003 | P0 | A draft holds header fields (customer name, business name, vehicle number, mechanic name, phone, notes) and ordered line items (product, qty, unit price, line discount). |
| FR-DRAFT-004 | P0 | Drafts carry a monotonically increasing `version`. Updates use optimistic concurrency (`If-Match`-style `base_version`); a mismatch returns `409 DRAFT_VERSION_CONFLICT` with the current server state. **The server never silently merges.** |
| FR-DRAFT-005 | P0 | **Drafts do not reserve stock.** Opening, editing or holding a draft has zero effect on inventory. |
| FR-DRAFT-006 | P0 | Each draft line stores `unit_price` (what will be billed) and `catalog_price_at_add` (what the catalog said when added), plus a `price_source` of `catalog` / `manual_override`. |
| FR-DRAFT-007 | P0 | Switching between drafts, refreshing the browser, restarting the browser or losing the network must not finalise a sale, change inventory, or lose draft content. |
| FR-DRAFT-008 | P0 | Drafts are editable offline and synchronised when connectivity returns (see §6.14). |
| FR-DRAFT-009 | P1 | Abandoned drafts older than `settings.drafts.stale_days` (default 7) are listed for review; they are archived, not deleted, and never auto-finalised. |
| FR-DRAFT-010 | P1 | A manager may **take over** a draft locked to another counter/device (`billing.draft.takeover`), which is audited. |
| FR-DRAFT-011 | P0 | Draft totals returned by the server are advisory previews. They are recomputed authoritatively at finalisation. |

### 6.5 Sale finalisation (`FR-SALE`)

| ID | P | Requirement |
|---|---|---|
| FR-SALE-001 | P0 | Finalisation is a **single database transaction** that: validates permissions and branch scope → recomputes all money server-side → locks the affected inventory rows → verifies stock → allocates the invoice number → inserts the invoice, immutable item snapshots, stock movements, and any payment + allocation → commits. Partial success is impossible. |
| FR-SALE-002 | P0 | Finalisation is **idempotent**. The client sends an `Idempotency-Key`; a repeated request with the same key and same payload returns the original invoice and creates nothing new. Same key with a different payload returns `409 IDEMPOTENCY_KEY_REUSE`. |
| FR-SALE-003 | P0 | All monetary values are computed by the server using exact decimals. Client-sent totals are ignored except as a cross-check: a mismatch beyond ±0.01 is reported as `TOTAL_MISMATCH` rather than silently accepted. |
| FR-SALE-004 | P0 | Every finalised sale is assigned an **internal sale reference** — `invoices.sequence_number`, allocated from a per-(business, branch, series, financial year) counter row **locked inside the finalisation transaction**, so a rolled-back attempt consumes nothing. It is **unique and sequential** within that scope and exists for **auditability, reconciliation and stable ordering**. ⚠️ **It is NEVER rendered to a customer and never appears in `print_payload`** — [OD-3](OWNER-DECISIONS.md#od-3) is re-answered: *"No need of invoice numbering."* The gapless question is **moot** for this scope. Whether law requires a visible serial number is **[OD-25](OWNER-DECISIONS.md#od-25)**, a go-live gate. |
| FR-SALE-005 | P0 | Each sale item snapshots: product id, name, SKU, unit, quantity, unit price, line discount, line total, tax fields as configured, and cost at time of sale (for margin reporting). Later catalog edits never change a past invoice. |
| FR-SALE-006 | P0 | If current catalog price differs from the draft's `catalog_price_at_add` for any line not explicitly marked as an override, finalisation returns `409 PRICE_CHANGED` listing the affected lines, unless the request sets `accept_price_changes: true`. |
| FR-SALE-007 | P0 | Insufficient stock returns `409 INSUFFICIENT_STOCK` with the per-line shortfall (`requested`, `available`). No stock is moved and no invoice is created. |
| FR-SALE-008 | P0 | A caller with `inventory.oversell.override` may finalise despite insufficient stock by sending `oversell_override: {reason: "..."}`. The reason is mandatory, minimum 10 characters, stored on the invoice and in the audit log with the acting user and device. |
| FR-SALE-009 | P0 | Cash and credit sales use the **same** endpoint and engine; they differ only in the payment records created and the resulting `payment_status`. |
| FR-SALE-010 | P0 | A finalised invoice is **immutable**. There is no update endpoint for invoices or invoice items. |
| FR-SALE-011 | P0 | An invoice may be **cancelled** (not deleted) by an authorised user within `settings.sales.cancel_window_hours` (default 24) with a mandatory reason. Cancellation writes reversing stock movements and a full-value credit note in one transaction. The invoice number is retained and the record stays visible as `cancelled`. |
| FR-SALE-012 | P0 | Finalising a draft marks the draft `finalized` and links it to the invoice. The draft can never be finalised twice. |
| FR-SALE-013 | P1 | An invoice records the counter, device and user that produced it, for shift reconciliation. |
| FR-SALE-014 | P0 | Zero-line, zero-quantity and negative-quantity bills are rejected. Zero-price lines are allowed only with `billing.price.override` and are flagged. |

### 6.6 Printing (`FR-PRINT`)

| ID | P | Requirement |
|---|---|---|
| FR-PRINT-001 | P0 | The backend returns a complete, print-ready invoice representation: business header fields, customer/vehicle/mechanic fields, ordered line snapshots, totals, amount in words, payment status and invoice number. The frontend renders it; the backend owns the content. |
| FR-PRINT-002 | P0 | The document title is driven by `settings.tax.mode`: `ESTIMATE` while tax mode is unconfirmed, `TAX INVOICE` only once GST configuration is validated. The system never labels a document a tax invoice on its own initiative. |
| FR-PRINT-003 | P0 | Reprinting is a pure read. It never creates a sale, number or stock movement. |
| FR-PRINT-004 | P1 | Reprints are flagged so the rendered document can show `DUPLICATE COPY`; print attempts are recorded with user, device and timestamp. |
| FR-PRINT-005 | P2 | Server-side PDF generation for archival/email. Deferred — browser printing is primary (see ADR-014). |

### 6.7 Inventory (`FR-INV`)

| ID | P | Requirement |
|---|---|---|
| FR-INV-001 | P0 | Inventory is tracked per `(branch, product)` as a balance row, backed by an **append-only stock movement ledger**. Every balance change has a corresponding movement row. |
| FR-INV-002 | P0 | Movement reasons are a closed set: `opening`, `purchase_receipt`, `sale`, `sale_cancellation`, `customer_return`, `supplier_return`, `adjustment_increase`, `adjustment_decrease`, `transfer_out`, `transfer_in`, `import_apply`. Each movement references its source document. |
| FR-INV-003 | P0 | Stock movements are immutable. Mistakes are corrected by a new, opposite, reasoned movement. |
| FR-INV-004 | P0 | Concurrent sales of the same product are serialised by row-level locks acquired in a deterministic order, so two counters can never both consume the same last unit. |
| FR-INV-005 | P0 | Balances may go negative **only** through an audited oversell override. All other paths reject it. |
| FR-INV-006 | P0 | A reconciliation routine recomputes every balance from the ledger and reports discrepancies. It runs on demand and nightly, and never silently rewrites balances — it raises an alert and requires an authorised adjustment. |
| FR-INV-007 | P0 | Manual adjustments require `inventory.adjust`, a reason from a configurable list plus free text, and are fully audited. |
| FR-INV-008 | P1 | Per-product reorder level and low-stock listing. |
| FR-INV-009 | P1 | Physical stock-count sessions: capture counted quantities, show variance, then apply as a batch of reasoned adjustments in one transaction. |
| FR-INV-010 | P2 | Branch-to-branch transfers with an in-transit state. Out of MVP scope; the schema is already branch-aware. |

### 6.8 Suppliers and purchases (`FR-PUR`)

| ID | P | Requirement |
|---|---|---|
| FR-PUR-001 | P0 | Suppliers have name, optional business name, phone, address, optional tax id, notes. |
| FR-PUR-002 | P0 | A purchase (goods receipt) records supplier, document number, document date, received-at branch, and line items with quantity and unit cost. |
| FR-PUR-003 | P0 | **Only a confirmed purchase receipt increases stock**, in one transaction with its movements. A price list or quotation never changes stock. |
| FR-PUR-004 | P0 | Purchase receipts are immutable once confirmed; corrections are made by supplier return or adjustment. |
| FR-PUR-005 | P1 | Receiving a purchase may optionally update the product's cost and suggest a new selling price; the selling-price change is always an explicit, separate, permissioned action. |
| FR-PUR-006 | P1 | Supplier document number is unique per supplier per business; duplicates are blocked to prevent double-receiving the same invoice. |
| FR-PUR-007 | P2 | Supplier payables and purchase returns. |

### 6.9 Customers (`FR-CUST`)

| ID | P | Requirement |
|---|---|---|
| FR-CUST-001 | P0 | Customers have: person name, optional business name, phone(s), optional address, optional vehicle numbers, optional default mechanic, credit terms, optional credit limit, notes. |
| FR-CUST-002 | P0 | A cash sale may be recorded with only a free-text customer name and no customer record. A **credit sale requires a customer record**. |
| FR-CUST-003 | P0 | Customers may be created from the billing flow without leaving it, with a client-generated UUID so the operation is idempotent and offline-safe. |
| FR-CUST-004 | P0 | Likely duplicates are detected on phone number and normalised name and surfaced for the user to choose; the system never silently merges. |
| FR-CUST-005 | P1 | Customer merge is an explicit, permissioned, audited operation that repoints invoices and payments and keeps a redirect from the merged id. |
| FR-CUST-006 | P0 | Vehicle numbers are normalised (uppercase, separators stripped) for search but the entered form is preserved for printing. |
| FR-CUST-010 | P0 | **Minimum customer identity for a credit sale = a persisted customer record with a stable `customer_id` and a non-empty `name`.** Nothing else is mandatory. Creating one inline requires only a name (plus the client-generated UUID). |
| FR-CUST-011 | P0 | `customer_id` is **immutable and permanent**. All invoice, payment and grouping associations key on it, never on the name string, so a rename re-labels displays and **breaks nothing** (FR-RPT-029). |
| FR-CUST-012 | P0 | A customer record is never hard-deleted once referenced by an invoice or payment; it is deactivated. Its invoice history stays intact and visible. |

#### 6.9.1 Customer field classification (required vs optional)

Mandated by the owner's 2026-10-29 workflow change: **no field may become mandatory merely because
the sale is on credit** ([OD-21](OWNER-DECISIONS.md#od-21)). Every customer field is classified
exactly once here; this table is authoritative for both repositories.

| Field | Classification | Justification |
|---|---|---|
| `customer_id` (system) | **Required — identity** | Stable association key for invoices, payments and grouping (FR-CUST-011) |
| `name` | **Required — identity** | The only human identifier; a credit debt must be attributable to someone (FR-CUST-010) |
| `business_name` | Optional | Useful for transport customers; not needed to identify a debtor |
| `phone` | Optional | Valuable for collection and duplicate detection, but a walk-in may not give one. A **warning**, never a block (Q-BIZ-5) |
| `address` | Optional | Not needed while `tax.mode = estimate_only`. **May become required** if GST is enabled and place-of-supply applies — [OD-2](OWNER-DECISIONS.md#od-2) |
| `vehicle_number` | Optional | Belongs to the sale, not the customer's identity; printed when present |
| `default_mechanic` | Optional | Convenience only |
| `credit_terms_days` | Optional | Falls back to `credit.default_terms_days` (30) |
| `credit_limit` | Optional | Unlimited when unset; enforcement is off by default (FR-CREDIT-006) |
| `notes` | Optional | Free text |

**Rules.**
- Promoting any optional field to required needs a documented justification here **and** an entry in
  the owner-decision register. It is never done implicitly by a UI form.
- Supplied optional values are **always persisted** — optional means "need not be given", never
  "may be discarded" (FR-CREDIT-012).
- A **cash** sale still needs no customer record at all (FR-CUST-002); only credit requires one.

### 6.10 Credit and payments (`FR-CREDIT`, `FR-PAY`)

| ID | P | Requirement |
|---|---|---|
| FR-CREDIT-001 | P0 | A credit sale is a finalised invoice with an outstanding balance. It is created by the same engine as a cash sale and appears in daily sales reports for its sale date. |
| FR-CREDIT-002 | P0 | Outstanding balance is **derived**: `invoice.total − allocated payments − allocated credit notes`. A denormalised `amount_paid`/`payment_status` is maintained inside the same transaction as the allocations purely for query speed, and an invariant test asserts the two always agree. |
| FR-CREDIT-003 | P0 | `payment_status` ∈ `unpaid`, `partially_paid`, `paid`, `overpaid`, `cancelled`, computed, never user-set. There is no editable "paid" checkbox anywhere in the system. |
| FR-CREDIT-004 | P0 | A credit sale may take an initial part payment in the same finalisation transaction. |
| FR-CREDIT-005 | P0 | Credit terms produce a `due_date`; aged receivables are reported in buckets (current, 1–30, 31–60, 61–90, 90+). |
| FR-CREDIT-006 | P1 | Credit limit check at finalisation: warn, or block if `settings.credit.enforce_limit` is on; override requires a permission and a reason. |
| FR-CREDIT-007 | P1 | Write-off is an explicit, owner-only, audited operation producing a credit note with reason `write_off`. |
| FR-PAY-001 | P0 | A payment records amount, method (`cash`, `upi`, `card`, `bank_transfer`, `cheque`, `adjustment`), received-at branch/counter, received-by user, timestamp, optional reference, notes. |
| FR-PAY-002 | P0 | Payments are allocated to invoices through explicit allocation rows. A payment may cover several invoices; an invoice may receive several payments. Sum of allocations ≤ payment amount. |
| FR-PAY-003 | P0 | Unallocated payment remainder is held as customer credit (on-account) and is itself allocatable later. |
| FR-PAY-004 | P0 | Payment recording is idempotent via `Idempotency-Key`; a double submit records one payment. |
| FR-PAY-005 | P0 | Payments are immutable. A wrong payment is reversed by a `reversal` payment referencing the original, with a reason — never edited or deleted. |
| FR-PAY-006 | P0 | Allocation can be automatic (oldest invoice first) or manual; the chosen strategy is stored on the payment. |
| FR-PAY-007 | P0 | Over-allocation to an invoice beyond its outstanding balance is rejected. |
| FR-CREDIT-008 | P0 | **A credit sale finalises immediately through the same engine as a cash sale**, in one atomic transaction: invoice + invoice number + item snapshots + stock movements + (optional) initial payment and allocation. The server requires no intermediate "credit details" step and exposes no second finalisation path. Owner-confirmed 2026-10-29 ([OD-21](OWNER-DECISIONS.md#od-21)). |
| FR-CREDIT-009 | P0 | Credit finalisation requires a persisted `customer_id`; a free-text name is **not** sufficient. Missing or unresolvable → `409 CREDIT_CUSTOMER_REQUIRED`, with no partial effect (FR-CUST-002, FR-CUST-010). |
| FR-CREDIT-010 | P0 | **Sale finalisation and payment collection are distinct financial events.** Collecting payment against an existing credit invoice creates payment and allocation rows **only** — it never creates or repeats a stock movement, and never alters the invoice's items or total. |
| FR-CREDIT-011 | P0 | Credit invoices are queryable **grouped by customer**: per-customer invoice lists, per-customer outstanding totals, and an overall outstanding total, all server-computed from invoice totals and valid allocations (FR-CREDIT-002). |
| FR-CREDIT-012 | P0 | An optional header value supplied at sale time (vehicle, mechanic, phone, business name, notes) is **persisted on the invoice snapshot** even though it was not required. Optional never means discarded. |
| FR-CREDIT-013 | P0 | **The credit confirmation must not display an invoice number** ([OD-3](OWNER-DECISIONS.md#od-3) re-answered). It states the **customer**, the **outstanding amount**, the **due date**, and — critically — **"Stock has been updated."** so a completed credit sale can never be mistaken for a saved draft (FR-CREDIT-026). |

### 6.11 Returns and credit notes (`FR-RET`)

| ID | P | Requirement |
|---|---|---|
| FR-RET-001 | P1 | A customer return references the original invoice and specific invoice items. Cumulative returned quantity per item can never exceed the sold quantity (enforced under lock). |
| FR-RET-002 | P1 | A return creates: stock-in movements (unless marked `damaged`, which routes to a scrap/adjustment reason), and a credit note for the returned value using the invoice's snapshot prices. |
| FR-RET-003 | P1 | A credit note may be allocated to outstanding invoices, refunded as cash, or held as customer credit — the choice is explicit. |
| FR-RET-004 | P1 | Returns require `returns.create`, are allowed within `settings.returns.window_days` (default 30), and are audited. |
| FR-RET-005 | P1 | Returning against a cancelled invoice is rejected. |

### 6.12 File imports (`FR-IMP`)

| ID | P | Requirement |
|---|---|---|
| FR-IMP-001 | P1 | Users can upload a spreadsheet (`.xlsx`, `.csv`) or PDF to create an **import job**. Accepted types and a size cap (default 10 MB) are enforced by both extension and sniffed content type. |
| FR-IMP-002 | P1 | Each job declares its **intent**: `price_list` (catalog only, never touches stock) or `purchase_receipt` (may increase stock, requires a supplier). The intent is chosen by the user and cannot be inferred silently. |
| FR-IMP-003 | P1 | Uploads are stored outside the webroot with generated names, never executed, and scanned for size/structure before parsing. Original filename is kept as metadata only. |
| FR-IMP-004 | P1 | `file_sha256` is unique per business. Re-uploading the same bytes returns the existing job and a `DUPLICATE_FILE` warning. Proceeding anyway requires an explicit flag and a reason, and is audited. |
| FR-IMP-005 | P1 | Parsing happens in a background worker. The job has an explicit state machine: `uploaded → parsing → parsed → needs_review → applying → applied` with `failed` and `cancelled` branches. `applied` is terminal and cannot be re-applied. |
| FR-IMP-006 | P1 | Every parsed row is stored with its raw source values, the interpreted values, and a `match_status` of `exact_sku`, `exact_alias`, `fuzzy_candidate`, `ambiguous`, `new_product`, `duplicate_in_file` or `invalid`. |
| FR-IMP-007 | P1 | Rows with `ambiguous`, `new_product`, `duplicate_in_file` or `invalid` status **cannot be applied without an explicit per-row human decision**. Nothing auto-creates products or stock from a fuzzy guess. |
| FR-IMP-008 | P1 | Applying a job is one transaction over the accepted rows, producing catalog changes and/or a purchase receipt with its stock movements, and is idempotent on the job id. |
| FR-IMP-009 | P1 | An import report records counts applied/skipped/failed per row with reasons, retained for audit. |
| FR-IMP-010 | P2 | PDF table extraction quality is explicitly best-effort; low-confidence PDFs fall back to "review every row". |

### 6.13 Reports (`FR-RPT`)

| ID | P | Requirement |
|---|---|---|
| FR-RPT-001 | P0 | Daily sales: invoices for a date range (branch-scoped), with counts, gross, discounts, tax (when configured), net, split by payment method, **including credit sales**. |
| FR-RPT-002 | P0 | Credit outstanding: per customer and per invoice, with ageing buckets, derived from authoritative data. |
| FR-RPT-003 | P0 | Daily sales and credit reports are two views of one dataset; totals must reconcile exactly. An automated test asserts this. |
| FR-RPT-004 | P1 | Stock position and low-stock report; stock movement ledger report per product. |
| FR-RPT-005 | P1 | Item-wise sales and margin report (needs `catalog.cost.read`). |
| FR-RPT-006 | P1 | Counter/shift reconciliation: cash expected vs. collected per counter per day. |
| FR-RPT-007 | P1 | CSV export for every report, for the accountant. |
| FR-RPT-008 | P0 | Reports run against committed data with a stated as-of timestamp, and exclude cancelled invoices from revenue while still listing them in audit views. |

### 6.14 Offline capability and synchronisation (`FR-OFFLINE`)

| ID | P | Requirement |
|---|---|---|
| FR-OFFLINE-001 | P0 | **Offline scope is explicitly limited to: viewing cached catalog/stock-at-last-sync, and creating/editing/deleting draft bills and new customer records.** Everything else requires connectivity. |
| FR-OFFLINE-002 | P0 | **Sale finalisation, payments, returns, adjustments and purchase receipts are never performed offline.** They require server validation and a server-allocated invoice number. |
| FR-OFFLINE-003 | P0 | Offline mutations are queued in a durable local operation log with client-generated `op_id`s, applied to the server in order per draft, and acknowledged individually. |
| FR-OFFLINE-004 | P0 | Every synced operation is idempotent on `op_id`. Replays are detected and return the original result. |
| FR-OFFLINE-005 | P0 | Operation outcomes are explicit: `pending`, `accepted`, `replayed`, `conflicted`, `rejected`. Conflicted and rejected operations are surfaced to the user, never dropped silently. |
| FR-OFFLINE-006 | P0 | Draft conflicts are resolved by version check with a user-visible choice. Automatic silent merging is prohibited. |
| FR-OFFLINE-007 | P0 | The server never trusts client timestamps for ordering financial effects; it records both `client_created_at` and authoritative `server_received_at`. |
| FR-OFFLINE-008 | P0 | The catalog cache is pulled incrementally (`since` cursor) and is a **bounded subset** — recently sold and active products up to a configured row cap, not the whole database. |
| FR-OFFLINE-009 | P0 | Cached stock figures are labelled as "as of <time>" and are advisory only. Stock truth is always the server at finalisation time. |
| FR-OFFLINE-010 | P0 | Operations from a revoked device or an expired session are rejected with a clear, actionable error; local data is retained so nothing is lost if access is restored. |
| FR-OFFLINE-011 | P0 | Recovery after browser refresh, browser restart, tab crash and network loss must preserve all drafts. |
| FR-OFFLINE-012 | P0 | The system must not rely on Background Sync being executed by the browser; sync is also attempted on app focus, on reconnect, on a timer, and on explicit user action. |
| FR-OFFLINE-013 | P2 | Offline sale finalisation, if ever required, needs per-counter stock allocation and per-counter invoice number blocks, designed and approved first (ADR-011). |

### 6.15 Tax configuration (`FR-TAX`)

| ID | P | Requirement |
|---|---|---|
| FR-TAX-001 | P0 | `settings.tax.mode` ∈ `estimate_only` (default), `gst_enabled`. In `estimate_only` the system computes no tax, prints no tax lines, and titles documents `ESTIMATE`. |
| FR-TAX-002 | P1 | Tax configuration is **effective-dated**: tax groups, rates, and whether prices are inclusive. Invoices snapshot the resolved tax treatment per line. |
| FR-TAX-003 | P0 | **Production readiness gate:** switching to `gst_enabled` is blocked unless the business GSTIN, legal name and address are set, an invoice series is configured, and every active product has a tax group. The switch is audited and irreversible for already-issued documents. |
| FR-TAX-004 | P0 | The system must never present an unconfigured document as a compliant tax invoice. A startup check logs a prominent warning while in `estimate_only`. |

### 6.16 Audit logging (`FR-AUDIT`)

| ID | P | Requirement |
|---|---|---|
| FR-AUDIT-001 | P0 | Every state-changing operation writes an audit row: actor user, device, counter, business/branch, action, entity type and id, timestamp, request id, and a reason where the action requires one. |
| FR-AUDIT-002 | P0 | Audit rows are append-only; no API can modify or delete them. Database privileges restrict `UPDATE`/`DELETE` on the audit table. |
| FR-AUDIT-003 | P0 | Sensitive-value changes (price override, discount, oversell override, cancellation, write-off, adjustment, device revocation, role change, tax-mode change) record before/after values. |
| FR-AUDIT-004 | P0 | Audit logs never contain passwords, tokens or full secrets. |
| FR-AUDIT-005 | P1 | Audit log is queryable by entity, actor and date range for users with `audit.read`. |

---

## 7. Non-functional requirements

### 7.1 Performance (`NFR-PERF`)

| ID | Requirement | How measured |
|---|---|---|
| NFR-PERF-001 | Product search p95 ≤ 150 ms server time at 100k products, 50 concurrent searches | Load test in CI-adjacent environment with a seeded 100k catalog; server-side timing histogram |
| NFR-PERF-002 | Sale finalisation p95 ≤ 600 ms, p99 ≤ 1.5 s for a 10-line bill | Endpoint latency metric per route |
| NFR-PERF-003 | Draft save p95 ≤ 250 ms | Endpoint latency metric |
| NFR-PERF-004 | Daily sales report for one day p95 ≤ 1 s; for one month ≤ 3 s | Query timing on seeded 2-year dataset |
| NFR-PERF-005 | Supports ≥ 5 concurrent counters and ≥ 20 open drafts per counter with no degradation beyond the above targets | Concurrency test |
| NFR-PERF-006 | No endpoint issues unbounded queries; every list endpoint is paginated with a hard max page size | Code review + automated test asserting max page size |

### 7.2 Reliability and availability (`NFR-REL`)

| ID | Requirement |
|---|---|
| NFR-REL-001 | Target availability 99.5% monthly during shop hours, measured by an external health probe. Single-region deployment; this target is deliberately honest rather than aspirational. |
| NFR-REL-002 | RPO ≤ 5 minutes via point-in-time recovery; RTO ≤ 4 hours. |
| NFR-REL-003 | Restore from backup is **tested** at least quarterly into a scratch environment, with the result recorded. An untested backup does not count as a backup. |
| NFR-REL-004 | No data loss of finalised financial records under process crash, container restart, or client disconnect mid-request. |
| NFR-REL-005 | All write endpoints that a client may retry are idempotent. |
| NFR-REL-006 | Graceful shutdown drains in-flight requests before exit. |

### 7.3 Security (`NFR-SEC`)

| ID | Requirement |
|---|---|
| NFR-SEC-001 | HTTPS only in production; HSTS enabled; no plaintext transport. |
| NFR-SEC-002 | Passwords hashed with Argon2id at documented parameters. No reversible storage of credentials. |
| NFR-SEC-003 | Access tokens are short-lived; refresh tokens are rotating, device-bound, stored in `HttpOnly`/`Secure`/`SameSite` cookies. Tokens are never written to `localStorage`. |
| NFR-SEC-004 | Authorisation is enforced server-side on every endpoint. UI-level hiding is not a control. |
| NFR-SEC-005 | Business and branch isolation is enforced in the data-access layer; a cross-tenant read is a test-failing defect. |
| NFR-SEC-006 | All input validated by schema; no raw string interpolation into SQL; ORM parameterisation only. |
| NFR-SEC-007 | Uploaded files are type- and size-checked, stored with generated names outside any served path, and never executed. |
| NFR-SEC-008 | Rate limiting on authentication, search and write endpoints. |
| NFR-SEC-009 | Secrets come from the environment or a secret manager; none are committed. CI scans for committed secrets. |
| NFR-SEC-010 | Dependency vulnerability scanning on every CI run; high/critical findings block release. |
| NFR-SEC-011 | Structured logs carry a request id and never contain secrets, tokens or full card/PII payloads. |
| NFR-SEC-012 | Database credentials and privileged secrets never appear in the frontend repository or any client bundle. |

### 7.4 Data integrity (`NFR-DATA`)

| ID | Invariant — each has a corresponding automated test |
|---|---|
| NFR-DATA-001 | For every product/branch: `inventory_balance.quantity` = sum of its stock movements. |
| NFR-DATA-002 | For every invoice: `total` = sum of line totals + tax − discounts + rounding adjustment. |
| NFR-DATA-003 | For every invoice: `amount_paid` = sum of its payment allocations + credit-note allocations, and `payment_status` matches the comparison of `amount_paid` to `total`. |
| NFR-DATA-004 | For every payment: sum of allocations ≤ payment amount. |
| NFR-DATA-005 | The **internal sale reference** is **unique** and **sequential** per (branch, series, financial year) among non-deleted rows. ⚠️ **Gaplessness is NOT an invariant** — it is moot now that no customer-visible number exists ([OD-3](OWNER-DECISIONS.md#od-3) re-answered). Uniqueness remains enforced by `uq_invoices__series_sequence`. If **[OD-25](OWNER-DECISIONS.md#od-25)** later requires a visible serial number, the gapless question revives and must be answered before any `TAX INVOICE` is issued. |
| NFR-DATA-002b | For every invoice: `subtotal` = sum of its `invoice_items.line_total`. *(Promoted from `DATA-MODEL.md §4` in Phase 2 closure — `ck_invoices__total` constrains `total` from its components but nothing otherwise ties `subtotal` to the line items, so without this an invoice could satisfy the database `CHECK` and still disagree with its own lines. Audit CF-9.)* |
| NFR-DATA-006 | No finalised invoice, invoice item, payment, stock movement or audit row is ever updated or deleted by application code. |
| NFR-DATA-007 | Inventory balance is negative only on rows whose movement history includes an oversell override. |
| NFR-DATA-008 | Cumulative returned quantity per invoice item ≤ sold quantity. |
| NFR-DATA-009 | Monetary columns are `NUMERIC`, never floating point, end to end. |
| NFR-DATA-010 | Every row in a business-scoped table carries a non-null `business_id`. |

### 7.5 Maintainability and operations (`NFR-MAINT`, `NFR-OPS`)

| ID | Requirement |
|---|---|
| NFR-MAINT-001 | Modular monolith with explicit module boundaries; cross-module access only through documented service interfaces. |
| NFR-MAINT-002 | Static type checking and linting pass in CI with no errors. |
| NFR-MAINT-003 | Test coverage gate: ≥ 85% on domain/service layers; the money, stock and sync modules require ≥ 95% branch coverage. |
| NFR-MAINT-004 | All schema changes go through reviewed, reversible migrations. No manual production DDL. |
| NFR-OPS-001 | Liveness and readiness endpoints; readiness fails if the database or migration state is bad. |
| NFR-OPS-002 | Structured JSON logs with request correlation ids propagated from the client. |
| NFR-OPS-003 | Error reporting to an aggregator with release and environment tagging. |
| NFR-OPS-004 | Metrics: request rate/latency/errors per route, finalisation success rate, sync queue depth, oversell override count, reconciliation discrepancies. |
| NFR-OPS-005 | Separate development, staging and production configurations with no shared credentials. |
| NFR-OPS-006 | Documented and rehearsed rollback procedure for both code and migrations. |

---

## 8. Key workflows and state transitions

### 8.1 Cash sale — "Print Bill"

```
Draft (open) ──validate──> POST /sales:finalize (Idempotency-Key)
                             │
   ┌─────────────────────────┼──────────────────────────┐
   │ permissions ok?         │ no → 403                 │
   │ lines valid?            │ no → 422                 │
   │ prices unchanged?       │ no → 409 PRICE_CHANGED   │
   │ lock inventory rows     │                          │
   │ stock sufficient?       │ no → 409 INSUFFICIENT_STOCK (unless override)
   │ allocate invoice number │                          │
   │ insert invoice+items+movements+payment             │
   │ COMMIT                  │                          │
   └─────────────────────────┴──────────────────────────┘
                             │
                   201 {invoice, print_payload}
                             │
                 draft → finalized, invoice → issued
                             │
            client renders + prints; print failure is harmless
```

**Invoice states:** `issued → cancelled` (one-way, windowed, audited). No other transitions.
**Payment status (derived):** `unpaid | partially_paid | paid | overpaid`, plus `cancelled`.

### 8.2 Credit sale — `Credit Bill`

Identical engine. Additional steps before finalisation: resolve or create the customer
(`FR-CUST-003`), capture business name, vehicle number, mechanic, phone, due date and notes,
optionally capture an initial part payment. The result is one finalised invoice with
`is_credit = true` and a payment of less than the total (possibly zero).

The invoice appears in **both** Daily Sales (by sale date) and Credit Bills (by outstanding
balance). There is **no separate credit billing engine** (C-6).

### 8.3 Draft lifecycle

`open → (editing, versioned) → finalized`
`open → cancelled` (explicit user discard, audited, items retained for a retention window)
`open → stale` (older than `drafts.stale_days`; listed for review, never auto-finalised)

### 8.4 Import job lifecycle

`uploaded → parsing → parsed → needs_review → applying → applied`
with `failed` reachable from `parsing`/`applying`, and `cancelled` from any pre-`applying` state.
`applied` is terminal.

### 8.5 Stock movement — always paired

No balance mutation exists without a ledger row in the same transaction. Enforced by routing all
stock changes through one `InventoryService.apply_movement` path, with a test that asserts no
other code path writes to the balance table.

---

## 9. Error handling and edge cases — decided behaviour

This section answers the hazardous cases raised in discovery. Each has a decided behaviour and a
test.

| # | Situation | Decided behaviour |
|---|---|---|
| E-1 | Internet fails **during** finalisation | Client keeps the request with its idempotency key in a durable outbox and shows "Not yet confirmed — do not hand over a final bill". On reconnect it retries the same key: either the original invoice comes back, or it finalises now. The customer can be given a clearly watermarked, un-numbered **counter slip**, never a fake invoice number. |
| E-2 | Server commits but the response is lost | The retry with the same `Idempotency-Key` returns the stored response plus `Idempotent-Replay: true`. Exactly one invoice exists. |
| E-3 | Two counters sell the last unit | Deterministic row locks serialise them. First commits; second gets `409 INSUFFICIENT_STOCK` with `available: 0`. No negative stock, no double sale. |
| E-4 | A draft is edited on two devices | Drafts are counter-owned. A second editor hitting a stale `base_version` gets `409 DRAFT_VERSION_CONFLICT` with the server state and must choose keep-mine / keep-theirs. No silent merge. Managers can force a takeover (audited). |
| E-5 | Catalog price changes while a draft is open | Finalisation compares each line against the live catalog price. Changed lines block with `409 PRICE_CHANGED` and the new prices, unless the user explicitly accepts them or the line is a permissioned manual override. |
| E-6 | A finalised invoice must be corrected | Never edited. Within the cancellation window: cancel + reverse + full credit note. After it: partial/full return and credit note, or a reasoned adjustment. The original number always remains visible. |
| E-7 | A payment is submitted twice | Idempotency key dedupes it. If it slips through as two genuinely distinct payments, the fix is a `reversal` payment, never a delete. |
| E-8 | The printer fails | Irrelevant to correctness — the invoice is already committed. The UI shows the invoice number with a Reprint action. Reprint is a read-only re-render. |
| E-9 | The same PDF is uploaded twice | `file_sha256` uniqueness returns the existing job with `DUPLICATE_FILE`. Proceeding requires an explicit flag plus a reason and is audited. An `applied` job can never be applied again. |
| E-10 | A customer returns part of a sale | Return references specific invoice items; cumulative returns can't exceed sold quantity; stock-in (or scrap if damaged) plus a credit note at snapshot prices; credit note is allocated, refunded, or held on account by explicit choice. |
| E-11 | A user loses a device holding offline data | Owner/manager revokes the device. Sync from it is refused (`DEVICE_REVOKED`) and its refresh family is invalidated. Unsynced **drafts** may be lost by design — they were never sales. Because drafts sync on every change while online, the exposure equals the offline window. Local storage is not encrypted, so disk encryption on counter machines is a documented operational requirement. |
| E-12 | An import has duplicate SKUs or ambiguous matches | Those rows are flagged `duplicate_in_file` / `ambiguous` and **cannot be applied** until a human decides per row. No auto-creation, no auto-merge, no quantity guessing. |
| E-13 | Stock received at branch A, sold at branch B | Balances are per branch, so branch B simply has no stock and the sale is refused. A proper transfer module is required for this to work and is explicitly out of MVP scope (`FR-INV-010`). Until then, mis-received stock is corrected by two reasoned adjustments. |
| E-14 | Clock skew on a counter device | Server time is authoritative for all financial records. Client timestamps are stored separately for diagnostics only. |
| E-15 | Draft references a product that was deactivated or deleted meanwhile | Products are never hard-deleted once referenced. A deactivated product blocks finalisation with `PRODUCT_INACTIVE` and names the line. |
| E-16 | Session expires mid-bill | Draft work is local and safe. The client re-authenticates and retries; no draft content is lost. |
| E-17 | Two users try to cancel the same invoice | Cancellation locks the invoice row and checks state; the second gets `409 INVOICE_ALREADY_CANCELLED`. |
| E-18 | Quantity with decimals (oil, grease by weight) | Quantities are `NUMERIC(14,3)`; per-unit-of-measure rules decide whether fractions are allowed. Integer-only units reject fractional input. |

---

## 10. Acceptance criteria (go-live gate)

Phase 1 defines these; Phase 2 turns each into named tests (see `docs/TESTING-STRATEGY.md`).

| ID | Criterion |
|---|---|
| AC-1 | A 5-line cash sale finalises and prints, with stock decremented exactly once, an **internal** sale reference allocated inside the finalisation transaction, and **no customer-visible invoice number anywhere on the printed bill**. |
| ~~AC-1a~~ | ❌ **WITHDRAWN.** It asserted conditional gaplessness for a customer-visible serial number. [OD-3](OWNER-DECISIONS.md#od-3) is re-answered — there is no such number — so the criterion has no subject. If **[OD-25](OWNER-DECISIONS.md#od-25)** establishes a statutory requirement, a replacement criterion is written then. |
| AC-2 | Replaying the finalise request with the same idempotency key creates no second invoice and no second stock movement. |
| AC-3 | Two concurrent finalisations for the last unit produce exactly one sale and one explicit `INSUFFICIENT_STOCK` error. |
| AC-4 | A credit sale with a part payment shows the correct outstanding balance and appears in both daily sales and credit reports, with reconciling totals. |
| AC-5 | 20 drafts per counter can be created, edited, switched and recovered after a browser restart with zero content loss. |
| AC-6 | With the network disabled, drafts can be created and edited; on reconnect they sync with explicit per-operation outcomes, and finalisation is correctly refused while offline. |
| AC-7 | Every invariant in `NFR-DATA` has a passing automated test, including a randomised concurrency test. |
| AC-8 | A cross-business read attempt fails for every endpoint in an automated matrix test. |
| AC-9 | An import of a file with duplicate SKUs and ambiguous names cannot be applied without per-row human decisions, and applying twice changes nothing the second time. |
| AC-10 | Backup restore into a scratch environment is demonstrated, with the recovered database passing all invariant tests. |
| AC-11 | While `tax.mode = estimate_only`, no document anywhere says "Tax Invoice", and the gate blocking `gst_enabled` is demonstrated. |
| AC-12 | A partial return adjusts stock, creates a correct credit note, and reduces outstanding without touching the original invoice rows. |

---

## 11. Dependencies and implementation priority

```
Phase 2A  Foundations    auth, business/branch/counter, settings, audit, migrations, CI
Phase 2B  Catalog        products, aliases, search  ──┐
Phase 2C  Inventory      balances, ledger, adjustments ├─> required by sales
Phase 2D  Sales          drafts, finalisation, idempotency, numbering, print payload
Phase 2E  Customers+Credit  customers, payments, allocations, outstanding, ageing
Phase 2F  Offline        draft sync, catalog pull, device registry, conflict handling
Phase 2G  Purchases      suppliers, goods receipt
Phase 2H  Reports        daily sales, credit ageing, stock, exports
Phase 2I  Imports        upload, parse worker, review, apply
Phase 2J  Returns        returns, credit notes
Phase 2K  Hardening      load tests, restore drill, monitoring, go-live gates
```

Hard dependencies: Sales requires Catalog + Inventory + Auth. Credit requires Sales. Offline sync
requires Drafts. Imports require Catalog + Purchases. Reports require Sales + Credit.

---

## 12. Out of scope for v1

E-invoicing/IRP and e-way bills; GST return filing; accounting ledger and journal entries; payroll;
supplier payables and ageing; barcode label printing; customer-facing app or portal; SMS/WhatsApp
campaigns; multi-currency; branch transfers; serial/batch/expiry tracking; warranty tracking;
job cards and workshop management; loyalty schemes; offline sale finalisation.

## 13. Future enhancements

Branch transfers with in-transit stock; supplier payables; WhatsApp/SMS invoice delivery and
payment reminders; e-invoicing once statutory status is confirmed; barcode scanning and label
printing; batch/expiry for lubricants; purchase suggestions from sales velocity; owner mobile
dashboard; offline finalisation with per-counter stock allocation; accounting export integration.

## 14. Release and production-readiness gates

A release to production is blocked unless **all** of these hold:

1. `settings.tax.mode` is explicitly set, and if `gst_enabled`, the `FR-TAX-003` gate passes.
2. Business legal name, address and contact are configured from owner-supplied values (no placeholders).
3. The print layout has been verified against the owner's actual Excel invoice and signed off.
4. All `NFR-DATA` invariant tests and the concurrency suite pass in CI.
5. Backups are enabled **and** a restore has been demonstrated (`NFR-REL-003`).
6. Monitoring, error reporting and health checks are live with alerting to a real recipient.
7. No high/critical dependency vulnerabilities outstanding.
8. Migrations are reversible and tested forward and backward on a production-sized copy.
9. Admin credentials rotated off any seeded defaults; forced password change verified.
10. Every blocking question in §5.1 is answered and reflected in configuration.
11. Staff have been trained on the "not yet confirmed" offline finalisation state (E-1) and on reprint-vs-new-sale.

---

## 15. Related documents

| Document | Contents |
|---|---|
| `docs/ARCHITECTURE.md` | Modules, layering, transactions, jobs |
| `docs/DATA-MODEL.md` | Tables, constraints, indexes, ER diagram |
| `docs/API-SPECIFICATION.md` | Endpoints, schemas, error codes, idempotency |
| `docs/SECURITY.md` | Auth, isolation, uploads, secrets, logging |
| `docs/OFFLINE-SYNCHRONIZATION.md` | Sync protocol and conflict rules |
| `docs/TESTING-STRATEGY.md` | Test layers and invariant tests |
| `docs/DEPLOYMENT.md` | Environments, migrations, backups, rollback |
| `docs/DECISIONS.md` | Architecture decision records |
| `docs/TRACEABILITY.md` | Requirement → API → entity → test matrix |
| `../../bmfrontend/docs/PRD.md` | Client-side requirements with matching IDs |
