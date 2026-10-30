# Phase 2 — Closure Report

| Field | Value |
|---|---|
| Status | **Durable project record.** Maintained here, like [`PHASE-1-EXIT-CRITERIA.md`](PHASE-1-EXIT-CRITERIA.md) — **not** a point-in-time snapshot |
| Purpose | Record what Phase 2 corrected, what it finalised, what remains, and why Phase 2 is closed |
| Verdict | # `PHASE 2 CLOSED` |
| Supersedes | Nothing. The Phase 1 closure reports and all four addenda remain **immutable**, and the Phase 2 investigation findings remain readable in their original form |
| Implementation performed | **None.** No application code, migration, dependency, executable test, Git operation or deployment |

> **What closure means.** The documentation foundation (Phase 1) and the implementation plan
> (Phase 2) are complete, internally consistent, and honest about what is still unknown.
> **It does not mean the application exists, works, or has been tested — no code or test exists.**
> It does not mean every business decision is approved, and it does not mean Phase 3 is wholly
> unblocked: 26 of 28 milestones can start, two cannot.

---

## 1. Closure criteria — evaluated

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Fidelity findings corrected or explicitly dispositioned | ✅ **MET** | All **11** resolved. [`PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md §6.1`](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md) gives per-finding files, validation method and status |
| 2 | Gapless misclassification removed from **every** affected authoritative document | ✅ **MET** | 11 files corrected (§3.1). Corpus re-scanned: every surviving `gapless` mention is OD-3's own question/options, a labelled cross-reference, or a correction note |
| 3 | Critical sale-finalisation recovery behaviour fully designed | ✅ **MET** | [Blueprint §5.4](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) — 12 cases, state machine, forbidden transitions, AC-REC-1…11 |
| 4 | Financial-year series initialisation fully designed | ✅ **MET** | [Blueprint §8.2](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) — 4 options, race analysis, inheritance rules, 8 named tests |
| 5 | Idempotency + canonical fingerprinting implementable | ✅ **MET** | [Blueprint §5.2](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) — 9 sub-sections, worked `"2500.0"`/`"2500.00"` table, include/exclude list, AC-IDEM-1…9 |
| 6 | Money-policy architecture coherent while OD-7 unapproved | ✅ **MET** | [Blueprint §7.1–7.5](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) — premise verified against the documents, schema, validation, 4 anti-divergence controls, historical-invoice protection |
| 7 | Critical/high risks have mitigations, tasks, or explicit blocking/accepted status | ✅ **MET** | [`PHASE-2-RISK-REGISTER.md §7`](PHASE-2-RISK-REGISTER.md) reviews all 22 CRITICAL/HIGH risks against four questions each. **No CRITICAL risk unmitigated** |
| 8 | Blueprint and milestones reflect all corrections | ✅ **MET** | [`PHASE-2-IMPLEMENTATION-PLAN.md §4`](PHASE-2-IMPLEMENTATION-PLAN.md): 24 ready, 2 conditional, 2 blocked; prerequisites updated; 4 Phase 3 tasks added (§1.1) |
| 9 | Owner-decision register accurate, no fabricated approvals | ✅ **MET** | §6 below. Criterion-14 re-test: **zero** approval dates, approver names, sign-offs or legal reviews anywhere |
| 10 | Traceability and cross-document consistency validated | ✅ **MET** | §8 below — 521 links, all anchors, 194 P0 IDs, 53 error codes, all ID families |
| 11 | Required documentation complete and linked from both repositories' AI context | ✅ **MET** | 9 Phase 2 artifacts + frontend index; both `CLAUDE.md`, both READMEs, workspace README updated |
| 12 | Remaining unknowns bounded, assigned, and cannot silently cause Phase 3 to implement an unapproved policy | ✅ **MET** | §6 and §7. Each open decision has an explicit gate; the two gated engines carry "must not build yet" rows naming exactly what is forbidden |

**12 of 12 closure criteria met.**

---

## 2. What was actually unresolved at the start of this pass

The investigation pass *recorded* corrections as tasks but **applied only two of eleven**. Verified
by inspection, not assumed:

| Finding | State found | Verification |
|---|---|---|
| CF-1 | **Unresolved** — gapless asserted in `PRD.md`, `DATA-MODEL.md`, `DECISIONS.md`, `ARCHITECTURE.md`, `TESTING-STRATEGY.md`, `TRACEABILITY.md`, `OWNER-DECISIONS.md` (OD-22), `README.md` | `grep -i gapless` over all files |
| CF-2 | **Unresolved** — matrix still read 88 / 189 | Direct read of `§13A.3` |
| CF-3 | **Unresolved** — 0 of 4 codes in the registry | `sed` over `§4.2` + grep per code |
| CF-4 | **Unresolved** — `CreditDetailsForm` still in `TRACEABILITY.md §5` | grep |
| CF-5 | **Unresolved** — 4 `Complete & Print` occurrences incl. a mis-stated ADR-027 stub | grep |
| CF-6 | **Unresolved** — stale "reconstructed from the owner's written description" | grep |
| CF-7 | ✅ Applied in the investigation pass | read |
| CF-8 | **Unresolved** — "Two items block implementation (OD-3, OD-5, OD-7) plus OD-14" | read |
| CF-9 | **Unresolved** — `NFR-DATA-002b` in `DATA-MODEL.md` only | grep count per file |
| CF-10 | **Unresolved** — `§3.14` before `§3.13`; 3 tables misplaced | section-header listing |
| CF-11 | ✅ Applied in the investigation pass | anchor validator |

Also verified: `P2-D-01…10` all present (6 `proposed`, 4 `accepted`); `R-01…R-35` all present;
`M1…M28` all present; `T-01…T-05` all open except the two applied README fixes.

---

## 3. Correction log

### 3.1 CF-1 — gapless numbering (CRITICAL)

| File | Change |
|---|---|
| `PRD.md` | **FR-SALE-004** rewritten: the mechanism and *unique + sequential* are the requirement; gaplessness is labelled a consequence under the current default, with OD-3 cited. **NFR-DATA-005** rewritten: gaplessness explicitly **not** asserted; gap-detection test marked informational. **AC-1** no longer asserts gaplessness; **new AC-1a** carries it as a conditional criterion blocked on OD-3 |
| `DATA-MODEL.md §4` | NFR-DATA-005 row restated as unique + sequential, with the gap test marked informational |
| `DECISIONS.md` | **ADR-009 retitled** "Invoice numbers allocated from a locked series row inside the transaction"; index row updated; decision now says *"as the implemented mechanism"* and that it does not settle OD-3; a `P2-D-02` note records the directional asymmetry that invalidates the ADR's original "wasted work" premise |
| `ARCHITECTURE.md §5.4` | Restated as unique + sequential; explicit ⚠️ that gaplessness is not an approved invariant; **new** financial-year rollover paragraph pointing at Blueprint §8.2 |
| `TESTING-STRATEGY.md §5` | Numbering property restated; gap detection split out as a separate **informational** property |
| `TRACEABILITY.md` | §3 FR-SALE-004 row, §12 NFR-DATA-005 row, §13 AC-1 row + **new AC-1a** row |
| `OWNER-DECISIONS.md` | OD-3 blocking level re-scoped `BLOCKS-IMPLEMENTATION` → **`BLOCKS-TESTING`** with the asymmetry table and the standing per-counter-series prohibition; summary table row updated; **OD-22's "gapless" assertion corrected** with a visible note |
| `bmbackend/README.md` | Non-negotiable rule 5 restated |
| `bmbackend/CLAUDE.md` | Three-way vocabulary rule (unique / sequential / gapless) added; the stale "known live defect" warning replaced |
| `bmfrontend/docs/DECISIONS.md` | ADR-009 stub row restated |

### 3.2 CF-2 — traceability tally (HIGH)

`TRACEABILITY.md §13A.3` → **101 backend / 93 frontend / 194 total**, with a note explaining the
contradiction and that coverage was always complete. Recounted from the authoritative
`^| FR-… | P0 |` rows in both PRDs; all 194 IDs then checked individually against the matrix.

### 3.3 CF-3 — error-code registry (MEDIUM)

| File | Change |
|---|---|
| `API-SPECIFICATION.md §4.2` | Four codes added; registry now states **53 codes** |
| `API-SPECIFICATION.md §4.2a` | **New sub-section** giving each code's status, emitter, semantics and required client behaviour, plus the note that two are **per-operation outcomes inside a `200` batch**, not HTTP statuses |
| `API-SPECIFICATION.md §4.1` | `428` added to the status-code table |
| `bmfrontend/docs/API-INTEGRATION.md §4` | Four rows added with retry policy and client behaviour |
| `bmfrontend/docs/API-INTEGRATION.md §3.3` | Generated-union note updated to 53 codes with a closure explanation |

