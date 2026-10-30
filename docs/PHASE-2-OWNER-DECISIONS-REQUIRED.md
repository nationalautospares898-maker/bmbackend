# Phase 2 — Owner Decisions Required

| Field | Value |
|---|---|
| Status | **Living Phase 2 record.** Reconciled at Phase 2 closure |
| Audience | The business owner, and their accountant for the tax and rounding items |
| Purpose | The **short** list of decisions that genuinely need you. Everything else has been decided by engineering or is already recorded |
| Authority | [`OWNER-DECISIONS.md`](OWNER-DECISIONS.md) remains the authoritative register. This file does not replace it — it is the **action list** derived from it, plus two new items |
| Implementation performed | **None** |

> ### What changed at Phase 2 closure
>
> **Nothing you decided was changed, and no answer was invented.** Two things improved:
>
> 1. **Two of the four "blocking" items turned out to be narrower than recorded.** Engineering can now
>    build both the numbering module and the money engine while your accountant considers items 1 and
>    2 — so those two questions no longer hold up the work, only the final sign-off of one test each.
>    (OD-3 and OD-7 moved from `BLOCKS-IMPLEMENTATION` to `BLOCKS-TESTING`.)
> 2. **Eleven documentation defects were corrected**, including one that had quietly turned your
>    *undecided* numbering question into a stated guarantee. It is now correctly marked undecided
>    everywhere — which is why item 2a below asks only for an acknowledgement, not a decision.
>
> **Still nine items, unchanged in substance.** Items 3–9 are exactly as before.

> ## Read this first
>
> **You do not need to re-answer anything you have already decided.** The following are **settled**
> and are being built as you specified — they are listed only so you can see they are not being
> re-asked:
>
> | Already decided | What it means |
> |---|---|
> | The four button labels | `Print Bill` · `Credit Bill` · `Save a Draft` · `Clear Draft` — fixed, not being renamed |
> | The credit workflow | `Credit Bill` completes the sale immediately, no extra page. Only a customer is required |
> | Amount display on the bill | **No decimals** on `PRICE` and `GRAND TOTAL` (`240`, `3850`). Unused rows blank, not `0`. *(One follow-up remains — see item 10)* |
> | Invoice numbering | **Not needed** — nothing is printed as an invoice number. *(One legal follow-up remains — see item 11)* |
> | Payment/balance on credit bills | Yes, on credit bills only |
> | The word `ESTIMATE` | Kept, for both cash and credit |
> | Same bill design for cash and credit | Yes — one layout, not two |
>
> **Ten items need you** — items 1, 4, 5, 6, 7, 8, 9/9a, 10, 11, 12 *(items 2 and 2a are closed by
> your no-invoice-numbering answer; **item 3 is closed because you sent the bill file**)*. Item 12 is
> new — it was found by reading that file. Nothing else is waiting on you.

---

## Summary

