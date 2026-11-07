# Owner decision required — editing a finalised bill in place

> ## ✅ RESOLVED 2026-11-07 — the owner granted Decision A
>
> Both questions below were answered and the work is **implemented, tested and running**.
> This document is kept as the decision record; it is no longer a request.
>
> **Question 1 — may finalised invoices be edited?** *Yes*, under controlled, audited,
> permission-gated in-place editing. The recommended design in §3 is what was built.
>
> **Question 2 — what happens when an edit drops below what has been paid?**
> *Refuse the edit.* No refund entity was invented, no on-account credit, no change to
> payment history. The operator is pointed at the existing return / credit-note
> workflow. Enforced by `EDIT_BELOW_PAID_TOTAL` and asserted by four tests, including one
> that proves a refused edit writes no revision and no stock movement.
>
> **Where it now lives:** [ADR-034](DECISIONS.md#adr-034) · OD-10's re-answer in
> [OWNER-DECISIONS.md](OWNER-DECISIONS.md#od-10) · `FR-SALE-010` and `FR-SALE-016` in
> [PRD.md](PRD.md) · invariant 1 in [CLAUDE.md](../CLAUDE.md) · migration `0024` ·
> `src/bm/domain/sales/amend.py`.
>
> `billing.sale.edit` is held by **owner and manager only** — not the cashier, matching
> `billing.sale.cancel`.

| Field | Value |
|---|---|
| Raised | 2026-11-07 |
| Raised by | Implementation of the owner's 2026-11-07 change request, features 7–11 |
| Blocked (now unblocked) | **F7** · **F8** · **F9** · **F10** — all implemented 2026-11-07 |
| Does **not** block | Everything else in the request — features 1–6 and 12–16 are implemented and tested |
| Decision owner | Owner, **and** the accountant for question 2 |

---

## 1. What was asked, and why it stopped here

Feature 9 of the request is explicit, and is labelled a critical requirement:

> Editing a finalised invoice must update the original invoice. It must NOT: cancel the
> original invoice · create a new invoice · allocate a new invoice number · duplicate the
> sale …

That cannot be built without reversing a decision this project has recorded in five
places and enforced in the database. It is not a missing screen; it is a deliberate
control, and the request to remove it needs to be a decision rather than an inference.

### Where the opposite is currently recorded

| Source | What it says |
|---|---|
| `OWNER-DECISIONS.md` **OD-10** | "Issued invoices are **immutable**… **There is no edit path, ever.**" Listed under *Already confirmed*, i.e. not one of the options being chosen between |
| `PRD.md` **FR-SALE-010** | "A finalised invoice is **immutable**." Priority **P0** |
| **ADR-015** | Corrections are cancellation, return, credit note or reversal |
| `CLAUDE.md` invariant 1 | "Finalised financial records are immutable… Database privileges enforce it; **do not try to work around them**" |
| `CLAUDE.md` §6 | "Do not add an endpoint that mutates a finalised financial record" |

### And where it is enforced

Migration `0011_invoices.py` does this, deliberately:

```sql
REVOKE UPDATE, DELETE, TRUNCATE ON invoices FROM bm_app;
GRANT  UPDATE (status, cancelled_at, cancelled_by_user_id, cancellation_reason,
               amount_paid, payment_status, updated_at) ON invoices TO bm_app;
REVOKE UPDATE, DELETE, TRUNCATE ON invoice_items FROM bm_app;
GRANT  UPDATE (returned_quantity) ON invoice_items TO bm_app;
```

Verified live on 2026-11-07 against the running stack:

```
$ psql -U bm_app -c "UPDATE invoices SET total = 1;"
ERROR:  permission denied for table invoices
```

So an in-place edit is not merely undesigned — **the application's own database role
cannot perform it.** Any implementation of F9 must begin with a migration that re-grants
`UPDATE` on `invoices.total`, `subtotal`, `discount_total`, `tax_total` and on
`invoice_items`. That is the single most consequential line of code in this change
request, and it is the one I am not willing to write on an inference.

There is also an existing test whose *name* asserts the opposite of what is now wanted —
`DailyBills.test.tsx` › *"Edit Bill corrects by cancelling and re-billing, never by
editing"*. Implementing F9 means deleting a test that exists specifically to prevent it.
That is a legitimate thing to do on an owner's instruction and an illegitimate thing to
do quietly.

---

## 2. The two questions

### Question 1 — for the owner

**Do you want finalised invoices to become editable, replacing OD-10?**

This is a real trade-off, not a formality. The current rule is the shop's strongest
internal control against staff fraud (threat **T-1** in `SECURITY.md`): a cashier cannot
make a sale disappear or shrink after the fact, because the database will not let them.
Making invoices editable removes that, and an audit trail is a weaker substitute than an
impossibility.

| | Keep OD-10 (today) | Reverse it (what F9 asks for) |
|---|---|---|
| Correcting a bill | Cancel, then re-bill. Two invoices: one cancelled, one issued | One invoice, amended in place |
| Invoice identity | A correction creates a new internal reference | Preserved, which is what the request wants |
| Staff cannot quietly alter a sale | Enforced by **database privilege** | Enforced by **audit log only** |
| Day's takings | Reconcile against two rows, one marked cancelled | Reconcile against one row plus its revision history |

### Question 2 — for the owner **and** the accountant

**What happens when an edit reduces a bill below what has already been paid?**

The request anticipates this and says:

> If the new total is below recorded payments, do not delete or silently alter payments.
> Follow the approved refund/credit adjustment policy…

**There is no approved refund policy, and no refund entity anywhere in the schema.** This
is already recorded as an open gap, not an oversight — `AmendBill.tsx` says so in as many
words: *"a cash refund is genuinely undecided policy, not a missing screen."* A ₹5,000
bill that is paid in full and then edited down to ₹3,000 leaves ₹2,000 that the system
can describe in exactly one of these ways, and it is not an engineering choice which:

* **refund** — money leaves the till and is recorded as leaving;
* **on-account credit** — the customer's balance goes ₹2,000 in credit against future bills;
* **refuse the edit** — the reduction is blocked and the correction must be a return or a
  credit note, which is what ADR-015 already provides.

---

## 3. What I recommend, if the answer to question 1 is yes

Not a plain `UPDATE`. The design below gives the request what it asks for while keeping
what OD-10 was actually protecting, and it is what I would build:

1. **The invoice row keeps its identity.** Same `id`, same `internal_reference`, same
   `customer_id`, same position in Daily Bills or the customer's credit folder. This is
   F9 satisfied literally.
2. **A new append-only `invoice_revisions` table** stores a complete snapshot of the
   header and lines *before* each edit, with the acting user and a mandatory reason.
   Nothing is ever destroyed; every historical state of every bill stays reconstructible.
   This is what replaces the privilege control with something auditable rather than
   with nothing.
3. **Stock is corrected by new movements, never by rewriting old ones.** A quantity
   changed from 3 to 5 writes a further movement of 2, so `stock_movements` stays
   append-only and `balance_after` stays truthful (invariant 4 intact).
4. **Payments are never touched.** `amount_paid` and `payment_status` are re-derived from
   the untouched allocations, exactly as they are today (ADR-022 intact).
5. **Optimistic concurrency** via a `version` column, so two counters editing one bill
   produces a `409` rather than a silent overwrite.
6. **`Idempotency-Key` required**, so a retried edit applies once.
7. **The narrowest possible grant** — `UPDATE` on the specific total columns only. Never
   `DELETE`, never `TRUNCATE`, and `invoice_items` gains inserts plus a `superseded_at`
   rather than destructive updates.
8. **Question 2's answer is enforced in one place** in the service, so the policy is
   stated once and testable.

Estimated effort once both answers are in hand: one migration, one domain service, one
endpoint, and revisions to the four documents plus the invariant tests — a substantial
piece of work, and all of it ready to start.

---

## 4. What is **not** blocked by this

Everything else in the 2026-11-07 request is implemented, tested and running locally.
Features 7, 8, 10 and 11 are listed as blocked only because each of them describes the
behaviour of an edit path that does not yet exist; none of them needs a separate
decision once question 1 is answered.

Of note: **F11 is already satisfied today.** The correction action has never depended on
whether a bill was printed — it is offered on persisted status plus the
`billing.sale.cancel` permission, which is the right criterion and is what F11 asks for.
