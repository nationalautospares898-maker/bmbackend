# Optimization Implementation Report

**Date:** 2026-11-21. Every change below corresponds to a finding in
`ARCHITECTURE-PERFORMANCE-AUDIT.md`, classified there as "safe to implement now." Nothing
speculative was implemented; nothing in this list changes a business workflow, a calculation, an
invoice number, or anything visible to the owner at the counter.

---

## Change 1 — Removed five unused dependencies (Finding F7)

**Repository:** `bmfrontend`
**Files:** `package.json`, `package-lock.json`

Removed from `dependencies`: `@tanstack/react-query`, `react-hook-form`, `react-router-dom`, `zod`.
Removed from `devDependencies`: `msw`.

**Why this was safe:** confirmed via `depcheck` and a manual `grep -r` across the **entire**
repository (not just `src/`) that none of the five are imported anywhere — not in application
code, not in tests, not in any config file. Nothing can reference a dependency that was already
referencing nothing.

**What was preserved:** the actual implementation these packages were documented (in `CLAUDE.md`)
as the intended stack for — TanStack Query for server-state caching, react-hook-form + zod for
forms, react-router for navigation, MSW for test mocking — was **never built that way** in the
first place. The application uses manual `useState`/`useEffect` fetching, uncontrolled native
inputs with hand-written validation, a manual screen-state switch, and `vi.mock()`. Removing the
unused packages changes nothing about how any of that works; it only removes dead weight from
`package.json`.

**Verification:**
- `npx vitest run` — 39 test files, 795 tests, all passing (unchanged from before the change).
- `npm run lint` (ESLint) — clean.
- `npm run typecheck` (`tsc -b --force`) — clean.
- `npm run build` — succeeds; **main bundle byte size identical, `492.03 kB`**, confirming these
  packages contributed zero bytes to the shipped application even before removal (tree-shaking
  already excluded anything unimported) — the benefit is entirely in `npm ci`/CI install time and
  `npm audit` surface, not runtime.

---

## Change 2 — Added the missing `notifications(business_id, status)` index (Finding F1)

**Repository:** `bmbackend`
**Files:** `migrations/versions/0029_notifications_business_status_index.py` (new),
`src/bm/infra/db/models/infra.py` (added the matching ORM `Index(...)` declaration)

A plain two-column index, built with `CREATE INDEX CONCURRENTLY` (via Alembic's
`op.get_context().autocommit_block()`, since the project's migrations each run inside their own
transaction and Postgres refuses `CONCURRENTLY` inside one). The ORM model change is required
because this project's own CI runs `alembic check` specifically to catch drift between the
migrations and the SQLAlchemy metadata — the index would otherwise exist in the database but not in
the code's picture of it.

**Why this is safe to have *written* now, and what still needs your approval:** writing and testing
a migration file changes nothing in any running database. **Applying it to the production Neon
database is a separate act, requiring your explicit approval** per this engagement's standing
rules — tracked in `REMAINING-RISKS-AND-APPROVALS.md`, not done here.

**Verification performed (against a local, disposable PostgreSQL container — never production):**
- `alembic upgrade head` from `0028` — succeeds, index created.
- `alembic downgrade 0028` — succeeds, index dropped.
- `alembic upgrade head` again — succeeds, index recreated. Full round-trip clean.
- `alembic check` (schema-drift detector) — clean, both before (caught the missing ORM
  declaration — see `REGRESSION-AND-PERFORMANCE-TEST-REPORT.md` for the exact failure this caught)
  and after the model fix.
- `uv run ruff check .`, `uv run mypy src/`, `uv run lint-imports` — all clean.
- Performance proof: see `RESOURCE-CONSUMPTION-REPORT.md` §2 (6.7x faster, 85% fewer buffers, at a
  realistic 80,000-row/0.3%-active synthetic dataset).
- Full `pytest` suite run against the migrated local database — see
  `REGRESSION-AND-PERFORMANCE-TEST-REPORT.md` for the exact command and result.

---

## Change 3 — Cached the `jobs`-table-existence check in the worker loop (Finding F8)

**Repository:** `bmbackend`
**Files:** `src/bm/infra/queue/runner.py`

`_jobs_table_exists()` previously opened a full database transaction on **every** call — once per
second, forever, from the worker's main loop — to re-answer a question that cannot change from
`True` back to `False` at runtime. A module-level flag now short-circuits every call after the
first confirmed `True`.

**Why this is safe:** the check is a pure read with no side effects either way; caching a
monotonic fact (a migrated table staying migrated for the life of a running process) cannot produce
a stale-data bug the way caching, say, a stock figure would. Grepped the test suite for any test
that exercises the "table does not exist" path against a live database — none exists (every test
runs against an already-migrated database) — so there is no test whose intent this could silently
defeat.

**Verification:** `uv run ruff check .`, `uv run mypy src/` — clean. Covered incidentally by every
existing test that calls `claim_and_run_one` (all of which require the table to exist, which it
always does in the test databases used) — see
`REGRESSION-AND-PERFORMANCE-TEST-REPORT.md`.

---

## Not implemented, by design

Every other finding in `ARCHITECTURE-PERFORMANCE-AUDIT.md` (F2 — HTTP call under a held lock, F3 —
job kinds never auto-enqueued, F4 — settings re-queried with no cache, F5 — business-id index gaps,
F9 — dead `_timezone()` call, F10 — dead `worker_concurrency` setting, F11 — middleware-order
comment) was deliberately **not** implemented in this pass — each either touches code this
engagement's rules single out for extra caution (sales, credit, job-handler transaction boundaries),
requires a product decision only you can make, or has low enough current impact that implementing
it now would be exactly the "optimise a working operation because it looks repetitive" this audit
was told not to do. Each has a specific, proposed fix recorded in `REMAINING-RISKS-AND-APPROVALS.md`
or `FINAL-ARCHITECTURE-RECOMMENDATIONS.md` so it does not need rediscovering later.
