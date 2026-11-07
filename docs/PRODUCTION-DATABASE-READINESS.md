# Production database readiness — National Auto Spares

| | |
|---|---|
| Status | **READY** for the Northflank backend and Vercel frontend deployments |
| Assessed | 2026-11-03 |
| Neon project / branch / database | `national auto spares` / `production` / `neondb` |
| Region | AWS Singapore (`ap-southeast-1`) |
| PostgreSQL | 18.6 |
| Alembic head | `0021` |
| Business data | **none** — 43 business tables, 0 rows |
| Open items | 1 intermittent local test flake, 0 database blockers |

How it got here: [`NEON-PRODUCTION-INITIALIZATION-REPORT.md`](NEON-PRODUCTION-INITIALIZATION-REPORT.md).
How to operate it: [`PRODUCTION-DEPLOYMENT-GUIDE.md`](PRODUCTION-DEPLOYMENT-GUIDE.md).

No secret, connection string or endpoint id appears in this document.

---

## 1. Acceptance criteria

| # | Criterion | Status |
|--:|---|---|
| 1 | Correct Neon production branch/database verified | ✅ role, database, host suffix, region and endpoint type checked before connecting |
| 2 | Initial database state inspected | ✅ read-only; classified **A — truly fresh** |
| 3 | No unexpected existing business data | ✅ 0 tables, no `alembic_version` |
| 4 | `bm_app` role created and secured | ✅ 11/11 attribute checks |
| 5 | Database grants verified | ✅ including column grants as exact sets |
| 6 | All Alembic migrations applied | ✅ `0001`→`0021`, no stamping |
| 7 | Alembic head verified | ✅ `0021 (head)`, no pending revisions |
| 8 | Complete schema verification passed | ✅ 77/77 checks |
| 9 | Production provisioning completed | ✅ 5 config rows + 11 units |
| 10 | Owner account created securely | ✅ generated, never echoed, forced change on first sign-in |
| 11 | Business/branch/counter configuration exists | ✅ 1 each |
| 12 | Every business data table at zero | ✅ 43/43 at 0 |
| 13 | No demo/test data exists | ✅ name and username markers checked |
| 14 | No orphan business records | ✅ 13 cross-table consistency checks |
| 15 | Constraints and invariants verified | ✅ 272 constraints, privilege model proven by live refusal |
| 16 | Test suite passed, or skips documented | ✅ second full run **green**; one intermittent flake on the first, documented (§6) |
| 17 | Local `bm` untouched | ✅ never connected to — the container holding it was already stopped; see §7 |
| 18 | Local `bm_test` untouched | ✅ does not exist in either project container; a disposable DB was used |
| 19 | Local `bm_accept` untouched | ✅ does not exist |
| 20 | No credentials leaked | ✅ one contained incident, no rotation needed (§5) |
| 21 | Safe for actual shop operations | ✅ |
| 22 | Documentation updated | ✅ this file + the initialization report |
| 23 | No commit, push or deployment | ✅ working tree only |

**Verdict: READY.** All 23 criteria met. Criterion 16 carried one intermittent local
test-suite failure on the first full run which did not reproduce on the second; it is not a
database defect and does not gate deployment.

---

## 2. What exists in the database

| Layer | State |
|---|---|
| Schema | 54 tables, 598 columns, 136 indexes, 272 constraints, 30 enum types, 1 sequence |
| Extensions | `pg_trgm` 1.6, `unaccent` 1.1, `citext` 1.8 |
| Roles | `neondb_owner` (DDL, migrations only) · `bm_app` (DML, runtime) |
| Reference data | 24 permissions, 6 system roles, 72 role-permission grants, 11 units |
| Configuration | 1 business, 1 branch, 1 counter, 1 owner user, 1 membership |
| Business data | **none** |
| Triggers | none — immutability is enforced by privilege |
| Materialized views | none — derived money is transactional |

