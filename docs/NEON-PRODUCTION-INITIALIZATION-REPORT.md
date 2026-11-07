# Neon production database — initialization report

| Field | Value |
|---|---|
| Date | 2026-11-03 |
| Outcome | **Database initialized, verified, and at zero business state.** |
| Neon project | `national auto spares` |
| Neon branch | `production` |
| Database | `neondb` |
| Region | AWS Singapore (`ap-southeast-1`) |
| PostgreSQL | **18.6** (`ac70818`) |
| Alembic revision | **`0021`** — the code's single head |
| Automated checks | **77 / 77 passed** (`production_db.py verify`) |
| Zero-state audit | **54 / 54 tables as expected**; all 43 business tables at 0 rows |
| Deployed | **Nothing.** No GitHub push, no commit, no backend or frontend deploy. |

This is the record of what was executed and what was observed. The operating procedure is
[`PRODUCTION-DEPLOYMENT-GUIDE.md`](PRODUCTION-DEPLOYMENT-GUIDE.md); the acceptance state is
[`PRODUCTION-DATABASE-READINESS.md`](PRODUCTION-DATABASE-READINESS.md).

**No secret appears in this document.** No password, no connection string, no endpoint id,
no JWT key, no pepper. The Neon endpoint id is deliberately omitted even though it is not
a credential: it is the routable address of the shop's database, and a report is the wrong
place for it.

---

## 1. Initial state — read-only inspection before any write

Classified **A — truly fresh database**.

| Observation | Value |
|---|---|
| Tables in `public` | **0** |
| `alembic_version` | absent — never migrated |
| Extensions | none of `pg_trgm`, `unaccent`, `citext` |
| Enum types | 0 (code defines 30) |
| Role `bm_app` | **absent** |
| Business data | none — nothing to classify |

No pre-existing business, customer, product, inventory, sales, payment or invoice data was
found, so the §5 stop condition did not arise and no write was gated on it.

The inspection ran as `neondb_owner` on the **direct** endpoint and opened no writing
transaction.

---

## 2. Configuration corrections made before connecting

Two problems in `.env.production` would each have stopped the work, and both were in the
operator-supplied half rather than in the project:

**The driver was missing from both URLs.** Neon's console hands out
`postgresql://…`, and SQLAlchemy maps a bare `postgresql://` to **psycopg2**, which this
project does not install — it pins `psycopg[binary]>=3.2`. Every connection failed with a
driver import error. Both URLs were rewritten to `postgresql+psycopg://`; nothing else
about either string was touched, including the `?sslmode=require&channel_binding=require`
query. `.env.production.example` already documents this conversion as a required step.

**`BM_DATABASE_URL` used the admin role.** It pointed at `neondb_owner`, which is correct
only as a transitional state — `bm_app` did not exist yet — but would have run the API as
the role that owns the schema, silently discarding the entire append-only privilege model.
Repointed at `bm_app` (§3).

### Target validation, before any connection was opened

| Check | Migration URL | Runtime URL |
|---|---|---|
| Scheme | `postgresql+psycopg` | `postgresql+psycopg` |
| Role | `neondb_owner` | `bm_app` |
| Database | `neondb` | `neondb` |
| Host suffix | `aws.neon.tech` | `aws.neon.tech` |
| Region | `ap-southeast-1` | `ap-southeast-1` |
| Endpoint | **direct** (required by Alembic) | **pooled** (`-pooler`) |
| `sslmode` | `require` | `require` |
| `channel_binding` | `require` | `require` |
| Points at a local database | no | no |
| Same Neon endpoint/branch as the other | yes | yes |

---

## 3. Application role `bm_app`

Created by running the committed `scripts/neon-init-roles.sql` — **before** the
migrations, which is the load-bearing part of the ordering. Every `GRANT`/`REVOKE` in
migrations 0004, 0007, 0011 and 0013 is wrapped in `_app_role_exists()`; with the role
absent, each migration **skips its grants and reports success**, producing a complete
schema with no append-only protection and nothing anywhere reporting it.

Neon permits this with no special request: `neondb_owner` is a member of
`neon_superuser`, which carries `CREATEROLE`.

### Role attributes — all 11 verified

