# bmbackend

Backend API for the **National Auto Spares** shop management system — billing, inventory, credit,
purchases and reporting for an automobile spare-parts shop.

> ## ⚠️ Project status: Phase 1 (documentation) complete — **no application code exists**
>
> Verified on 2026-10-29: this directory contained **nothing** before Phase 1. It is not yet a Git
> repository and has no source, dependency manifest, configuration or migration. Everything below
> describes the **agreed design for Phase 3 implementation**, not something that runs today.
>
> Commands in §4 are the intended interface and **will not work yet**.

---

## 1. What this service is responsible for

The backend is the **single source of truth** for money and stock. It independently enforces every
business rule, regardless of what any client sends.

- Product catalog and fast autocomplete search
- Draft bills (multiple concurrent bills per counter)
- **Sale finalisation** — atomic invoice + stock movement + payment, idempotent and auditable
- Inventory balances backed by an append-only stock movement ledger
- Customers, credit sales, payments and allocations, ageing
- Suppliers and goods receipts
- Returns and credit notes
- Spreadsheet/PDF import with mandatory human review
- Daily sales, credit and stock reporting
- Authentication, roles, permissions, device registry, audit log
- Offline draft synchronisation for the counter PWA

**Not** responsible for: UI, print rendering (it supplies the content, the client draws it),
accounting ledgers, e-invoicing, or tax compliance claims.

---

## 2. Proposed stack

Everything here is a Phase 1 **recommendation** (see `docs/DECISIONS.md` ADR-002…005):

Python 3.11 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 (sync) · Alembic · PostgreSQL 16 ·
`pg_trgm` search · Postgres-backed job queue (no Redis) · Docker · `uv` · pytest

Verified present on the development machine: Python 3.11.9, uv 0.8.22, Docker 29.3.1, Git 2.47.1.

---

## 3. Documentation — read in this order

| # | Document | What it answers |
|---|---|---|
| 1 | [docs/PRD.md](docs/PRD.md) | What must be built, with requirement IDs, priorities and acceptance criteria |
| 1b | [docs/OWNER-DECISIONS.md](docs/OWNER-DECISIONS.md) | **Authoritative register of business decisions** (OD-1…**OD-25**) — question, options, consequences, recommended default, blocking level, approver |
| 1c | [docs/PHASE-1-EXIT-CRITERIA.md](docs/PHASE-1-EXIT-CRITERIA.md) | **The 19 Phase 1 exit criteria, each evaluated with evidence**, plus the Phase 2 and go-live gates carried forward |
| 2 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Modules, layering, transactions, locking, jobs |
| 3 | [docs/DATA-MODEL.md](docs/DATA-MODEL.md) | Tables, constraints, indexes, invariants, migrations |
| 4 | [docs/API-SPECIFICATION.md](docs/API-SPECIFICATION.md) | Endpoints, schemas, error codes, idempotency |
| 5 | [docs/SECURITY.md](docs/SECURITY.md) | Threat model, auth, isolation, uploads, secrets |
| 6 | [docs/OFFLINE-SYNCHRONIZATION.md](docs/OFFLINE-SYNCHRONIZATION.md) | What offline does and does not do, and why |
| 7 | [docs/TESTING-STRATEGY.md](docs/TESTING-STRATEGY.md) | Test layers and the invariant suite |
| 8 | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Environments, migrations, backups, go-live gates |
| 9 | [docs/DECISIONS.md](docs/DECISIONS.md) | ADR-001…022 with rationale and consequences |
| 10 | [docs/TRACEABILITY.md](docs/TRACEABILITY.md) | Requirement → endpoint → table → test |
| — | [CLAUDE.md](CLAUDE.md) | Persistent engineering rules for AI-assisted work |

### Phase 2 planning set — read before writing any code