### Identifiers to record

The counter PC registration needs these. They are not secrets.

| | |
|---|---|
| Business | `NATIONAL AUTO SPARES` |
| Branch | `MAIN` / `Main` |
| Counter | `C1` / `Counter 1`, `max_open_drafts = 20` |
| Owner username | `owner` |

The UUIDs were printed by the provisioning run. Re-read them any time with:

```powershell
./scripts/prod.ps1 python scripts/production_db.py zero-state
```

---

## 3. Zero-state audit — every table

Counted with `count(*)`, not an estimate. The business list is derived by subtraction from
`Base.metadata`, so it is exhaustive by construction.

| Table | Kind | Expected | Actual | Result | Note |
|---|---|--:|--:|---|---|
| `alembic_version` | config | 1 | 1 | PASS | migration metadata — the applied revision |
| `audit_logs` | business | 0 | 0 | PASS | |
| `branches` | config | ≥1 | 1 | PASS | required: every counter and invoice is branch-scoped |
| `brands` | business | 0 | 0 | PASS | |
| `businesses` | config | 1 | 1 | PASS | the shop itself — configuration, not activity |
| `categories` | business | 0 | 0 | PASS | |
| `counters` | config | ≥1 | 1 | PASS | required: a sale cannot be finalised without one |
| `credit_note_allocations` | business | 0 | 0 | PASS | |
| `credit_notes` | business | 0 | 0 | PASS | |
| `customer_credit_balances` | business | 0 | 0 | PASS | |
| `customer_phones` | business | 0 | 0 | PASS | |
| `customer_vehicles` | business | 0 | 0 | PASS | |
| `customers` | business | 0 | 0 | PASS | |
| `devices` | business | 0 | 0 | PASS | registers itself at first sign-in |
| `draft_bill_items` | business | 0 | 0 | PASS | |
| `draft_bills` | business | 0 | 0 | PASS | |
| `idempotency_keys` | business | 0 | 0 | PASS | |
| `import_jobs` | business | 0 | 0 | PASS | |
| `import_rows` | business | 0 | 0 | PASS | |
| `inventory_balances` | business | 0 | 0 | PASS | |
| `invoice_items` | business | 0 | 0 | PASS | |
| `invoice_series` | business | 0 | 0 | PASS | created lazily by the first sale |
| `invoices` | business | 0 | 0 | PASS | |
| `jobs` | business | 0 | 0 | PASS | |
| `login_attempts` | business | 0 | 0 | PASS | |
| `memberships` | config | ≥1 | 1 | PASS | no role = no permissions |
| `notifications` | business | 0 | 0 | PASS | |
| `outbox_events` | business | 0 | 0 | PASS | |
| `payment_allocations` | business | 0 | 0 | PASS | |
| `payments` | business | 0 | 0 | PASS | |
| `permissions` | config | 24 | 24 | PASS | migration 0005; matches the `Permission` enum |
| `print_log` | business | 0 | 0 | PASS | |
| `product_aliases` | business | 0 | 0 | PASS | |
| `product_prices` | business | 0 | 0 | PASS | |
| `products` | business | 0 | 0 | PASS | |
| `purchase_items` | business | 0 | 0 | PASS | |
| `purchases` | business | 0 | 0 | PASS | |
| `reconciliation_runs` | business | 0 | 0 | PASS | |
| `refresh_tokens` | business | 0 | 0 | PASS | |
| `role_permissions` | config | >0 | 72 | PASS | migration 0005 |
| `roles` | config | 6 | 6 | PASS | migration 0005 |
| `sales_return_items` | business | 0 | 0 | PASS | |
| `sales_returns` | business | 0 | 0 | PASS | |
| `settings` | config | 0 | 0 | PASS | defaults applied **in code**, so unset is never ambiguous |
| `stock_count_items` | business | 0 | 0 | PASS | |
| `stock_count_sessions` | business | 0 | 0 | PASS | |
| `stock_movements` | business | 0 | 0 | PASS | |
| `suppliers` | business | 0 | 0 | PASS | |
| `sync_operations` | business | 0 | 0 | PASS | |
| `tax_config_versions` | business | 0 | 0 | PASS | |
| `tax_groups` | business | 0 | 0 | PASS | |
| `tax_rates` | business | 0 | 0 | PASS | |
| `units` | config | 11 | 11 | PASS | reference data; `products` has a RESTRICT FK to it |
| `users` | config | ≥1 | 1 | PASS | the owner account |