| Check | Result |
|---|---|
| `bm_app` can log in | PASS |
| is **not** a superuser | PASS |
| cannot `CREATE DATABASE` | PASS |
| cannot `CREATE ROLE` | PASS |
| cannot bypass row-level security | PASS |
| has no replication privilege | PASS |
| may `CONNECT` to `neondb` | PASS |
| has `USAGE` on schema `public` | PASS |
| has **no** `CREATE` on schema `public` | PASS |
| schema `public` is owned by `pg_database_owner`, not `bm_app` | PASS |
| default privileges recorded for `bm_app` (2 rows) | PASS |

The password was generated in-process, written only into the git-ignored
`.env.production`, and never printed, logged or passed on a command line. The committed
`.sql` file still contains only its placeholder — asserted before every run, because a
substituted password in a tracked file is one `git add -A` from a public repository.

> **One incident during this work, disclosed in full.** The first attempt failed at
> SQLAlchemy *compile* time: `text()` scans for `:name` bind parameters with a regex that
> does not understand SQL comments, and `neon-init-roles.sql` quotes the Python guard it
> warns about — which contains `rolname = :role`. No statement reached Neon. The
> traceback, however, printed the substituted SQL into a captured log file on disk,
> including the generated password. That file was deleted immediately. The password had
> never been sent to the server, so it was never a live credential, and the role was later
> created with a freshly generated one. The tool now uses `exec_driver_sql`, which hands
> the SQL to the driver without that parsing, and stderr is no longer captured to disk for
> this command.

---

## 4. Migrations

`alembic upgrade head` via the **direct** endpoint as `neondb_owner`, through the guarded
wrapper. Revisions `0001` → `0021`, each in its own transaction
(`transaction_per_migration=True`), so a failure would have left a known revision rather
than a half-applied schema.

| Result | Value |
|---|---|
| `alembic current` | `0021 (head)` |
| Pending migrations | none |
| Errors | none |
| `alembic stamp` used | **no** — every revision actually ran |
| Tables created | 54 (53 ORM + `alembic_version`) |
| Enum types | 30 of 30 |
| Indexes | 136 |
| Constraints | 54 primary key, 27 unique, 152 foreign key, 39 check |
| Extensions | `pg_trgm` 1.6, `unaccent` 1.1, `citext` 1.8 |
| Sequences | 1 (`import_jobs_ordinal_seq`) |
| Triggers | 0 — immutability is by privilege, not trigger |
| Materialized views | 0 — derived money is transactional, never cached |

### PostgreSQL 18.6, where the project targets 16

Worth stating plainly because it is a real difference from everything that has been
tested: Neon provisioned **18.6**, while `docker-compose.yml`, `.github/workflows/ci.yml`
and `CLAUDE.md §3` all specify PostgreSQL 16.

It was verified rather than assumed, two ways:

1. **`alembic check` against Neon** → *"No new upgrade operations detected."* The ORM
   metadata and the migrated 18.6 schema agree exactly, with zero drift.
2. **Object-by-object comparison against a freshly migrated PostgreSQL 16** — same 21
   revisions, both databases built from scratch:

   | Object kind | Neon 18.6 | Local 16.14 | Verdict |
   |---|--:|--:|---|
   | Tables | 54 | 54 | identical |
   | Columns (name, type, nullability, default) | 598 | 598 | identical |
   | Indexes (full definition) | 136 | 136 | identical |
   | Enum types (with ordered labels) | 30 | 30 | identical |
   | Sequences | 1 | 1 | identical |
   | Extensions | 4 | 4 | identical |
   | Constraints | 698 | 272 | **see below** |

   The constraint count differs by exactly 426, and all 426 are `contype = 'n'`.
   PostgreSQL 17 began cataloguing `NOT NULL` as a row in `pg_constraint`; 16 records it
   only in `pg_attribute.attnotnull`. `698 − 426 = 272`, precisely the PG16 figure — and
   the **columns** comparison, which includes `is_nullable`, is identical. So this is a
   catalogue-representation difference between server versions, not a schema difference.