**Semantics were derived from the existing normative text, not invented** — each code already had
defined behaviour in `API-SPECIFICATION.md §6` or `OFFLINE-SYNCHRONIZATION.md §4.3/§5.2/§5.4`. No
conflict was found between those definitions and the registry; the codes were simply missing.

### 3.4 CF-4 — prohibited `CreditDetailsForm` reference (MEDIUM)

`TRACEABILITY.md §5`: FR-CREDIT-001 now describes the approved one-request flow with inline customer
selection and names `test_no_credit_details_route`; FR-CREDIT-004 states payment is optional and not
requested at save time. `bmfrontend/CLAUDE.md` and `bmfrontend/README.md` warnings rewritten from
"T-02 pending" to "corrected — treat any surviving mention as a **rejected** design".

**Per the closure brief, rejected designs were labelled rather than erased** where the mention
explains the rejected option.

### 3.5 CF-5 — residual `Complete & Print` and the mis-stated ADR-027 stub (MEDIUM)

| File | Change |
|---|---|
| `OFFLINE-SYNCHRONIZATION.md §8` | Flow label → `"Print Bill" (cash) or "Save / Credit Bill" (credit)` |
| `TRACEABILITY.md §3` | FR-SALE-001 frontend behaviour → both approved labels |
| `TRACEABILITY.md §7` | FR-OFFLINE-002 → both actions refuse, creating nothing |
| `DECISIONS.md` §Stubs | **ADR-027 row corrected** — it had stated the *rejected* single-button option as the decision. Now states the actual decision (keep two actions, fix clarity by wording) and labels the single button as considered-and-rejected |

**Four `Complete & Print` mentions deliberately remain**, all inside rejected-option lists
(OD-14 option (b); frontend ADR-027 options 1–3). Removing them would make the rejected design
unexplainable.

### 3.6 CF-6 through CF-11

| Finding | File | Change |
|---|---|---|
| CF-6 | `bmfrontend/docs/UI-UX-SPECIFICATION.md §12` | Preamble replaced: upper section **transcribed from an inspected screenshot**, lower section `[UNSEEN]`, owner cannot currently supply more, partial verification stated, superseded `Q-PRINT-1` citation dropped |
| CF-6 | `bmfrontend/docs/DECISIONS.md` | "Also outstanding" paragraph rewritten to the accurate partial-verification position |
| CF-7 | `bmbackend/README.md §6` | Approval wording aligned to the register's four-level vocabulary *(applied in the investigation pass)* |
| CF-8 | `OWNER-DECISIONS.md` | Summary sentence now names **four** items (OD-3, OD-5, OD-7, OD-22), drops OD-14, and records the Phase 2 re-scoping |
| CF-9 | `PRD.md §7.4` | **NFR-DATA-002b promoted** with rationale; back-referenced from `DATA-MODEL.md §4`; traced in `TRACEABILITY.md §12` |
| CF-10 | `DATA-MODEL.md` | `jobs`, `outbox_events`, `notifications` moved into **§3.12**; tax renumbered **§3.15** so order is monotonic. **§3.14 still denotes canonical identifiers**, so all existing cross-references remain valid |
| CF-11 | `bmfrontend/README.md` | OD-21 anchor moved into the link target *(applied in the investigation pass)* |

### 3.7 Designs finalised (not corrections — new specification)

| Design | Location | Content |
|---|---|---|
| Sale-finalisation recovery | **Blueprint §5.4** | 12 required cases; persisted-state schema; Mermaid state machine; case-6 blocking rule with the supervised `Discard this attempt` exit; case-12 human arbitration; 7 forbidden transitions; AC-REC-1…11 |
| Idempotency + fingerprinting | **Blueprint §5.2** | Claim-before-lock rationale; same-key concurrency; 10-rule canonicalisation; worked decimal examples; include/exclude field list with reasons; stored-outcome recovery; retention arithmetic (168× margin); client identity preservation; transaction/uniqueness requirements; AC-IDEM-1…9 |
| Financial-year series init | **Blueprint §8.2** | 4 options evaluated; race sequence diagram; lock-order compliance; 13 specification rules incl. timezone, template and kind inheritance; constraints; 8 named tests; owner-dependency statement |
| Money policy | **Blueprint §7.1–7.5** | Evidence check of the bootstrap premise; typed schema with allowed values; one-definition/four-consumers diagram; 4 anti-divergence controls; historical-invoice protection |