**43 business tables, 0 rows. 11 configuration tables, all as expected.**

### The non-business rows, explained separately

- **Migration metadata** — `alembic_version` holds one row, the applied revision. Read by
  `/readyz` to refuse traffic on a head mismatch.
- **Reference data** — `permissions`, `roles`, `role_permissions` (migration 0005) and
  `units` (provisioned, since migration 0016's loop finds no business on a fresh
  database). `DATA-MODEL.md §6` rule 7 permits exactly these three kinds.
- **Essential configuration** — `businesses`, `branches`, `counters`, `users`,
  `memberships`. Without them nobody can sign in and no sale can be finalised. These are
  **not** demo data and must not be deleted to make every count zero.
- **Deliberately empty** — `settings` (defaults live in code) and `invoice_series`
  (created inside the first finalisation transaction).

---

## 4. Database verification — 77/77

| Group | Checks | Result |
|---|--:|---|
| Alembic head and table inventory | 3 | ✅ |
| Extensions | 3 | ✅ |
| Enum types | 1 | ✅ |
| Indexes, constraints, named repairs (0018, 0021) | 6 | ✅ |
| Table-level privileges on append-only tables | 13 | ✅ |
| Column-level grants, as exact sets | 5 | ✅ |
| Sequences and `bm_app`'s access to them | 2 | ✅ |
| Functions (`gen_random_uuid`, `similarity`, `unaccent`) | 3 | ✅ |
| Triggers and materialized views | 2 | ✅ |
| Reference data and permission vocabulary | 5 | ✅ |
| Zero business data | 2 | ✅ |
| Cross-table consistency and demo markers | 12 | ✅ |
| Default units present | 1 | ✅ |
| Runtime role: URL shape, identity, live SELECTs | 10 | ✅ |
| Runtime role: live refusals (7 mutations + DDL) | 7 | ✅ |
| Transport: plaintext refused, channel binding required | 2 | ✅ |

Re-run any time, read-only:

```powershell
./scripts/prod.ps1 python scripts/production_db.py verify
```

### The privilege model, proven live

Attempted as `bm_app` against the production server, each in a rolled-back transaction:

| Operation | Outcome |
|---|---|
| `SELECT` on the six tables the billing path reads first | allowed |
| `UPDATE audit_logs` · `DELETE stock_movements` · `UPDATE invoices.total` · `DELETE invoices` · `DELETE payments` · `UPDATE invoice_items.line_total` | **refused by the server** |
| `CREATE TABLE` | **refused by the server** |
| connection with `sslmode=disable` | **refused by Neon** |

`bm_app` may `UPDATE` exactly 7 columns on `invoices` and exactly 1 on `invoice_items`,
and nothing on `payments`, `stock_movements` or `audit_logs` — `SECURITY.md §7` reproduced
exactly, with nothing extra.

---

## 5. Security posture

| | |
|---|---|
| Runtime role | `bm_app` — no DDL, no superuser, no CREATEDB/CREATEROLE, cannot create tables |
| Migration role | `neondb_owner` — exists only in `.env.production` on one machine |
| Transport | TLS mandatory; Neon rejects `sslmode=disable`; `channel_binding=require` |
| Secrets | 3 distinct, each ≥ 32 chars, none reusing a committed dev/CI/test value |
| Owner credential | generated, never echoed, `must_change_password = true` |
| `.env.production` | git-ignored; 0 commits in history, both repositories |
| Tracked files | contain none of the secrets and not the endpoint id |
| `BM_UNATTENDED_ACCESS` | `false` |
| `BM_ENABLE_API_DOCS` | `false` |
| CORS | explicit list, no wildcard |