**Nothing in the stack requires a version decision today.** The features the design
depends on — `FOR UPDATE SKIP LOCKED`, `NUMERIC`, partial indexes, native enums,
column-level privileges, `text_pattern_ops`, `gin_trgm_ops`, `gen_random_uuid()` — are all
present and were each verified. The gap to record is that **CI still proves the migrations
on 16 and production runs 18**, so a future migration could pass CI and behave differently
in production. Closing it means moving CI's `services.postgres` image to 18, which is a
separate change and is listed as a recommendation rather than done here.

---

## 5. Essential configuration provisioned

Five configuration rows plus reference units. Nothing else.

| Table | Rows | What |
|---|--:|---|
| `businesses` | 1 | `NATIONAL AUTO SPARES`, `AUTO NAGAR, HYDERABAD`, two phone numbers, `Asia/Kolkata`, financial year starts month 4 |
| `branches` | 1 | `MAIN` / `Main` |
| `counters` | 1 | `C1` / `Counter 1`, `max_open_drafts = 20` |
| `users` | 1 | `owner` / `Owner`, Argon2id hash, `must_change_password = true` |
| `memberships` | 1 | the `owner` system role, all branches |
| `units` | 11 | `NOS PCS SET PAIR BOX LTR ML KG GM MTR FT` |

All in one transaction: a business with no owner, or an owner with no membership, is
unusable and harder to diagnose than nothing at all.

**The identity values are the owner's own, not invented.** They are read from rows 1–3 of
`Sheet1` of the owner's estimate workbook `ANAS_1234_20260606_231456.xlsx`, and are the
same constants `scripts/set_business_identity.py` already uses. `legal_name` and
`tax_identifier` are **NULL**, deliberately: the workbook shows a trading name rather than
a registered legal name, no GSTIN appears anywhere in it, and OD-4 is still open. Guessing
either is the precise mistake that script exists to prevent.

### The owner credential

- Generated as 32 url-safe characters, never echoed, never on a command line.
- Stored **only** in the git-ignored `.env.production`, as
  `BM_PROVISION_OWNER_PASSWORD`, with an instruction to delete that line once used.
- It is **not** any development, demo, seed or previously-exposed password. Verified
  against the committed local, CI and test constants and against
  `seed_e2e_tenant.py`'s credential.
- `must_change_password = true` blocks every non-auth endpoint until the owner replaces it
  (`api/deps.py`), and the counter application renders the change-password screen for that
  state (`src/app/AppShell.tsx`). The go-live requirement "all seeded credentials rotated;
  forced password change verified" is therefore satisfied by construction.

### Why `units` is provisioned here, and why that is reference data

Migration 0016 seeds units with `for business_id in businesses` — a loop that runs **zero
times** on a fresh database, because the business does not exist yet. The migration's own
docstring says so and points at `CatalogService.ensure_default_units`, reachable at
`POST /api/v1/catalog/units:ensure-defaults`.

Leaving it to that call would have left a database where the shop cannot create its first
product: `products` has a `RESTRICT` foreign key to `units`. The remedy would have been an
undocumented API call nobody would think to make.

`DATA-MODEL.md §6` rule 7 permits exactly three kinds of seed — permissions, system roles
and units — and this is the third. The tuple is imported from
`domain/catalog/units.py`, the same one migration 0016 and the service both read, and the
ids are `uuid5` under migration 0016's namespace, so re-running that migration updates
these rows instead of creating a second set.

### What was deliberately **not** created

No product, customer, supplier, stock figure, invoice, draft, sale, payment, credit note,
purchase or return. This is structural, not a matter of care: an AST audit of
`scripts/production_db.py` confirms it contains **five** `INSERT` statements, into
`businesses`, `branches`, `counters`, `users` and `memberships` (plus the units loop), and
**no** `UPDATE`, `DELETE`, `TRUNCATE` or `DROP` statement anywhere. There is no code path
that could write business data.

`settings` is **0 rows**, which is correct: defaults are applied in code
(`domain/settings/defaults.py`) precisely so an unset key is never ambiguous and a fresh
database behaves identically to a configured one. `invoice_series` is **0 rows**, also
correct: a series is created lazily inside the first finalisation transaction
(`domain/sales/numbering.py`), so the first sale after each 1 April cannot fail for want of
a row.

---

## 6. Verification — 77 checks, all passing

