# Phase 3 Implementation Progress and Resumption Record

| Field | Value |
|---|---|
| Status | **Living record. Update it in the same commit as the work it describes.** |
| Purpose | The master implementation tracker required by the Phase 3 directive: every milestone's real state, the evidence for it, and the exact next action |
| Scope | Both repositories — `bmbackend` and `bmfrontend` |
| Last updated | 2026-10-30 |
| Authority | Implementation status only. It grants no owner, legal, accounting or physical acceptance, and it never supersedes [`PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md`](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) |

> **Read this before writing any Phase 3 code.** It records what is actually built and
> verified, so work resumes from here rather than repeating anything. Status words are
> used strictly, per §1.

---

## 1. Status vocabulary

These five words are used exactly as defined. A milestone is never described with a
stronger word than its evidence supports.

| Word | Means |
|---|---|
| **Implemented & verified** | Code exists, its milestone tests exist and were **executed**, and the static gates passed. The commit hash is recorded. |
| **Partially implemented** | Some deliverables exist and pass; the rest are named explicitly below. |
| **Not started** | No code. Design is complete (Phase 2), implementation has not begun. |
| **Design-complete, awaiting external acceptance** | Everything buildable is built; what remains needs a printer, a person or a legal opinion. |
| **Blocked** | Cannot proceed without a named external input. The input is named. |

"Tests pass" appears only where the suite was run and its output observed.

---

## 2. Milestone status

### 2.1 Backend foundations