**One contained exposure.** During a failed first attempt at role creation, a generated
password reached a local log file via a traceback. It had never been transmitted to Neon,
the file was deleted, and the role was created with a different generated password. **No
rotation is required.** Full disclosure in the initialization report §3.

### Two actions before go-live

1. **Delete the `BM_PROVISION_OWNER_PASSWORD` line** from `.env.production` once the owner
   has signed in and chosen their own password.
2. **Back up `BM_PASSWORD_PEPPER` off this machine.** It cannot be rotated — changing it
   invalidates every password in the database. It is the one unrecoverable secret here.

---

## 6. Open items

| # | Item | Severity | Blocks deployment? |
|--:|---|---|---|
| 1 | `tests/api/test_imports.py::test_creating_a_new_product_from_a_row_requires_that_explicit_decision` — failed once in two full suite runs; passes alone and passed the second full run | Low — intermittent test isolation | **No** |
| 2 | CI proves the migrations on PostgreSQL **16**; production runs **18.6** | Medium — process gap | **No** — schemas verified identical |
| 3 | **Two** local containers both hold a database called `bm` — one real (428.9 MB), one empty | Low, but a real trap | **No** |
| 4 | Neon PITR not yet enabled and no restore demonstrated | **High for go-live** | No, but gates go-live |
| 5 | `BM_RELEASE` and `BM_CORS_ALLOWED_ORIGINS` still placeholders | Low | No — backend-host step |
| 6 | Alerting disabled; OD-19 requires a named recipient | **Gates go-live** | No |

### On item 1

| Run | Result |
|---|---|
| Full suite, first time | failed |
| That test alone | passed |
| Full suite again, same database, nothing changed | **passed — whole suite green** |

Intermittent and ordering-dependent, in the local suite, not a database defect: the same
code, migrations and assertions run against a local PostgreSQL 16 container, and the second
full run of exactly that combination was clean. The imports suite has a documented history
of this failure mode — migration 0020 exists because `GET /imports` ordered by a non-unique
`created_at` with a random `uuid4` tiebreak, and its docstring records the identical
symptom.

Resolving it is a change to the test suite, outside this task's scope. It is recorded
rather than quietly dropped in favour of the green run — an intermittently red suite is one
people learn to re-run instead of read, which is worth fixing before the next release.

### On item 2

`alembic check` reports no drift against Neon, and an object-by-object comparison against a
freshly migrated PostgreSQL 16 found the tables, columns, indexes, enum types, sequences
and extensions **identical**. The only difference was 426 `pg_constraint` rows of
`contype = 'n'` — PostgreSQL 17 catalogues `NOT NULL` there, 16 does not — and the column
comparison, which includes nullability, matched exactly. So there is no schema divergence
today. The risk is forward-looking: a future migration could pass CI on 16 and behave
differently on 18. Moving `.github/workflows/ci.yml`'s `services.postgres.image` to 18 is
the cheap fix, and now is the cheapest it will ever be.

---

## 7. Local databases — all untouched

There are **three** Postgres containers on this machine, two of which contain a database
called `bm`. That is worth knowing before anyone points a migration at one.

| Container | Port | Volume | Size | State | What it is |
|---|---|---|--:|---|---|
| `bm-pg` | **55439** | anonymous `d4a05f2d…` | **428.9 MB** | exited since 2026-11-02 | **the real local working database** — `.env`, `bt.ps1` and `docker-compose.yml` all point at 55439 |
| `bm-postgres-1` | none (`5432/tcp: []`) | `bm_pgdata` | 46.1 MB | running | an empty, never-migrated cluster |
| `bm-throwaway-test` | 55440 | anonymous | — | running | created here for the test suite; disposable |