`production_db.py verify`. The tool was extended during this work; §8 of the task asked for
checks it did not have.

### Added to the verification tool

| Area | What was added |
|---|---|
| Sequences | the exact set, plus `bm_app`'s `USAGE` on each |
| Functions | `gen_random_uuid()`, `similarity(text,text)`, `unaccent(text)` are callable |
| Triggers | none exist (enforcement is by grant, not trigger) |
| Materialized views | none exist (derived money is transactional) |
| Permission vocabulary | the `permissions` table equals the `Permission` enum exactly |
| Orphans | `role_permissions` → `permissions`; branches/counters/users/memberships/units → their parents |
| Tenant coherence | a counter's branch belongs to the counter's own business |
| Demo markers | no business name matching `DEMO`/`E2E`/`NOT A REAL`/`TEST`; no `demo-owner`/`e2e-owner`/`test`/`admin` username |
| Credentials | every active user has a hash, and every hash is `$argon2id$` |
| Column grants | asserted as an **exact set** rather than by spot check |
| Runtime role | a real connection as `bm_app`, with real refusals |
| Transport | the server is made to refuse plaintext |

### Three corrections the new checks forced

**The column-grant check was a spot check, and a spot check cannot do this job.** It
sampled three columns on `invoices`. A migration adding `GRANT UPDATE (total)` — exactly
the failure the column-level model exists to prevent — would have passed it. It now
compares the whole granted set per table. Live result:

| Table | `bm_app` may `UPDATE` | Expected |
|---|---|---|
| `invoices` | `status`, `cancelled_at`, `cancelled_by_user_id`, `cancellation_reason`, `amount_paid`, `payment_status`, `updated_at` | exactly those 7 |
| `invoice_items` | `returned_quantity` | exactly that 1 |
| `payments` | *(none)* | none |
| `stock_movements` | *(none)* | none |
| `audit_logs` | *(none)* | none |

That is `SECURITY.md §7` reproduced exactly, with nothing extra.

**One check named a column that does not exist.** It probed `invoices.total_amount`; the
column is `invoices.total`. The probe raised `UndefinedColumn`, which an "did it throw?"
test reads as a successful refusal — a check that passes for the wrong reason. Corrected,
and extended to `DELETE invoices`, `DELETE payments` and
`UPDATE invoice_items.line_total`.

**The TLS check was measuring the wrong network hop.** It read
`pg_stat_ssl WHERE pid = pg_backend_pid()`, which reports `ssl = false` on **both** the
pooled and the direct Neon endpoint — every Neon endpoint sits behind Neon's own proxy,
which terminates TLS and speaks to the Postgres compute over its internal network, so the
backend's view is never the client's leg. Trusting it reported an encryption failure on a
correctly encrypted connection. The obvious alternative,
`psycopg.ConnectionInfo.ssl_in_use`, does not exist — that is a psycopg2 API and psycopg
3.3 exposes no SSL attribute at all.

It is now an experiment rather than an observation: connect with `sslmode=disable` and
require the attempt to **fail**. Neon answers
`ERROR: connection is insecure (try using sslmode=require)`. That is the server refusing
plaintext, which is a stronger statement than any client flag because it holds for every
client that will ever connect — including the deployed backend.

### The runtime role, proven against the live server

Not asked of the catalogue — actually attempted, as `bm_app`, over the pooled endpoint,
each inside a rolled-back transaction so no row and no sequence value was consumed:

| Attempt | Result |
|---|---|
| `SELECT` on `permissions`, `roles`, `units`, `businesses`, `branches`, `counters` | **allowed** (6/6) |
| `UPDATE audit_logs` | **refused by the server** |
| `DELETE FROM stock_movements` | **refused by the server** |
| `UPDATE invoices SET total = total` | **refused by the server** |
| `DELETE FROM invoices` | **refused by the server** |
| `DELETE FROM payments` | **refused by the server** |
| `UPDATE invoice_items SET line_total = line_total` | **refused by the server** |
| `CREATE TABLE …` | **refused by the server** |
| connection with `sslmode=disable` | **refused by Neon** |

NFR-DATA-006 is enforced in production by privilege, demonstrated rather than assumed.

---

## 7. Zero-state audit

