# Phase 2 — Phase 3 Implementation Plan

> **Current Phase 2 design disposition (2026-10-30):** [Design Completion Addendum](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) is the current cross-cutting decision record. It resolves superseded open labels and defines the Phase 3 implementation baseline. Historical closure reports/addenda remain unchanged; legal, physical and operational gates remain gates. It does not represent implementation or test completion.


| Field | Value |
|---|---|
| Status | **Living Phase 2 record.** The ordered plan Phase 3 executes |
| Purpose | Ordered milestones with prerequisites, affected modules, acceptance criteria, exit gates, and what must **not** be built until a decision is approved |
| Companion | Architecture in [`PHASE-2-IMPLEMENTATION-BLUEPRINT.md`](PHASE-2-IMPLEMENTATION-BLUEPRINT.md); dependency graph in its §15 |
| Implementation performed | **None.** No code, migration, dependency or test exists |

> **Sequencing was derived from dependency analysis, not imposed.** The PRD's own `Phase 2A…2K`
> (backend) and `2A…2L` (frontend) sketches were used as inputs and largely hold. Three changes were
> made on evidence: **money and numbering are promoted to their own early milestones** (they are
> prerequisites of finalisation and each carries a gate); **three documentation corrections are
> scheduled as blocking tasks** before the modules they would break; and **frontend milestones are
> explicitly parallelised** against the backend via contract mocking rather than sequenced after it.

---

## 1. Pre-flight tasks — ✅ ALL COMPLETE

Documentation-only, no code. **All five were applied during Phase 2 closure**; the table is retained
as the record of what was corrected and why. The "Blocks" column states what each *was* blocking.

| ID | Task | Was blocking | Source |
|---|---|---|---|
| **T-01** | Add `PRECONDITION_REQUIRED`, `SKIPPED_AFTER_EARLIER_FAILURE`, `CORRUPT_VERSION`, `DRAFT_ALREADY_CANCELLED` to `API-SPECIFICATION.md §4.2`; add `428` to `§4.1`; mirror into `../../bmfrontend/docs/API-INTEGRATION.md §4` | **M14, M22** — the frontend's `ErrorCode` union is generated from the registry and must be handled exhaustively; a required-but-unregistered code can never be handled | Audit **CF-3** |
| **T-02** | Replace the superseded credit-details references: `TRACEABILITY.md §5` (FR-CREDIT-001 "`CreditDetailsForm`", FR-CREDIT-004 "Payment field in the credit form"); `../../bmfrontend/docs/TESTING-STRATEGY.md §4.5` stale part-payment-preview lines; remove "credit details" from the modal allow-list in `../../bmfrontend/docs/PRD.md` FR-NAV-007 and `UI-UX-SPECIFICATION.md §1` | **M19** — the matrix is the document most likely used as a work list, and it currently points at the design the owner rejected | Audit **CF-4** |
| **T-03** | Reclassify the *gapless* clause in `PRD.md` FR-SALE-004 / NFR-DATA-005 / AC-1 as conditional on OD-3; correct `OWNER-DECISIONS.md` OD-22's "gapless" assertion and the OD-3/OD-5/OD-7/OD-22 summary miscount (CF-8); correct the ADR-027 stub title (CF-5) | **M9's acceptance test**, not M9's code | Audit **CF-1**, **CF-5**, **CF-8** · needs **`OD-3-A`** acknowledgement |
| T-04 | Batch the remaining hygiene corrections: CF-2 (P0 tally in `TRACEABILITY.md §13A.3`), CF-6 (stale "reconstruction" passages in `UI-UX-SPECIFICATION.md §12` and FE `DECISIONS.md`), CF-9 (promote `NFR-DATA-002b` into `PRD.md §7.4` + trace it), CF-10 (`DATA-MODEL.md §3.12–3.14` ordering), plus API items A-2/A-4/A-5/A-7 | Nothing | Audit **CF-2/6/9/10** |
| ~~T-04a~~ | ~~CF-7 (README approval wording), CF-11 (malformed anchor)~~ | — | ✅ **Applied during Phase 2** — both were in non-authoritative README files already being updated as AI-context |
| T-05 | Record `P2-D-01…P2-D-10` as accepted or rejected in [`PHASE-2-DECISION-REGISTER.md`](PHASE-2-DECISION-REGISTER.md) | M1 | — |

**Status after Phase 2 closure:** **T-01, T-02, T-03 and T-04 are COMPLETE** — all corrections were
applied to the authoritative documents (see
[`PHASE-2-CLOSURE-REPORT.md`](PHASE-2-CLOSURE-REPORT.md) §3 for the per-file correction log). T-05 is
complete. **No pre-flight task remains open**, so no milestone is blocked on documentation any more.

## 1.1 Phase 3 application-change tasks identified during closure

These are **application changes**, so Phase 2 specifies them and does **not** implement them. Each
must land with the milestone named.

| ID | Task | Lands with | Acceptance criteria |
|---|---|---|---|
| **T3-01** | Allow exact invoice lookup by idempotency key, so §5.4.5 case-12 arbitration is exact rather than heuristic. `invoices.idempotency_key` already exists "for forensic tracing" (`DATA-MODEL.md §3.8`); add it to the `GET /invoices` filter set, restricted to `reports.sales.read` | **M12** | `GET /invoices?idempotency_key=<uuid>` returns the invoice created under that key, or an empty page; it is **not** exposed in `print_payload`; a cross-tenant key returns an empty page, never a `403` |
| **T3-02** | Add `businesses.timezone` (text, NOT NULL, default `Asia/Kolkata`) so the financial-year boundary and report day-boundaries are evaluated in business-local time rather than UTC. No document currently records the business timezone | **M2** (schema) + **M9** (used by numbering) | A sale at 23:59 on 31 March and one at 00:01 on 1 April land in **different** financial years in business-local time; the daily-sales report's day boundary uses the same timezone; `issued_at` remains `timestamptz` |
| **T3-03** | Persist `invoices.money_policy_version` (int, NOT NULL) at issue time, alongside the existing `tax_config_version_id`, so a disputed historical bill can be explained under the rule that produced it | **M10** (must land before any invoice exists) | Every issued invoice records the `policy_version` in force; a policy change does not alter any stored invoice; a reprint of an older invoice still renders its original figures |
| **T3-04** | Extend the nightly reconciliation job to assert, per invoice series, that `max(sequence_number) + 1 == next_number` for committed invoices. A mismatch indicates a lost update | **M9** + **M26** | An artificially advanced `next_number` is **reported, not corrected**; the discrepancy raises the existing `Critical` reconciliation alert |
| **T3-05** | Reconcile the remaining superseded credit-details references in the frontend specification and test plan. Remove “credit details” from the modal allow-list in `../../bmfrontend/docs/PRD.md` FR-NAV-007 and `UI-UX-SPECIFICATION.md §1`; remove the stale part-payment-preview expectations from `../../bmfrontend/docs/TESTING-STRATEGY.md §4.5` while retaining valid payment and credit-finalisation coverage | **M19** + **M25**; depends on completed T-02 and settled OD-21 / ADR-033 | Both modal lists permit only conflict resolution, destructive confirmation, and supervisor authorisation; the credit flow remains inline and finalises in one request without requesting payment; the testing strategy contains no superseded preview flow; M19/M25 acceptance references agree with these rules. M19/M25 may start before this low-priority cleanup, but T3-05 is complete before either milestone exits |

**T3-02 and T3-03 are additive single-column changes and must land with their milestone, not later** —
both touch `invoices`, and after go-live any change to that table requires the
expand→migrate→contract discipline on live financial data.

## 1.2 Tasks added by the owner's fourth answer set, and by the workbook inspection