| # | Decision | Who decides | What it blocks | Recommended |
|---|---|---|---|---|
| **1** | [OD-7](#d1) — rounding precision and method | Accountant (+ you for coins) | **One test's expected values** — the money engine is already buildable | Round each line to 2 paise, then add up. No rupee-rounding |
| ~~2~~ | ~~OD-3 — must invoice numbers have no gaps?~~ | — | ✅ **CLOSED — you answered: no invoice numbering needed.** The gapless question no longer exists | — |
| ~~2a~~ | ~~OD-3-A acknowledgement~~ | — | ✅ **CLOSED** by the same answer | — |
| ~~3~~ | ~~OD-5 — the bottom of your Excel bill~~ | — | ✅ **CLOSED — you sent the file and we have read all of it.** Layout, sizes, colours, borders and both logos are now verified | — |
| **4** | [OD-22](#d4) — where the **credit balance block** goes *(the invoice-number half is closed)* | You | Final print layout | Decide on a printed sample |
| **5** | [OD-1 + OD-2](#d5) — GST status, and rates/HSN if registered | You + accountant | Go-live | Stay on estimates unless your accountant says otherwise |
| **6** | [OD-4](#d6) — your legal name, address and phone for the bill header | You | Go-live | Send the exact text; we will not invent it |
| **7** | [OD-6](#d7) — paper size and printer | You | Go-live | A5 plain paper, switchable to A4 |
| **8** | [OD-8](#d8) — accept that offline means capturing bills, not issuing them | You | Go-live (staff training) | Accept; it is the only safe option |
| **9** | [OD-19](#d9) — hosting, and **who gets the 9 p.m. alert** | You | Go-live | Managed hosting; name a person |
| **9a** | [OD-19-A](#d10a) — what should happen when an alert fires at night? | You | Go-live runbook | "Best effort, next morning" is a fine answer |
| **10** | **OD-23 — what if a price has paise?** *(new)* | You | Printing a bill that may misstate a price | **Only allow whole-rupee prices** — then every bill adds up |
| **11** | **OD-25 — does the law require a numbered invoice?** *(new)* | Accountant | Go-live | Keep numbering off as you asked; your accountant confirms it is acceptable |
| **12** | **[OD-26](#d12) — which way round should the date read?** *(new, found in your bill file)* | You | Go-live | **Day first** (`07-08-2026`) — or day + month name (`07-Aug-2026`) to remove all doubt |

---

<a id="d1"></a>
## 1 · Money rounding — precision and method ([OD-7](OWNER-DECISIONS.md#od-7))

**Why it matters.** This is the one calculation rule used identically by the server and by the screen
your staff watch while adding items. If we pick it now and you change it later, every money test in
both parts of the system has to be rewritten.

**What is already decided (by you).** The system must have one written rounding policy; **the server
is the authority** on every amount; the screen and the printed bill show what the server calculated
and never change its meaning. All three are being built.

**What is still open.** How many decimal places, which rounding method, and whether rounding happens
per line or only on the final total.

**Why this is worth a minute of your accountant's time.** Take a bill of three identical lines worth
`473.0320…` each:

| Rule | Printed lines | Printed total | Does the bill add up? |
|---|---|---|---|
| **Round each line, then add** | `473.03` × 3 | **`1419.09`** | ✅ Yes |
| Keep full precision, round the total | `473.03` × 3 | **`1419.10`** | ❌ No — differs by 1 paisa |

Under the second rule a customer who adds up the column gets a different number from the total, and
your staff cannot explain why.

**Recommended option.** Amounts to 2 decimal places; **round each line up on a half-paisa, then add
the lines**; **no rounding to whole rupees**. This is how a hand-written bill works, and it is the
only option where the bill always adds up.

**If you want to avoid coins.** We can round the final total to the nearest rupee — but the
difference then **must be printed on the bill** as its own line, or the total looks arbitrary. We
can now place that line precisely, because we have read your bill file — but it changes the printed
layout, so it needs your confirmation alongside item 7.

**Exact approval needed.** Your accountant confirms: *"2 decimal places, round half up at each line,
then sum; no whole-rupee rounding"* — or names a different rule.

**What this blocks.** Not the work. We build the calculation so the rule is a **setting**, not
something baked into the code — so we can build and test it now and switch the rule without
rewriting anything. What it blocks is **freezing the final expected numbers in the acceptance
tests**.

**One thing to know about changing it later.** A change takes effect on **new** bills only. Bills
already issued keep the figures they were printed with and are never recalculated — so a later change
cannot alter your past records or your past reports. It also needs no database rebuild.

**If you never answer.** We keep the recommended rule as a clearly-labelled default. It is safe, but
it is our proposal, not your accountant's decision — and the system would go live on it.

---

<a id="d2"></a>
## 2 · Must invoice numbers have no gaps? ([OD-3](OWNER-DECISIONS.md#od-3))

**Why it matters.** "Numbered in order" and "no numbers missing" are different things, and only the
first is decided.

**What is already decided (by you).** Numbers run **in order**, from a series, allocated by the
server, in the format `NAS/2026-27/00001`, resetting each financial year (April).

**What is still open.** Whether **every** number must belong to a real bill. If a sale fails halfway
through — the power goes, the internet drops — does it matter that number `00413` was never used?

| | |
|---|---|
| **No gaps allowed** | A failed sale uses up no number. Strictest reading of invoice-numbering rules |
| **Gaps acceptable** | Simpler, but an auditor may ask why `00413` is missing |

**Recommended option.** We are building the **no-gaps** version, because it satisfies both answers —
if gaps turn out to be fine, a no-gaps system is still perfectly acceptable. The reverse is not true.

**Exact approval needed.** Your accountant answers yes or no: *"Does this business legally require
strictly consecutive invoice numbers with no gaps?"*

**Important connection — please answer this with item 8.** If no gaps are required, then **billing
during an internet outage becomes permanently impossible**, not just postponed. Issuing bills offline
would need each counter to reserve a block of numbers in advance, and unused numbers in a block are
permanent gaps.

**What this blocks.** Only the *wording of the guarantee* we publish and the test that checks it — not
the work itself.

**If you never answer.** We build and ship the no-gaps version, and the documentation describes it as
our default rather than as a legal guarantee.

---

<a id="d2a"></a>
## 2a · **New** — please acknowledge how we are proceeding on numbering (`OD-3-A`)

**Why we are asking.** Our own records currently say two different things: the decision register says
the gap-free question is undecided and the numbering work cannot start, while the requirements
document already states gap-free numbering as a firm guarantee with a test attached. We will not
quietly pick one.

**What we found.** Building the gap-free version is **never wasted work**, whichever way item 2 is
answered — so there is no reason to hold up the work. The numbering module sits on the critical path;
everything else in billing waits behind it.

**What we propose.** Start building gap-free numbering now, and treat "numbers have no gaps" as **our
default** rather than as your confirmed requirement, until item 2 is answered.

**Exact approval needed.** A one-line acknowledgement: *"Understood — proceed with gap-free numbering
as the default while the legal question is checked."*

**What this blocks — corrected at Phase 2 closure: nothing.** The documentation defect has been
fixed: every place that wrongly stated gap-free numbering as a settled guarantee now marks it
undecided, and the numbering module is **already unblocked** because the design we are building
satisfies either answer. So this item is a **courtesy confirmation that you are content with that
approach**, not a gate. If you would rather we waited for your accountant before writing the
numbering code, say so and we will hold it — but there is no technical need.

**Honest note.** We are telling you this because the earlier version of this list said this reply was
"the single highest-value reply". That was true when the numbering module was recorded as blocked; it
is no longer.

---

<a id="d3"></a>
## 3 · The bottom of your Excel bill ([OD-5](OWNER-DECISIONS.md#od-5))

**Why it matters.** We have seen the **top** of your bill from the screenshot you sent, and we have
copied it exactly: the heading, address, mobile numbers, the Customer / Mechanic / Vehicle / DATE
block, the word `ESTIMATE` in its real position, and the columns `S R · ITEM · QTY · PRICE · TOTAL`.
✅ **Update 2026-10-30: the workbook arrived and was inspected. This item is closed** — what remains
is the paper/printer choice (item below) and the **date order** ([OD-26](OWNER-DECISIONS.md#od-26)).

The screenshot stops partway down. We have **not** seen: the **grand total**, any signature line, any
footer text, how many item rows the bill has, or the page setup.

**What we will not do.** Guess. The grand total is the most-read number on the page and we will not
invent where it goes or what it is called. We have also not gone looking for a substitute bill.

**You have already told us** you cannot supply the file, a scrolled screenshot or a photo at the
moment. **That is recorded and this is not a re-ask** — it stays on the list so that when it becomes
possible, we both know it is the one thing that finishes the bill.

**Any one of these closes it.** The original `ANA….xlsx` file (best — it also answers the page setup
and item-row count) · a second screenshot scrolled to the bottom · **a photo of a printed bill on
your shop's paper** (easiest, and it answers how the bill actually fits the page).

**What this blocks.** Only the **bottom** of the printed bill. The heading, the customer/vehicle
block and the item table are being built now from what we can see.

**If it never arrives.** We finish everything except the bottom section, and the printed bill cannot
be signed off for shop use. We will not fill the gap with a guess.

---

<a id="d4"></a>
## 4 · Where the invoice number and the balance go on the bill ([OD-22](OWNER-DECISIONS.md#od-22))

**Why it matters.** You have told us to **include** both, and we are building both. But your current
bill shows neither, so we have nowhere evidenced to put them.

**What is already decided (by you).** Payment and balance information is printed on **credit** bills.
Cash bills do **not** carry the payment block — we are deliberately not assuming otherwise.
**The invoice-number half of this question is gone**, because you have since said invoice numbering
is not needed.

**What is still open.** Where each one sits on the page.

**How we are handling it.** Both are built in a **switchable position** that can be moved — or turned
off — without breaking the rest of the layout. Our proposal is the invoice number top-right and a
`Paid / Balance / Due` block below the total.

**Exact approval needed.** Look at a printed sample and either accept the positions or point at where
they should go. **This needs item 3 first** — we cannot place a block below a total we have not seen.

**What this blocks.** The final print layout and the printed-sample sign-off.

---

<a id="d5"></a>
## 5 · GST registration, and rates if you are registered ([OD-1](OWNER-DECISIONS.md#od-1), [OD-2](OWNER-DECISIONS.md#od-2))

**Why it matters.** We will not print the words "Tax Invoice" on a document unless the tax setup is
complete and valid. Until then every document says `ESTIMATE`, which is what your current bill says.

**What is already decided.** Documents are titled `ESTIMATE` and carry no tax lines. The system
cannot be switched to tax invoices until a full checklist passes.

**What is still open.** Whether National Auto Spares is registered for GST; if so, the GSTIN,
registered legal name and state; which rate and HSN code applies to each product group; and whether
your shelf prices already include tax.

**Recommended option.** Stay on estimates unless your accountant says otherwise. An unconfigured
document labelled a tax invoice is a legal problem; an estimate is only a limitation.

**What you should know.** An `ESTIMATE` is **not a tax invoice**, so a business customer cannot claim
input credit against it. If that matters to your transport customers, raise it with your accountant.

**Please scope this early if you are registered.** Every active product would need a tax group before
GST can be switched on. With a large catalog that is a **significant data-entry job** — far better
known now than discovered at go-live.

**Exact approval needed.** You and your accountant confirm either *"not registered — stay on
estimates"* or supply the GSTIN, legal name, state, and the rate/HSN per product group.

**What this blocks.** Go-live only. Everything is built either way.

---

<a id="d6"></a>
## 6 · Your legal name, address and phone for the bill header ([OD-4](OWNER-DECISIONS.md#od-4))

**Why it matters.** This is printed on every bill a customer takes away.

**What is already decided.** The heading is **NATIONAL AUTO SPARES**. From your screenshot we also
have `AUTO NAGAR ,HYDERABAD` and `Mob:9948207903,9848136564`.

**What is still open.** Your **registered legal entity name** (if different from the trading name),
the full postal address as you want it printed, and whether both mobile numbers and any email should
appear.

**What we will not do.** Invent an address. If a field is empty the bill **prints nothing** there —
never a placeholder like "Address line 1". An obviously incomplete bill is better than a plausible
wrong one.

**Exact approval needed.** The exact text, line by line, as you want it to appear.

**What this blocks.** Go-live only.

---

<a id="d7"></a>
## 7 · Paper size and printer ([OD-6](OWNER-DECISIONS.md#od-6))

**What is still open.** What printer is at the counter and what paper bills print on.

**Recommended option.** A5 plain paper, portrait, switchable to A4 by a setting.

**One thing to flag.** If you use **pre-printed stationery** (paper that already has your heading and
borders) or a **thermal till roll**, tell us — both need a different template, not a setting change.
A thermal roll in particular is too narrow for the five-column table and would be a separate piece of
work.

**Exact approval needed.** Printer make/model and paper size — or just "A5 plain paper".

**What this blocks.** Go-live. The printed bill has to be checked on the real printer and paper.

✅ **New measurement (2026-10-30), from your actual bill file.** Your bill's printed block measures
about **92 mm wide × 176 mm tall**. On **A5 it fits** with roughly 18 mm to spare at the margins we
plan to use. On A4 it fits easily but would sit in the upper-left corner of a much larger sheet.
This **supports** the A5 recommendation — it does not replace your confirmation, because the
workbook never specified a paper size at all.

---

<a id="d12"></a>
## 12 · **New** — which way round should the date read? ([OD-26](OWNER-DECISIONS.md#od-26))

**Why we are asking.** This looks like a non-question, which is exactly why it is worth thirty
seconds of your time.

Your bill shows `06-06-2026` — **6 June 2026**. Because the day and the month are both `06`, that
example cannot tell us which way round you read it. We assumed **day-month-year**, the normal
Indian order. When we opened your actual Excel file we found it is formatted the **American** way:
**month first**.

On 6 June it makes no difference. On **7 August** it makes all the difference: the same bill would
read `07-08-2026` one way and `08-07-2026` the other. Months later, when you are chasing a credit
payment, that is the date you will be arguing about.

**Please pick one:**

| | Reads as | Example: 7 August 2026 |
|---|---|---|
| **(a) Day first** *(recommended)* | the normal Indian order | `07-08-2026` |
| **(b) Month first** | exactly what your Excel file does today | `08-07-2026` |
| **(c) Day, month name, year** *(clearest)* | impossible to misread | `07-Aug-2026` |

**Our recommendation: (a)** — we think the American format in your file is an accident of how Excel
was set up, not a choice you made. **(c)** removes the ambiguity permanently and still fits the
column.

**What we will not do.** We will not quietly "fix" your file's format, and we will not blindly copy
it either. Until you answer, we build with (a) and keep the question recorded.

**What this blocks.** Go-live only. Everything else can proceed.

---

<a id="d8"></a>
## 8 · Please accept what happens during an internet outage ([OD-8](OWNER-DECISIONS.md#od-8))

**Why it matters.** This is the biggest gap between the phrase "works offline" and what we are
building, and we would rather you hear it from us now than discover it during an outage.

**What happens with no internet:**

| Your staff **can** | Your staff **cannot** |
|---|---|
| Open the app | Complete a sale — cash **or** credit |
| Search the recently-used product list | Take a payment |
| Create, edit and switch between bills — **nothing is lost** | Receive stock |
| Add a new customer | Print a real invoice |
| Print an un-numbered **counter slip** | |

When the connection returns, everything finalises with one press. Nothing is re-typed.

**Why it cannot be otherwise.** You asked for three things: works offline, never oversell, and
auditable server-numbered bills. **All three cannot hold at once.** Two counters that are both offline
cannot each check the last unit of stock, and neither can safely issue an invoice number. Letting
them try produces negative stock and duplicate numbers — invisible corruption instead of a visible
inconvenience.

**One detail worth knowing.** A bill captured during an outage is **dated when it is finalised**, not
when it was typed. If that matters for your books, tell us — it is a separate question.

**Exact approval needed.** *"Understood — during an outage staff capture bills and print a counter
slip, and the real bill is issued when we are back online."* Then your staff must be trained on it.

**What this blocks.** Go-live. **Also answer item 2 with this one** — they constrain each other.

---

<a id="d9"></a>
## 9 · Hosting, and who receives a 9 p.m. alert ([OD-19](OWNER-DECISIONS.md#od-19))

**What is still open.** Where the system is hosted, **who is alerted when something breaks**, and
what monthly cost is acceptable.

**Recommended option.** A managed hosting platform in the region nearest the shop, with managed
database backups, one server. Low cost, backups handled for you.

**The part that matters most.** We will set up alerts for a system outage, for backups failing, and —
importantly — for **stock and the stock ledger disagreeing**, which means a bug worth finding
immediately. **An alert that reaches nobody is not monitoring.** We need a real named person and a
real phone number or email.

**Exact approval needed.** A named person for alerts, agreement to managed hosting, and a rough
monthly budget.

**What this blocks.** Go-live.

---

<a id="d10a"></a>
## 9a · **New** — what should happen when an alert fires at night? (`OD-19-A`)

**Why we are asking.** We found that our plans say every alert must reach a real person, but nothing
says **what that person is expected to do or how quickly**. Without that, a 9 p.m. alert about stock
and ledger disagreeing has no defined expectation, and in practice it gets ignored.

**This is a small decision and "do nothing until morning" is a perfectly good answer** for a
one-shop system.

| Option | What it means |
|---|---|
| **Best effort, next morning** (recommended) | Alerts are logged and reviewed when the shop opens. Billing keeps working through most problems anyway — staff can capture bills offline |
| Same evening | You or the developer check that night |
| Business hours only | Alerts outside shop hours wait |

**Exact approval needed.** Pick one. We write it into the operations runbook so the expectation is
explicit.

**What this blocks.** Nothing in the build — only the completeness of the go-live operations runbook.

---

## Not asking you about these

Decided by engineering, or already recorded with a safe default. **No reply needed.**

| Topic | Status |
|---|---|
| Programming languages, frameworks, database, hosting shape | Engineering decisions (ADR-001…033) |
| How invoices are made uncorrectable-but-fixable | Decided: never edited; corrected by cancellation, return, credit note or reversal |
| Whether an open bill holds stock | Default: no. Stock is checked when the sale completes — matches a part being taken off the shelf |
| Who may cancel a bill and for how long | Default: manager and owner, within 24 hours, reason required |
| Returns window | Default: 30 days, manager permission, damaged items scrapped |
| Credit terms and limits | Default: 30 days per customer; limits warn rather than block |
| Whether a cashier can give discounts | Default: no (0%). Easy to raise later |
| Marking reprints `DUPLICATE COPY` | Default: yes |
| Keeping a PDF of every bill | Default: no — a past bill reprints exactly from the stored record |
| How staff sign in | Default: username + password, trusted device for 30 days, optional PIN for shift changes |
| Counters and branches at launch | Default: one shop, 1–3 counters |
| Browser and screen size | Default: Chrome or Edge, 1366×768 or larger |
| Whether a credit customer must give a phone number | Default: no — warn, never block |

If you disagree with any default, say so and we will change it — but none of them is holding up work.

---

## Related documents

[`OWNER-DECISIONS.md`](OWNER-DECISIONS.md) *(the authoritative register, with full analysis)* ·
[`PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md`](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md) ·
[`PHASE-2-DECISION-REGISTER.md`](PHASE-2-DECISION-REGISTER.md) ·
[`PHASE-2-IMPLEMENTATION-PLAN.md`](PHASE-2-IMPLEMENTATION-PLAN.md) ·
[`PHASE-1-EXIT-CRITERIA.md`](PHASE-1-EXIT-CRITERIA.md)