### 3.8 Phase 3 application-change tasks specified (not implemented)

| ID | Task | Lands with |
|---|---|---|
| **T3-01** | Invoice lookup by idempotency key, for case-12 arbitration | M12 |
| **T3-02** | `businesses.timezone` — the FY boundary must be business-local, not UTC. **Newly discovered gap** | M2 + M9 |
| **T3-03** | `invoices.money_policy_version` — so a disputed historical bill can be explained | M10 |
| **T3-04** | Series-sequence reconciliation assertion | M9 + M26 |

---

## 4. Risk status

| Severity | Count | Unmitigated |
|---|---|---|
| CRITICAL | 9 | **0** |
| HIGH | 13 | 0 unmitigated; **5 gate go-live**, 1 gates M27, 2 gate a single acceptance criterion |
| MEDIUM | 12 | 3 accepted-irreducible (R-23, R-25, R-32), rest mitigated or go-live-gated |
| LOW | 1 | accepted |

**Closed in this pass:** R-02, R-03, R-14, R-15, R-20, R-31. **Mechanism closed, values still owner-gated:** R-07.

**Explicitly accepted, not eliminated:** an authorised insider acting within their permissions
(R-23 — the control is attribution and owner review, not prevention); unencrypted IndexedDB on a
stolen counter machine (R-25); the 15-minute access-token window (R-26); unsynced drafts lost with a
device (R-22); and the irreversibility of per-counter series if ever enabled before OD-3 (R-16).

**This system is not and will not be free of risk.** The financial and inventory invariants are
defended by database constraints, database privileges and real-concurrency tests — the strongest
practical arrangement at this scale — and the residual exposure is named above rather than hidden.

---

## 5. Milestone readiness

| Status | Count | Milestones |
|---|---|---|
| ✅ **Ready** | **24** | M1–M3, M5–M8, M10–M26 |
| 🟡 **Conditionally ready** — build fully; one acceptance criterion unfreezable | **2** | **M4** (OD-7 values) · **M9** (OD-3 / AC-1a) |
| ⛔ **Blocked — owner evidence** | **1** | **M27** print bottom + final placement (OD-5, OD-22) |
| ⛔ **Blocked — go-live approvals** | **1** | **M28** (OD-1, OD-2, OD-4, OD-6, OD-8, OD-19, OD-24 + operational gates) |

**M14, M19 and M22 were unblocked by this pass** (T-01 and T-02 are complete). No milestone is
blocked on documentation any more.

---

## 6. Owner decisions still required

**Nothing already decided is being re-asked.** Settled and preserved unchanged: **OD-14** action
labels · **OD-21** credit workflow · **OD-23** amount display and blank rows · **OD-22**'s
*inclusion* preference · **OD-24**'s *wording* preference.

| # | Decision | Who | Blocks | Changed by closure? |
|---|---|---|---|---|
| 1 | **OD-7** rounding precision, method, stage, whole-rupee | Accountant (+ owner for coins) | **M4's expected-value assertions only** | Re-scoped `BLOCKS-IMPLEMENTATION` → `BLOCKS-TESTING` |
| 2 | **OD-3** is gapless numbering legally required? | Accountant | **AC-1a only** | Re-scoped → `BLOCKS-TESTING` |
| 2a | **`OD-3-A`** acknowledge gap-free-as-default | Owner | **Nothing** | **Downgraded to a courtesy confirmation** — it was previously described as the highest-value reply; that is no longer true |
| 3 | **OD-5** the bill's lower section | Owner *(cannot currently supply)* | **M27** + print sign-off | Unchanged |
| 4 | **OD-22** invoice-number / payment-block placement | Owner | **M27** + print sign-off | Unchanged |
| 5 | **OD-1 + OD-2** GST status, rates, HSN | Owner + accountant | Go-live | Unchanged |
| 6 | **OD-4** legal shop identity | Owner | Go-live | Unchanged |
| 7 | **OD-6** paper and printer | Owner | Go-live | Unchanged |
| 8 | **OD-8** accept offline = capture, not issue | Owner | Go-live (training) | Unchanged |
| 9 | **OD-19** hosting + **named** alert recipient | Owner | Go-live | Unchanged |
| 9a | **`OD-19-A`** on-call response expectation | Owner | Go-live runbook completeness | Unchanged — genuinely necessary (R-24) |