| # | Document | What it answers |
|---|---|---|
| **P00** | [**docs/PHASE-2-CLOSURE-ADDENDUM-1.md**](docs/PHASE-2-CLOSURE-ADDENDUM-1.md) | ★ **READ FIRST.** The owner's fourth answer set — **no invoice numbering**, **four** primary actions, **no decimals** on `PRICE`/`GRAND TOTAL`, the full bill layout, GitHub verification, and the re-evaluated closure verdict |
| P0 | [docs/PHASE-2-CLOSURE-REPORT.md](docs/PHASE-2-CLOSURE-REPORT.md) | The closure verdict, all 11 findings and their corrections, the four finalised designs, risk status and milestone readiness |
| P1 | [docs/PHASE-2-IMPLEMENTATION-PLAN.md](docs/PHASE-2-IMPLEMENTATION-PLAN.md) | **Milestones M1…M28** with prerequisites, acceptance criteria, exit gates and *"must not build yet"* |
| P2 | [docs/PHASE-2-IMPLEMENTATION-BLUEPRINT.md](docs/PHASE-2-IMPLEMENTATION-BLUEPRINT.md) | Target design, the **source-of-truth hierarchy**, diagrams, transaction/idempotency design |
| P3 | [docs/PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md](docs/PHASE-2-REQUIREMENTS-FIDELITY-AUDIT.md) | Which requirements are **owner-confirmed** vs defaults, and the 11 conflicts found (CF-1…CF-11) |
| P4 | [docs/PHASE-2-INVESTIGATION-REPORT.md](docs/PHASE-2-INVESTIGATION-REPORT.md) | Feature inventory, alternatives, failure modes, security review, DB/API findings |
| P5 | [docs/PHASE-2-DECISION-REGISTER.md](docs/PHASE-2-DECISION-REGISTER.md) | Phase 2 technical decisions `P2-D-01…P2-D-10` |
| P6 | [docs/PHASE-2-RISK-REGISTER.md](docs/PHASE-2-RISK-REGISTER.md) | Risks `R-01…R-37` with prevention, detection, recovery, residual |
| P7 | [docs/PHASE-2-OWNER-DECISIONS-REQUIRED.md](docs/PHASE-2-OWNER-DECISIONS-REQUIRED.md) | The short list of decisions that genuinely need the owner |
| P8 | [docs/PHASE-2-TRACEABILITY-MATRIX.md](docs/PHASE-2-TRACEABILITY-MATRIX.md) | Requirement → decision → milestone → test → release gate |

Frontend counterpart: `../bmfrontend/` (independent repository).

---

## 4. Local development (Phase 2 — not yet runnable)

```bash
cp .env.example .env
docker compose up -d db                     # PostgreSQL 16 + pg_trgm, unaccent, citext
uv sync
uv run alembic upgrade head
uv run python -m bm.cli seed-reference      # permissions, roles, units ONLY

uv run uvicorn bm.main:app --reload --port 8000
uv run python -m bm.worker                  # background jobs

uv run pytest                               # full suite (needs Docker)
uv run pytest tests/unit -q                 # fast, no database
uv run ruff check . && uv run mypy src/
```

`seed-reference` never seeds a business, tax rates, products or demo data — those are owner-supplied
facts, not defaults.

API docs at `http://localhost:8000/docs` (disabled in production).

---

## 5. Rules that are not negotiable

These exist because the system handles real money and real stock. Full rationale in `CLAUDE.md` and
`docs/DECISIONS.md`.

1. **Finalised invoices, invoice items, payments, stock movements and audit rows are never updated or
   deleted.** Corrections are cancellations, returns, credit notes or reversals. Database privileges
   enforce this, not just code review.