| Milestone | Status | Evidence |
|---|---|---|
| **M1** Repository, CI, conventions | **Implemented & verified** | `pyproject.toml`, `src/bm/{main,worker,config}.py`, `api/{errors,middleware,deps,route_policy}.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `.github/workflows/ci.yml`, `.importlinter`. `/healthz`, `/readyz` (database + Alembic head), `/api/v1/meta`, `/api/v1/openapi.json`. A deliberate reverse import was introduced and **both** gates failed as designed (import-linter contract + `tests/structural/test_module_boundaries.py`), then reverted. |
| **M2** Database foundations | **Implemented & verified** | Migrations `0001`–`0004`: extensions, every ENUM type, tenancy (incl. **T3-02** `businesses.timezone`), identity, `audit_logs` with `REVOKE UPDATE, DELETE, TRUNCATE` **in the same migration as the table**. Fix **S-3** applied. `alembic downgrade base` → `upgrade head` clean. `tests/invariants/` connects as `bm_app` and asserts the database *refuses* mutation; it skips with a reason when the role is absent rather than passing vacuously. |
| **M3** Authentication and authorisation | **Implemented & verified** | `domain/auth/{service,tokens,hashing,devices,users,permissions}.py`, `api/v1/{auth,admin}.py`, migration `0005` seeds permissions and the six system roles from `PRD.md §3.2`. Rotation with reuse detection, lockout, `must_change_password` gating, device revocation on every request. Permission and isolation matrices are **generated from the live route table**. |
| **M4** Settings and the money engine | **Implemented & verified** | `domain/shared/{money,quantity}.py`, `domain/settings/{defaults,service}.py`, `api/v1/settings.py`. Addendum §3 implemented as a policy-parameterised algorithm. `money.py` **96% branch coverage** (floor 95%). 1,900+ generated Hypothesis cases. |

### 2.2 Backend core domain — **not started**

| Milestone | Status | Prerequisites met? |
|---|---|---|
| **M5** Catalog and search | **Not started** | Yes (M3 done) |
| **M6** Inventory ledger | **Not started** | Needs M5 |
| **M7** Customers | **Not started** | Yes (M3 done) |
| **M8** Drafts | **Not started** | Needs M5, M7 |
| **M9** Invoice numbering | **Not started** | Yes — M3 done and **T3-02 already landed in M2** |
| **M10** Sale finalisation and idempotency | **Not started** | Needs M4 ✅, M6, M7, M8, M9. **T3-03** (`invoices.money_policy_version`) must land *with* this milestone |
| **M11** Credit, payments, allocations | **Not started** | Needs M10 |
| **M12** Invoice read, cancellation, print payload | **Not started** | Needs M10, M11. **T3-01** lands here |
| **M13** Reports and exports | **Not started** | Needs M11, M12 |
| **M14** Sync and catalog delta | **Not started** | Needs M8, M7 |
| **M15** Suppliers and purchases | **Not started** | Needs M5, M6 |

### 2.3 Frontend

| Milestone | Status | Evidence |
|---|---|---|
| **Frontend M1** (scaffold; the plan folds this into M1) | **Implemented & verified** | `package.json`, `vite.config.ts`, `tsconfig.*`, `eslint.config.js`, `vitest.config.ts`, `index.html`, `src/{main.tsx,App.tsx}`, `src/lib/{money,strings}.ts`, `src/styles/app.css`, `tests/structural/rules.test.ts`, `.github/workflows/ci.yml`. **`src/lib/money.ts` at 100% branch coverage** as required. |
| **M16** Shell, API client, auth | **Partially implemented** | Done: strict TS, the four `VITE_` variables with a structural assertion, strict CSP, print root, `lib/money.ts`, `lib/strings.ts`, the structural suite. **Not done:** `src/api/{client,errors,endpoints}.ts`, generated types, routing, the shared refresh promise, `db/meta.ts`, component primitives. |
| **M17** Billing counter and draft persistence | **Not started** | Needs M16 |
| **M18** Cash finalisation | **Not started** | Needs M17; `P2-D-08` recovery design is a hard prerequisite |
| **M19** Credit finalisation | **Not started** | Needs M17. **T3-05** must complete before exit |
| **M20** Print layout (top) and counter slip | **Not started** | Needs M18 |
| **M21** Daily Bills and Credit Bills | **Not started** | Needs M17 |
| **M22** Offline layer | **Not started** | Needs M17, M18 |

### 2.4 Secondary and release

| Milestone | Status | Note |
|---|---|---|
| **M23** Imports (P1) | **Not started** | Needs M5, M15 |
| **M24** Returns and credit notes (P1) | **Not started** | Needs M12, M11, M6 |
| **M25** Frontend P1 screens | **Not started** | Needs M21, M13, M15, M23 |
| **M26** Hardening and operational readiness | **Not started** | Needs M13, M22 |
| **M27** Print completion and physical validation | **Not started** | Needs M20. Its sign-off is an **external gate** regardless of implementation |
| **M28** Go-live | **Blocked** | Named external gates in §5. Not a coding task |

---

## 3. Phase 3 task register (T3-01 … T3-12)

| Task | Status | Where |
|---|---|---|
| **T3-01** Invoice lookup by idempotency key | Not started | Lands with M12 |
| **T3-02** `businesses.timezone` | **Implemented & verified** | Migration `0002`; `domain/shared/clock.py` `business_date()` / `financial_year()` evaluate the financial-year boundary in business-local time |
| **T3-03** `invoices.money_policy_version` | Not started — **prepared** | `SettingsService.money_policy()` already derives and refuses unversioned combinations; the column lands with M10 |
| **T3-04** Reconciliation asserts `max(sequence)+1 == next_number` | Not started | M9 + M26 |
| **T3-05** Remove superseded credit-details references | **Partially** | The client structural suite asserts no `CreditDetailsForm` is reinstated. The specification/test-plan cleanup remains, due before M19/M25 exit |
| **T3-06** Remove the customer-visible invoice number end to end | **Partially** | Client structural assertion in place; the server side lands with M12/M20 |
| **T3-07** Credit confirmation and payment history without a number | Not started | `lib/strings.ts` already carries `"Stock has been updated."` and no number |
| **T3-08** No-decimal display without corrupting precision | **Implemented & verified** (both sides) | Backend `format_for_display` + property test; client `formatForPrint` + round-trip test. A fractional value is a blocking error on both sides |
| **T3-09** The four primary actions | **Partially** | Labels and shortcuts exist in `lib/strings.ts` with a structural assertion; the behaviour lands with M17–M19 |
| **T3-10** Printed bill reconciled to the workbook | Not started | M20, M27 |
| **T3-11** Printed date order | **Partially** | `print.date_format` is a single configurable setting defaulting to `MM-DD-YYYY` from workbook cell E7. The renderer and the day≠month test land with M20 |
| **T3-12** Embed the two workbook logos | Not started | M20, M27. The owner's workbook is **not** modified and **not** committed (it is gitignored in both repos) |

---

## 4. Decisions taken during implementation

Recorded here because each departs from, or sharpens, a Phase 2 document. None changes
an owner decision.

| # | Finding | Resolution |
|---|---|---|
| **I-1** | `RoundingMode.INVOICE_HALF_UP` ("keep line totals exact, round only the invoice") **cannot satisfy the data model.** A property test produced `0.001 × 1 = 0.0000001`, which `NUMERIC(14,2)` cannot store; rounding it for storage breaks NFR-DATA-002b, and not rounding it breaks the column. | Mode removed. Any rounding mode must round **at the line**. Reason recorded in the `RoundingMode` docstring; `_assert_storable` now proves every amount is representable at the stored scale. The remaining modes still cover the precision/method/stage/whole-rupee space OD-7 would select from. |
| **I-2** | `ck_invoices__total` and NFR-DATA-002b only agree while tax is zero. | `subtotal` is implemented as `Σ` post-discount pre-tax extensions — the only reading under which the `CHECK` is arithmetically sound. The two coincide exactly in v1 because `estimate_only` makes every `tax_amount` zero. **Enabling GST requires restating NFR-DATA-002b in terms of `taxable_amount`**; recorded, not silently resolved. |
| **I-3** | Failed login attempts and refresh-family revocations were written in the request transaction, which rolls back with the error they describe. Lockout could never trigger and a copied cookie stayed usable. | Both now commit on their own connection. A narrow, documented exception to "one request, one transaction": security evidence must survive the failure it records, and neither path touches money, stock or an invoice. |
| **I-4** | `settings_dep()` read `get_settings()` rather than the settings the app was created with, so an explicitly configured app authenticated against a different pepper. | Reads `request.app.state.settings`, falling back to the process cache only when no app is attached. |
| **I-5** | Migration numbering diverges from `DATA-MODEL.md §6.1` (the seed is `0005`, not `0016`). | Migrations are a dependency chain, not a fixed numbering. `§6.1` records intended **order**, not mandatory revision ids. Documented in the migration's own docstring. |
| **I-6** | `prettier --write .` reformatted the authoritative Phase 2 markdown. | Reverted; `*.md` and `docs/` added to `.prettierignore`. Specifications are records, not source to reflow. |
| **I-7** | The client structural rules were tripping on the comments explaining them. | They now scan code with comments stripped, so the rule means "no such string can be rendered" rather than "nobody may document the rule". |

---

## 5. External gates — unchanged by any of this work

No implementation can clear these. They are listed so nothing below is mistaken for
progress on them.

| Gate | What is still required |
|---|---|
| **OD-25 / R-36** numbering and tax legality | Qualified tax/accounting advice before any document is represented as a compliant tax invoice. **No legal conclusion is made anywhere in this codebase.** |
| **OD-7 / OD-23** money policy | The implemented policy is the documented **engineering default**, not an accountant's approval. A change needs a written replacement policy before M4 exit. |
| **OD-4** business identity | Owner-supplied legal name, address and phone. `businesses.legal_name` is deliberately nullable and is **never** defaulted or invented. |
| **OD-6 / M27** printer and paper | A real printer, real paper, a 25-row sample, cash and credit examples, both logos, no clipping, and signed owner acceptance. The A5 profile is a **proposal**. |
| **OD-22** credit block placement | Sample comparison and owner acceptance. Switchable off via `print.show_credit_block` until then. |
| **OD-26 / M27** date order | `MM-DD-YYYY` matches workbook cell E7, but owner/staff interpretation must be confirmed on a physical sample. |
| **OD-24** document label | `ESTIMATE` retained; owner/accountant terminology confirmation remains a go-live gate. |
| **OD-8** offline expectations | Staff training that offline **cannot** finalise a sale, cash or credit. |
| **OD-19 / M26** hosting and alerts | Named alert recipient, budget, escalation expectation, runbook. |
| **M28** go-live | The full `DEPLOYMENT.md §11.2` checklist: opening stock physically verified, catalog spot-checked by the owner, seeded credentials rotated, full-disk encryption on counter machines, printed sample signed, staff trained, break-glass stored outside the system. |

---

## 6. Verified evidence

Commands run, with observed results, at the time of writing.

### Backend

```
alembic downgrade base && alembic upgrade head   → clean, head 0005
pytest tests/                                     → 261 passed
ruff check .                                       → All checks passed
ruff format --check .                              → clean
mypy src/                                          → Success: no issues found in 53 source files
lint-imports                                       → Contracts: 3 kept, 0 broken
pytest --cov --cov-branch                          → 88% overall; money.py 96% branch
```

Deliberate-violation probe: a reverse import `bm.infra → bm.domain` was added; both
`lint-imports` and `tests/structural/test_module_boundaries.py` failed, then it was
reverted.

### Frontend

```
npm run typecheck    → clean
npm run lint         → clean
npm run format:check → All matched files use Prettier code style
npm test             → 62 passed
npm run build        → built; PWA precache 5 entries
test:coverage        → src/lib/money.ts 100% statements/branches/functions/lines
```

### Not yet run — and therefore not claimed

Concurrency suites, the 20-parallel numbering probe, the deadlock probe, real-offline
E2E, print snapshots, axe, load tests, and the restore drill. All belong to milestones
that have not started.

---

## 7. Exact next action

**Backend, in dependency order.** M5 (catalog and search) and M7 (customers) are both
unblocked and independent, so either may start. The critical path to a working bill is:

```
M5 catalog → M6 inventory ↘
M7 customers            → M8 drafts → M10 finalisation → M11 credit → M12 print payload
M9 numbering            ↗
```

Start with **M5**: migrations `0006`–`0007` (`units`, `brands`, `categories`,
`tax_groups`, `tax_rates`, `tax_config_versions`, `products`, `product_aliases`,
`product_prices` with the prefix and trigram indexes), `domain/catalog/`,
`api/v1/catalog.py`. Its acceptance criteria are first-character search, the documented
ranking order, `cost_price` **omitted** (not nulled) without `catalog.cost.read`, and
fix **S-8** (an alias insert refreshes the parent's `search_text`).

**Frontend.** Finish M16: `src/api/client.ts` with the idempotency-key lifecycle, the
single shared refresh promise, the exhaustive `ErrorCode` switch, and routing. The
`ErrorCode` union has **53 codes** (`API-SPECIFICATION.md §4.2`); `CORRUPT_VERSION` and
`SKIPPED_AFTER_EARLIER_FAILURE` arrive as per-operation outcomes inside a `200` sync
batch, not HTTP errors, and are branched on identically.

**Before M10.** Read `PHASE-2-IMPLEMENTATION-BLUEPRINT.md §5.2` (idempotency claim
ordering and canonicalisation, `P2-D-06`/`P2-D-07`) and **§5.4** (the 12-case recovery
state machine, `P2-D-08`, AC-REC-1…11). §5.4 is also a hard prerequisite for M18 and
M22, and the illustrative `onDraftEdited()` in the client's `API-INTEGRATION.md §5`
**must not** be implemented as written — it discards the idempotency key of a sale that
may already have committed.

---

## 8. Related documents

[`PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md`](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) ·
[`PHASE-2-IMPLEMENTATION-PLAN.md`](PHASE-2-IMPLEMENTATION-PLAN.md) ·
[`PHASE-2-IMPLEMENTATION-BLUEPRINT.md`](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) ·
[`OWNER-DECISIONS.md`](OWNER-DECISIONS.md) ·
[`PHASE-2-TRACEABILITY-MATRIX.md`](PHASE-2-TRACEABILITY-MATRIX.md) ·
[`DEPLOYMENT.md`](DEPLOYMENT.md)