**No approval was fabricated.** No approval date, approver name, accounting sign-off or legal review
is recorded anywhere in either repository. `OD-3-A` and `OD-19-A` were re-tested for genuine
necessity: `OD-19-A` is necessary (an alert with no defined response is not monitoring);
`OD-3-A` is **not** a gate and is now presented honestly as optional.

---

## 7. Go-live gates outstanding

**Business:** OD-1 GST status · OD-2 rates/HSN · OD-4 legal identity · OD-6 paper/printer ·
OD-8 offline acceptance · OD-19 hosting + named recipient · OD-24 terminology approval ·
OD-5/OD-22 printed-sample sign-off.

**Operational** (`PRD.md §14`, `DEPLOYMENT.md §11.2`): demonstrated restore with the invariant suite
run against restored data · monitoring alerting a real named person · `OD-19-A` on-call expectation
written into a runbook · printed-sample sign-off · staff training (reprint vs new sale, the
not-confirmed state and counter slips, oversell policy, what offline does **not** do) · opening stock
loaded via `opening` movements and **physically verified** · products/prices/aliases spot-checked ·
seeded credentials rotated · counter machines with full-disk encryption · rollback rehearsed ·
break-glass stored outside the system · no high/critical dependency vulnerabilities.

---

## 8. Validation performed

Every check below was **actually executed** in this pass.

| Check | Method | Result |
|---|---|---|
| Relative links resolve | Script extracting every Markdown link target in every `.md` file and testing it against the filesystem | **521 targets, 0 broken** |
| Anchor fragments resolve | Script comparing `#frag` against `<a id>` and generated heading anchors | **0 missing** |
| Backend P0 count | `grep -cE '^\| FR-…-[0-9]+ \| P0 \|'` | **101** |
| Frontend P0 count | same | **93** → total **194**, matching the corrected tally |
| P0 coverage | Each of 194 IDs checked individually against `TRACEABILITY.md` | **0 untraced** |
| Error-code registry | Recount of `§4.2` | **53**; all 4 new codes present in backend registry **and** frontend §4 |
| Cited-but-undefined requirement IDs | Cross-check of all `FR-*`/`NFR-*` citations against PRD definitions | **0** (was 1 — `NFR-DATA-002b`, now promoted) |
| `gapless` assertions | Full-corpus grep with allow-list for question/option/cross-reference/correction text | **0 assertions remain** |
| `CreditDetailsForm` | grep | 0 instruction-like uses; remaining mentions labelled **rejected** |
| `Complete & Print` | grep | 4 remain, **all** in rejected-option lists |
| `P2-D-*` IDs | Section count vs references | **10 defined, 10 referenced, consistent** |
| Risk IDs and severity totals | Script counting `^### R-` and `^**Severity:` | **35 risks; 9 / 13 / 12 / 1** — matches the summary table |
| Milestone IDs | `grep -cE '^#### M[0-9]'` | **28**, M1…M28 contiguous |
| `CF-*` dispositions | Section count vs disposition table | **11 / 11**, each with files + validation method |
| Mermaid diagrams | Type check, fence balance, bracket balance per block | **11 blocks**, all balanced, 6 diagram types |
| Non-Markdown files | `find . -type f ! -name '*.md'` | **0** |
| `.git` directories | `find . -name .git` | **0** |
| Phase 1 closure reports unchanged | Byte-size comparison against the pre-Phase-2 inventory | **5 / 5 identical** |
| Planned tests described as executed | Review of all Phase 2 artifacts | **None** — every test reference is labelled planned |

**One self-correction made during validation:** the risk register's own severity tally read
8 / 11 / 13 / 1 (= 33) against 35 actual risks. Recounted and corrected to **9 / 13 / 12 / 1 = 35**.

---

## 9. Files created and modified in Phase 2

### 9.1 Created — 10