`production_db.py zero-state` counts every table in `Base.metadata` individually with
`count(*)` — not a `reltuples` estimate, because this figure is the evidence. The business
list is computed by **subtraction** from the ORM metadata, so it is exhaustive by
construction and a table added by a future migration is treated as business data until
somebody deliberately classifies it otherwise.

**43 business tables counted. 0 rows total. 11 configuration tables, all as expected.**

The full per-table table is in
[`PRODUCTION-DATABASE-READINESS.md §3`](PRODUCTION-DATABASE-READINESS.md).

Cross-table consistency was checked as well, because zero rows is not the same as a
coherent configuration — a business with no owner would pass a row count and leave a
database nobody can sign in to:

| Check | Result |
|---|---|
| every branch belongs to an existing business | PASS |
| every counter's branch belongs to the counter's own business | PASS |
| every user belongs to an existing business | PASS |
| every user has at least one membership | PASS |
| every membership points at an existing role | PASS |
| at least one user holds the `owner` role | PASS |
| every unit belongs to an existing business | PASS |
| no business name looks like demo or fixture data | PASS |
| no seeded username survived | PASS |
| every active user has a password hash | PASS |
| every password hash is Argon2id | PASS |
| no `role_permissions` row points at a missing code | PASS |
| `permissions` matches the `Permission` enum exactly (24 = 24) | PASS |

---

## 8. Tests

### Against Neon production — read-only only

Permission probes and `SELECT`s, as §11 requires. **No test fixture, no seeder and no test
suite was pointed at Neon**, and no write test was performed: inserting and deleting a
test row would leave `audit_logs` and sequence residue and would spend the zero-state
guarantee to prove something the privilege probes already prove.

### Against an isolated local database

The project's own local Postgres container turned out to have **no published port**
(`5432/tcp: []`), so the host could not reach it. Rather than recreate that container — its
volume is the one the owner's `bm` database lives in — a **separate disposable** container
was used: its own volume, port 55440, `postgres:16-alpine`, containing only `bm_test`,
with `bm_app` created in the same shape as `init-roles.sql`.

| Step | Result |
|---|---|
| `alembic upgrade head` on the throwaway | `0021 (head)` |
| `alembic check` (ORM vs migrated schema, PG16) | *No new upgrade operations detected* |
| Full `pytest` suite, run 1 | **1 failure** in `tests/api/test_imports.py` |
| Full `pytest` suite, run 2 (same database, no changes) | **green — exit 0, no failures** |

The suite covers unit, API, integration, concurrency, invariant, structural and property
layers, including the privilege tests that attempt real mutations as `bm_app`.

### The one failing test — confirmed non-deterministic

```
FAILED tests/api/test_imports.py::test_creating_a_new_product_from_a_row_requires_that_explicit_decision
```

Three runs establish what it is:

| Run | Result |
|---|---|
| Full suite, first time | **failed** |
| That test alone | **passed** |
| Full suite again, same database, nothing changed | **passed — whole suite green** |

So it is a genuinely intermittent, ordering-dependent flake in the local test suite, not a
defect in the production database or its schema. It has nothing to do with Neon: the same
code, the same migrations and the same assertions run against a local PostgreSQL 16
container, and the second full run of exactly that combination was clean.

The imports suite has a documented history of this exact failure mode — migration 0020
exists because `GET /imports` ordered by a non-unique `created_at` with a random `uuid4`
tiebreak, and its docstring records that "the full test suite reproduced it … while the
same test passed in isolation."

**It is not fixed here**, because diagnosing a test-isolation flake is a change to the test
suite rather than to the production database, and this task's scope is the database.
Reporting the clean second run without the first would have been the dishonest version of
this result, so both are recorded. Tracked as an open item in
[`PRODUCTION-DATABASE-READINESS.md §6`](PRODUCTION-DATABASE-READINESS.md).

### Not run, and why

| Suite | Why |
|---|---|
| `tests/load` (`-m slow`) | Needs a seeded 100 000-product catalogue via `seed_demo.py`. Would be meaningless on a 4-row database and must never run against Neon. |
| Frontend E2E / full-stack | Needs a running API and a browser; this task is the database. Covered by `PRODUCTION-DEPLOYMENT-GUIDE.md §15`. |
| Any write test against Neon | Deliberate — see above. |

