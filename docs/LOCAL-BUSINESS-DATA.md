# Local business data and safe verification

Updated 2026-11-07 for the developer ZIP in `C:\progromming\bmL1`.

## Local databases

| Database | Purpose | Data policy |
|---|---|---|
| `bm_business` | Local application API on port 8000 | Restored from the user's verified local shop database; use only for normal shop work |
| `bm_e2e` | Browser acceptance API on port 8001 | Disposable `E2E FIXTURE — NOT A REAL BUSINESS`; may be reset by the guarded E2E seeder |
| `bm_test` | Backend pytest | Disposable; test fixtures truncate its business tables |
| `bm_perf` | Load/performance tests | Synthetic `SEED DATA — NOT A REAL BUSINESS`; never use as an application database |
| `bm` on `bm-pg-local-run:55489` | Original local source | Preserved as the source copy; schema remains at revision 0021 |

The verified pre-migration archive is `../../local-backups/bm-business-before-0022-0024-20261107.dump`.
It was restored to the new `bm_business` database and checked before migrations 0022–0024:
business identity, record counts, invoice totals, paid/outstanding amounts, and inventory totals
matched the source. Migration and test commands must never target the original source database.

## Running the local shop

The API runs at `http://localhost:8000` against `bm_business`; the frontend runs at
`http://localhost:5173` and is explicitly configured to call that local API. The API container
uses the local `bm_default` Docker network and the existing database container. Local attachment
storage is mounted from this repository's `storage` directory. No production service, Neon
database, migration, or deployment is part of this local setup.

## Safe test targets

Backend pytest truncates tables, so set both test URLs explicitly to `bm_test` before running it.
Playwright uses the separate API on port 8001 and `bm_e2e`; it must not use the shop API on port
8000. The E2E seeder's `--reset` guard refuses any database containing a non-fixture business.

After adding a new local database, run the project's role setup in that database before applying
migrations. Default privileges are database-scoped in PostgreSQL; without them the `bm_app` role
may be missing access to ordinary tables created by migrations.

The local 100,000-product search benchmark was rerun after browser testing stopped: p95 was
233 ms against the 150 ms NFR-PERF-001 target. This is a local developer-machine measurement,
not a staging result; the threshold remains unmet here and should be measured on production-like
hardware before claiming the performance target is met.