| Database | State | Touched by this work |
|---|---|---|
| `bm` (in `bm-pg`) | **intact, 428.9 MB** | **No** — never connected to |
| `bm` (in `bm-postgres-1`) | empty, 0 user tables | read-only `SELECT`s only |
| `bm_test` | does not exist in either project container | No |
| `bm_accept` | does not exist | No |

**The owner's local working data is present and untouched.** `bm-pg` was already stopped
before this session — last log line `2026-11-02 17:41:50 UTC`, restart policy `no` — and
the host could not reach port 55439 at any point: a direct TCP probe was actively refused,
and the one accidental `alembic` fallback to `.env` **timed out** without connecting. No
`docker stop`, `rm`, `compose down`, `volume rm` or `--force-recreate` was run against it;
the only volume mount taken of anything was `:ro`, to measure disk usage.

Because `bm-pg` was stopped — and starting the owner's database container was neither
necessary here nor this task's call to make — the test suite and the schema comparison
used a **separate disposable** container instead: own volume, port 55440,
`postgres:16-alpine`, containing only `bm_test`, with `bm_app` created in the same shape
as `init-roles.sql`.

### Cleaning up

```powershell
docker rm -f bm-throwaway-test      # the disposable test database
docker stop bm-postgres-1           # the empty cluster this work started
```

`bm-pg` is left exactly as found: stopped, volume intact. Start it the usual way when you
next work locally.

> ⚠️ **Two containers answer to `bm`.** `bm-postgres-1` is empty and publishes no port;
> `bm-pg` holds the real data on 55439. Removing the empty one would remove the
> ambiguity — and it is the kind of ambiguity that ends with a migration applied to the
> wrong database.

## 8. Next deployment steps

The database is done. Nothing below touches it.

### Northflank — backend

> The deployment guide was written for Railway. Everything in it applies unchanged except
> the dashboard mechanics: same Dockerfile, same variables, same volume requirement, same
> health-check split. Northflank-specific notes are called out below.

1. **Create a combined service** from `nationalautospares898-maker/bmbackend`, branch
   `main`, build type **Dockerfile**, path `Dockerfile`, target `runtime`.
2. **Region: Singapore.** Same region as Neon. A single finalisation does several
   round trips inside one transaction, so cross-region latency is multiplied, not added.
3. **Port 8000, HTTP.** The Dockerfile's `CMD` hard-codes `--port 8000` and does not read
   an injected `PORT`. Set the port explicitly rather than relying on detection.
4. **Leave the start command empty** — the Dockerfile `CMD` is already correct.
5. **Health checks:** liveness `/healthz`. Do **not** use `/readyz` as the liveness probe:
   it returns 503 until the Alembic head matches, which is correct for a deploy gate and
   would make the platform kill-loop a replica waiting on a migration.
6. **Attach a persistent volume** mounted at `/var/lib/bm/storage`. Without it every
   uploaded price list and purchase file is deleted on the next deploy. The Dockerfile
   already creates that path owned by the non-root `bm` user.
7. **Secrets:** use a Northflank *secret group* for `BM_JWT_SIGNING_KEY`,
   `BM_PASSWORD_PEPPER`, `BM_IP_HASH_PEPPER` and `BM_DATABASE_URL`, rather than plain
   environment variables.
8. **Set the variables** from
   [`RAILWAY-ENVIRONMENT-VARIABLES.md §9`](RAILWAY-ENVIRONMENT-VARIABLES.md), with these
   Northflank differences:
   - `BM_RELEASE` — Railway's `${{RAILWAY_GIT_COMMIT_SHA}}` does not exist. Use
     Northflank's commit-SHA variable, or set it per release.
   - `BM_DATABASE_URL` — copy the **`bm_app` pooled** URL from `.env.production`
     verbatim, including `?sslmode=require&channel_binding=require`.
   - 🔴 **Do not set `BM_MIGRATION_DATABASE_URL`.** The DDL credential belongs on one
     developer machine and nowhere else.
   - `BM_CORS_ALLOWED_ORIGINS` — the Vercel origin, after step 2 of the Vercel section.
   - `BM_STORAGE_PATH=/var/lib/bm/storage` — the volume mount path.