---

## 9. Security verification

| Check | Result |
|---|---|
| `bm_app` is the runtime role, not `neondb_owner` | PASS |
| Admin role is used only for migrations, from one machine | PASS |
| Runtime endpoint is pooled; migration endpoint is direct | PASS |
| Both URLs require `sslmode=require` | PASS |
| Both URLs require `channel_binding=require` (SCRAM channel binding, TLS-only) | PASS |
| Neon **refuses** an unencrypted connection | PASS — verified by attempting one |
| `BM_ENV=production` | PASS |
| API docs disabled | PASS |
| Unattended access disabled | PASS |
| CORS is not a wildcard | PASS |
| Three secrets, all distinct, each ≥ 32 characters | PASS |
| No secret reuses a committed dev / CI / test value | PASS |
| Owner password is not a known seeded credential, ≥ 20 characters | PASS |
| `.env.production` is git-ignored | PASS |
| `.env.production` has **never** appeared in git history | PASS — 0 commits, both repositories |
| The Neon endpoint id appears in **no** tracked file and **no** history blob | PASS |
| None of the three secrets appears in any tracked file | PASS |
| The owner password appears in no tracked file | PASS |
| `neon-init-roles.sql` still holds only its placeholder | PASS |
| Log format is `json`, through the redaction filter | PASS |

**One exposure occurred and was contained**, disclosed in §3: a generated role password
reached a local log file in a traceback. It had never been sent to Neon, the file was
deleted, and a different password was generated for the role that was actually created.
**No rotation is required**, and the tool no longer captures that stderr to disk.

Two values in `.env.production` are still placeholders: `BM_RELEASE` and
`BM_CORS_ALLOWED_ORIGINS`. Neither affects the database; both belong to the backend-host
step.

### Hardening applied during this work

`prod.ps1` and `production_db.py` both printed the full Neon endpoint id in a banner
emitted by **every** production command — so it accumulated in terminal scrollback and in
any pasted output. It is not a credential, but it is the routable address of the shop's
database and there is no operational reason to see it; what an operator needs to confirm
is the role, the database, the region and whether the endpoint is pooled, all of which
survive redaction. Both now print `ep-<redacted>`.

---

## 10. Local databases — all untouched

There are **three** Postgres containers on this machine, and they are easy to confuse.
Establishing which is which took two passes; the first conclusion recorded here was wrong
and is corrected below rather than quietly replaced.

| Container | Port | Volume | Size | State | Role |
|---|---|---|--:|---|---|
| `bm-pg` | **55439** (published) | anonymous `d4a05f2d…` | **428.9 MB** | exited, since 2026-11-02 | **the real local working database** — this is what `.env` and `bt.ps1` point at |
| `bm-postgres-1` | none (`5432/tcp: []`) | `bm_pgdata` | 46.1 MB | running | an empty cluster from `docker-compose.yml`, never migrated |
| `bm-throwaway-test` | 55440 | anonymous | — | running | created by this work for the test suite; disposable |

| Database | State | Touched by this work |
|---|---|---|
| `bm` (in `bm-pg`) | **intact, 428.9 MB** | **No** — never connected to; the container was already stopped |
| `bm` (in `bm-postgres-1`) | empty, 0 user tables, never migrated | read-only `SELECT`s only |
| `bm_test` | does not exist in either project container | No |
| `bm_accept` | does not exist | No |

### The correction

