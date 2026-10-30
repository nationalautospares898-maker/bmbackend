# Phase 1 Exit Criteria — and their evaluation

| Field | Value |
|---|---|
| Status | **Durable project record.** Not a snapshot — maintained in the repository |
| Created | 2026-10-29, during final Phase 1 completion |
| Why it exists | **The Phase 1 exit criteria had never been recorded in either repository.** They lived only in the task prompts, so "has Phase 1 met its exit criteria?" could not be answered from the documentation. That was itself a documentation gap; this file closes it. |
| Scope | Phase 1 is a **documentation and analysis phase**. Its criteria are documentation criteria. |

---

## 1. What Phase 1 is, and what it is not

Phase 1 was commissioned as **discovery and documentation only** — explicitly *"DO NOT IMPLEMENT THE
APPLICATION"*. Its deliverable is a documentation foundation that Phase 2 can plan against.

This matters for closure, because it determines which outstanding items are **Phase 1 deficiencies**
and which are **inputs that Phase 2 or go-live will need**:

| An item is a **Phase 1** blocker if… | An item is **not** a Phase 1 blocker if… |
|---|---|
| A required document is missing | It is an owner/accountant decision, correctly recorded as open with options, consequences and a recommended default |
| Documents contradict each other | It is external evidence that is unavailable, where the **absence is explicitly recorded** |
| A requirement has no traceable verification | It is code, tests or infrastructure — Phase 2/3 work by definition |
| An approval or verification is **falsely claimed** | It is a genuine business risk, correctly surfaced rather than hidden |

**A correctly recorded open decision is a Phase 1 success, not a Phase 1 failure.** Phase 1's job was
to *find and document* these, not to answer them — answering them needs an owner, an accountant, or
a document the owner does not currently have.

---

## 2. The criteria, and whether each is met

Derived from the Phase 1 commissioning brief and the closure-audit definition of done.

| # | Exit criterion | Status | Evidence |
|---|---|---|---|
| **1** | Both repositories inspected; actual state recorded, not assumed | ✅ **MET** | Both were verified **empty, non-Git**; every document states this. No existing stack was fabricated |
| **2** | Required documentation set exists in the correct repository | ✅ **MET** | 11 backend docs + README/CLAUDE.md; 6 frontend docs + README/CLAUDE.md; ownership per ADR-021 |
| **3** | Both PRDs free of contradictions | ✅ **MET** | Audited repeatedly; all contradictions found were corrected (Addenda 1–3) |
| **4** | Every major feature has a defined workflow with a clear data owner | ✅ **MET** | Module table in `ARCHITECTURE.md §4`; exclusive table ownership |
| **5** | Financial and inventory invariants explicit | ✅ **MET** | `NFR-DATA-001…010`; DB `CHECK`s and privileges where possible |
| **6** | Offline behaviour does not contradict stock consistency | ✅ **MET** | `OFFLINE-SYNCHRONIZATION.md §1, §3.1`; both sale actions refused offline |
| **7** | Frontend and backend API contracts agree | ✅ **MET** | 0 frontend-only endpoints; 0 invented error codes; 49/49 error codes handled |
| **8** | Role permissions consistent | ✅ **MET** | All cited permissions defined in the role matrix; canonical identifiers in `DATA-MODEL.md §3.14` |
| **9** | Error handling and recovery documented | ✅ **MET** | `PRD.md §9` (E-1…E-18); client mapping in `../../bmfrontend/docs/PRD.md §6.1` |
| **10** | Every critical requirement has measurable acceptance criteria | ✅ **MET** | `PRD.md §10` AC-1…AC-12; `../../bmfrontend/docs/PRD.md §9` AC-FE-1…12 |
| **11** | Architecture scalable without unnecessary infrastructure | ✅ **MET** | Modular monolith; no Redis/K8s/broker; escalation triggers documented |
| **12** | Every **P0** requirement has a named planned test and verification method | ✅ **MET** | **194 P0 (101 backend + 93 frontend), 0 untraced** (`TRACEABILITY.md §13A`) |
| **13** | Unresolved decisions recorded with options, consequences, default, blocking level, approver | ✅ **MET** | `OWNER-DECISIONS.md` OD-1…OD-24, seven fields each |
| **14** | **No fabricated approval, date, approver or sign-off** | ✅ **MET** | Audited: zero found. Four-level status vocabulary separates preference from approval |
| **15** | **The print reference is verified *or its absence explicitly recorded*** | ✅ **MET** *(by the second branch)* | Top section transcribed from the inspected screenshot; lower section recorded `[UNSEEN]` with a 7-row unknowns table (`UI-UX-SPECIFICATION.md §12.1b`). **Never claimed verified** |
| **16** | Both `CLAUDE.md` files agree with the specifications | ✅ **MET** | Audited each round; both describe repos as empty and the stack as proposed |
| **17** | All checkable links, IDs and cross-references validated | ✅ **MET** | 86 links, 285 IDs, 24 OD anchors, 33 ADRs — **0 faults** |
| **18** | No implementation introduced | ✅ **MET** | 0 non-Markdown files; 0 `.git` directories; nothing run, committed or deployed |
| **19** | A closure report exists and reports verification honestly | ✅ **MET** | Closure report + 4 addenda, each a dated immutable snapshot |

