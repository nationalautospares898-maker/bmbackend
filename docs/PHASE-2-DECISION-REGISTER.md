# Phase 2 — Decision Register

> **Current Phase 2 design disposition (2026-10-30):** [Design Completion Addendum](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) is the current cross-cutting decision record. It resolves superseded open labels and defines the Phase 3 implementation baseline. Historical closure reports/addenda remain unchanged; legal, physical and operational gates remain gates. It does not represent implementation or test completion.


| Field | Value |
|---|---|
| Status | **Living Phase 2 record** |
| Scope | Technical decisions **made or proposed during Phase 2**. Phase 1 architecture decisions remain in [`DECISIONS.md`](DECISIONS.md) (ADR-001…033) and are **not** restated or renumbered here |
| ID scheme | `P2-D-nn`, a namespace separate from ADR numbering so neither can collide |
| Status values | `proposed` → `accepted` / `rejected` / `superseded by P2-D-nn` |
| Implementation performed | **None** |

> ## Two rules that govern this file
>
> 1. **Business decisions do not live here.** Anything the owner must decide belongs in
>    [`OWNER-DECISIONS.md`](OWNER-DECISIONS.md) (the authoritative register) and is surfaced for
>    action in [`PHASE-2-OWNER-DECISIONS-REQUIRED.md`](PHASE-2-OWNER-DECISIONS-REQUIRED.md). Where a
>    decision below *touches* an owner decision, it says exactly what owner input it needs and does
>    **not** assume it.
> 2. **No decision here supersedes a Phase 1 ADR or an owner decision.** Three records (`P2-D-02`,
>    `P2-D-03`, `P2-D-04`) *re-scope* or *clarify* existing records; none reverses one. Supersession,
>    if it ever happens, is recorded explicitly — never silently.

---

## Index