2. **The server computes all money.** Client totals are a cross-check, never a source.
3. **Every balance change has a stock movement**, written in the same transaction.
4. **Sale finalisation and payments are idempotent** via `Idempotency-Key`.
5. **An INTERNAL sale reference is allocated inside the finalisation transaction** from a locked
   series row, so it is **unique and sequential** and a rolled-back sale consumes nothing.
   ⚠️ **No customer-visible invoice number is produced** — [OD-3](docs/OWNER-DECISIONS.md#od-3) is
   re-answered: *"No need of invoice numbering."* The reference is **never printed or rendered**, and
   no replacement visible scheme may be invented. Statutory exposure is
   [OD-25](docs/OWNER-DECISIONS.md#od-25), a go-live gate.
6. **No `float` for money** anywhere — `Decimal` in Python, `NUMERIC` in Postgres, strings in JSON.
7. **No overselling** without an authorised override carrying a recorded reason.
8. **No offline sale finalisation.** Drafts sync; sales do not.
9. **Tax stays `estimate_only`** until the owner's GST status is confirmed and the gate passes. The
   system never calls a document a tax invoice on its own initiative.
10. **No database credential or privileged secret ever enters `bmfrontend`.**

---

## 6. Open owner decisions

Full analysis in **[docs/OWNER-DECISIONS.md](docs/OWNER-DECISIONS.md)**; the owner-facing action
list is **[docs/PHASE-2-OWNER-DECISIONS-REQUIRED.md](docs/PHASE-2-OWNER-DECISIONS-REQUIRED.md)**.

**Three items are settled by owner decision (OD-14, OD-21, OD-23) and five are partly settled. No
item has received legal, tax or accounting approval, and no approval date, approver or sign-off is
recorded anywhere — because none has been given.** Most of the system can be built on its
recommended defaults.

**Gates specific work** — and Phase 2 re-scoped two of them to be narrower than they read:

| ID | Question | What is actually blocked |
|---|---|---|
| [OD-3](docs/OWNER-DECISIONS.md#od-3) | Sequential selected — **is gapless numbering required?** | The numbering **policy statement and its acceptance test**. The design is buildable now — it satisfies either answer ([`P2-D-02`](docs/PHASE-2-DECISION-REGISTER.md#p2-d-02)) |
| [OD-7](docs/OWNER-DECISIONS.md#od-7) | Principles selected — **precision and rounding method still open** | The money **policy values and expected-value assertions**. The engine is buildable now as a policy-parameterised algorithm ([`P2-D-03`](docs/PHASE-2-DECISION-REGISTER.md#p2-d-03)) |
| [OD-5](docs/OWNER-DECISIONS.md#od-5) | **Bill's lower section — owner cannot supply further evidence yet** | Print template, **bottom section only**. Header, field block and item table are buildable |
| [OD-22](docs/OWNER-DECISIONS.md#od-22) | Invoice number + credit payment block — **inclusion selected, placement unverified** | **Final placement** only; both elements stay switchable |

**Settled 2026-10-29** — no longer blocking: [OD-21](docs/OWNER-DECISIONS.md#od-21) credit workflow ·
[OD-23](docs/OWNER-DECISIONS.md#od-23) amount display and blank empty rows ·
[OD-14](docs/OWNER-DECISIONS.md#od-14) action labels — **re-answered as four actions**:
`Print Bill` / `Credit Bill` / `Save a Draft` / `Clear Draft`.

**Blocks go-live only** — build freely, do not run the shop on it:
[OD-1](docs/OWNER-DECISIONS.md#od-1) GST status · [OD-2](docs/OWNER-DECISIONS.md#od-2) rates/HSN ·
[OD-4](docs/OWNER-DECISIONS.md#od-4) legal shop identity · [OD-6](docs/OWNER-DECISIONS.md#od-6)
paper and printer · [OD-8](docs/OWNER-DECISIONS.md#od-8) acceptance that offline means draft capture,
not invoice issuing · [OD-19](docs/OWNER-DECISIONS.md#od-19) hosting and a named alert recipient ·
[OD-24](docs/OWNER-DECISIONS.md#od-24) printed-document terminology (`ESTIMATE` selected, but a
wording preference is **not** a tax-terminology approval).

⚠️ **Bill reference — partially resolved 2026-10-29.** The owner supplied a **screenshot** of the
actual Excel bill, which **was inspected**; the top section of the layout is now transcribed from
evidence (heading, address, mobile, customer/mechanic/vehicle/date block, the `ESTIMATE` label and
the exact column headers `S R · ITEM · QTY · PRICE · TOTAL`). ✅ **Superseded 2026-10-30 — the
original workbook was supplied and inspected; the layout is now VERIFIED.** The sentence below was
written before that and is retained for the record. The **original `.xlsx` was not
available**, and the screenshot ends partway down, so the **grand total, signature area, footer and
page setup remain unseen**. The layout is **partially verified, not verified** —
`bmfrontend/docs/UI-UX-SPECIFICATION.md §12.1b` lists exactly what is still missing.

✅ **Credit workflow confirmed.** **`Credit Bill`** now **finalises the credit sale
immediately** through the same engine as a cash sale — no mandatory credit-details page. See
[OD-21](docs/OWNER-DECISIONS.md#od-21) and ADR-033.

---

## 7. Repository conventions

- This repository is **independent** of `bmfrontend`: separate history, branches, CI and deploys.
- Branches: `main` protected; work on `feat/*`, `fix/*`, `chore/*`; PR review required.
- Conventional commit messages.
- CI must pass lint, types, all test suites, dependency audit and secret scan before merge.
- A change to a **shared** ADR or requirement ID requires a matching commit in `bmfrontend`
  (ADR-021).
- `.env` is never committed; only `.env.example` with placeholders.

---

## 8. Phase status

| Phase | State |
|---|---|
| **1 — Discovery and documentation** | ✅ **CLOSED** 2026-10-29 — all 19 exit criteria met ([docs/PHASE-1-EXIT-CRITERIA.md](docs/PHASE-1-EXIT-CRITERIA.md)) |
| **2 — Investigation and implementation planning** | ✅ **CLOSED** — all 12 closure criteria met ([docs/PHASE-2-CLOSURE-REPORT.md](docs/PHASE-2-CLOSURE-REPORT.md)). 11 fidelity findings resolved, 4 critical designs finalised. Documentation only |
| 3 — Implementation | ⏸ **Not started.** **24 milestones ready, 2 conditionally ready** (M4 money, M9 numbering — build fully, one acceptance criterion each unfreezable); **M27** blocked on OD-5/OD-22, **M28** on the go-live gates. No milestone is blocked on documentation |

**Neither Phase 1 nor Phase 2 means the application exists, works or has been tested — no code or
test exists.** Phase 1 produced the documentation foundation; Phase 2 produced the plan to build
against it.

Nothing in Phase 1 or Phase 2 modified source code, dependencies, database schemas, Git history or
deployment configuration — there was none to modify.