**19 of 19 Phase 1 exit criteria are met.**

### 2.1 Criterion 15 — the one that needs explaining

This is the criterion that kept Phase 1 open through three addenda, so its resolution should be
explicit.

The criterion is **disjunctive**: *verified **or** its absence explicitly recorded*. It was written
that way deliberately, because a documentation phase cannot manufacture evidence the business does
not have.

- The **verified** branch is **not** satisfied, and we do not claim it. Only a screenshot of the
  upper portion was inspected; the workbook was never available; the owner has confirmed they cannot
  currently supply the workbook, a bottom-scrolled screenshot, or a photo of a printed bill.
- The **absence-recorded** branch **is** satisfied, thoroughly: every element is classified
  `[OBSERVED]` / `[UNSEEN]` / `[PROPOSED]`; the unknowns are enumerated; the limitation is repeated
  in both PRDs, both READMEs, `bmfrontend/CLAUDE.md`, the traceability gap table and OD-5; and
  `[PROPOSED]` elements must be switchable off without breaking the layout.

So the criterion is met. **What remains is not a Phase 1 documentation gap — it is a Phase 2
implementation gate**, recorded as such below.

---

## 3. What Phase 1 closure does **not** mean

Closing Phase 1 means the documentation foundation is complete, consistent and honest.
**It does not mean:**

- ❌ The application exists, works, or has been tested. **No code or test exists.**
- ❌ The bill layout is approved or fully verified.
- ❌ Any tax, accounting or legal question is settled.
- ❌ The system may be used in the shop.
- ❌ Any owner decision marked open has been answered.

---

## 4. Gates carried forward

These are **not** unmet Phase 1 criteria. They are inputs that Phase 2 and go-live require, recorded
here so closure cannot be mistaken for permission to proceed past them.

### 4.1 Phase 2 implementation gates — a specific module cannot start

| Gate | Blocks | Smallest input that clears it | From |
|---|---|---|---|
| **[OD-5](OWNER-DECISIONS.md#od-5)** — bill's lower section unseen | Print template (**bottom only**; header, field block and item table are buildable now) | The `ANA….xlsx` workbook, **or** a screenshot scrolled to the bottom, **or** a photo of a printed bill | Owner |
| **[OD-22](OWNER-DECISIONS.md#od-22)** — invoice-number and payment-block placement | Print template | Depends on OD-5, then a reviewed sample | Owner |
| **[OD-3](OWNER-DECISIONS.md#od-3)** — is numbering required to be **gapless**? | Invoice-numbering module | A yes/no on gapless *(sequential is already selected)* | Accountant |
| **[OD-7](OWNER-DECISIONS.md#od-7)** — decimal precision and rounding method | Money module (**both repos**) | Precision, rounding method, and any exceptions *(principles already selected)* | Accountant |

**Everything else may be implemented now** — auth, tenancy, catalog, search, inventory ledger,
drafts, sale finalisation, customers, credit, payments, purchases, sync, reports, audit, imports,
returns, and the whole frontend shell and billing counter.

### 4.2 Go-live gates — build and test freely; do not run the shop

[OD-1](OWNER-DECISIONS.md#od-1) GST status · [OD-2](OWNER-DECISIONS.md#od-2) rates and HSN ·
[OD-4](OWNER-DECISIONS.md#od-4) legal shop identity · [OD-6](OWNER-DECISIONS.md#od-6) paper and
printer · [OD-8](OWNER-DECISIONS.md#od-8) acceptance that offline means draft capture, not invoice
issuing · [OD-19](OWNER-DECISIONS.md#od-19) hosting and a **named** alert recipient ·
[OD-24](OWNER-DECISIONS.md#od-24) final printed-document terminology.

Plus the operational gates already in `PRD.md §14` and `DEPLOYMENT.md §11.2` — tested restore,
monitoring with a real recipient, printed-sample sign-off, staff training, and the invariant,
concurrency and isolation suites passing.

### 4.3 Settled — no further input needed

[OD-21](OWNER-DECISIONS.md#od-21) credit workflow (ADR-033) ·
[OD-14](OWNER-DECISIONS.md#od-14) action labels ·
[OD-23](OWNER-DECISIONS.md#od-23) amount display and blank empty rows ·
[OD-9…OD-13](OWNER-DECISIONS.md), [OD-15…OD-18](OWNER-DECISIONS.md),
[OD-20](OWNER-DECISIONS.md#od-20) — safe on their documented defaults.

---

## 5. Maintaining this file

If a Phase 1 criterion is ever found to be unmet — a contradiction surfaces, a fabricated approval is
discovered, or a required document is lost — **record it here and reopen Phase 1 for that criterion**
rather than quietly amending history. The closure reports and addenda are immutable snapshots; this
file is the living evaluation.

**Related:** `OWNER-DECISIONS.md` · `PRD.md §14` (release gates) · `DEPLOYMENT.md §11.2` (go-live
checklist) · `TRACEABILITY.md` · `../../PHASE-1-CLOSURE-REPORT.md` and Addenda 1–4.