| ID | Task | Acceptance criteria | Depends on | Milestone(s) | Owner dependency | Status |
|---|---|---|---|---|---|---|
| **T3-06** | **Remove the customer-visible invoice number end to end.** Drop `invoice_number` from `print_payload`; keep it on the `invoice` resource as an internal reference; remove every client-side rendering path; retire `FR-PRINT-019` | `print_payload` contains **no** invoice-number field; a **structural** test asserts no invoice number is rendered in `src/`; `test_no_invoice_number_in_print_payload` passes; the `invoice` resource still exposes the internal reference to `reports.sales.read`; `GET /invoices?idempotency_key=` (T3-01) still works | [OD-3](OWNER-DECISIONS.md#od-3) ✅ answered | **M12**, **M20** | None — answered | **Open** |
| **T3-07** | **Update credit confirmation and payment history** to stop referencing an invoice number. The confirmation states customer, outstanding, due date and **"Stock has been updated."** (FR-CREDIT-013/026); Daily Bills and Credit Bills identify invoices by **date + customer + amount**, with the internal reference available on detail views only | `test_credit_confirmation_states_stock_updated` asserts **no** invoice number is shown; list screens render no invoice-number column; invoice detail may show the internal reference labelled as internal | T3-06 | **M19**, **M21** | None — answered | **Open** |
| **T3-08** | **Implement the no-decimal display for `PRICE` and `GRAND TOTAL`** without corrupting monetary precision. Formatting happens only at the render boundary; stored values untouched | `240.00` → `240`; `3850.00` → `3850`; a **property test** asserts the stored decimal is unchanged by formatting; a fractional monetary input or computed amount is rejected before finalization with a visible blocking validation error; no rounding or truncation | [OD-23](OWNER-DECISIONS.md#od-23) amended | **M20** | Design default complete per addendum §3; implement and pass M4/M20 validation/print tests | **Design complete — implementation open** |
| **T3-09** | **Implement the four primary actions** `Print Bill` · `Credit Bill` · `Save a Draft` · `Clear Draft`, with `Clear Draft`'s safety rules | All four labels present and **no fifth** primary action; `Clear Draft` requires confirmation stating the line count, **never** touches a finalised sale, and is **disabled while an unresolved `pending_finalizations` row exists** (AC-REC-4 extended); `Save a Draft` calls `PUT /drafts/{id}` only | [OD-14](OWNER-DECISIONS.md#od-14) re-answered | **M17**, **M18**, **M19** | None — answered | **Open** |
| **T3-10** | **Reconcile the printed bill against the verified workbook layout.** ✅ *The workbook was supplied and inspected 2026-10-30; the layout, geometry, typography, borders, merges and number formats are recorded in [PHASE-2-CLOSURE-ADDENDUM-2.md §3](PHASE-2-CLOSURE-ADDENDUM-2.md) and `../../bmfrontend/docs/UI-UX-SPECIFICATION.md §12.1`.* Remaining scope: make the print snapshot match it | Print snapshot reproduces header, field block, **25-row** item table with header **`QTY`**, `GRAND TOTAL` (11 pt bold, value in the last column), `THANK YOU  VISIT AGAIN`, both dealer-credit lines and both logos; column proportions follow the measured widths (4.664 / 19.777 / 3.332 / 8.000 / 10.441 chars); **no signature block, no amount in words, no invoice number, no tax line** | ✅ Workbook supplied and inspected | **M20**, **M27** | ✅ **[OD-5](OWNER-DECISIONS.md#od-5) resolved.** Printed-sample sign-off remains **[OD-6](OWNER-DECISIONS.md#od-6)** | **Open — unblocked** |
| **T3-11** | **Resolve the printed date order.** The workbook formats the date cell as **`mm-dd-yyyy`**; every other project document assumed `dd-mm-yyyy`. The sole example date (6 June 2026) reads identically both ways, so the earlier `[OBSERVED]` classification was an **inference** | The printed date format comes from a **single** configurable source, not a scattered literal; a test renders a date whose day ≠ month (e.g. 7 August) and asserts the chosen order; the chosen order is recorded in the print specification with its OD reference | Workbook inspection (complete) | **M20**, **M27** | Resolved by workbook evidence: `MM-DD-YYYY` from cell E7; sample acceptance confirms interpretation | **Design complete — implementation open** |
| **T3-12** | **Extract and embed the two logo images.** `image1.jpeg` (TATA, 55×57 px, 2,163 bytes) and `image2.jpeg` (Ashok Leyland, 58×64 px, 2,797 bytes) live inside the workbook package at `xl/media/`. Copy them into the frontend print assets at their measured print sizes and footer positions | Both images render in the printed footer — TATA at the left edge, Ashok Leyland at the right, aligned with the dealer-credit lines; sizes ≈ 20×21 pt and 21×23 pt; print CSS embeds them so printing works offline; **the owner's workbook is not modified and is not committed** | Workbook inspection (complete) | **M20**, **M27** | ℹ️ Trademark use is the owner's to confirm — recorded, not resolved | **Open — unblocked** |

**T3-10 is unblocked, and T3-11/T3-12 are new** — all three arise from the 2026-10-30 workbook
inspection. **T3-05 is unchanged and still correctly scoped** — it remains a low-priority frontend
specification/test cleanup due before **M19/M25** exit, and it does not block either start. Its
dependency on completed **T-02** and settled **OD-21/ADR-033** is unaffected by the fourth answer
set. **T3-09 partly overlaps it** (both touch the modal allow-list), so T3-09 should land first and
T3-05 then verifies the residue.

---

## 2. Milestone plan

Each milestone carries the 15 required fields. **"Must not build yet"** is the field that keeps a
gated decision from leaking into code.

### 2.1 Backend foundations

#### M1 — Repository, CI and conventions
| | |
|---|---|
| **Goal** | Both repositories initialised **against the owner's named GitHub remotes**, CI green on an empty-but-correct skeleton, boundaries enforced from the first commit |
| **Observed baseline (2026-10-30)** | Both project directories contain planning documentation only; neither has `.git`, application source, dependency manifests, or CI workflow files. GitHub repository identity and account access are unverified. This audit did not create repositories or perform Git operations. |
| **Prerequisites** | T-05 · ✅ **The two GitHub remotes are now known and verified**: `https://github.com/nationalautospares898-maker/bmfrontend.git` and `https://github.com/nationalautospares898-maker/bmbackend.git`. A read-only `git ls-remote` confirmed both **exist, are publicly readable, and contain zero refs** (completely empty) · 🔴 **Push authentication is NOT available** — `gh auth status` reports the token for account `AshwinSharma-git` is **invalid**, and that account is **not** the repository owner (`nationalautospares898-maker`), so write permission must also be confirmed. See [`PHASE-2-CLOSURE-ADDENDUM-1.md §6`](PHASE-2-CLOSURE-ADDENDUM-1.md) |
| **Modules / files** | `pyproject.toml`, `uv.lock`, `Dockerfile`, `docker-compose.yml`, `alembic.ini`, `src/bm/{main,worker,config}.py`, `.env.example`, `.github/workflows/`, importlinter config · FE: `package.json`, `vite.config.ts`, `tsconfig.json`, `eslint`, `prettier` |
| **DB changes** | None |
| **Backend APIs** | `/healthz`, `/readyz` (DB reachable + Alembic head match), `/api/v1/meta`, `/api/v1/openapi.json` |
| **Frontend** | None |
| **Shared contracts** | None |
| **Security** | Fail-fast on missing/malformed required env var; `.env` git-ignored; CI secret scan; non-root container user; exactly 4 `VITE_` variables allow-listed |
| **Tests** | `tests/unit/test_config.py` (missing var → startup failure); `/readyz` head-mismatch test; env allow-list test; **import-linter config as a merge blocker (`P2-D-05`)** |
| **Acceptance** | `uv run pytest`, `ruff`, `mypy src/` and the import linter all pass; `npm run lint && npm run typecheck && npm run build` pass; a deliberate reverse import **fails CI** |
| **Docs** | Record actual tool versions; confirm the `README.md §4` commands now work |
| **Risks** | Boundary erosion if the linter is deferred → mitigated by making it a blocker now, before there is code to untangle |
| **Exit gate** | CI green on both repositories; import linter enforcing |
| **Must not build yet** | Nothing |

#### M2 — Database foundations (migrations 0001–0004)
| | |
|---|---|
| **Goal** | Extensions, enums, tenancy, identity and the audit table exist, **with the privilege grants** |
| **Prerequisites** | M1 |
| **Modules / files** | `migrations/versions/0001…0004`, `infra/db/{session,locks}.py`, `infra/db/models/`, `domain/audit/` |
| **DB changes** | `pg_trgm`, `unaccent`, `citext`, enum types; `businesses` (**incl. T3-02 `timezone`**), `branches`, `counters`, `settings`; `users`, `roles`, `permissions`, `role_permissions`, `memberships`, `devices`, `refresh_tokens`, `login_attempts`; `audit_logs` + `REVOKE UPDATE, DELETE` from `bm_app`. **Apply S-3** (expression unique index on the `settings` scope) |
| **Backend APIs** | None |
| **Frontend** | None |
| **Shared contracts** | None |
| **Security** | Two DB roles (`bm_migrate` DDL, `bm_app` DML); audit append-only at the privilege level |
| **Tests** | `tests/migrations/` up+down per revision; `invariants/test_immutability.py::audit_logs` (app role `UPDATE`/`DELETE` **fails**); `invariants/test_tenant_columns.py`; `invariants/test_no_float_columns.py` |
| **Acceptance** | Every revision upgrades and downgrades cleanly; the app role **cannot** mutate `audit_logs`; no money column is `float`/`real`/`double precision` |
| **Docs** | Confirm `DATA-MODEL.md §6.1` order as built |
| **Risks** | Grants applied in a later migration → immutability untested for a window. Mitigation: grants ship **in the same migration as the table** |
| **Exit gate** | Migration suite green; immutability tests green |
| **Must not build yet** | Nothing. **Do not seed** a business, tax rates, products or demo data — ever |

#### M3 — Authentication and authorisation
| | |
|---|---|
| **Goal** | Every request resolves to an `AuthContext`; authorisation is enforced server-side and **proven by generated matrices** |
| **Prerequisites** | M2 |
| **Modules / files** | `domain/auth/`, `api/deps.py`, `api/middleware.py`, `api/v1/auth.py`, `api/v1/admin.py` (users, devices) |
| **DB changes** | Migration `0016` seeds **permissions, system roles and units only** |
| **Backend APIs** | `POST /auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/select-branch`, `GET /auth/me`; `GET/PUT /admin/users`, `POST /admin/users/{id}:reset-password`, `GET /admin/devices`, `POST /admin/devices/{id}:revoke` |
| **Frontend** | None (M16 consumes) |
| **Shared contracts** | Login/`me` response shapes; auth error codes |
| **Security** | Argon2id at documented parameters; access JWT 15 min; refresh opaque, rotating, device-bound, `HttpOnly`; **reuse → revoke the whole family + audit**; lockout 10/5 min per username, 30/5 min per IP with **hashed** IPs; `must_change_password` blocks every non-auth endpoint |
| **Tests** | `api/test_auth.py` (rotation, reuse detection, lockout countdown, revoked device, inactive user, forced change); **`api/test_permissions.py` generated per route**; **`api/test_isolation.py` generated matrix** — every endpoint × 2 businesses × 2 branches × every role |
| **Acceptance** | **AC-8**: no cross-business read or write succeeds on any endpoint; a route without a declared permission **fails CI**; refresh-token reuse revokes the family |
| **Docs** | — |
| **Risks** | A later endpoint forgetting tenant scoping → the matrix is **generated from the route table**, so new routes are covered automatically |
| **Exit gate** | AC-8 green; route-permission structural test green |
| **Must not build yet** | PIN quick-switch (P1, FR-AUTH-005) |

#### M4 — Settings and the money engine
| | |
|---|---|
| **Goal** | Typed, audited settings with documented defaults; the money engine built **as a policy-parameterised algorithm**. ⚠️ **Display readiness ≠ financial acceptance readiness**: the no-decimal *display* rule is known (OD-23), the *calculation* policy is not (OD-7) |
| **Prerequisites** | M2 · decision **`P2-D-03`**, **`P2-D-10`** |
| **Modules / files** | `domain/settings/`, `domain/shared/money.py`, `domain/shared/quantity.py`, `domain/shared/clock.py`, `api/v1/admin.py` (settings) |
| **DB changes** | None beyond M2 |
| **Backend APIs** | `GET /admin/settings`, `PUT /admin/settings/{key}` |
| **Frontend** | None (M17 consumes the policy via bootstrap) |
| **Shared contracts** | **The money policy object published by `GET /sync/bootstrap`** |
| **Security** | `admin.settings.manage`; every change audited with before/after |
| **Tests** | `unit/test_money.py` + `property/test_money_conservation.py` — line total, invoice total composition, Σ lines + rounding adjustment **exactly** equals the grand total, parameterised over **every** policy combination; `test_no_float_in_domain` (structural grep for `float(`); fractional quantity on an integer-only unit rejected (E-18); `amount_in_words` for 0, 1, 99, 100, 1 00 000, 1 00 00 000 and a paise value; `test_settings_defaults_and_audit`; `test_no_hardcoded_business_rule` |
| **Acceptance** | Money arithmetic is correct and reconciles **for every policy combination**, not just the proposed default; coverage on `money.py` ≥ 95% branch |
| **Docs** | Record the policy object in `ARCHITECTURE.md §6.5` as configuration, not constants |
| **Risks** | Building to one hardcoded policy and having OD-7 answer differently → **this milestone exists specifically to remove that risk** |
| **Exit gate** | Money property tests green across all policy combinations |
| **Must not build yet** | ⛔ **Do not freeze the production policy values, and do not write expected-value assertions that assume them. OD-7 selects precision, method, stage and whole-rupee rounding.** The default (`line_half_up`, amounts 2 dp, whole-rupee off) is a **documented proposal** and must be labelled as such everywhere |

### 2.2 Backend core domain

#### M5 — Catalog and search
| | |
|---|---|
| **Goal** | Products, aliases and first-character ranked search meeting the latency budget |
| **Prerequisites** | M3 |
| **Modules / files** | `domain/catalog/`, `api/v1/catalog.py`, migrations `0005`–`0006` |
| **DB changes** | `units`, `brands`, `categories`, `tax_groups`, `tax_rates`, `tax_config_versions`; `products`, `product_aliases`, `product_prices` + `ix_products__sku_prefix`, `ix_products__name_prefix`, `ix_products__search_trgm` (GIN), `ix_products__active`, `uq_products__business_sku`, `uq_products__business_barcode` |
| **Backend APIs** | `GET /products/search`, `GET /products/{id}`, `POST /products`, `PATCH /products/{id}`, alias add/delete, `POST /products/{id}:deactivate`, `GET /products/lookup-barcode`, `GET /brands|categories|units` |
| **Frontend** | None (M17 consumes) |
| **Shared contracts** | Search response item incl. `stock.as_of`; `cost_price` **omitted** without permission |
| **Security** | `catalog.read` / `catalog.write` / `catalog.cost.read`; **`cost_price` omitted, not nulled** |
| **Tests** | First-character search; ranking order (exact SKU → SKU prefix → name prefix → alias → trigram); `JK-123`/`jk 123`/`jk123`/`JK.123` all match; `limit` above max **clamped**; duplicate SKU → `409 DUPLICATE_SKU` naming the conflict; deactivated excluded by default; **`test_cost_price_omitted_without_permission`**; a referenced product cannot be hard-deleted; **S-8: alias insert refreshes the parent `search_text`** |
| **Acceptance** | Search returns from the first character; **NFR-PERF-001** p95 ≤ 150 ms at 100k products / 50 concurrent (nightly) |
| **Docs** | — |
| **Risks** | `search_text` drift because it is app-maintained → covered by S-8 |
| **Must not build yet** | Price history (P2); barcode scanning verification (needs hardware) |

#### M6 — Inventory ledger
| | |
|---|---|
| **Goal** | `inventory` is the **sole writer** of stock; every balance change has a movement row in the same transaction |
| **Prerequisites** | M5 |
| **Modules / files** | `domain/inventory/{service,rules,repository}.py`, `api/v1/inventory.py`, migration `0007` |
| **DB changes** | `inventory_balances` (+ `uq_inventory_balances__branch_product`), `stock_movements` (+ `ix_stock_movements__product_time`, `__source`) and **`GRANT INSERT, SELECT` only — no `UPDATE`, no `DELETE`** |
| **Backend APIs** | `GET /inventory/balances`, `GET /inventory/movements`, `POST /inventory/adjustments`, `GET /inventory/reconciliation/latest`, `POST /inventory/reconciliation:run` |
| **Frontend** | None |
| **Shared contracts** | Movement reason enum (closed set) |
| **Security** | `inventory.adjust` + reason from a list **plus** mandatory free text, audited |
| **Tests** | **`test_balance_equals_ledger_sum`** + `property/test_stock_conservation.py`; **a test asserting no code path other than `InventoryService.apply_movements` writes the balance table** (patch it and run the whole API suite); reconciliation detects a corrupted balance, reports it and **does not fix it**; movement reason enum closed; `apply_movements` produces the correct signed delta and `balance_after` for each reason; app role cannot `UPDATE`/`DELETE` movements |
| **Acceptance** | **NFR-DATA-001** holds under a randomised sequence of receipts, sales, returns, cancellations and adjustments |
| **Docs** | — |
| **Risks** | A second write path appearing later → the patch-and-run-suite test is the defence |
| **Exit gate** | NFR-DATA-001 invariant + property tests green |
| **Must not build yet** | Stock-count sessions (P1); **branch transfers (out of MVP)** — but **do** test E-13's documented behaviour (refusal + two reasoned adjustments) |

#### M7 — Customers
| | |
|---|---|
| **Goal** | Stable `customer_id` identity, inline-creatable with a name alone, with duplicate detection that never merges |
| **Prerequisites** | M3 |
| **Modules / files** | `domain/customers/`, `api/v1/customers.py`, migration `0009` |
| **DB changes** | `customers` (+ `ix_customers__name_trgm`), `customer_phones` (+ `uq_customer_phones__business_phone`), `customer_vehicles` (+ normalised index) |
| **Backend APIs** | `GET /customers/search`, `PUT /customers/{id}` (upsert, client id), `GET /customers/{id}`, `GET /customers/{id}/ledger`, `POST /customers/check-duplicates` |
| **Frontend** | None (M19 consumes) |
| **Shared contracts** | Customer upsert shape; **only `name` required**; `CUSTOMER_DUPLICATE_SUSPECTED` warning |
| **Security** | `customer.manage` / `credit.read`; outstanding summary only with `credit.read` |
| **Tests** | `test_minimum_customer_identity` (name alone succeeds); `test_customer_upsert_idempotent` (client id is the server id, no remapping); `test_duplicate_candidates_no_automerge`; **`test_rename_preserves_invoice_links`**; `test_customer_deactivate_not_delete`; `test_vehicle_normalisation_preserves_input` (normalised for search, **entered form preserved for printing**); optional fields supplied are **persisted** |
| **Acceptance** | **FR-CUST-010/011/012**: a name-only customer can receive a credit invoice; a rename breaks nothing; a referenced customer is never hard-deleted |
| **Docs** | — |
| **Risks** | An implicit UI requirement promoting an optional field to required → `PRD.md §6.9.1` is authoritative and a test asserts name-only works |
| **Must not build yet** | ⛔ **Customer merge (P1, FR-CUST-005).** And **no automatic merge policy, ever** — duplicates are surfaced for the user to choose |

#### M8 — Drafts
| | |
|---|---|
| **Goal** | Server-persisted drafts with client-generated ids, optimistic concurrency, and **zero** stock effect |
| **Prerequisites** | M5, M7 |
| **Modules / files** | `domain/sales/{drafts,service}.py`, `api/v1/drafts.py`, migration `0010` (drafts part) |
| **DB changes** | `draft_bills` (+ `uq_draft_bills__invoice`, `ix_draft_bills__open`, `ck_draft_bills__finalized`), `draft_bill_items` |
| **Backend APIs** | `GET /drafts`, `PUT /drafts/{id}` (upsert + `If-Match`), `GET /drafts/{id}`, `DELETE /drafts/{id}`, `POST /drafts/{id}:takeover` |
| **Frontend** | None (M17 consumes) |
| **Shared contracts** | Draft upsert shape; `preview_totals` **labelled advisory**; `DRAFT_VERSION_CONFLICT` carries `current_state` |
| **Security** | `billing.draft.manage`; `billing.draft.takeover` audited; other counter → `403 DRAFT_OWNED_BY_OTHER_COUNTER` |
| **Tests** | 20 drafts open; the 21st → `DRAFT_LIMIT_REACHED` and **no existing draft touched**; `version` increments; stale `base_version` → `409` with `current_state`; missing `If-Match` on an existing draft → `428`; **`test_draft_edit_creates_no_movements`** (movement count before/after); 50 rapid alternating updates finalise nothing and lose nothing; delete marks `cancelled` and keeps the row; `test_draft_line_price_provenance` |
| **Acceptance** | **FR-DRAFT-005** zero stock effect; **FR-DRAFT-002** limit never discards; **NFR-PERF-003** draft save p95 ≤ 250 ms |
| **Docs** | — |
| **Risks** | Server-side silent merge creeping in → ADR-007 forbids it; the test asserts `409` + `current_state` |
| **Must not build yet** | Stale-draft review (P1); takeover UI (M25) |

#### M9 — Invoice numbering
| | |
|---|---|
| **Goal** | In-transaction allocation of an **internal-only** sale reference from a locked series row, with financial-year rollover handled. ⚠️ **No customer-visible invoice number is produced** ([OD-3](OWNER-DECISIONS.md#od-3) re-answered) |
| **Prerequisites** | M3 · decisions **`P2-D-02`**, **`P2-D-09`** (both **accepted**, designs complete) · task **T-03** ✅ done · **T3-02** `businesses.timezone` must land first |
| **Modules / files** | `domain/sales/numbering.py`, migration `0010` (series part) |
| **DB changes** | `invoice_series` (+ `uq_invoice_series__scope`) |
| **Backend APIs** | `GET /admin/invoice-series`, `PUT /admin/invoice-series/{id}` |
| **Frontend** | None — **the client never invents or predicts a number** |
| **Shared contracts** | `invoice_number` and `sequence_number` in the invoice response |
| **Security** | `admin.settings.manage` for series configuration |
| **Tests** | **20 parallel finalisations on one series → 20 invoices, sequence `n…n+19`, no gaps, no duplicates**; rollback leaves `next_number` **unchanged**; **FY-boundary test with a frozen clock crossing 1 April → a new series row is created lazily and allocation succeeds** (`P2-D-09`); cancellation leaves no hole; a gap-detection query over committed rows returns empty; `lock_timeout` exceeded → fast retryable failure, not a hang |
| **Acceptance** | Committed numbers are unique and sequential within (business, branch, series, FY); a failed sale consumes no number |
| **Docs** | Record the lazy-creation mechanism in `DATA-MODEL.md §3.8` / `ARCHITECTURE.md §5.4` |
| **Risks** | **R-14** — now **fully closed**: OD-3's re-answer removes the customer-visible serial entirely, so the gapless invariant has no subject. **R-15** (FY series creation) — closed by `P2-D-09`. **R-16** — per-counter series still prohibited pending [OD-25](OWNER-DECISIONS.md#od-25). **New: R-36** — statutory numbering exposure |
| **Exit gate** | 20-parallel numbering test green; FY rollover test green |
| **Must not build yet** | ⛔ **Do not render the internal reference to a customer, and do not place it in `print_payload`** — [OD-3](OWNER-DECISIONS.md#od-3) is re-answered. ⛔ **Do not invent a replacement visible numbering scheme.** ⛔ Do not apply the per-counter-series remedy without re-checking [OD-25](OWNER-DECISIONS.md#od-25) first |

#### M10 — Sale finalisation and idempotency
| | |
|---|---|
| **Goal** | The one atomic engine for cash **and** credit, idempotent and fully audited |
| **Prerequisites** | M4, M6, M7, M8, M9 · decisions **`P2-D-06`**, **`P2-D-07`** (both **accepted**; full spec at [Blueprint §5.2](PHASE-2-IMPLEMENTATION-BLUEPRINT.md)) · **T3-03** `invoices.money_policy_version` must land with this milestone |
| **Modules / files** | `domain/sales/{service,rules}.py`, `domain/sync/idempotency.py`, `api/v1/sales.py`, migrations `0011`, `0015` (idempotency part) |
| **DB changes** | `invoices` (+ `ck_invoices__total`, `ck_invoices__credit_customer`, `ck_invoices__cancel_fields`, `ck_invoices__oversell_reason`, `uq_invoices__series_sequence`, `uq_invoices__business_number`, `ix_invoices__branch_issued`, `ix_invoices__customer_status`, `ix_invoices__outstanding`, **S-2** `ix_invoices__customer_outstanding`), `invoice_items` (+ `ck_invoice_items__returned`), `idempotency_keys` (+ `uq_idempotency__scope`) and the **column-level grants** from `SECURITY.md §7` |
| **Backend APIs** | **`POST /sales:finalize`** — `sale_type: cash | credit`, one engine, one endpoint |
| **Frontend** | None (M18/M19 consume) |
| **Shared contracts** | The finalise request/response; **every documented 409/422**; `Idempotent-Replay` header; `warnings` array |
| **Security** | `billing.sale.finalize`; `inventory.oversell.override` + reason ≥ 10 chars persisted and audited; `billing.price.override`; discount cap; audit row in the same transaction |
| **Tests** | Cash happy path (`paid`, stock down exactly once); credit with zero payment (`unpaid`, `outstanding = total`, `due_date` from terms); credit with part payment (`partially_paid`); **structural: the OpenAPI document contains no "prepare credit"/"credit details" endpoint**; free-text name for credit → `409 CREDIT_CUSTOMER_REQUIRED` with **no invoice, movement or consumed number**; name-only customer succeeds; optional header values persisted; **cash and credit compared field-by-field → structurally identical apart from payment rows**; **atomicity: inject a failure after the invoice insert → nothing persists and `next_number` is unchanged**; **idempotency: same key+payload → one invoice + `Idempotent-Replay: true`; same key, different payload → `409 IDEMPOTENCY_KEY_REUSE`; concurrent same key → exactly one invoice; missing key → `422`**; `P2-D-07` canonicalisation fixtures (equivalent payloads replay, non-equivalent conflict); **`P2-D-06`: a duplicate submit returns `IDEMPOTENT_REQUEST_IN_PROGRESS` without holding inventory locks**; insufficient stock → `409` with per-line `requested`/`available`, no side effects; oversell without permission → `403`, without reason → `422`, with both → negative balance + flagged movement + audit; `PRICE_CHANGED` and `accept_price_changes`; `PRODUCT_INACTIVE`; `TOTAL_MISMATCH`; empty/zero/negative-quantity rejected; zero-price requires permission; **double finalisation → `409 DRAFT_ALREADY_FINALIZED` carrying the invoice**; **concurrency: two counters on the last unit → one sale + one `INSUFFICIENT_STOCK available: 0`; ten sales with stock 3 → exactly 3 succeed; deadlock probe 50 parallel multi-product sales → zero deadlocks**; **A-3: a `lines` vs draft mismatch is not silently reconciled**; **no route mutates an invoice or item (OpenAPI scan)**; app role cannot `UPDATE`/`DELETE` `invoice_items` |
| **Acceptance** | **AC-1, AC-2, AC-3**; invariants **I-1, I-3, I-5, I-6, I-10, I-11**; **NFR-PERF-002** finalise p95 ≤ 600 ms / p99 ≤ 1.5 s for a 10-line bill |
| **Docs** | Record the claim-key-before-lock ordering constraint in `ARCHITECTURE.md §5.3`; record the canonicalisation rule |
| **Risks** | Fingerprint mismatch turning a legitimate retry into `IDEMPOTENCY_KEY_REUSE` at the counter → `P2-D-07` + shared fixtures. Lock-order deviation → the deadlock probe |
| **Exit gate** | AC-1/2/3 green; the full concurrency suite green; atomicity test green |
| **Must not build yet** | ⛔ **No second finalisation path, ever.** ⛔ No endpoint that mutates a finalised record. ⛔ No sync operation type that finalises a sale |

#### M11 — Credit, payments and allocations
| | |
|---|---|
| **Goal** | Payments allocated to invoices with derived status; **payment collection never moves stock** |
| **Prerequisites** | M10 |
| **Modules / files** | `domain/credit/{service,rules}.py`, `api/v1/credit.py`, `api/v1/payments.py`, migration `0012` |
| **DB changes** | `payments` (+ `ck_payments__reversal`, `ix_payments__customer_time`, `__branch_day`), `payment_allocations` (+ `uq_payment_allocations__pair`), `credit_notes`, `credit_note_allocations`, `customer_credit_balances` |
| **Backend APIs** | `POST /payments`, `GET /payments`, `POST /payments/{id}:reverse`, `GET /credit/outstanding`, `GET /credit/ageing-summary`, **`GET /credit/customers`**, `POST /credit-notes/{id}/allocations`, `POST /credit/write-off` |
| **Frontend** | None (M21 consumes) |
| **Shared contracts** | Payment request + allocation strategies; `GET /credit/customers` aggregate shape; `OVER_ALLOCATION` |
| **Security** | `credit.payment.record`; **`credit.writeoff` owner-only**; **no endpoint sets `payment_status`/`amount_paid`**; column grants restrict the derived-cache write |
| **Tests** | Outstanding derivation across part payment, several payments, over-payment, credit-note allocation, cancellation and return; **`amount_paid` always equals the allocation sum, including after a reversal**; all three allocation strategies; remainder → on-account credit, allocatable later; over-allocation → `422 OVER_ALLOCATION`; **duplicate payment with the same key → one payment**; reversal creates a new row and leaves the original untouched; **structural: no API path sets `payment_status` or `amount_paid` (OpenAPI scan)**; **ageing buckets at exact boundaries 0/1/30/31/60/61/90/91 with a frozen clock**; credit limit warn vs enforced; **`test_payment_creates_no_stock_movement` (movement count before/after)**; **`GET /credit/customers` aggregates equal the per-invoice outstanding sums — an invariant test, not a spot check**; grouping survives a rename; settled invoices returned under `include_settled=true`; **concurrency: two payments allocating to one invoice → correct `amount_paid`, over-allocation refused, no lost update** |
| **Acceptance** | **AC-4**; invariants **I-2, I-7**; NFR-DATA-003/004 |
| **Docs** | — |
| **Risks** | `amount_paid` cache drift → written only in the allocation transaction, column-grant restricted, invariant-tested, **nightly job alerts rather than repairs** |
| **Exit gate** | NFR-DATA-003/004 invariants + allocation concurrency green |
| **Must not build yet** | ⛔ **No paid/unpaid toggle, checkbox or endpoint — ever.** Credit limit enforcement and write-off are P1 |

#### M12 — Invoice read, cancellation and print payload
| | |
|---|---|
| **Goal** | Read, cancel-with-reversal, and the authoritative print payload |
| **Prerequisites** | M10, M11 · **T3-01** invoice lookup by idempotency key lands here (§1.1) |
| **Modules / files** | `domain/sales/{print_payload,cancellation}.py`, `api/v1/invoices.py`, migration `0011` (`print_log`) |
| **DB changes** | `print_log` |
| **Backend APIs** | `GET /invoices`, `GET /invoices/{id}`, `GET /invoices/{id}/print-payload`, `POST /invoices/{id}:cancel`, `POST /invoices/{id}/print-events` |
| **Frontend** | None (M20/M21 consume) |
| **Shared contracts** | **`print_payload`** — identical in shape for cash and credit; `totals.tax` is `null` (not `"0.00"`) in `estimate_only`; `document_title` from the server only |
| **Security** | `reports.sales.read`; `billing.sale.cancel` within the window with a mandatory reason, audited; **A-7**: confirm a cashier may log a print event for an invoice they created |
| **Tests** | `test_print_payload_complete`; **`test_estimate_only_never_says_tax_invoice`** — scan serialised responses across the whole API suite; **reprint creates nothing** (no sale, number or movement); print-event logged, and a logging failure **never blocks printing**; cancellation within the window reverses stock **exactly**, issues a full-value credit note, **retains the number**, keeps the record listed; outside the window → `409 CANCEL_WINDOW_EXPIRED`; second cancel → `409 INVOICE_ALREADY_CANCELLED`; cancelled excluded from revenue but present in audit views; `test_item_snapshot_survives_catalog_edit`; business identity empty → **prints nothing, never a placeholder** |
| **Acceptance** | **AC-11**; invariant **I-10**; FR-PRINT-001/002/003 |
| **Docs** | — |
| **Risks** | The client adding a field to the payload → FR-PRINT-010 plus a structural test on the client side |
| **Must not build yet** | ⛔ Server-side PDF generation (P2, FR-PRINT-005) |

#### M13 — Reports and exports
| | |
|---|---|
| **Goal** | Daily sales and credit outstanding as **two views of one dataset**, reconciling exactly |
| **Prerequisites** | M11, M12 |
| **Modules / files** | `domain/reports/`, `api/v1/reports.py` |
| **DB changes** | None (read-only; indexes from M10) |
| **Backend APIs** | `GET /reports/daily-sales`, `/credit-outstanding`, `/stock-position`, `/item-sales`, `/counter-reconciliation`, `/{name}/export?format=csv` |
| **Frontend** | None (M21/M25 consume) |
| **Shared contracts** | Every report carries `{as_of, filters}` and an `excluded` block |
| **Security** | `reports.sales.read` (cashier: own counter/shift only), `reports.financial.read`, margin needs `catalog.cost.read` |
| **Tests** | **`test_daily_and_credit_reconcile`** (the single most valuable report test); daily sales **includes credit sales** on their sale date; cancelled excluded from revenue and present in `excluded`; **timezone boundary — a sale at 23:59:59 and one at 00:00:00 land on the right days**; margin requires permission; **`test_csv_injection_escaped`** (a product named `=cmd()` exports safely); pagination and `as_of` on every report |
| **Acceptance** | **FR-RPT-003** totals reconcile exactly; **NFR-PERF-004** 1 day ≤ 1 s, 1 month ≤ 3 s on a 2-year dataset |
| **Docs** | — |
| **Risks** | Report/credit divergence → the reconciliation test is the gate |
| **Must not build yet** | Nothing. **Do not add a report that is not in FR-RPT-001…008 / FR-RPT-020…033** |

#### M14 — Sync and catalog delta
| | |
|---|---|
| **Goal** | Operation intake for drafts and customers, plus the bounded catalog delta — with finalisation **structurally absent** |
| **Prerequisites** | M8, M7 · task **T-01** ✅ **done** — the four sync codes are registered (`API-SPECIFICATION.md §4.2a`), so this milestone is **no longer blocked** |
| **Modules / files** | `domain/sync/`, `api/v1/sync.py`, migration `0015` (`sync_operations`) |
| **DB changes** | `sync_operations` (+ `ix_sync_operations__device_seq`, `__entity`) |
| **Backend APIs** | `POST /sync/operations`, `GET /sync/catalog?since=`, **`GET /sync/bootstrap`** |
| **Frontend** | None (M22 consumes) |
| **Shared contracts** | Operation envelope; the **three** operation types; per-operation outcomes; `cache_policy`; **the bootstrap payload incl. the money policy** |
| **Security** | Revoked device → `403 DEVICE_REVOKED` **on sync too**; `cost_price` **never** in the sync payload regardless of permission |
| **Tests** | Replay a whole batch → all `replayed`, nothing changed; out-of-order `client_seq` applied in order per entity; one invalid operation does not block other entities; offline-vs-online edit → exactly one `conflicted` with both states; deleted/finalised server-side vs local edit → correct `rejected` codes (**`DRAFT_ALREADY_CANCELLED`, `DRAFT_ALREADY_FINALIZED` — needs T-01**); revoked device refused; catalog cursor stable under concurrent product updates; `reset_required` on an expired cursor; row cap respected; **`cost_price` never present**; **server time used for ordering — a client clock a year back changes nothing**; **structural: the operation registry contains no type that finalises a sale, records a payment or moves stock (AC-OFF-8)** |
| **Acceptance** | **AC-OFF-1…8** server side |
| **Docs** | — |
| **Risks** | A future financial operation type being added → the structural registry test is the defence |
| **Exit gate** | AC-OFF-8 green |
| **Must not build yet** | ⛔ **No sync operation type that finalises a sale, records a payment, confirms a purchase, creates a return or applies an import.** Adding one requires revisiting ADR-011 **and owner approval** |

#### M15 — Suppliers and purchases
| | |
|---|---|
| **Goal** | Goods receipt where **only an explicit confirm** increases stock |
| **Prerequisites** | M5, M6 |
| **Modules / files** | `domain/purchases/`, `api/v1/purchases.py`, migration `0008` |
| **DB changes** | `suppliers`, `purchases` (+ `uq_purchases__supplier_doc`), `purchase_items` |
| **Backend APIs** | `GET/PUT /suppliers`, `POST /purchases`, `POST /purchases/{id}:confirm`, `GET /purchases`, `POST /purchases/{id}:cancel` |
| **Frontend** | None (M25 consumes) |
| **Shared contracts** | Purchase shapes; `DUPLICATE_SUPPLIER_DOCUMENT` |
| **Security** | `inventory.receive`; confirm is idempotent |
| **Tests** | **`test_draft_purchase_no_stock`** then `test_confirm_creates_movements`; duplicate supplier document → `409` with the existing purchase; confirmed receipts immutable; confirm twice with the same key → one effect |
| **Acceptance** | **FR-PUR-003** only a confirmed receipt increases stock |
| **Docs** | — |
| **Must not build yet** | ⛔ Supplier payables and purchase returns (P2, out of scope) |

### 2.3 Frontend — parallel from M1, contract-mocked

Frontend milestones depend on the **contract**, not on the backend module. MSW handlers are written
from `API-SPECIFICATION.md`, so passing against a mock means honouring the real contract. **The
frontend must never stub a business rule to unblock itself.**

#### M16 — Shell, API client, auth
| | |
|---|---|
| **Goal** | Routing, the API client with idempotency/retry/error handling, permission-aware rendering, session handling |
| **Prerequisites** | M1 · contract: M3 |
| **Modules / files** | `src/main.tsx`, `routes/`, `api/{client,errors,endpoints}.ts`, `api/generated/schema.d.ts`, `db/meta.ts`, `lib/strings.ts`, `components/` primitives |
| **Shared contracts** | Generated types; `ErrorCode` union; error envelope |
| **Security** | **Access token in memory only**; refresh via `HttpOnly` cookie; **single shared refresh promise**; only 4 `VITE_` variables; strict CSP; no `dangerouslySetInnerHTML` |
| **Tests** | `authClient.test.ts`; **one refresh on concurrent 401s, not 20**; refresh failure → login prompt **with drafts intact**; stable `device_id` across reloads; **structural: no token in `localStorage`/`sessionStorage`**; no `fetch`/`axios` outside `src/api/`; `generated/` matches a fresh generation (CI diff); permission-based rendering |
| **Acceptance** | `test_cashier_lands_on_billing`; **AC-FE-10** (partial — token storage) |
| **Risks** | Hand-written types drifting → CI regenerates and fails on a diff |
| **Must not build yet** | Nothing |

#### M17 — Billing counter and draft persistence
| | |
|---|---|
| **Goal** | The product: search, 20+ tabs, inline item editing, live preview totals, Dexie-durable drafts |
| **Prerequisites** | M16 · contract: M5, M8 · **money policy from M4 via bootstrap** |
| **Modules / files** | `features/billing/{draftStore,BillTabStrip,BillItemsTable,TotalsPanel,BillHeaderFields,BillActions}`, `features/catalog/ProductSearchBox`, `db/{schema,drafts}.ts`, `lib/money.ts`, `lib/normalize.ts` |
| **Shared contracts** | Draft upsert; search response; **the money policy from bootstrap — never hardcoded** |
| **Security** | Price cell read-only without `billing.price.override`, with a supervisor path |
| **Tests** | Search: one request on the first character, debounce, cancellation, keyboard model, **focus returns to search after every added line**, add-existing increments, zero stock selectable, **typed query preserved on error**; table: inline edit, validation blocks submit with a specific reason, tab order, 5 s undo on remove, non-blocking stock warning, inactive-product row; tabs: 20 open and all reachable, `Ctrl+Tab`/`Ctrl+1…9`, **switching fires no network request and never finalises**, 21st shows the limit and **closes nothing**; **persistence: reload mid-edit intact; `fake-indexeddb` crash/reopen intact; E2E force-close with 20 bills → all 20 intact**; **IndexedDB unavailable → blocking warning, online-only, never silent**; two tabs → one owner tab, other read-only; `lib/money.ts` **100% branch** + property test matching the server rule; **structural: no money arithmetic outside `lib/money.ts`**; **no business-rule constant hardcoded where bootstrap provides it** |
| **Acceptance** | **AC-FE-2** (20 bills survive a force-close), **AC-FE-11**, **AC-FE-12**; **NFR-FE-PERF-002/003/006** |
| **Risks** | A focus regression silently halving billing speed → an explicit E2E keyboard test |
| **Must not build yet** | ⛔ Do not hardcode the rounding policy; take it from bootstrap (`P2-D-03`) |

#### M18 — Cash finalisation
| | |
|---|---|
| **Goal** | `Print Bill` with correct idempotency-key lifecycle and every documented error state |
| **Prerequisites** | M17 · contract: M10 · **`P2-D-08` recovery design is a hard prerequisite** — [Blueprint §5.4](PHASE-2-IMPLEMENTATION-BLUEPRINT.md), 12 cases, AC-REC-1…11 |
| **Modules / files** | `features/billing/finalize.ts`, `db/pendingFinalizations.ts`, error panels |
| **Shared contracts** | Finalise request/response; `Idempotency-Key`; every 409/422 |
| **Security** | Supervisor authorisation recorded **against the approver** |
| **Tests** | One POST with the correct body; **double-click → exactly one request**; **three retries → the same `Idempotency-Key`, three different `X-Request-Id`**; **a new key only after an edit**; **the key is in Dexie before the request resolves**; each of `INSUFFICIENT_STOCK` / `PRICE_CHANGED` / `DRAFT_VERSION_CONFLICT` / `DRAFT_ALREADY_FINALIZED` / `TOTAL_MISMATCH` / `PERMISSION_DENIED` / `CREDIT_LIMIT_EXCEEDED` produces its specified UI; **no auto-accept of `PRICE_CHANGED`, no auto-resubmit on `TOTAL_MISMATCH`, no retry affordance on `DRAFT_ALREADY_FINALIZED`**; network/503 → **not-confirmed state, persisted, survives reload, retries the same key**; stale threshold stops auto-retry with the "check Daily Bills" message; **`P2-D-08`: an edit attempt while unconfirmed is blocked and the pending row is intact**; **business conflicts render as inline panels above the actions — never toasts**; `test_all_error_codes_handled` |
| **Acceptance** | **AC-FE-4, AC-FE-5**; FR-SALE-020…028 |
| **Risks** | Confusing `X-Request-Id` with `Idempotency-Key` — "the single most likely way to create a duplicate invoice" → asserted directly by test |
| **Must not build yet** | ⛔ Never print an invoice for an unconfirmed sale — counter slip only |

#### M19 — Credit finalisation
| | |
|---|---|
| **Goal** | **`Credit Bill`** finalising in **one** request from the billing screen |
| **Prerequisites** | M17 · contract: M10, M7, M11 · task **T-02** ✅ **done** — `TRACEABILITY.md §5` now describes the approved one-request flow, so this milestone is **no longer blocked** · **T3-05** must be complete before M19 exits (it does not block starting M19) |
| **Modules / files** | `features/billing/BillActions`, `features/catalog/CustomerQuickCreate` (in the **header field**), confirmation card |
| **Shared contracts** | `sale_type: credit`; `customer.mode`; `payment` omitted |
| **Security** | As M18 |
| **Tests** | **Disabled without an identified customer, with an inline hint naming what is missing — and it must not open a form**; with a customer → **exactly one** `POST /sales:finalize` and **no intermediate dialog rendered**; **no print dialog opens**; succeeds with a customer created from a **name alone**; optional header values present in the body and on the stored invoice; duplicate candidates listed, **nothing auto-merged**; **offline → refused, creating nothing: no key, no `pending_finalizations` row, no queued operation**; **confirmation card states the invoice number, the outstanding amount and "Stock has been updated."**; **structural: a single finalisation code path**; `three-actions-distinct.spec.ts` |
| **Acceptance** | FR-CREDIT-020…031; `credit-appears-in-both.spec.ts` |
| **Risks** | **R-31** closed: `TRACEABILITY.md §5` corrected in Phase 2 closure. Surviving mentions of `CreditDetailsForm` are explicitly labelled **rejected** and must never be read as instructions |
| **Must not build yet** | ⛔ **Do not reinstate a mandatory credit-details page or dialog.** ⛔ Do not request an initial payment at save time. ⛔ Do not open a print dialog on success |

#### M20 — Print layout (top) and counter slip
| | |
|---|---|
| **Goal** | Everything `[OBSERVED]` plus the settled display rules, with `[PROPOSED]` elements switchable |
| **Prerequisites** | M18 · contract: M12 |
| **Modules / files** | `features/print/{PrintRoot,InvoicePrintLayout,CounterSlipLayout}`, `styles/print.css` |
| **Shared contracts** | `print_payload` rendered **verbatim** |
| **Security** | — |
| **Tests** | A5 snapshot vs an approved baseline + A4 variant; **columns exactly `S R · ITEM · QTY · PRICE · TOTAL` in that order**; **`ESTIMATE` left-aligned on the DATE row, not a centred banner**; **cash and credit layouts byte-identical for the same data**; amount formatting `240.00`→`240`, `240.50`, `240.05`, `3850.00`→`3850`, **never padded, never truncated, no separators, no symbol**; **a property test that parsing the printed string back yields exactly the backend decimal**; **empty rows blank — no `0` in `TOTAL`**; ❌ **no invoice number anywhere** (OD-3 re-answered) and a structural assertion that none is rendered; **the payment block appears for credit and is absent for cash**, with figures equal to the payload's `amount_paid`/`outstanding` and no client arithmetic; `totals.tax === null` → **no tax row**; **structural: `"Tax Invoice"`, `BILL`, `INVOICE` appear nowhere as source literals**; long names **wrap in full**; multi-page repeats heading + header with `Page n of m`; `is_duplicate` → `DUPLICATE COPY`; counter slip watermarked with no invoice number and "not an invoice"; app chrome absent from print output; **empty optional business fields print nothing, never a placeholder** |
| **Acceptance** | FR-PRINT-010…021 except the bottom section; **AC-FE-7** |
| **Risks** | Print drift when the app is restyled → `print.css` fully isolated (ADR-028) + a structural test |
| **Exit gate** | Print snapshots approved for the top section |
| **Must not build yet** | ✅ *The bottom section is now VERIFIED from the workbook and may be built.* ⛔ **Final placement of the credit payment block (OD-22)** — the verified footer has no slot for it. ⛔ **Do not freeze the printed date order** until [OD-26](OWNER-DECISIONS.md#od-26) is answered. ⛔ Do not add a signature block, amount in words, tax line or any invoice number — all are **verified absent** from the original. ⛔ Do not put the payment block on cash bills. ⛔ Do not fix paper size or scaling — the workbook sets none (OD-6) |

#### M21 — Daily Bills and Credit Bills
| | |
|---|---|
| **Goal** | Two views of one dataset; customer-wise credit with payment recording |
| **Prerequisites** | M17 · contract: M11, M12, M13 |
| **Modules / files** | `routes/{daily-bills,credit-bills}`, `features/credit/{CreditBillsScreen,RecordPaymentDialog}`, `InvoiceActions` |
| **Shared contracts** | `GET /invoices`, `GET /credit/customers`, `GET /payments?invoice_id=` (**needs A-4, A-5**) |
| **Security** | `Cancel` only with permission and in-window; `Record payment` with `credit.payment.record` |
| **Tests** | Daily Bills: date filters, totals, row expand, counter/payment filters, cancelled struck through and excluded from revenue; Credit Bills: **two-level grouping keyed on `customer_id`**, ageing buckets as filters, **per-customer and overall totals rendered from server values — never summed client-side**, settled invoices visible and distinguished, settled customers behind `Show settled`; payment dialog submits with an **idempotency key reused on retry**, disables in flight, shows the remaining allocatable amount rather than letting the cashier guess; **grouping survives a customer rename**; **structural: no paid-status input control anywhere**; pagination via the API cursor, never a full-dataset fetch |
| **Acceptance** | FR-RPT-020…033; **AC-FE-10** (no paid control) |
| **Risks** | Client-side summing becoming authoritative → ADR-032 + a test |
| **Must not build yet** | ⛔ No paid/unpaid toggle. ⛔ No invoice edit UI |

#### M22 — Offline layer
| | |
|---|---|
| **Goal** | Outbox, scheduler, conflict UI, sync panel, and the two **separate** queues |
| **Prerequisites** | M17, M18 · contract: M14 · task **T-01** ✅ **done** · **`P2-D-08`** [Blueprint §5.4](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) governs the `pending_finalizations` lifecycle |
| **Modules / files** | `features/sync/{outbox,scheduler,SyncStatusPanel,DraftConflictDialog}`, `db/{outbox,catalogCache}.ts`, service worker config |
| **Shared contracts** | Operation envelope; outcomes; `cache_policy` |
| **Security** | `DEVICE_REVOKED` → blocking screen, stop sync, **never delete local data**; no `cost_price` cached |
| **Tests** | Create/edit drafts offline (real `setOffline`); reconnect drains with explicit per-operation outcomes; replay changes nothing; **60 s of typing on one draft coalesces to one operation**; conflict dialog shows both versions **and both totals**, neither lost; rejected stays visible until acknowledged; `SYNC_CURSOR_EXPIRED` → clean rebuild with no duplicates; **finalisation refused offline and the counter slip prints with no invoice number**; each of the six sync triggers verified independently; **only the owner tab syncs**; **structural: no `/api/` route in the service-worker cache config**; **no mutation queued by the service worker** |
| **Acceptance** | **AC-6, AC-FE-3, AC-FE-9**; AC-OFF-1…8 client side |
| **Risks** | Merging the two queues → ADR-010 + separate Dexie stores make the distinction structural |
| **Exit gate** | Real-offline E2E suite green |
| **Must not build yet** | ⛔ **No offline finalisation, cash or credit.** ⛔ No offline payment. ⛔ Never drain `pending_finalizations` through `/sync/operations` |

### 2.4 Secondary features and hardening

#### M23 — Imports (P1)
| | |
|---|---|
| **Goal** | Upload → parse in a worker → per-row human review → idempotent apply |
| **Prerequisites** | M5, M15 |
| **Modules / files** | `domain/imports/`, `api/v1/imports.py`, `infra/storage/`, `infra/queue/`, migration `0014`, `routes/imports` |
| **DB changes** | `import_jobs` (+ `uq_import_jobs__file`), `import_rows` (+ `ck_import_rows__decision_required`) |
| **Security** | The full `SECURITY.md §5` upload chain |
| **Tests** | Rejection matrix (`.exe` renamed `.csv`, HTML as `.csv`, oversized, zip bomb, path-traversal filename); same bytes twice → `409 DUPLICATE_FILE`, override needs a flag **and** a reason, audited; state machine — every illegal transition rejected, `applied` cannot be re-applied; one test per `match_status`; **apply blocked while any required row is `pending`**; **`price_list` intent produces zero stock movements under all circumstances**; `purchase_receipt` creates a purchase + movements in one transaction, and a mid-apply failure leaves nothing applied; apply twice with the same key → one effect |
| **Acceptance** | **AC-9** |
| **Risks** | Best-effort PDF parsing → **mandatory** human review is what is tested, not parser accuracy |
| **Must not build yet** | PDF table extraction quality guarantees (P2, explicitly best-effort) |

#### M24 — Returns and credit notes (P1)
| | |
|---|---|
| **Goal** | Partial/full returns with credit notes at snapshot prices |
| **Prerequisites** | M12, M11, M6 |
| **Modules / files** | `domain/returns/`, `api/v1/returns.py`, migration `0013` |
| **Security** | `returns.create` within `returns.window_days`, audited |
| **Tests** | Partial return: stock in, credit note at the **snapshot** price, `returned_quantity` updated, **original invoice rows otherwise untouched**; cumulative over sold → `409 RETURN_QUANTITY_EXCEEDED` **and the DB `CHECK` exercised directly**; **concurrent returns of one item cannot jointly exceed the sold quantity**; `damaged` + `restock: false` → scrap movement, not sellable stock; outside the window → `409`; against a cancelled invoice → `409 INVOICE_CANCELLED`; each `credit_handling` option produces the right financial effect |
| **Acceptance** | **AC-12**; invariant NFR-DATA-008 |
| **Must not build yet** | Returns UI is P2 (M25) |

#### M25 — Frontend P1 screens
| | |
|---|---|
| **Goal** | Stock, products, purchases, customers, imports, reports, settings, sync panel, audit log |
| **Prerequisites** | M21, M13, M15, M23 · **T3-05** must be complete before M25 exits (it does not block starting M25) |
| **Tests** | Per `../../bmfrontend/docs/TESTING-STRATEGY.md`; **axe zero serious/critical on every new screen**; `test_modal_allowlist` (**must not whitelist "credit details"** — T3-05) |
| **Acceptance** | FR-MOD-001…009; **AC-FE-8** |
| **Must not build yet** | Returns screen (P2) |

#### M26 — Hardening and operational readiness
| | |
|---|---|
| **Goal** | Performance budgets met, monitoring live, restore demonstrated, runbooks written |
| **Prerequisites** | M13, M22 |
| **Scope** | Performance suites both sides; load test at 100k catalog; deadlock probe at scale; monitoring + 13 alerts; dashboards; **restore drill whose proof is the invariant suite passing on restored data**; rollback rehearsal on staging; runbooks (restore, reconciliation triage, device revocation, break-glass, deploy/rollback, **on-call expectation — `R-24`**) |
| **Acceptance** | All `NFR-PERF-*` / `NFR-FE-PERF-*` budgets met **on a counter-spec machine, not a developer laptop**; **AC-10** restore demonstrated |
| **Exit gate** | Restore drill recorded with a date, duration, restored timestamp and result |
| **Must not build yet** | ⛔ Do not add a second API replica without first moving the rate limiter to a shared store |

#### M27 — Print layout completion and physical validation (Phase 3 acceptance gate)
| | |
|---|---|
| **Goal** | Implement the verified `GRAND TOTAL` and footer, 25-row table, A5 profile and credit-only payment block placement; no customer-visible invoice number or signature block |
| **Prerequisites** | M20; design baseline and workbook are complete per [Design Completion Addendum](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) |
| **Acceptance gate** | Physical printer/paper comparison, cash and credit samples, no clipping, date interpretation and owner sign-off remain Phase 3 acceptance; workbook page setup is absent |
| **Must not build yet** | Nothing in the design scope; implement the documented workbook-faithful A5 profile. Physical printer/sample/owner sign-off is a Phase 3 acceptance gate, not a design prerequisite |

#### M28 — Go-live — **BLOCKED**
| | |
|---|---|
| **Goal** | Production release |
| **Prerequisites** | M26, M27 · **OD-1, OD-2, OD-4, OD-6, OD-8, OD-19, OD-24, OD-25** · the full `DEPLOYMENT.md §11.2` checklist |
| **Includes** | Opening stock loaded via `opening` movements and **physically verified**; products/prices/aliases loaded and spot-checked by the owner; users created with real names and **all seeded credentials rotated**; devices registered with **full-disk encryption** on counter machines; **printed sample signed off**; **staff trained** on reprint-vs-new-sale, the not-confirmed state and counter slips, the oversell policy, and what offline does **not** do; owner briefed that `ESTIMATE` documents are not tax invoices; break-glass stored outside the system |
| **Must not build yet** | ⛔ **The system may not be used in the shop until every gate is cleared** |

---

## 3. Parallelisation

| Can run in parallel | Why |
|---|---|
| **M5 · M7 · M8** after M3 | Independent aggregates, no shared tables |
| **M4 · M5 · M6** | Money is pure; catalog and inventory are separate modules |
| **M16 → M17 → M18 → M20** alongside **M5 → M14** | Frontend depends on the **contract**, mocked with MSW from `API-SPECIFICATION.md` |
| **M13 · M14 · M15** after their prerequisites | Independent |
| **M23 · M24 · M25** | Independent P1 streams |
| **T-01 · T-02 · T-04** | Documentation-only, no conflicts |

| Must remain strictly sequential | Why |
|---|---|
| M1 → M2 → M3 | Privileges and auth context underpin everything |
| M4, M6, M7, M8, M9 → **M10** | Finalisation needs money, stock, customer, draft and numbering |
| M10 → M11 → M13 | Payments need invoices; reports need both |
| M10 → M12 | The print payload needs a finalised invoice |
| **T-01 → M14, M22** | The registry must be complete before the exhaustive switch is written |
| **T-02 → M19** | Prevents rebuilding the rejected design |
| M20 → **M27** | The top must be approved before the bottom is designed |
| M26, M27 → **M28** | Go-live needs both |

**Critical path:** M1 → M2 → M3 → M9 → M10 → M11 → M13 → M26 → M28. M27 runs as a parallel print branch after M20; physical printer validation is a Phase 3 acceptance gate.

---

## 4. Current Phase 3 readiness and external gates — 2026-10-30

The 28 milestones have complete design scope and acceptance criteria and are **design-ready to enter Phase 3**, subject to their documented dependency order. This does not mean implementation, testing, physical acceptance, legal clearance or production readiness. The previous 26/1/1/1 readiness table was a dated pre-completion snapshot and is superseded for current planning by this section; the prior closure reports preserve that history.

| Work | Phase 3 status | Required evidence before exit/release |
|---|---|---|
| M1–M3, M5–M26 | Design-ready; sequence and prerequisites in §2 apply | Their existing milestone tests and acceptance criteria |
| M4 money | Design-ready to implement §3 of the Design Completion Addendum | Exact input/calculation/storage/display tests; record any owner/accountant change to the default before M4 exit |
| M27 print | Design-ready to implement the workbook-faithful template and proposed profile | Real printer and paper sample, cash and credit print checks, owner sign-off |
| M28 go-live | Design complete; production use remains gated | Qualified tax/numbering advice; confirmed business identity; hosting/alert recipient; staff training; loaded/verified catalog and stock; restore, security and operational evidence |

No Phase 3 application code, migrations, or tests were created by this documentation update. P1/P2 legal, owner, physical-printer, data-loading, training, monitoring and deployment gates remain explicit release controls. See [Design Completion Addendum](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) §§2–7 for the normative disposition and [Deployment](DEPLOYMENT.md) for release evidence.

---

## 5. Related documents
## 5. Related documents

[`PHASE-2-IMPLEMENTATION-BLUEPRINT.md`](PHASE-2-IMPLEMENTATION-BLUEPRINT.md) ·
[`PHASE-2-DECISION-REGISTER.md`](PHASE-2-DECISION-REGISTER.md) ·
[`PHASE-2-RISK-REGISTER.md`](PHASE-2-RISK-REGISTER.md) ·
[`PHASE-2-OWNER-DECISIONS-REQUIRED.md`](PHASE-2-OWNER-DECISIONS-REQUIRED.md) ·
[`PHASE-2-TRACEABILITY-MATRIX.md`](PHASE-2-TRACEABILITY-MATRIX.md) ·
[`TESTING-STRATEGY.md`](TESTING-STRATEGY.md) · [`DEPLOYMENT.md`](DEPLOYMENT.md)