| ID | Decision | Status | Owner approval needed | Affects |
|---|---|---|---|---|
| [P2-D-01](#p2-d-01) | Phase 2 artifacts live in `bmbackend/docs/`; `bmfrontend` carries a pointer index | **accepted** | No | Documentation governance |
| [P2-D-02](#p2-d-02) | Internal sale reference; no current customer-visible numbering or gapless requirement | **accepted** | No; OD-3 is resolved for current scope | M9, internal audit/recovery |
| [P2-D-03](#p2-d-03) | Versioned policy object; whole-INR/no-rounding default defined in addendum §3 | **accepted** *(mechanism)* | No; values are a documented design default, not owner/accountant approval | M4, M17, M20 |
| [P2-D-04](#p2-d-04) | Explicit source-of-truth hierarchy | **accepted** | No | All documentation |
| [P2-D-05](#p2-d-05) | The import-boundary linter is a merge blocker from M1 | **accepted** | No | M1 onward |
| [P2-D-06](#p2-d-06) | Claim the idempotency key **before** acquiring inventory locks | **accepted** | No | M10 |
| [P2-D-07](#p2-d-07) | Normative idempotency-fingerprint canonicalisation + shared fixtures | **accepted** | No | M10, M18 |
| [P2-D-08](#p2-d-08) | Block draft edits while a finalisation is unconfirmed; clear the pending row only on resolution | **accepted** | No | M18, M22 |
| [P2-D-09](#p2-d-09) | Lazy financial-year `invoice_series` creation inside the finalisation transaction | **accepted** | No | M9 |
| [P2-D-10](#p2-d-10) | Settings resolution: defaults in code, overridable by rows, published to the client | **accepted** | No | M4, M14, M17 |

**`accepted` here means "engineering has decided and no owner input is required"** — the same meaning
the ADR status vocabulary carries. It does **not** mean implemented, and it never implies that an
owner decision the design *depends on* has been answered.

> ### Status changes made at Phase 2 closure
>
> `P2-D-02`, `P2-D-03`, `P2-D-04`, `P2-D-07`, `P2-D-08` and `P2-D-09` moved `proposed → accepted`
> because each is a **technical** decision within engineering's authority, and each now has a
> complete, implementable design with acceptance criteria:
>
> | Decision | Design completed at |
> |---|---|
> | `P2-D-07` idempotency + canonicalisation | [Blueprint §5.2](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) — 9 sub-sections, 9 acceptance criteria |
> | `P2-D-08` sale-finalisation recovery | [Blueprint §5.4](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) — 12 cases, state machine, forbidden transitions, 11 acceptance criteria |
> | `P2-D-09` financial-year series init | [Blueprint §8.2](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) — options, race analysis, 8 tests |
> | `P2-D-03` money policy | [Blueprint §7.1–7.5](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) — evidence check, schema, 4 anti-divergence controls |
>
> **Two things did NOT change status**, deliberately:
> - **No ADR was promoted.** ADR-001…032 remain `proposed`; ADR-033 remains the only `accepted` one
>   (owner-specified). Phase 2 has no authority to accept an ADR, and `P2-D-*` is a separate
>   namespace precisely so it cannot be mistaken for one.
> - Current owner-decision dispositions are in the 2026-10-30 Design Completion Addendum. OD-3 is resolved for current product scope; OD-25 remains the legal gate. OD-7 values are now a documented engineering default, not owner/accountant approval.

---

<a id="p2-d-01"></a>
## P2-D-01 — Phase 2 artifacts live in `bmbackend/docs/`

**Status** accepted · **Owner approval** no · **Related** ADR-021

### Problem
Where do eight cross-cutting Phase 2 planning documents live, given that ADR-021 forbids a third
repository and forbids anything authoritative at the workspace root?

### Context
ADR-021 established: documentation lives in the repository that owns the contract; shared ADRs are
recorded fully in the owning repository with a stub in the other; `bm/README.md` is explicitly
non-authoritative and must contain **links, not facts**; the one exception at the root is a
*point-in-time audit snapshot*. The Phase 2 artifacts are **living planning records**, not snapshots,
so the exception does not apply to them.

### Alternatives
1. **All eight in `bmbackend/docs/`, with a thin pointer index in `bmfrontend/docs/`.**
2. All eight at the workspace root, alongside the closure reports.
3. Split by repository — frontend-relevant parts duplicated into `bmfrontend/docs/`.
4. A third `bmdocs` repository.

### Decision
Option 1.

### Rationale
Option 2 puts living, authoritative planning records outside any repository — untracked, unreviewed,
and in a location ADR-021 reserves for disposable evidence. Option 3 duplicates requirement IDs,
milestones and decisions across two repositories, which is the failure mode ADR-021 was written to
prevent; the traceability matrix is already single-sourced in `bmbackend` for exactly this reason.
Option 4 contradicts C-10. Option 1 also matches the existing precedent: `OWNER-DECISIONS.md` and
`TRACEABILITY.md` are both cross-cutting and both live in `bmbackend/docs/` with the frontend linking
to them.

### Consequences
- Eight new files in `bmbackend/docs/`, all prefixed `PHASE-2-`.
- `bmfrontend/docs/PHASE-2-INDEX.md` is a **pointer file only**, carrying links and one-line
  descriptions — no facts, subject to the same maintenance rule as `bm/README.md`.
- Both `CLAUDE.md` files and both READMEs link the authoritative set.
- A Phase 2 document that changes a *shared* requirement still requires a matching commit in both
  repositories (ADR-021's maintenance rule is unchanged).

---

<a id="p2-d-02"></a>
## P2-D-02 — Internal sale reference design; no current visible-number or gapless requirement

> **Current disposition:** The 2026-10-30 Design Completion Addendum supersedes this section's earlier open-scope wording. P2-D-02 no longer asserts a gapless customer invoice series; only the internal reference remains for current scope. P2-D-03's complete design default is specified in addendum §3; this remains an engineering default, not owner/accountant approval.



**Status** **accepted** *(engineering scope; `OD-3-A` acknowledgement still requested)* · **Owner approval: the gapless *policy* remains OD-3** ·
**Related** [OD-3](OWNER-DECISIONS.md#od-3), ADR-009, FR-SALE-004, NFR-DATA-005, AC-1, audit **CF-1**

### Problem
[OD-3](OWNER-DECISIONS.md#od-3) records `BLOCKS-IMPLEMENTATION` for the **whole numbering module**
because the gapless question is undecided. Meanwhile FR-SALE-004, NFR-DATA-005, AC-1, ADR-009,
`DATA-MODEL.md §4`, `TESTING-STRATEGY.md §5` and `TRACEABILITY.md` all already specify **gapless**
numbering as a settled requirement, invariant and test. Both positions cannot be right.

### Context
OD-3 justifies the blockage with: *"the allocation code and its concurrency tests differ materially
between gapless and gap-tolerant designs, so building one and switching later wastes the work."*

That reasoning is **directionally asymmetric**, which OD-3 does not account for:

| Built | Satisfies a **gapless** requirement? | Satisfies a **gap-tolerant** requirement? |
|---|---|---|
| In-transaction locked counter row (ADR-009 option a) | ✅ | ✅ |
| `SEQUENCE` (option b) | ❌ | ✅ |
| Per-counter series (option c) | ❌ | ✅ |

Gaplessness is a **strictly stronger** property. A gapless implementation therefore satisfies either
answer; a gap-tolerant one satisfies only one.

### Alternatives
1. **Build the gapless design now. Re-scope OD-3 to gate the *policy statement* (NFR-DATA-005, AC-1)
   and its acceptance test, not the module.**
2. Keep the module fully blocked until OD-3 is answered.
3. Build a `SEQUENCE` now, convert later if gapless is required.
4. Build both behind a strategy flag.

### Decision
Option 1 — **subject to owner acknowledgement `OD-3-A`**, because it changes a recorded blocking
level and that is the register's to change, not engineering's.

### Rationale
Option 2 blocks the module on the **critical path** (M9 → M10 → M11 → M13) for a decision that
cannot change the recommended implementation. Option 3 is the one genuinely wasteful path: converting
a `SEQUENCE` to gapless numbering after invoices exist means a hot-row design *plus* living with
historical gaps that can never be closed — renumbering issued documents is falsifying records and is
rejected outright (ADR-009 option 3). Option 4 doubles the concurrency test surface for a flag that
will be set once and never changed.

What OD-3 **genuinely** blocks is narrower and still real: whether *"invoice numbers are gapless"* may
be stated as a **data invariant with a go-live acceptance criterion**. If the accountant says gaps
are acceptable, the invariant becomes an implementation property rather than a requirement — the code
is unchanged, but NFR-DATA-005 and AC-1 must be worded differently, and the gap-detection test
changes from *required* to *informational*.

### Consequences
- **M9 is unblocked**; the critical path is not held by an accountant's reply.
- `PRD.md` FR-SALE-004 / NFR-DATA-005 / AC-1 must be reworded so the **mechanism** is the requirement
  and **gaplessness is its consequence under the current default** (task **T-03**).
- [OD-3](OWNER-DECISIONS.md#od-3)'s blocking level is re-scoped from "the numbering module" to "the
  numbering policy statement and its acceptance test" — **only after `OD-3-A`**.
- OD-22's "Already confirmed" paragraph, which asserts gaplessness as fact, must be corrected.
- **The per-counter-series contention remedy must not be applied before OD-3 is answered** — it
  guarantees permanent gaps.
- The OD-3 ↔ OD-8 interaction is unchanged and must stay visible: a gapless answer makes offline
  finalisation **permanently** impossible, not merely deferred.

### Evidence
Audit CF-1 lists the eleven locations asserting gaplessness. ADR-009's own options table establishes
the asymmetry.

### If rejected
M9 stays blocked, and the critical path waits on OD-3. Nothing else changes; the recommended
implementation would be identical when it does start.

---

<a id="p2-d-03"></a>
## P2-D-03 — Money policy object and v1 whole-INR policy default

> **Current disposition:** The 2026-10-30 Design Completion Addendum supersedes this section's earlier open-scope wording. P2-D-02 no longer asserts a gapless customer invoice series; only the internal reference remains for current scope. P2-D-03's complete design default is specified in addendum §3; this remains an engineering default, not owner/accountant approval.



**Status** **accepted** *(mechanism only)* · **Owner approval: the policy *values* remain [OD-7](OWNER-DECISIONS.md#od-7) — still unapproved** ·
**Related** OD-7, ADR-013, ADR-032, FR-SALE-003, NFR-DATA-002

### Problem
[OD-7](OWNER-DECISIONS.md#od-7) records `BLOCKS-IMPLEMENTATION` for the **money module in both
repositories** because precision, rounding method, rounding stage and whole-rupee handling are
undecided. The money engine is a prerequisite of sale finalisation, so this blocks the critical path.

### Context
Three things are already true and they point to the answer:

1. **`GET /sync/bootstrap` already publishes the rounding policy to the client**
   (`API-SPECIFICATION.md §3.12`).
2. **`bmfrontend/CLAUDE.md §5` rule 15 forbids the client from hardcoding any business rule that
   bootstrap provides** — naming rounding explicitly.
3. `DATA-MODEL.md §3.1` already defines `sales.rounding_mode` and
   `sales.round_grand_total_to_rupee` as **settings keys**, not constants.

So the policy is *already specified as configuration*. What OD-7 is actually blocking is the
selection of its **values** and the freezing of the **expected-value assertions** in two test suites.

### Alternatives
1. **Make the policy an explicit `MoneyPolicy` object resolved from `settings`, threaded through
   every money function on both sides, published by bootstrap. Build and property-test the algorithm
   now across all policy combinations; select values when OD-7 answers.**
2. Keep the module fully blocked until OD-7 is answered.
3. Hardcode the documented default (`line_half_up`, 2 dp, whole-rupee off) and change it later.
4. Support only one policy, chosen at build time by a constant.

### Decision
Option 1. **The policy values remain OD-7's to decide and this record does not pre-empt them.**

### Rationale
Option 3 is the trap: the documented default would quietly become the de facto decision, which is
precisely the promotion-of-a-default failure that OD-7's status vocabulary exists to prevent — and
`ARCHITECTURE.md §6.5` already risks it by stating `ROUND_HALF_UP` at line level as though settled.
Option 2 blocks the critical path on an accountant's reply for a parameter the architecture already
treats as configuration. Option 4 is Option 3 with extra steps.

Option 1 also **reduces** the project's largest duplication risk. ADR-032 accepts that the rounding
rule is duplicated between `domain/shared/money.py` and `lib/money.ts`. Today those would be two
hardcoded constants that can silently diverge; under Option 1 they are **two consumers of one
published policy**, so divergence requires ignoring the bootstrap payload — which a structural test
already forbids.

### Consequences
- **M4 is unblocked.** The algorithm, its property tests and `lib/money.ts` can all be built now,
  parameterised by policy.
- What OD-7 blocks shrinks to **(i)** selecting production values and **(ii)** freezing the
  expected-value assertions in the two money acceptance suites.
- Property tests must pass **for every policy combination**, not only the proposed default — a
  materially stronger test suite than one tied to a single rule.
- A later policy change affects **newly computed** values only: issued invoices keep their snapshots
  and are never recomputed (FR-SALE-005, ADR-015), so **no migration on live financial data**.
- `ARCHITECTURE.md §6.5` must be reworded to present `ROUND_HALF_UP`/line-then-sum as the
  **documented default value of a configurable policy**, not as the rule.
- `invoices.rounding_adjustment` already exists, so enabling whole-rupee rounding later needs no
  schema change.

### Recommended default values (TECHNICAL RECOMMENDATION — **not** approved)
Amounts 2 dp · unit prices 4 dp · quantities 3 dp · `ROUND_HALF_UP` · **line-then-sum** · whole-rupee
rounding **off** · no domain exceptions. Worked examples showing where policies diverge are in
[`PHASE-2-INVESTIGATION-REPORT.md §5.2`](PHASE-2-INVESTIGATION-REPORT.md). The decisive argument for
line-then-sum: it is the only option where **the printed bill visibly adds up**, which is the
property a customer at the counter will actually check.

### If rejected
M4 and therefore M10 stay blocked until OD-7 answers. The implementation would be identical when it
starts, minus the multi-policy test coverage.

---

<a id="p2-d-04"></a>
## P2-D-04 — Explicit source-of-truth hierarchy

**Status** **accepted** · **Owner approval** no · **Related** ADR-021, audit §1

### Problem
Several documents declare local precedence (`API-SPECIFICATION.md` wins over `API-INTEGRATION.md`;
`OWNER-DECISIONS.md` governs `PRD.md §5`; `bm/README.md` is non-authoritative), but **no document
states the full ordering**. Resolving a three-way disagreement required reconstructing the hierarchy
during this audit.

### Alternatives
1. **State the hierarchy once, in the Phase 2 blueprint, and link it from both `CLAUDE.md` files.**
2. Leave it implicit.
3. Put it in `bm/README.md`.

### Decision
Option 1. Recorded in
[`PHASE-2-IMPLEMENTATION-BLUEPRINT.md §1`](PHASE-2-IMPLEMENTATION-BLUEPRINT.md).

### Rationale
Option 3 violates ADR-021 — the root README must contain links, not facts, and a precedence rule is a
fact. Option 2 leaves every future conflict to be re-derived. The hierarchy is **descriptive, not
new**: it codifies the precedence the documents already assert individually, which is why it is a
clarification rather than a change.

### Consequences
Nine tiers, with `CLAUDE.md` deliberately **below** the specifications (it is rules, not spec) and
`TRACEABILITY.md` below its sources (it is derived). The overriding rule stays unchanged: **when
documentation and code disagree, the code is the fact** — which becomes active for the first time in
Phase 3.

---

<a id="p2-d-05"></a>
## P2-D-05 — The import-boundary linter is a merge blocker from M1

**Status** accepted · **Owner approval** no · **Related** ADR-001, `ARCHITECTURE.md §3`

### Problem
`ARCHITECTURE.md §3` says the layering rules are "checked by an import-linter configuration in CI",
but does not say when that configuration must exist or whether it blocks a merge.

### Decision
The configuration ships in **M1**, before any domain code, and **fails the build** on violation.

### Rationale
ADR-001's own rationale says the import linter "is what distinguishes [a modular monolith] from [an
unstructured monolith]" — it is the mechanism, not a nicety. Boundary violations are cheap to prevent
and expensive to unwind: once `infra` imports `domain`, or one module reaches into another's
repository, untangling it means touching working financial code. Adding the linter after the fact
means starting with a backlog of violations and a temptation to allow-list them.

### Consequences
A deliberate reverse import must **fail CI in M1**, verified as an M1 acceptance test. The five rules
enforced are `ARCHITECTURE.md §3`'s, including **exclusive table ownership** — no module writes
another module's tables.

---

<a id="p2-d-06"></a>
## P2-D-06 — Claim the idempotency key before acquiring inventory locks

**Status** accepted · **Owner approval** no · **Related** `ARCHITECTURE.md §5.2/§5.3`, FR-SALE-002

### Problem
The finalisation sequence claims the idempotency key (step 2) then locks inventory (step 5). Nothing
states that this ordering is **required**, so a future optimisation could reorder it.

### Decision
The ordering is a **correctness constraint**: claim the key first, take inventory locks second. To be
stated explicitly in `ARCHITECTURE.md §5.3`.

### Rationale
If locks were taken first, a duplicate submit (double-click, or a client retry arriving while the
first is in flight) would **block a genuine sale of the same product** for up to `lock_timeout`
(3 s) before discovering it is a replay. Claiming the key first makes the duplicate fail in
microseconds with `409 IDEMPOTENT_REQUEST_IN_PROGRESS` while holding **no** business locks. At a busy
counter with two tills selling the same fast-moving part, this is the difference between a duplicate
click being invisible and it stalling the other counter.

A second reason: the unique constraint `uq_idempotency__scope` is the **serialisation point** for
concurrent same-key requests. Making it the first write keeps that role unambiguous.

### Consequences
- The documented step order becomes normative, not illustrative.
- Test: a duplicate submit returns `IDEMPOTENT_REQUEST_IN_PROGRESS` **without** holding inventory
  locks — verified by showing a concurrent sale of the same product is not delayed.
- Lock order (`inventory_balances` → `invoice_series` → `customers` → `invoices`) is unchanged;
  idempotency-key insertion is not a lock in that ordering and precedes all of it.

---

<a id="p2-d-07"></a>
## P2-D-07 — Normative idempotency-fingerprint canonicalisation

**Status** **accepted** · **Owner approval** no · **Design** [Blueprint §5.2](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) · **Related** `ARCHITECTURE.md §5.3`, FR-SALE-002, E-2

### Problem
`ARCHITECTURE.md §5.3` stores a `request_fingerprint` described as "SHA-256 of the canonicalised
body" — but **canonicalisation is never defined**. Two serialisations of the same intent would
produce different fingerprints and turn a legitimate retry into `409 IDEMPOTENCY_KEY_REUSE`.

### Context
This is not hypothetical. The contract sends money as **strings** (ADR-013), and a client that
reserialises `"2500.00"` as `"2500.0"`, reorders JSON keys, or emits an explicit `null` where it
previously omitted the field would break **its own retry** — in the one code path where a retry is the
designated safety mechanism for a lost response (E-2). The user-visible result is a sale failing at
the counter with "Please reload this bill", which the client treats as a client bug.

### Alternatives
1. **A normative canonicalisation rule plus a shared cross-repository fixture list.**
2. Fingerprint the raw bytes as received.
3. Fingerprint a server-side subset of semantically significant fields.
4. Drop the fingerprint and key on the `Idempotency-Key` alone.

### Decision
Option 1.

```text
canonical(body) =
  1. Drop every key whose value is JSON null.
  2. Sort object keys lexicographically, recursively.
  3. Normalise each money/quantity/percent string to its Decimal value, then re-render
     at the field's declared scale   ("2500.0" and "2500.00" → "2500.00").
  4. Preserve array order exactly (line order is semantically significant).
  5. Exclude Idempotency-Key, X-Request-Id and all headers.
  6. Serialise with no insignificant whitespace, UTF-8, then SHA-256.
```

### Rationale
Option 2 is the most literal and the most fragile — it makes the safety mechanism depend on byte-level
serialiser stability across client versions. Option 3 is tempting but silently weakens the guarantee:
a field outside the subset could change between attempts and the server would replay a stored
response for a *different* request, which is worse than a false conflict. Option 4 abandons the
protection against genuine key reuse (a real client bug the registry explicitly refuses to accept
silently).

Rule 3 is load-bearing. Rule 4 matters because reordering lines changes the bill's `line_no`
assignment and is therefore a different request.

### Consequences
- One canonicalisation implementation server-side; the client must produce a byte-identical
  canonical form for the fingerprint check to behave.
- **A shared fixture list of equivalent / non-equivalent payload pairs lives in both test suites** —
  the same pattern already used for part-number normalisation, which is the project's established way
  of holding an unavoidable duplication.
- Must be documented in `ARCHITECTURE.md §5.3`.

---

<a id="p2-d-08"></a>
## P2-D-08 — Block draft edits while a finalisation is unconfirmed

**Status** **accepted** · **Owner approval** no ·
**Design** [Blueprint §5.4](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) — 12 cases, state machine,
forbidden transitions, AC-REC-1…11 ·
**Related** `OFFLINE-SYNCHRONIZATION.md §3.1` invariant 4, `API-INTEGRATION.md §5`, FR-SALE-027

### Problem
Two authoritative documents describe compatible intentions but **incompatible mechanics**:

| Source | Says |
|---|---|
| `OFFLINE-SYNCHRONIZATION.md §3.1` invariant 4 | *"If the user has edited the bill, the old attempt must first be resolved — a new key for an edited bill is a different sale, and **the UI must not allow that while one is unconfirmed**."* |
| `API-INTEGRATION.md §5` | `onDraftEdited()` → `db.pending_finalizations.delete(draft.id)` |

If a draft is edited while an attempt is unresolved, **deleting the row discards the only record of a
key that may already have created an invoice.** The next submit mints a new key and can produce a
**second invoice for the same sale** — exactly the failure the two-queue design exists to prevent.

### Alternatives
1. **Block editing while an unresolved `pending_finalizations` row exists; clear the row only on
   resolution.**
2. Allow editing but keep the old row for reconciliation.
3. Keep the delete-on-edit behaviour.

### Decision
Option 1, which is what invariant 4 already requires.

### Rationale
Option 3 is the current contradiction and is unsafe. Option 2 sounds safer but produces a state the
UI has no vocabulary for — a bill whose contents no longer match an in-flight attempt, with a human
expected to reconcile two different bills. Option 1 matches both the invariant and the existing UI
design: `UI-UX-SPECIFICATION.md §4.9` already specifies that the unconfirmed state is *loud*,
survives reload, and offers `Retry now` / `Print counter slip`, and that the tab cannot be closed
without an explicit warning. Extending "cannot be closed" to "cannot be edited" is consistent, not
new.

The deeper principle: **an unconfirmed outcome is never permission to create another sale**
(invariant 4). Editing is how a user *would* create another sale, so editing must be where the block
sits.

### Consequences
- The billing screen gains a read-only state for unconfirmed bills, with the three documented actions
  plus `Check Daily Bills`.
- `onDraftEdited` clears the pending row **only** after resolution — a 201, a business 409, or an
  explicit human decision. Never as a side effect of a keystroke.
- `API-INTEGRATION.md §5`'s illustrative snippet must be corrected.
- Test: *edit attempt while unconfirmed → blocked, row intact, key unchanged.*
- Minor usability cost: a user who wants to change a bill stuck in `unconfirmed` must first resolve
  it. That is the correct trade — the alternative risks a duplicate invoice.

---

<a id="p2-d-09"></a>
## P2-D-09 — Lazy financial-year `invoice_series` creation

**Status** **accepted** · **Owner approval** no ·
**Design** [Blueprint §8.2](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) — options, race analysis, 8 tests ·
**Related** FR-SALE-004, `DATA-MODEL.md §3.8`, OD-3

### Problem
`invoice_series` is scoped by `(business_id, branch_id, series_code, financial_year)` and the
financial year rolls over per `businesses.financial_year_start_month` (default April). **No document
says who creates the row for a new financial year.** As specified, the first sale after midnight on
1 April would find no series row.

### Alternatives
1. **Lazy creation inside the finalisation transaction:** `INSERT … ON CONFLICT DO NOTHING` on
   `uq_invoice_series__scope`, then `SELECT … FOR UPDATE`.
2. A scheduled job that pre-creates next year's rows.
3. Manual creation by the owner via `PUT /admin/invoice-series/{id}`.
4. Eager creation at business setup for N years ahead.

### Decision
Option 1.

### Rationale
Option 3 is a counter outage waiting to happen: the shop's first sale of the new financial year fails
and someone must reach an admin screen before billing can continue. Option 2 adds a scheduled job
whose failure mode is the same outage, discovered at the worst possible moment, and whose correctness
depends on a timer rather than on the request that needs it. Option 4 creates rows that may never be
used and still fails if the business runs longer than N years.

Option 1 keeps the property that matters: **a series row exists exactly when a sale needs one**, and
it is created in the same transaction that allocates from it — so a rolled-back first sale of the year
leaves a series row at `next_number = 1`, which is harmless and correct. `ON CONFLICT DO NOTHING`
makes it safe under two concurrent first-sales-of-the-year.

### Consequences
- Numbering must resolve the financial year from `financial_year_start_month` and the **server** clock
  (never a client timestamp).
- `prefix_template` and `document_kind` for a newly created row are inherited from the branch's
  current active series configuration — **this must be specified explicitly**, since a series
  "never changes kind mid-year" (`DATA-MODEL.md §3.8`) and a new year is the only moment it may
  legitimately change.
- **Test: a frozen clock crossing 1 April creates a new series row lazily and allocates `00001`.**
- Document in `DATA-MODEL.md §3.8` and `ARCHITECTURE.md §5.4`.

---

<a id="p2-d-10"></a>
## P2-D-10 — Settings resolution: defaults in code, overridable by rows, published to the client

**Status** accepted · **Owner approval** no · **Related** FR-ADMIN-004, `DATA-MODEL.md §3.1`

### Problem
`DATA-MODEL.md §3.1` lists documented defaults and notes they are "applied in code, not rows, so an
unset key is never ambiguous". The resolution order and the client's view of it are not stated in one
place.

### Decision
A three-layer resolution, stated normatively:

```
1. Documented default in code        — authoritative when no row exists; never ambiguous
2. Business-level settings row       — overrides the default
3. Branch-level settings row         — overrides the business level
→ The client-relevant subset is published by GET /sync/bootstrap and NEVER hardcoded client-side
```

### Rationale
Keeping defaults in code means an unset key cannot be "missing" — a frequent source of production
surprises when a seeded row is deleted. Rows for overrides mean the owner changes tax mode, draft
limits, credit terms, cancellation window, discount caps and invoice prefixes **without a deploy**
(FR-ADMIN-004). Publishing the client-relevant subset via bootstrap means the client never holds a
second copy of a business rule — already a hard rule (`bmfrontend/CLAUDE.md §5` rule 15) and already
enforced by a structural test.

### Consequences
- Every settings read goes through one typed resolver; no service reads a raw row.
- Every change is audited with before/after (FR-ADMIN-004).
- **The env/settings split is a boundary, not a preference:** infrastructure in environment variables
  (fail fast if missing), business rules in `settings`. `DEPLOYMENT.md §3` lists what may **never** be
  an environment variable — tax rates, business identity, invoice prefixes, draft limits, credit
  terms.
- This is the mechanism `P2-D-03` depends on for the money policy.

---

## Decisions explicitly **not** made in Phase 2

| Topic | Why not |
|---|---|
| Any business rule, rate, identity, paper size, terminology or workflow | Owner's, by definition. See [`PHASE-2-OWNER-DECISIONS-REQUIRED.md`](PHASE-2-OWNER-DECISIONS-REQUIRED.md) |
| The money policy **values** | [OD-7](OWNER-DECISIONS.md#od-7) — `P2-D-03` makes the mechanism configurable; it does not choose the values |
| Whether numbering must be gapless | [OD-3](OWNER-DECISIONS.md#od-3) — `P2-D-02` re-scopes the blockage; it does not answer the question |
| The bill's lower layout | [OD-5](OWNER-DECISIONS.md#od-5) — evidence does not exist; **must not be invented** |
| Invoice-number / payment-block placement | [OD-22](OWNER-DECISIONS.md#od-22) — depends on OD-5 |
| Customer merge policy | FR-CUST-005 is P1 and explicit/permissioned/audited. **No automatic merge policy is proposed** — duplicates are surfaced for the user to choose (FR-CUST-004) |
| Reopening any Phase 1 ADR | None met the four revisit grounds. All `proposed` ADRs are endorsed unchanged |
| Changing the agreed scope | No feature was added or removed |

---

## Related documents

[`PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md`](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md) ·
[`PHASE-2-INVESTIGATION-REPORT.md`](PHASE-2-INVESTIGATION-REPORT.md) ·
[`PHASE-2-IMPLEMENTATION-BLUEPRINT.md`](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) ·
[`PHASE-2-IMPLEMENTATION-PLAN.md`](PHASE-2-IMPLEMENTATION-PLAN.md) ·
[`PHASE-2-OWNER-DECISIONS-REQUIRED.md`](PHASE-2-OWNER-DECISIONS-REQUIRED.md) ·
[`DECISIONS.md`](DECISIONS.md) · [`OWNER-DECISIONS.md`](OWNER-DECISIONS.md)