This report initially stated that "the local `bm` database is empty". That was measured
against **`bm-postgres-1`**, which is not the container the project uses. The container
that publishes port 55439 — the port in `.env`, in `bt.ps1` and in
`docker-compose.yml` — is **`bm-pg`**, and its volume is intact at 428.9 MB. Its own log
shows it serving the real application (inventory inserts, product foreign keys, and the
privilege suite's `CREATE TABLE should_not_exist` probe).

So the owner's local working data is **present and untouched**. The empty `bm` is a
second, unused cluster.

### Why nothing here touched it

- `bm-pg` was **already stopped before this session**: its last log line is
  `2026-11-02 17:41:50 UTC`, and its restart policy is `no`, so it did not come back when
  the Docker engine was started. The `FinishedAt` timestamp of `2026-11-03T13:54:40Z` is
  when the engine reconciled its state during startup, not when it was running.
- No connection was ever opened to it. The host could not reach port 55439 at any point in
  this session — a direct TCP probe was actively refused — and the one accidental attempt
  (an `alembic` invocation that fell back to `.env`) **timed out** without connecting.
- No `docker stop`, `docker rm`, `docker compose down`, `docker volume rm` or
  `--force-recreate` was run against it. `docker compose up -d postgres` started
  `bm-postgres-1`, a different container.
- Its volume is anonymous and was never mounted writable by anything here; the only mount
  taken of any volume was `:ro` to measure disk usage.

### Why the comparison used a throwaway instead

`bm-pg` being stopped meant the §4 schema comparison could not use it — and starting the
owner's database container was not necessary for this task and not this task's call to
make. A fresh PostgreSQL 16 container was used instead, which is the better comparison
anyway: both sides were built from the same 21 revisions, so the only remaining variable
is the server version, which is exactly the question being asked.

### Worth the owner's attention, separately from this deployment

`bm-postgres-1` and its `bm_pgdata` volume were both created `2026-10-31T13:16:06Z` — the
date `tests/conftest.py` and `.gitignore` record for the incident in which "a bare
`uv run pytest` truncated the demo database … and there was no backup". It looks like a
replacement cluster created that day and then abandoned when `bm-pg` was used instead.
Two containers both answering to the name `bm` is a trap worth removing before someone
points a migration at the wrong one.

---

## 11. Changes made to the repository

No commit, no push. All changes are in the working tree.

| File | Change |
|---|---|
| `scripts/production_db.py` | `create-app-role` and `zero-state` subcommands; ~30 new verification checks; three check corrections (§6); endpoint-id redaction; `exec_driver_sql` for the role script; units in `provision` |
| `scripts/prod.ps1` | endpoint-id redaction in the banner |
| `.env.production` *(git-ignored)* | driver scheme corrected on both URLs; `BM_DATABASE_URL` repointed at `bm_app`; `BM_PROVISION_OWNER_PASSWORD` added |
| `docs/NEON-PRODUCTION-INITIALIZATION-REPORT.md` | this document |
| `docs/PRODUCTION-DATABASE-READINESS.md` | new |

`scripts/neon-init-roles.sql` is **unchanged** and still holds only its placeholder.

---

## 12. Recommendations, in priority order

1. **Delete the `BM_PROVISION_OWNER_PASSWORD` line** from `.env.production` once the owner
   has signed in and chosen their own password. It is a one-time bootstrap credential.
2. **Back up `BM_PASSWORD_PEPPER`** somewhere outside this machine and outside the backend
   host, before go-live. It cannot be rotated: changing it invalidates every password in
   the database, including the owner's. This is the single unrecoverable secret in the
   system.
3. **Move CI's Postgres image to 18** (`.github/workflows/ci.yml`,
   `services.postgres.image`). Production runs 18.6 and CI proves the migrations on 16; a
   future migration could pass CI and behave differently in production. The schemas are
   identical today, which is the right moment to close the gap cheaply.
4. **Remove the empty `bm-postgres-1` container** (§10). Two containers answering to the
   name `bm` — one with 428.9 MB of real data on port 55439, one empty with no published
   port — is the kind of ambiguity that ends with a migration applied to the wrong
   database.
5. **Enable Neon point-in-time restore** and the longest retention your plan allows, then
   **demonstrate a restore** onto a scratch branch. `DEPLOYMENT.md §7.1` makes this a
   release gate: an untested backup is treated as no backup.
6. **Turn off Neon autosuspend**, or set the longest delay available. A suspended compute
   adds a cold start to the first request of the morning, which is felt at a counter with a
   customer waiting.
7. **Resolve the imports test flake** (§8) before the next release. It is intermittent —
   one failure in two full runs — and an intermittently red suite is one people learn to
   re-run rather than read.
8. **Consider `sslmode=verify-full`.** `require` encrypts but does not authenticate the
   server. `verify-full` failed here only for want of a local root certificate bundle — a
   client-side configuration matter, not a server weakness. Worth configuring on the
   backend host, where the CA bundle is present.