`bmbackend/docs/`: `PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md` · `PHASE-2-INVESTIGATION-REPORT.md` ·
`PHASE-2-IMPLEMENTATION-BLUEPRINT.md` · `PHASE-2-IMPLEMENTATION-PLAN.md` ·
`PHASE-2-DECISION-REGISTER.md` · `PHASE-2-RISK-REGISTER.md` ·
`PHASE-2-OWNER-DECISIONS-REQUIRED.md` · `PHASE-2-TRACEABILITY-MATRIX.md` ·
**`PHASE-2-CLOSURE-REPORT.md`** (this file)
`bmfrontend/docs/`: `PHASE-2-INDEX.md`

### 9.2 Modified — 15

| File | Why |
|---|---|
| `bmbackend/docs/PRD.md` | CF-1 (FR-SALE-004, NFR-DATA-005, AC-1, new AC-1a), CF-9 (NFR-DATA-002b) |
| `bmbackend/docs/OWNER-DECISIONS.md` | CF-1 (OD-3 blocking level, OD-22 assertion), CF-8 (summary), OD-7 re-scoping |
| `bmbackend/docs/API-SPECIFICATION.md` | CF-3 (§4.1, §4.2, new §4.2a) |
| `bmbackend/docs/DATA-MODEL.md` | CF-1 (§4), CF-9, CF-10 (section order, table placement) |
| `bmbackend/docs/DECISIONS.md` | CF-1 (ADR-009 title/index/decision/rationale), CF-5 (ADR-027 stub) |
| `bmbackend/docs/ARCHITECTURE.md` | CF-1 (§5.4) + FY rollover reference |
| `bmbackend/docs/TESTING-STRATEGY.md` | CF-1 (§5 numbering property) |
| `bmbackend/docs/TRACEABILITY.md` | CF-1, CF-2, CF-4, CF-5, CF-9 |
| `bmbackend/docs/OFFLINE-SYNCHRONIZATION.md` | CF-5 (§8 flow label) |
| `bmbackend/CLAUDE.md` | Phase status, Phase 2 doc map, numbering vocabulary rule |
| `bmbackend/README.md` | Phase status, Phase 2 doc index, rule 5, CF-7 |
| `bmfrontend/docs/API-INTEGRATION.md` | CF-3 (§3.3, §4) |
| `bmfrontend/docs/UI-UX-SPECIFICATION.md` | CF-6 (§12 preamble) |
| `bmfrontend/docs/DECISIONS.md` | CF-1 (ADR-009 stub), CF-6 |
| `bmfrontend/CLAUDE.md` | Phase status, Phase 2 index link, three repository-specific warnings |
| `bmfrontend/README.md` | Phase status, Phase 2 index, CF-4 note, CF-11 |
| `README.md` (workspace) | Phase status, Phase 2 pointer rows |

*(17 rows; `bmbackend/CLAUDE.md` and `bmfrontend/CLAUDE.md` plus both READMEs and the workspace
README were each touched in both passes — counted once.)*

### 9.3 Not modified

**The five Phase 1 closure reports and all four addenda are byte-identical** — verified. The Phase 1
exit-criteria evaluation, `SECURITY.md`, `DEPLOYMENT.md`, `bmfrontend/docs/PRD.md`,
`bmfrontend/docs/ARCHITECTURE.md` and `bmfrontend/docs/TESTING-STRATEGY.md` required no correction.

> **Two deferred items.** The investigation pass listed `../../bmfrontend/docs/TESTING-STRATEGY.md §4.5`
> (stale part-payment-preview test lines) and `../../bmfrontend/docs/PRD.md` FR-NAV-007 /
> `UI-UX-SPECIFICATION.md §1` ("credit details" in the modal allow-list) under CF-4. These are
> **cosmetic residues of the superseded design that do not instruct an implementer to build it**, and
> correcting a P0 requirement's modal allow-list is a scope change rather than a defect fix. They are
> recorded here as **Phase 3 task T3-05** (low priority, lands with M19/M25): drop "credit details"
> from the modal allow-list and the stale part-payment test lines. **This is disclosed rather than
> silently closed.**

---

## 10. Tests planned — none executed

**No test has been written or executed in Phase 1 or Phase 2.** New planned tests specified in this
pass:

| Group | Count | Where |
|---|---|---|
| `AC-REC-1…11` sale-finalisation recovery | 11 | Blueprint §5.4.7 |
| `AC-IDEM-1…9` idempotency and canonicalisation | 9 | Blueprint §5.2.9 |
| Financial-year series initialisation | 8 | Blueprint §8.2.4 |
| `AC-1a` conditional gap detection | 1 | `PRD.md §10` — **informational until OD-3** |
| `NFR-DATA-002b` subtotal invariant | 1 | `TRACEABILITY.md §12` |
| T3-01…T3-04 acceptance criteria | 4 groups | Plan §1.1 |

These are **in addition** to the 194 P0 planned tests already in `TRACEABILITY.md`.

---

## 11. Verdict

# `PHASE 2 CLOSED`

All 12 closure criteria are met. The 11 fidelity findings are resolved, the four critical designs are
complete and implementable, all critical and high risks have mitigations or explicit accepted/blocked
status, and the plan reflects every correction.

### What this means

Phase 3 implementation may begin on **24 milestones unconditionally** and **2 conditionally** (build
fully; do not freeze one acceptance criterion each).

### What this explicitly does **not** mean

- ❌ **The application does not exist.** No code, no tests, no infrastructure.
- ❌ **Nothing has been tested.** Every test is planned.
- ❌ **Phase 3 is not wholly unblocked** — M27 and M28 remain blocked.
- ❌ **Not every business decision is approved.** Nine remain, including two the system must never
  decide for itself (gapless numbering, rounding policy).
- ❌ **The bill layout is not fully verified.** The lower section is unseen and **must not be
  invented**.
- ❌ **No tax, accounting or legal question is settled.**
- ❌ **The system may not be used in the shop.** Go-live gates are unmet.
- ❌ **Risk is not eliminated.** §4 names what remains.

### If a closure criterion is later found unmet

Record it **here** and reopen Phase 2 for that criterion, rather than quietly amending history —
the same rule `PHASE-1-EXIT-CRITERIA.md §5` applies to Phase 1.

---

## 12. Related documents

[`PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md`](PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md) ·
[`PHASE-2-INVESTIGATION-REPORT.md`](PHASE-2-INVESTIGATION-REPORT.md) ·
[`PHASE-2-IMPLEMENTATION-BLUEPRINT.md`](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) ·
[`PHASE-2-IMPLEMENTATION-PLAN.md`](PHASE-2-IMPLEMENTATION-PLAN.md) ·
[`PHASE-2-DECISION-REGISTER.md`](PHASE-2-DECISION-REGISTER.md) ·
[`PHASE-2-RISK-REGISTER.md`](PHASE-2-RISK-REGISTER.md) ·
[`PHASE-2-OWNER-DECISIONS-REQUIRED.md`](PHASE-2-OWNER-DECISIONS-REQUIRED.md) ·
[`PHASE-2-TRACEABILITY-MATRIX.md`](PHASE-2-TRACEABILITY-MATRIX.md) ·
[`PHASE-1-EXIT-CRITERIA.md`](PHASE-1-EXIT-CRITERIA.md) ·
[`OWNER-DECISIONS.md`](OWNER-DECISIONS.md)


## 12. Phase 3 planning addendum — 2026-10-30

This addendum preserves the Phase 2 closure findings above and records the current Phase 3 planning state.

- **T3-05** is now specified in [PHASE-2-IMPLEMENTATION-PLAN.md](PHASE-2-IMPLEMENTATION-PLAN.md#11-phase-3-application-change-tasks-identified-during-closure), with acceptance criteria, its dependency on settled OD-21 / ADR-033 and completed T-02, and an exit-gate relationship to M19 and M25. It does not prevent either milestone from starting; it must be complete before either exits.
- **M1 baseline:** the backend and frontend directories contain documentation only; neither has .git, application source, dependency manifests or CI workflow files. GitHub repository identity and account access remain unverified. No repository was created and no Git operation was performed. Record intended repository URLs and owner-provided access before repository initialisation and the CI exit gate.
- The plan readiness table distinguishes decision readiness from actual execution. M1 is conditional on repository identity/access evidence; predecessor milestones still govern sequencing. OD-3 and OD-7 remain open exactly as recorded; OD-5 / OD-22 evidence and M28 go-live gates remain open.
- The Phase 2 risk review remains unchanged: 9 critical / 13 high risks, no unmitigated critical risk; high-risk items retain their documented owner gates and mitigations.