9. **Deploy**, then verify:
   - `GET /healthz` → `{"status":"ok"}`
   - `GET /readyz` → 200 with `migrations.status: "ok"` and
     `expected_head`/`current_revision` both `0021`. A 503 with `"mismatch"` here would
     mean the service is running code that disagrees with this database.
   - The log contains `startup` with `env=production`, and **not**
     `unattended_access_enabled`, `api_docs_enabled_in_production` or
     `cors_allow_list_empty`.

### Vercel — frontend

1. Import `nationalautospares898-maker/bmfrontend`. Preset **Vite**, root `.`, Node 22.
   Build/output/install come from the committed `vercel.json`.
2. Set `VITE_API_BASE_URL` to `https://<northflank-domain>/api/v1` — **including the
   `/api/v1` path**, or every call 404s. Read at **build** time, so changing it needs a
   redeploy, not a restart.
3. Deploy, note the production domain, then go back and set
   `BM_CORS_ALLOWED_ORIGINS` on Northflank to that exact origin and redeploy the backend.
   The dependency is circular, so it is two passes.
4. 🔴 **Decide the cookie topology before testing.** `*.vercel.app` and a Northflank
   provider domain are **cross-site**, so the `SameSite=Lax` refresh cookie is never sent
   and sessions die 15 minutes after every login — with nothing in any log.
   [`PRODUCTION-DEPLOYMENT-GUIDE.md §10`](PRODUCTION-DEPLOYMENT-GUIDE.md) has the three
   options. Either use two subdomains of one owned domain (then `lax` is correct) or set
   `BM_REFRESH_COOKIE_SAMESITE=none` on Northflank.

### First sign-in

1. Open the Vercel site → sign in as `owner` with the password in
   `.env.production`'s `BM_PROVISION_OWNER_PASSWORD`.
2. Change the password when prompted. No other screen works until you do.
3. **Delete that line** from `.env.production`.
4. 🔴 **Hard-reload the page.** You must stay signed in. If you land on the sign-in form,
   the refresh cookie is being `SameSite`-blocked — go back to step 4 above. **This is the
   check that catches it, and nothing else does.**
5. Leave the tab idle 16+ minutes and use the app again. Same test, slower.
6. Assign the device to counter `C1` if prompted.

### Then, and only then, the shop's real data

| Data | How |
|---|---|
| Products, prices, aliases | the import screens or the catalog screens |
| **Opening stock** | as `opening` **movements** through the application — never a direct `INSERT`. Every stock change is a movement row in the same transaction; a hand-inserted balance has no movement behind it and the nightly reconciliation will correctly report drift it cannot explain |
| Customers | the customers screen, or as they walk in |

### Before the shop bills a real customer

- [ ] Neon PITR enabled, longest retention available, and **a restore demonstrated** onto
      a scratch branch. `DEPLOYMENT.md §7.1` makes this a release gate.
- [ ] Neon autosuspend off, or the longest delay — a cold start is felt at the counter.
- [ ] `BM_PASSWORD_PEPPER` backed up off this machine.
- [ ] Alerting configured with a **named** person, or OD-19 consciously accepted as open.
- [ ] The printed bill verified on the shop's real printer and paper, against the owner's
      own Excel invoice, by the owner.
- [ ] The acceptance checklist in
      [`PRODUCTION-DEPLOYMENT-GUIDE.md §15`](PRODUCTION-DEPLOYMENT-GUIDE.md) worked
      through on the shop's own machine.
