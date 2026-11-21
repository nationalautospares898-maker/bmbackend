# Regression and Performance Test Report

**Date:** 2026-11-21. All commands below, run exactly as shown, against either the frontend repo's
own toolchain or a **local, disposable PostgreSQL 16 container** (never production). Every result
is as actually observed in this session — nothing here is asserted without having been run.

---

## Frontend (`bmfrontend`)

| Command | Result |
|---|---|
| `npx vitest run` | **39 test files, 795 tests — all passed.** (0 failed, 0 skipped.) Run twice: once before Change 1 (baseline, also 795/795) and once after (still 795/795) — identical. |
| `npm run lint` (ESLint) | Clean, no output, exit 0. |
| `npm run typecheck` (`tsc -b --force`) | Clean, no output, exit 0. |
| `npm run build` (`tsc -b && vite build`) | Succeeds. Main bundle `492.03 kB` (gzip `157.32 kB`) — **byte-identical** before and after Change 1, confirming the five removed dependencies contributed zero bytes to the shipped application even before removal. |

No frontend test, lint rule, type, or build output changed in any way other than the dependency
removal itself. The four regression categories specifically named in the task (notifications,
timers/cleanup, polling cadence, hidden/visible-tab behaviour) were already covered by the 20
`NotificationBell` tests (16 pre-existing + 4 added in the earlier pass of this engagement, covering
exactly this) and ran clean in both full-suite runs above.

## Backend (`bmbackend`)

Environment: a local, disposable `postgres:16-alpine` container (`docker compose up -d postgres`,
the same image/config the project's own `docker-compose.yml` and CI use), migrated to head,
against a database named `bm_test` — the project's own test-suite guard
(`tests/structural/test_test_database_guard.py`) refuses to run against anything that isn't an
explicitly-named scratch database, specifically to prevent truncating real data; this guard worked
exactly as intended when first attempted against the default-named `bm` database and was corrected
by pointing at `bm_test` instead.

| Command | Result |
|---|---|
| `uv run ruff check .` | **All checks passed.** |
| `uv run mypy src/` | **Success: no issues found in 131 source files.** |
| `uv run lint-imports` | **3 contracts kept, 0 broken** (171→169 files scanned across runs, 971 dependencies). |
| `uv run alembic upgrade head` (from `0028`) | Succeeds; creates `ix_notifications__business_status` via `CREATE INDEX CONCURRENTLY`. |
| `uv run alembic downgrade 0028` | Succeeds; index confirmed dropped (`SELECT count(*) FROM pg_indexes ...` → `0`). |
| `uv run alembic upgrade head` (again) | Succeeds; index confirmed recreated (→ `1`). Full round-trip clean. |
| `uv run alembic check` (schema drift) | **First run: FAILED** — correctly caught the new index existing in the database but not yet in the SQLAlchemy ORM metadata (`infra.py` hadn't been updated yet at that point). **After adding the matching `Index(...)` declaration: clean — "No new upgrade operations detected."** This is exactly what this project's own CI runs this check to catch, and it caught a real omission in this session's own work before it could ship. |
| `uv run pytest -q` (full suite) | **1,071 tests collected (confirmed via a separate `--collect-only -q` run), full run exited 0, all-dots output, zero failures, zero errors.** |

### What the full pytest run actually covers

Per `pyproject.toml`'s own test markers and the directory layout, the 1,071 tests include (not an
exhaustive re-listing — see the collection output for exact per-file counts):
`tests/unit/` (business-rule unit tests, including `test_worker.py` and the inventory/stock-entry/
sync rule suites), `tests/api/` (full API integration tests against a real database, including
auth, finalisation, credit, imports, mechanics, reminders/orders, settings, stock movement report),
`tests/integration/` (`test_jobs.py` — exercises `claim_and_run_one`, the exact function Change 3
touched, directly; `test_billing_workflows.py`; `test_reminder_and_order_jobs.py`),
`tests/concurrency/test_parallel_billing.py`, `tests/invariants/` (immutability, restored-data,
**schema drift**, **schema shape** — the two suites most relevant to Change 2's migration),
`tests/property/test_money_conservation.py`, and `tests/structural/` (module boundaries, route
permissions, and the test-database guard that caught the `bm` vs. `bm_test` mistake above). The
`-m slow` load-test suite (100,000-product performance budgets) was **not** re-run — it requires a
multi-minute seed step and nothing in this audit's changes touches the query paths it covers; its
previously-recorded results are cited, not re-claimed, in `RESOURCE-CONSUMPTION-REPORT.md` §2.

### Specific regressions checked for, per the task's own list

- **Invoice numbers remain correct / no duplicate sales / stock changes exactly once:** covered by
  `tests/api/test_finalize_rules.py`, `tests/integration/test_billing_workflows.py`,
  `tests/concurrency/test_parallel_billing.py` — all passed, unchanged by this session's work (none
  of the three changes touch `domain/sales/` finalisation logic).
- **Notifications still work correctly:** `tests/api/test_reminders_and_orders.py`,
  `tests/unit/test_alerting.py`, and the notification-adjacent paths in
  `tests/integration/test_reminder_and_order_jobs.py` all passed. The new index (Change 2) changes
  *how fast* `GET /notifications` is answered, never *what* it answers — no test needed to change,
  and none did.
- **Job-queue correctness** (the function Change 3 touched): `tests/integration/test_jobs.py` (15
  tests) and `tests/unit/test_job_kinds.py` (4 tests) both passed unchanged — `claim_and_run_one`'s
  observable behaviour (claim exactly one job, run its handler, record the outcome) is identical;
  only the number of times its internal existence-check re-queries the database changed.
- **Schema/migration correctness** (Change 2): `tests/invariants/test_schema_drift.py` (31 tests)
  and `tests/invariants/test_schema_shape.py` (10 tests) both passed against the post-`0029`
  schema.

### Database safety during testing

All of the above ran against a local container; **at no point did any command in this session
connect to, read, write, or migrate the production Neon database.** The only destructive-looking
operations performed — `TRUNCATE notifications` (twice, to clean up synthetic benchmark rows) and
`DROP INDEX`/`CREATE INDEX` (to isolate the before/after measurement) — were executed exclusively
against the local disposable container, confirmed by the connection string used in every command
(`localhost:55439`, the port `docker-compose.yml` maps for the local container only).
