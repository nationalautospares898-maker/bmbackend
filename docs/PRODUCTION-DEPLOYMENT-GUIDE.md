# Production deployment guide — National Auto Spares

Neon PostgreSQL · Railway (API) · Vercel (counter application)

| | |
|---|---|
| Status | **Prepared, not executed.** Nothing has been provisioned, migrated, pushed or deployed. |
| Prepared | 2026-11-03 |
| Supersedes | nothing. `DEPLOYMENT.md` remains the provider-neutral design (environments, backups, rollback, scaling, go-live gate). This document is the concrete Neon/Railway/Vercel procedure. Where they differ on a provider detail, this one is current; where they differ on a *rule*, `DEPLOYMENT.md` and `CLAUDE.md` win. |

Companion documents:
[`PRODUCTION-ENVIRONMENT-VARIABLES.md`](PRODUCTION-ENVIRONMENT-VARIABLES.md) (master
variable reference) ·
[`RAILWAY-ENVIRONMENT-VARIABLES.md`](RAILWAY-ENVIRONMENT-VARIABLES.md) ·
[`../../bmfrontend/docs/VERCEL-ENVIRONMENT-VARIABLES.md`](../../bmfrontend/docs/VERCEL-ENVIRONMENT-VARIABLES.md)
· [`SECURITY.md`](SECURITY.md) · [`DEPLOYMENT.md`](DEPLOYMENT.md)

---

## Contents

1. [Production architecture](#1-production-architecture)
2. [Neon setup and migration](#2-neon-setup-and-migration)
3. [Exact local production environment setup](#3-exact-local-production-environment-setup)
4. [Railway project and service setup](#4-railway-project-and-service-setup)
5. [Railway environment variables](#5-railway-environment-variables)
6. [Railway deployment settings](#6-railway-deployment-settings)
7. [Vercel project setup](#7-vercel-project-setup)
8. [Vercel environment variables](#8-vercel-environment-variables)
9. [Vercel deployment settings](#9-vercel-deployment-settings)
10. [CORS, cookies and CSP between Vercel and Railway](#10-cors-cookies-and-csp-between-vercel-and-railway) ← **read before deploying**
11. [Health check verification](#11-health-check-verification)
12. [Authentication and security checks](#12-authentication-and-security-checks)
13. [Database verification](#13-database-verification)
14. [Empty production database verification](#14-empty-production-database-verification)
15. [Production acceptance testing checklist](#15-production-acceptance-testing-checklist)
16. [Rollback procedure](#16-rollback-procedure)
17. [Future Alembic migrations](#17-future-alembic-migrations)
18. [Keeping demo data out of production](#18-keeping-demo-data-out-of-production)
19. [Local vs Railway vs Vercel — who does what](#19-local-vs-railway-vs-vercel--who-does-what)
20. [Deployment order and dependencies](#20-deployment-order-and-dependencies)

---

## 1. Production architecture

```
                      ┌──────────────────────────────┐
                      │  Vercel (global edge)         │
   Shop browser ─────►│  bmfrontend — static SPA/PWA  │
   (Chrome/Edge)      │  React 19 · Vite 6 · Workbox  │
                      └──────────────┬───────────────┘
                                     │  HTTPS, JSON, /api/v1
                                     │  Authorization: Bearer <15-min access token>
                                     │  Cookie: bm_refresh (HttpOnly, Secure, Path=/api/v1/auth)
                                     ▼
                      ┌──────────────────────────────┐
                      │  Railway — Singapore          │
                      │  bmbackend API × 1 replica    │
                      │  uvicorn · FastAPI            │
                      │  /healthz  /readyz            │
                      │  + Volume for uploads         │
                      └──────────────┬───────────────┘
                                     │  postgresql+psycopg, sslmode=require
                                     │  role bm_app — DML only, POOLED endpoint
                                     ▼
                      ┌──────────────────────────────┐
                      │  Neon — AWS Singapore          │
                      │  project  national auto spares │
                      │  branch   production            │
                      │  database neondb               │
                      │  roles    neondb_owner (DDL)   │
                      │           bm_app       (DML)   │
                      └──────────────▲───────────────┘
                                     │  DIRECT endpoint, role neondb_owner
                                     │  alembic upgrade head — explicit, manual
                      ┌──────────────┴───────────────┐
                      │  Developer machine            │
                      │  .env.production (git-ignored)│
                      │  scripts/prod.ps1             │
                      │  scripts/production_db.py     │
                      └──────────────────────────────┘
```

### Decisions this topology commits to, and why

**One API replica.** `DEPLOYMENT.md §2`. The design load is 5 counters and roughly 500
invoices a day — about three orders of magnitude below what one modest Postgres instance
handles. Scaling is a contingency, not a plan.

> One correction to `DEPLOYMENT.md §9`, which lists "replace the in-process rate limiter
> with a shared store" as the prerequisite for adding replicas: **there is no in-process
> rate limiter.** `BM_RATE_LIMIT_ENABLED` is a declared setting that nothing reads, and
> `RateLimited` is an error class with no raiser. Login lockout *is* implemented and is
> database-backed, so it is already correct across replicas. One replica remains the
> right choice at this load — but for simplicity, not for that reason.

**Migrations never run on deploy.** ADR-016. Automatic start-up migrations make every
replica restart a potential schema change and turn a crash loop into a migration loop.
`/readyz` returns 503 until the Alembic head in the database matches the head in the code,
which is what stops a half-migrated deployment from writing inconsistent data.

**Two database roles, as in development.** `SECURITY.md §7`. `neondb_owner` owns the
schema and holds DDL; `bm_app` is the application role whose `UPDATE`/`DELETE` rights on
`audit_logs`, `stock_movements`, `payments`, `invoices` and `invoice_items` are revoked by
the migrations that create them. **This privilege model is the only thing making
finalised financial records immutable** — code discipline is the second line, not the
first.

**Uploads need a Volume.** Railway's container filesystem is ephemeral. Parsed import
rows live in `import_rows` and survive; the original file, which is what a dispute is
settled against, does not.

**The worker is optional, and its absence is not silent-but-fine.** Without a service
running `python -m bm.worker`, `POST /api/v1/imports` accepts an upload, stores it, and
enqueues an `import.parse` job that nothing ever claims. Billing, stock, credit and
reports all work without it. File imports do not.

**Region matters more than it looks.** A single finalisation does several database round
trips inside one transaction (lock stock → check → allocate the series number → insert
invoice, items, movements, payment → audit → outbox). Cross-region latency is multiplied
by that count, not added once. Railway in **Singapore**, same region as the Neon branch.

---

## 2. Neon setup and migration

### 2.1 What exists already

| | |
|---|---|
| Project | `national auto spares` |
| Branch | `production` |
| Database | `neondb` |
| Owner role | `neondb_owner` |
| Region | AWS Singapore (`ap-southeast-1`) |

### 2.2 Create the application role — **before** the first migration

Run [`../scripts/neon-init-roles.sql`](../scripts/neon-init-roles.sql) in the Neon SQL
Editor as `neondb_owner`, having replaced both occurrences of the placeholder password
with one generated value.

**This step is not optional and its order is not cosmetic.** Every `GRANT`/`REVOKE` in
migrations 0004, 0007, 0011 and 0013 is wrapped in:

```python
def _app_role_exists() -> bool:
    return bool(bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": "bm_app"}
    ).first())
```

If `bm_app` does not exist when a migration runs, that migration **skips its grants
silently and reports success**. You get a complete schema with no append-only
protection — `UPDATE audit_logs`, `DELETE FROM stock_movements` and
`UPDATE invoices SET total_amount = …` all become possible, and nothing anywhere reports
it. NFR-DATA-006 is gone and the deployment looks perfect.

This is not a theoretical ordering concern. The `migrations` job in
`.github/workflows/ci.yml` runs in exactly that state deliberately, and the same guard
concealed a missing foreign key (`fk_counters__invoice_series`) on every CI run until
migration 0021 repaired it forward.

`production_db.py migrate` refuses to proceed while the role is absent, and
`production_db.py verify` asserts the privileges actually landed — so a wrong order is
caught rather than assumed. But getting it right first is cheaper than repairing it.

Neon note: `neondb_owner` is a member of `neon_superuser`, which carries `CREATEROLE`, so
`CREATE ROLE bm_app` works with no Neon-side request. The role appears in the Neon Console
under Roles afterwards and can be issued a pooled connection string like any other.

### 2.3 Collect the two connection strings

Neon Console → project → branch `production` → **Connect** → database `neondb`.

| Need | Role | Pooling | Goes in |
|---|---|---|---|
| DDL, for Alembic | `neondb_owner` | **off** | `BM_MIGRATION_DATABASE_URL` in `.env.production` |
| DML, for the API | `bm_app` | **on** | `BM_DATABASE_URL` in `.env.production` **and** Railway |

For each one: change the scheme `postgresql://` → `postgresql+psycopg://`, and make sure
it ends `?sslmode=require`. Nothing else changes.

```
direct   ep-xxxx-yyyy-123456.ap-southeast-1.aws.neon.tech
pooled   ep-xxxx-yyyy-123456-pooler.ap-southeast-1.aws.neon.tech
                              ^^^^^^^  the only difference
```

**Alembic needs the direct endpoint.** Migration 0001 runs `CREATE EXTENSION` and
`CREATE TYPE`, and `env.py` sets `transaction_per_migration=True` so each revision runs in
its own transaction. PgBouncer in transaction mode will not carry that reliably.
`production_db.py migrate` refuses a `-pooler` host outright.

**The API wants the pooled endpoint.** Railway restarts the container on every deploy and
the application opens a fresh pool each time; the pooler absorbs that without consuming
Neon's direct-connection allowance. One consequence to know rather than discover:
`infra/db/session.py` issues `SET statement_timeout`, `SET lock_timeout` and
`SET idle_in_transaction_session_timeout` on connect, and under transaction pooling those
are scoped to the carrying transaction rather than the whole session. The timeouts still
cover the statement that matters — a finalisation is one transaction — but they are not a
standing session guarantee. If you prefer that guarantee, use the direct endpoint and keep
`BM_DB_POOL_SIZE` at 5; both are correct, and pooled is the better default here.

### 2.4 Inspect before you write

```powershell
./scripts/prod.ps1 python scripts/production_db.py inspect
```

Read-only. It opens no writing transaction and runs no DDL. It reports the masked target,
whether the endpoint is pooled or direct, the server version, the connected role, the
installed extensions, the table and index and constraint counts, the `alembic_version`
row, whether `bm_app` exists, and a `count(*)` of every business table — then a verdict:

| Verdict | Exit | Means |
|---|---|---|
| `FRESH` | 0 | No tables, no `alembic_version`. Safe to migrate. |
| `MIGRATED AND EMPTY` | 0 | At the code's head, zero business rows. Ready to provision. |
| `BEHIND` | 0 | At an older revision. `migrate` will apply the pending ones. |
| `UNSAFE` | 2 | **Tables exist with no `alembic_version` row.** Something created this schema outside Alembic. **Stop.** |
| `NOT EMPTY` | 2 | Business rows present. This database is in use. **Stop.** |

For the last two, do **not** drop objects to force a clean migration. Find out where they
came from — most likely another project shares the branch, or a schema was restored by
hand. Either way the fix is a new, empty Neon branch, not a `DROP`.

### 2.5 Migrate

```powershell
./scripts/prod.ps1 python scripts/production_db.py migrate          # pre-flight only
./scripts/prod.ps1 python scripts/production_db.py migrate --yes    # apply
```

Without `--yes` it runs the full pre-flight, prints what it *would* do, and writes
nothing. The pre-flight refuses: a placeholder URL, a localhost host, a database not named
`neondb`, a host not ending `neon.tech`, a pooled endpoint, tables without
`alembic_version`, any business data, and a missing `bm_app`. Each refusal reports and
stops; none adapts.

With `--yes` it applies `0001 → 0021` — 21 revisions, each in its own transaction, so a
failure leaves the database at a known revision rather than halfway between two.

What the migrations do, which is worth knowing before you run them against a real
database:

| Revisions | |
|---|---|
| `0001` | Extensions `pg_trgm`, `unaccent`, `citext` and all 30 native enum types. |
| `0002`–`0003` | Tenancy and identity: businesses, branches, counters, users, memberships, devices, refresh tokens, login attempts. |
| `0004` | `audit_logs`, append-only at the privilege level. |
| `0005` | **Seeds permissions and the 6 system roles.** Reference data only. |
| `0006`–`0015` | Catalog, inventory, customers, purchases, series, invoices, drafts, credit, returns, infrastructure. |
| `0016` | **Seeds default units.** Reference data only — and only for businesses that already exist, so on a fresh database this writes nothing. |
| `0017`–`0021` | Child-table tenancy, the search indexes, import fixes, the `fk_counters__invoice_series` repair. |

**No migration seeds a business, a tax rate, a product, a customer, a demo account or a
default password** (`CLAUDE.md §7`). `0005` and `0016` are the only seeding revisions and
both are reference data.

No migration uses `CREATE INDEX CONCURRENTLY`, so none needs a non-transactional block —
checked across all 21.

### 2.6 Verify

```powershell
./scripts/prod.ps1 python scripts/production_db.py verify
```

Roughly 50 assertions, each printed `PASS`/`FAIL`, non-zero exit on any failure. Covered
in [§13](#13-database-verification) and [§14](#14-empty-production-database-verification).

### 2.7 Provision the one thing that is not business data

A migrated database is complete and **nobody can sign in**. There is no bootstrap
endpoint — creating the first owner is a deployment step (`CLAUDE.md §10`).

```powershell
$env:BM_PROVISION_OWNER_PASSWORD = "<a long passphrase>"
./scripts/prod.ps1 python scripts/production_db.py provision --dry-run
./scripts/prod.ps1 python scripts/production_db.py provision --yes
Remove-Item Env:\BM_PROVISION_OWNER_PASSWORD
```

It writes exactly five rows:

| Table | Row |
|---|---|
| `businesses` | `NATIONAL AUTO SPARES`, `AUTO NAGAR, HYDERABAD`, two phone numbers, `Asia/Kolkata`, FY starts month 4 |
| `branches` | `MAIN` / `Main` |
| `counters` | `C1` / `Counter 1`, `max_open_drafts = 20` |
| `users` | `owner` / `Owner`, Argon2id hash, `must_change_password = true` |
| `memberships` | the `owner` system role, all branches |

All in one transaction — a business with no owner, or an owner with no membership, is
unusable and harder to diagnose than nothing at all.

**The identity values are the owner's own, not invented.** They are read from rows 1–3 of
`Sheet1` in the owner's estimate workbook `ANAS_1234_20260606_231456.xlsx`, and are the
same constants `scripts/set_business_identity.py` already uses. `legal_name` and
`tax_identifier` stay **NULL**: the workbook shows a trading name rather than a registered
legal name, no GSTIN appears anywhere in it, and OD-4 is still open. Guessing either is
the precise mistake these defaults exist to avoid. Override any of them with
`--display-name`, `--address-line`, `--phone`, `--branch-code`, `--counter-code`,
`--owner-username` and so on; `--dry-run` prints exactly what would be written.

The password is read from the environment, never a flag, so it does not land in shell
history or a process listing. It is stored only as an Argon2id hash peppered with
`BM_PASSWORD_PEPPER` — **which must be the same value Railway runs with**, or the owner
cannot sign in and the error is indistinguishable from a wrong password.

`must_change_password = true` blocks every non-auth endpoint until the owner changes it
(`api/deps.py`), and the counter application renders the change-password screen for that
state (`src/app/AppShell.tsx`). That is the go-live gate "all seeded credentials rotated;
forced password change verified", satisfied by construction.

`provision` refuses to run twice. A second business would be a second tenant, and every
query is scoped by `business_id` from the token — so the shop would silently see an empty
catalogue.

**What it does not create, deliberately:** no product, customer, supplier, stock figure,
invoice, draft, sale or payment. There is no statement in the function that touches those
tables. See [§18](#18-keeping-demo-data-out-of-production).

### 2.8 Neon settings to turn on

| Setting | Value | Why |
|---|---|---|
| Point-in-time restore window | the longest your plan allows (7 days target) | `DEPLOYMENT.md §7`: RPO ≤ 5 min (NFR-REL-002). This is the primary recovery path. |
| Autosuspend | **off**, or the longest delay available | A suspended compute adds a cold start to the first request of the morning. At a counter with a customer waiting, that is felt. |
| Branch protection on `production` | on, if available | Stops an accidental reset of the branch holding the shop's data. |
| Allowed IPs | only if you can cover both Railway's egress and the developer machine | Railway egress addresses are not static on all plans. Turning this on without covering both locks out the API. Verify before enabling. |

🔴 **Do not create a Neon branch *from* `production` to get a staging database once the
shop is live.** A Neon branch is a copy-on-write clone: it would contain real customers,
real prices and real takings, reachable with a different connection string and no audit
trail. Staging gets its own empty branch, migrated the same way and provisioned with an
obviously-fake business name.

---

## 3. Exact local production environment setup

This is the only machine that ever holds a DDL-capable production credential.

### 3.1 One-time

```powershell
cd C:\progromming\bm\bmbackend
Copy-Item .env.production.example .env.production   # if not already present
```

`.env.production` is git-ignored by the `.env.*` rule. Confirm it, every time, before
filling anything in:

```powershell
git check-ignore -v .env.production     # must print a .gitignore line
git status --short                      # must NOT list .env.production
```

Fill in:

| Variable | Value |
|---|---|
| `BM_MIGRATION_DATABASE_URL` | Neon **direct**, role `neondb_owner`, `postgresql+psycopg://…?sslmode=require` |
| `BM_DATABASE_URL` | Neon **pooled**, role `bm_app`, same form |
| `BM_JWT_SIGNING_KEY` | secret 1 |
| `BM_PASSWORD_PEPPER` | secret 2 — **the same value you will set on Railway** |
| `BM_IP_HASH_PEPPER` | secret 3 |
| `BM_CORS_ALLOWED_ORIGINS` | the Vercel origin (recorded here; the live value is Railway's) |

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"   # run three times
```

### 3.2 Always use the wrapper

```powershell
./scripts/prod.ps1 <command...>
```

`scripts/prod.ps1` loads `.env.production` into the process environment, prints the
resolved target with the password masked, refuses to run `pytest`, and runs from the
repository root so `alembic.ini` resolves.

🔴 **A bare `uv run alembic upgrade head` migrates the LOCAL database, silently.**
`migrations/env.py::_load_dotenv()` reads `.env`, which on this machine points
`BM_MIGRATION_DATABASE_URL` at the working `bm`. The command looks identical whether it is
about to touch Neon or the shop's own data. This is the same class of mistake `bt.ps1`
exists for on the test side — and its header records what happened the last time it was
made: a bare `uv run pytest` truncated the demo database on 2026-10-31, because only one
of the two variables had been overridden.

Both layers are needed and neither is sufficient alone:

| Layer | Protects against |
|---|---|
| `prod.ps1` loads the file into the **process** environment | `_load_dotenv()` uses `os.environ.setdefault`, and pydantic-settings ranks real environment variables above `env_file` — so the production value wins over `.env` rather than racing it. |
| `production_db.py` guards the **resolved URL** | A correct value in the wrong variable, or a local URL that survived a copy-paste. Checked against the server actually about to be written to, not against which file it came from. |

### 3.3 The commands

```powershell
./scripts/prod.ps1 python scripts/production_db.py inspect
./scripts/prod.ps1 python scripts/production_db.py migrate --yes
./scripts/prod.ps1 python scripts/production_db.py verify
./scripts/prod.ps1 python scripts/production_db.py provision --dry-run
./scripts/prod.ps1 alembic current
./scripts/prod.ps1 alembic history
```

### 3.4 What must never happen on this machine

- **`.env` and `.env.production` never share a value.** Not the database URL, not a
  secret. If a production secret is in `.env`, every `uv run pytest`, every
  `docker compose up` and every editor plugin that loads dotenv files has it.
- **Never run `pytest` with the production environment loaded.** The suite truncates every
  business table between tests. `tests/conftest.py` would refuse — `neondb` is not on its
  allow-list and the name does not end `_test`/`_ci`/`_scratch` — and `prod.ps1` refuses
  before that. Use `bt.ps1`.
- **Never run `scripts/seed_dev.py`, `seed_demo.py` or `seed_e2e_tenant.py` against Neon.**
  [§18](#18-keeping-demo-data-out-of-production).
- **Never paste a filled-in connection string into a report, an issue, a commit message or
  a chat.** The Neon password is in it.

---

## 4. Railway project and service setup

1. **New Project** → Deploy from GitHub repo → `nationalautospares898-maker/bmbackend`,
   branch `main`.
2. **Region: Singapore.** Same region as Neon — see §1.
3. **Builder: Dockerfile.** Not Nixpacks. The repository has a tested multi-stage
   Dockerfile; Nixpacks would guess an install and a start command and miss
   `PYTHONPATH=/app/src`. Set the build target to `runtime`.
4. **Leave the start command empty.** The Dockerfile's `CMD` is already
   `uvicorn bm.main:create_app --factory --host 0.0.0.0 --port 8000`.
5. **Port 8000.** The `CMD` hard-codes `--port 8000` and does not read Railway's injected
   `PORT`. Set the service's public port to 8000 explicitly rather than relying on
   detection — a mismatch here is the classic "deploy succeeded, domain returns 502".
6. **Healthcheck path: `/healthz`.** Not `/readyz` — see §6.
7. **Attach a Volume**, mounted at `/var/lib/bm/storage`. The Dockerfile already creates
   that path owned by the non-root `bm` user.
8. **Add the environment variables** from §5, then let it deploy.
9. **Generate a public domain** (or attach a custom one — see §10).

**Do not deploy before the Neon migration has run.** The service will start, and `/readyz`
will correctly return 503 with `migrations.status: "mismatch"`. That is the gate working,
but it is a confusing first impression. §20 has the order.

---

## 5. Railway environment variables

Full reasoning per variable, including the ones that are declared and read by nothing:
[`RAILWAY-ENVIRONMENT-VARIABLES.md`](RAILWAY-ENVIRONMENT-VARIABLES.md).

The complete set for the API service — fill in the four `<…>` values:

```
BM_ENV=production
BM_RELEASE=${{RAILWAY_GIT_COMMIT_SHA}}

BM_DATABASE_URL=postgresql+psycopg://bm_app:<password>@<pooled-neon-host>/neondb?sslmode=require
BM_DB_POOL_SIZE=5
BM_DB_MAX_OVERFLOW=2
BM_DB_STATEMENT_TIMEOUT_MS=15000
BM_DB_LOCK_TIMEOUT_MS=3000

BM_JWT_SIGNING_KEY=<secret 1>
BM_PASSWORD_PEPPER=<secret 2 — identical to .env.production, and PERMANENT>
BM_IP_HASH_PEPPER=<secret 3>
BM_ACCESS_TOKEN_TTL_SECONDS=900
BM_REFRESH_TOKEN_TTL_DAYS=30

BM_CORS_ALLOWED_ORIGINS=https://<vercel-or-custom-domain>
BM_ENABLE_API_DOCS=false
BM_UNATTENDED_ACCESS=false
BM_REFRESH_COOKIE_SAMESITE=lax

BM_STORAGE_BACKEND=local
BM_STORAGE_PATH=/var/lib/bm/storage
BM_MAX_UPLOAD_MB=10

BM_LOG_LEVEL=INFO
BM_LOG_FORMAT=json

BM_ALERTING_ENABLED=false
```

The four to think about rather than copy:

| Variable | |
|---|---|
| `BM_CORS_ALLOWED_ORIGINS` | Must match the Vercel origin **exactly** — scheme, host, any `www.`, no trailing slash. `main.py` installs `CORSMiddleware` only when this is non-empty, so getting it wrong means `curl` works and the browser does not. Never `*`: `allow_credentials=True` is set, so a wildcard would let any site on the internet make credentialed requests. |
| `BM_REFRESH_COOKIE_SAMESITE` | `lax` **only if** the front end and API share a registrable domain. `none` otherwise. [§10](#10-cors-cookies-and-csp-between-vercel-and-railway) — **this is the one that fails fifteen minutes after it looks fine.** |
| `BM_PASSWORD_PEPPER` | Identical to `.env.production`. 🔴 Permanent: changing it invalidates every password. Back it up outside Railway and outside the developer machine before go-live. |
| `BM_STORAGE_PATH` | The Volume mount path. Without a volume, uploads are deleted on every redeploy. |

🔴 **`BM_MIGRATION_DATABASE_URL` must not exist on this service.** `CLAUDE.md §11`. With
it present, any container that can be made to run Alembic can alter the schema and bypass
the column-level `REVOKE`s that make finalised financial records immutable.

### Optional second service — the worker

Same repository, same Dockerfile, start command `python -m bm.worker`. Needs
`BM_DATABASE_URL`, the three secrets, `BM_LOG_*`, `BM_STORAGE_*` on the **same volume**,
and `BM_WORKER_CONCURRENCY`. It never reads `BM_CORS_ALLOWED_ORIGINS` or
`BM_ENABLE_API_DOCS`. Jobs are claimed with `FOR UPDATE SKIP LOCKED`, so more than one
worker is already safe.

---

## 6. Railway deployment settings

| Setting | Value |
|---|---|
| Builder | Dockerfile, target `runtime` |
| Start command | *(empty — the `CMD` is correct)* |
| Port | `8000` |
| Healthcheck | `/healthz`, 30 s interval |
| Replicas | 1 |
| Restart policy | on failure |
| Volume | mounted at `BM_STORAGE_PATH` |
| Region | Singapore |
| Auto-deploy | **off** for the first release; on afterwards if you want it |

### `/healthz` for the probe, `/readyz` by hand

```python
@app.get("/healthz")   # liveness. Deliberately does not touch the database.
@app.get("/readyz")    # DB reachable AND alembic head matches the code. 503 otherwise.
```

Using `/readyz` as the platform healthcheck would be wrong in a specific way: a replica
waiting on a migration returns 503 by design, the platform would kill it, and it would
restart into the same 503 forever. `/healthz` ignores the database on purpose — a
database blip must not make the orchestrator kill a healthy process. Check `/readyz`
manually after every deploy ([§11](#11-health-check-verification)).

### Auto-deploy and schema changes

Auto-deploy on `main` is fine for a release with no migration. For a release **with** one,
the migration has to run first (§17), so either turn auto-deploy off for that release or
accept a few minutes of `/readyz` 503 — during which the API refuses traffic and the
counter stays on drafts, which is the designed behaviour rather than an outage.

### Proxy headers

`api/deps.py::client_ip` reads the first hop of `X-Forwarded-For`, which is what Railway
sets, and falls back to the socket address. Deliberately the first hop only: reading the
whole chain would let a client prepend any address and evade the per-IP half of login
lockout. Nothing extra to configure.

---

## 7. Vercel project setup

1. **Add New → Project** → import `nationalautospares898-maker/bmfrontend`.
2. Framework Preset **Vite**. Root Directory **`.`** — this repository *is* the front end;
   there is no subdirectory.
3. Build/Output/Install commands come from the committed `vercel.json`
   (`npm run build`, `dist`, `npm ci`). Leave the dashboard fields on their defaults.
4. Node.js **22.x**, matching CI.
5. Production Branch `main`.
6. Add `VITE_API_BASE_URL` (§8) **before the first build** — it is baked in at build time.
7. Deploy. Then attach a custom domain if you are using one (§10).

`npm run build` is `tsc -b && vite build`, so a type error fails the Vercel build. That is
deliberate: this client renders money and stock figures, `strict` is a hard requirement
(ADR-023), and a deploy that skipped the typecheck would be the one step in the pipeline
that did.

### What you do not need

No `api/` directory, no serverless functions, no Vercel Postgres/KV/Blob. This is a static
SPA; state lives in Neon and in the browser's IndexedDB.

---

## 8. Vercel environment variables

Full detail: [`../../bmfrontend/docs/VERCEL-ENVIRONMENT-VARIABLES.md`](../../bmfrontend/docs/VERCEL-ENVIRONMENT-VARIABLES.md).

**One variable is read by the application.**

| Key | Value | Environments |
|---|---|---|
| `VITE_API_BASE_URL` | `https://<api-host>/api/v1` | Production |

It must include the `/api/v1` path — `src/api/client.ts` appends endpoint paths directly,
so a bare host produces `https://api.example.com/auth/login` and a 404 on every call.

Read at **build** time. Changing it needs a **redeploy**, not a restart.

Three further `VITE_` names are allow-listed in `src/vite-env.d.ts` and have **no reader in
`src/`** — `VITE_APP_VERSION`, `VITE_SENTRY_DSN`, `VITE_ENABLE_DEVTOOLS`. Leave them
unset; setting one creates the impression something is configured when nothing reads it.
A fifth name fails `tests/structural/rules.test.ts`.

🔴 **No backend secret belongs in this project — including without the `VITE_` prefix.**
Anything `VITE_`-prefixed is compiled into a publicly readable bundle. A non-prefixed
variable is not in the bundle but is still a production credential sitting somewhere with
no reason to hold one. The structural suite fails on the literals `DATABASE_URL`,
`JWT_SIGNING_KEY`, `PASSWORD_PEPPER`, `IP_HASH_PEPPER`, `postgres://`, `AWS_SECRET` and
`PRIVATE_KEY` anywhere in `src/`, and `gitleaks` runs on every push.

### Preview deployments

Vercel gives every preview a fresh hostname, so it cannot be in
`BM_CORS_ALLOWED_ORIGINS` ahead of time. Either point Preview at a separate staging API
(cleanest), add a specific preview origin by hand while testing it, or leave Preview unset
so preview builds have no backend and fail honestly at sign-in. **Never widen CORS to a
wildcard to make previews work.**

---

## 9. Vercel deployment settings

From the committed `vercel.json`:

| | |
|---|---|
| `rewrites` | `/(.*)` → `/index.html`. Vercel applies rewrites **after** the filesystem check, so `/assets/*`, `/sw.js`, `/manifest.webmanifest` and `/workbox-*.js` are served normally and only unmatched paths fall through. Without it a deep link like `/reports/stock-movement` 404s on first visit — the service worker's `navigateFallback` only covers it once the worker is installed, which a first visit and a hard reload are not. |
| Security headers | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Strict-Transport-Security`. The same four the API sets on its own responses, set here too because these are *document* responses from Vercel which that middleware never sees. |
| `/assets/*` | `public, max-age=31536000, immutable`. Vite content-hashes those filenames, so a stale cache is impossible. |
| `/sw.js`, `/registerSW.js`, `/index.html` | `no-cache, must-revalidate`. A cached service worker would pin the shop to an old build indefinitely, including past a security fix. |

### No CSP header, deliberately

The Content-Security-Policy stays in `index.html`'s `<meta>` tag. A header and a meta tag
are **both** enforced and the effective policy is their intersection — so declaring it
twice means the stricter one silently wins and the next person to widen the one they can
find gets no effect and no error. One source, and it is the one
`tests/structural/rules.test.ts` reads.

`connect-src` is handled at build time — see §10.

### Service-worker behaviour (ADR-031)

Unchanged by any of this, and worth knowing before the first acceptance run: the worker
caches the **app shell only**. No `/api/` route is ever cached and no mutation is ever
queued by it — a cached stock figure or a replayed sale would both be worse than being
offline. Updates are a deliberate prompt (`registerType: 'prompt'`, `skipWaiting: false`,
`clientsClaim: false`), never a reload mid-sale. All four properties are asserted by the
structural suite.

---

## 10. CORS, cookies and CSP between Vercel and Railway

**Read this before deploying.** It contains the one problem in this stack that produces a
perfect-looking deployment and then breaks at the counter a quarter of an hour later.

Three separate mechanisms have to agree. They fail differently, and only one of them fails
quietly.

### 10.1 CORS — fails loudly

The browser refuses a cross-origin response whose `Access-Control-Allow-Origin` does not
name the requesting origin.

`main.py` adds `CORSMiddleware` **only when `settings.cors_origins` is non-empty**, with
`allow_origins` as the explicit list, `allow_credentials=True`, every header the client
sends, and `X-Request-Id`/`Idempotent-Replay`/`X-Api-Version` exposed. Nothing to add.

Set `BM_CORS_ALLOWED_ORIGINS` to the Vercel origin exactly. Symptom of a mismatch:
"blocked by CORS policy" in the console while `curl` works perfectly. Compare the two
strings character by character, including `https://` and any `www.`.

### 10.2 CSP `connect-src` — fails loudly, in a confusing place

`index.html` declares:

```
connect-src 'self' http://localhost:8000
```

That is a real security control and the literal is correct for local development. It is
also the **only** origin the browser will let the client talk to — so a Railway API at any
other address is blocked by the page's own policy. The deployment builds, serves and looks
fine; every request fails with nothing in the network tab and only a console violation.

**Handled at build time.** `vite.config.ts` carries a `bm-csp-connect-src` plugin that
appends the origin of `VITE_API_BASE_URL` to the `connect-src` directive:

- an **append**, not a substitution, so the policy stays an explicit allow-list;
- `http://localhost:8000` remains in the literal, so local development, `vite preview` and
  the Playwright suites are unaffected — for them it is a no-op;
- a relative base (`/api/v1`) adds nothing, because `'self'` already covers it;
- derived from the one variable that already decides where requests go, so the two cannot
  drift apart.

Verify it landed: view source on the deployed page and read the `connect-src` directive.

### 10.3 🔴 The refresh cookie — fails silently, fifteen minutes later

The refresh token is an `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth` cookie
(`api/v1/auth.py`). `SameSite` compares the **registrable domain**, not the origin. And
`vercel.app` and `up.railway.app` are both public suffixes — so `nas.vercel.app` and
`nas-api.up.railway.app` are **cross-site**, and a `Lax` cookie is never sent between
them.

What that looks like, in order:

1. Sign-in works. Everything works.
2. Reload, or open the app the next morning: the sign-in form again. `bootstrap.ts` calls
   `refreshSession()` on every cold start, no cookie is sent, there is nothing to recover
   a token from.
3. **Fifteen minutes after each sign-in** — `BM_ACCESS_TOKEN_TTL_SECONDS=900` — the access
   token expires, the client's one transparent refresh-and-retry gets no cookie either, and
   the cashier is signed out. Possibly mid-bill. Drafts survive (IndexedDB, never cleared
   on an auth error) but the bill cannot be finalised until somebody signs in again.

Nothing in a build log, a health check or a five-minute smoke test shows this.

### 10.4 Choose a topology — this is the decision to make before deploying

**A — two subdomains of one owned domain. Recommended.**

```
app.your-domain.com  →  Vercel
api.your-domain.com  →  Railway
```

| | |
|---|---|
| `VITE_API_BASE_URL` | `https://api.your-domain.com/api/v1` |
| `BM_CORS_ALLOWED_ORIGINS` | `https://app.your-domain.com` |
| `BM_REFRESH_COOKIE_SAMESITE` | `lax` |
| CSP | handled automatically |

Cross-origin but **same-site**, so the `Lax` cookie is sent and the CSRF property the
cookie was designed around is kept intact. Direct browser→Railway requests: lowest
latency, no proxy body-size or duration limits. Costs a domain and two DNS records.

**B — provider domains, `SameSite=None`.**

```
nas.vercel.app          →  Vercel
nas-api.up.railway.app  →  Railway
```

| | |
|---|---|
| `VITE_API_BASE_URL` | `https://nas-api.up.railway.app/api/v1` |
| `BM_CORS_ALLOWED_ORIGINS` | `https://nas.vercel.app` |
| `BM_REFRESH_COOKIE_SAMESITE` | **`none`** |
| CSP | handled automatically |

No domain needed. Relaxes the CSRF control — bounded, because every other endpoint
authenticates with a bearer header rather than the cookie, and `/auth/refresh` is
additionally bound to `X-Device-Id`, but genuinely relaxed. `none` requires `Secure`,
which `BM_ENV=production` already sets.

**C — Vercel proxies `/api/*` to Railway.**

Add to `vercel.json`, **before** the catch-all or it is swallowed:

```jsonc
"rewrites": [
  { "source": "/api/:path*", "destination": "https://<railway-domain>/api/:path*" },
  { "source": "/(.*)",       "destination": "/index.html" }
]
```

| | |
|---|---|
| `VITE_API_BASE_URL` | `/api/v1` |
| `BM_CORS_ALLOWED_ORIGINS` | not needed |
| `BM_REFRESH_COOKIE_SAMESITE` | `lax` |
| CSP | nothing to add — `'self'` covers it |

The browser only ever sees one origin, so all three mechanisms become non-issues. The
codebase already supports it: `client.ts` defaults to the relative `/api/v1`, and
`vite.config.ts` has a dev proxy built for exactly this shape.

The cost is an extra hop (Vercel edge → Railway Singapore) on every request, and Vercel's
proxy body-size and duration limits then apply to this application's two awkward requests:
the 10 MB import upload and the 30-second finalisation timeout. **Check both against your
Vercel plan before choosing this for a shop that imports price lists.**

### 10.5 Summary

| Topology | `BM_REFRESH_COOKIE_SAMESITE` | `BM_CORS_ALLOWED_ORIGINS` | `VITE_API_BASE_URL` |
|---|---|---|---|
| A — own domain, two subdomains | `lax` | the front-end origin | absolute |
| B — `*.vercel.app` + `*.up.railway.app` | **`none`** | the front-end origin | absolute |
| C — Vercel proxy | `lax` | *(not needed)* | `/api/v1` |

---

## 11. Health check verification

After every Railway deploy.

### `GET /healthz`

```json
{"status": "ok"}
```

Liveness only, and deliberately does not touch the database.

### `GET /readyz`

```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "migrations": {"expected_head": "0021", "current_revision": "0021", "status": "ok"},
    "alerting": {"state": "disabled", "recipient_name": null, "destination": null}
  }
}
```

200 when ready, **503 otherwise**. How to read a failure:

| Symptom | Means | Fix |
|---|---|---|
| `database: "unreachable"` | Bad `BM_DATABASE_URL`, a rotated password, missing `?sslmode=require`, a suspended Neon compute, or the `bm_app` role was never created. | Check the deploy log for `readiness_database_unreachable`, then re-run `production_db.py inspect`. |
| `migrations.status: "mismatch"` | **The database and the code disagree about the schema.** The service is correctly refusing traffic (ADR-016). | Run the Neon migration (§2.5). Never "fix" this by deploying older code against a newer schema. |
| `expected_head: null` | `migrations/versions/` has more than one head — an unmerged branch. | Merge it. `/readyz` deliberately fails loudly rather than picking one arbitrarily. |
| `alerting.state: "unconfigured"` | Alerting is **on** with no webhook, so every alert is being dropped and logged at `ERROR`. Does not make the replica unready — refusing traffic because the pager is unconfigured would take the shop's billing offline to protest a missing variable. | Set `BM_ALERT_WEBHOOK_URL` + `BM_ALERT_RECIPIENT_NAME`, or set `BM_ALERTING_ENABLED=false`. |
| `alerting.state: "no_named_recipient"` | A webhook with nobody's name on it. OD-19 asks for a person, not an endpoint. | Set `BM_ALERT_RECIPIENT_NAME`. |

### Startup log

The Railway deploy log must contain `startup` with `env=production`, and must **not**
contain:

| Line | Meaning |
|---|---|
| `unattended_access_enabled` | 🔴 Password-free sessions are enabled on an internet-reachable server. Treat as an incident; set `BM_UNATTENDED_ACCESS=false` and redeploy. |
| `api_docs_enabled_in_production` | `BM_ENABLE_API_DOCS=true`. Set it false. |
| `cors_allow_list_empty` | No CORS middleware was installed, so the browser client cannot work. |

---

## 12. Authentication and security checks

Run these against the deployed API once, at go-live.

### 12.1 The refresh cookie

Sign in through the deployed front end. DevTools → Network → the `login` request →
Response Headers:

```
set-cookie: bm_refresh=…; HttpOnly; Secure; Path=/api/v1/auth; SameSite=Lax; Max-Age=2592000
```

- [ ] `HttpOnly` present — the token is unreadable from JavaScript (NFR-SEC-003).
- [ ] `Secure` present — `BM_ENV=production`. Absent means `BM_ENV` is wrong.
- [ ] `Path=/api/v1/auth` — the cookie is not sent to any other endpoint.
- [ ] `SameSite` matches your topology from §10.5.
- [ ] The access token is in the **response body**, not a cookie, and `Application →
      Local Storage` / `Session Storage` are empty for this origin. A structural test
      enforces this in the source; this confirms it at runtime.

### 12.2 🔴 The two checks nothing else catches

- [ ] **Sign in, then hard-reload.** You must stay signed in. Landing back on the sign-in
      form means the refresh cookie is not being sent → §10.3.
- [ ] **Sign in, leave the tab idle 16+ minutes, then use the app.** You must stay signed
      in. Being signed out means the same thing.

### 12.3 Security headers

```powershell
curl -sI https://<railway-domain>/healthz
```

- [ ] `x-content-type-options: nosniff`
- [ ] `x-frame-options: DENY`
- [ ] `referrer-policy: strict-origin-when-cross-origin`
- [ ] `content-security-policy: default-src 'none'; frame-ancestors 'none'`
- [ ] `cache-control: no-store`
- [ ] `permissions-policy: camera=(), microphone=(), geolocation=()`

And on the Vercel document response: the five headers from §9.

### 12.4 Authorisation

- [ ] Any `/api/v1/*` endpoint without a token → `401`, in the project error envelope
      `{"error": {"code", "message", "details", "request_id", "retryable"}}`.
- [ ] `/api/v1/docs` → `404` (`BM_ENABLE_API_DOCS=false`).
- [ ] `/api/v1/openapi.json` → `200`. Expected: it is the contract the front end's types
      are generated from.
- [ ] `POST /api/v1/auth/unattended` → `403 PERMISSION_DENIED`. 🔴 A `200` here means
      anyone on the internet can obtain a session for this shop with no password.
- [ ] Five wrong passwords in a row → `429`-class lockout with `retry_after_seconds`, and
      the sixth attempt is refused even with the **correct** password for 15 minutes. This
      is database-backed, so it is correct across replicas.
- [ ] The owner's first sign-in demands a password change, and no other endpoint answers
      until it is done.
- [ ] A secret never appears in a response body or a log line. Search the Railway logs for
      a fragment of `BM_JWT_SIGNING_KEY` — the redaction filter in
      `infra/logging/setup.py` scrubs JWTs, `Authorization` headers,
      `password=`/`pepper=`/`token=` pairs and Postgres URLs carrying a password.

### 12.5 Transport and privileges

- [ ] `http://` on either domain redirects to `https://`.
- [ ] The Neon connection carries `?sslmode=require`.
- [ ] `production_db.py verify` reports every `bm_app CANNOT …` check as PASS (§13).

---

## 13. Database verification

```powershell
./scripts/prod.ps1 python scripts/production_db.py verify
```

Each assertion prints `PASS`/`FAIL`; any failure exits non-zero.

### Schema

- [ ] `alembic_version` = `0021`, the code's single head.
- [ ] All **53** ORM tables exist, plus `alembic_version` = **54**.
- [ ] No unexpected tables — a stray table means something else shares this branch.
- [ ] Extensions `pg_trgm`, `unaccent`, `citext` installed.
- [ ] All **30** native enum types created.
- [ ] Indexes present (>50) and both foreign-key and check constraints present.
- [ ] `fk_counters__invoice_series` exists. Specifically asserted because migration 0010
      created it only `if _app_role_exists()`, so every CI run and every single-role
      cluster produced a schema silently missing it until 0021 repaired it forward.
- [ ] `ix_products__sku_prefix`, `ix_products__name_prefix`, `ix_products__search_trgm`
      exist. Also specifically asserted: migration 0006's docstring described all three
      and created none, so **every product search was a sequential scan** — correct, and
      fast enough on a seeded test catalogue, which is exactly why no test caught it.
      NFR-PERF-001 is 150 ms p95 at 100 000 products.

### Privileges — the only proof immutability is real

- [ ] Role `bm_app` exists.
- [ ] `bm_app` **cannot** `UPDATE` or `DELETE`: `audit_logs`, `stock_movements`,
      `payments`. Cannot `DELETE` `invoices`.
- [ ] `bm_app` **can** `INSERT` into `invoices`, `invoice_items`, `stock_movements`,
      `payments`, `audit_logs`, and `UPDATE` `products`.
- [ ] Column-level on `invoices`: `bm_app` **can** `UPDATE` `status`, `cancelled_at`,
      `amount_paid`; **cannot** `UPDATE` `total_amount` or `issued_at`. Both halves are
      asserted — a blanket table grant would defeat the column-level model
      (`SECURITY.md §7`).

A FAIL in this block almost always means the migrations ran **before**
`neon-init-roles.sql`. The repair is to create the role and re-apply the grants, **never**
to drop and re-migrate.

### Reference data

- [ ] `permissions` seeded (>20 rows) — migration 0005.
- [ ] `roles` = exactly 6 system roles.
- [ ] `role_permissions` non-empty.

---

## 14. Empty production database verification

The claim is that production contains a schema and **zero business or demo records**. Here
is how it is established rather than asserted.

`production_db.py` computes the business-table list by **subtraction**:

```python
CONFIG_TABLES = {alembic_version, permissions, roles, role_permissions, units,
                 businesses, branches, counters, users, memberships, settings}
business_tables = set(Base.metadata.tables) - CONFIG_TABLES     # 42 tables
```

Derived from `Base.metadata` rather than written out, so a table added by a future
migration is checked automatically — and the fail-safe direction: **a new table is treated
as business data until somebody deliberately adds it to the config list.**

Every one of those 42 is counted with `count(*)`, not a `reltuples` estimate. This number
is the evidence; an estimate is not evidence.

### Expected state of a correctly provisioned production database

| Table group | Expected | Why |
|---|---|---|
| 42 business tables | **0 rows each** | Nothing has happened yet. Includes `products`, `customers`, `invoices`, `invoice_items`, `stock_movements`, `inventory_balances`, `payments`, `draft_bills`, `purchases`, `suppliers`, `import_jobs`, `audit_logs`, `devices`, `jobs`. |
| `permissions`, `roles`, `role_permissions` | seeded | Migration 0005. Reference data. |
| `units` | seeded | Migration 0016. Reference data a spare-parts shop cannot operate without. |
| `businesses`, `branches`, `counters`, `users`, `memberships` | **1 row each** | From `provision`. Configuration, not business data — without them nobody can sign in. |
| `settings` | **0 rows** | Defaults are applied **in code** (`domain/settings/defaults.py`) precisely so an unset key is never ambiguous, and a fresh database behaves identically to a configured one (`DATA-MODEL.md §3.1`). |
| `invoice_series` | **0 rows** | Created lazily inside the first finalisation transaction (`domain/sales/numbering.py`, `P2-D-09`), so the first sale after each 1 April cannot fail for want of a row and two concurrent first-sales cannot conflict. |

### Confirm it by hand

```sql
-- Business identity: exactly one row, the owner's own values, legal_name NULL.
SELECT display_name, legal_name, address_lines, phone_numbers, tax_identifier
FROM businesses;

-- Must all be zero.
SELECT
  (SELECT count(*) FROM products)            AS products,
  (SELECT count(*) FROM customers)           AS customers,
  (SELECT count(*) FROM invoices)            AS invoices,
  (SELECT count(*) FROM stock_movements)     AS stock_movements,
  (SELECT count(*) FROM inventory_balances)  AS inventory_balances,
  (SELECT count(*) FROM payments)            AS payments,
  (SELECT count(*) FROM draft_bills)         AS draft_bills,
  (SELECT count(*) FROM invoice_series)      AS invoice_series,
  (SELECT count(*) FROM settings)            AS settings;

-- No demo or fixture tenant can have reached this database.
SELECT display_name FROM businesses
WHERE display_name ILIKE '%DEMO%'
   OR display_name ILIKE '%E2E%'
   OR display_name ILIKE '%NOT A REAL%';   -- must return 0 rows

-- No seeded username survived.
SELECT username FROM users
WHERE username IN ('demo-owner', 'e2e-owner');   -- must return 0 rows
```

### Before go-live, the shop's real data arrives through the application

- Products, prices and aliases: the import screens or the catalog screens.
- **Opening stock: as `opening` movements through the application**, never a direct
  `INSERT`. Every stock change is a movement row in the same transaction
  (`CLAUDE.md §5` invariant 4) — a hand-inserted balance has no movement behind it and
  the nightly reconciliation will correctly report drift it cannot explain.
- Customers: the customers screen, or as they walk in.

---

## 15. Production acceptance testing checklist

Against the deployed stack, on the shop's own machine and browser. Each item is an
observation, not a reading of the code.

### 15.1 Reachability

- [ ] `https://<vercel-domain>/` renders the sign-in form.
- [ ] A deep link (`/reports`) loads directly in a fresh tab — the SPA rewrite works.
- [ ] `https://<railway-domain>/healthz` → `{"status":"ok"}`.
- [ ] `https://<railway-domain>/readyz` → 200, `migrations.status: "ok"`.
- [ ] View source: `connect-src` in the CSP includes the API origin.
- [ ] No console errors on first load.

### 15.2 Authentication

- [ ] Owner signs in with the provisioned password.
- [ ] The password change is demanded, and no other screen works until it is done.
- [ ] 🔴 Hard-reload → still signed in.
- [ ] 🔴 Idle 16+ minutes → still signed in.
- [ ] Sign out, then reload → sign-in form, and **drafts still present**.
- [ ] Five wrong passwords → lockout with a retry time; the correct password is refused
      during it.

### 15.3 Counter and device

- [ ] The device registers itself on first sign-in.
- [ ] If the counter is unassigned, the counter-assignment screen appears and assigning
      `C1` sticks across a reload. (A device registered to a branch with no counter is the
      state a brand-new counter PC is actually in, and it is what left one unable to bill —
      C-2.)
- [ ] `GET /api/v1/sync/bootstrap` returns `max_open_drafts >= 20`, `tax_mode`
      `estimate_only`, the money policy and the server time.

### 15.4 Billing — the product

- [ ] Add one product through the catalog screen; add opening stock as an `opening`
      movement.
- [ ] The billing grid shows **fifteen numbered rows**, search inside the Item cell, header
      fields across the top, `GRAND TOTAL` at the foot, navigation in a right-hand column.
- [ ] Search is auto-focused on load and after every added line. **This single behaviour is
      what makes keyboard-only billing possible (FR-BILL-001).**
- [ ] Results from the first character, ~120 ms debounce, prior request aborted.
- [ ] `↓`/`↑` highlight, `Enter` adds, `Esc` closes, focus returns to search.
- [ ] `Space` types a space — a part is called `AXEL PACKING`. The window-level key
      handler returns immediately unless `Ctrl` is held, so spreadsheet keys never reach
      `Print Bill`.
- [ ] Exactly four actions, no fifth: `Print Bill`, `Credit Bill`, `Save a Draft`,
      `Clear Draft`.
- [ ] ≥20 bill tabs, all reachable, switching is instant and local-only.
- [ ] Closing a tab with items warns and offers to save it as a draft.
- [ ] Line removal has a 5-second inline undo, not a confirmation dialog.
- [ ] `GRAND TOTAL` is labelled *"preview — confirmed on completion"*.

### 15.5 Finalisation

- [ ] `Print Bill` (`Ctrl+Enter`) issues an invoice, reduces stock, and prints.
- [ ] `Credit Bill` (`Ctrl+Shift+Enter`) finalises **in one request, with no intermediate
      dialog**, requires only a persisted customer, and the confirmation says
      **"Stock has been updated."**
- [ ] `Save a Draft` (`Ctrl+S`) changes no stock and issues no invoice.
- [ ] `Clear Draft` touches no finalised sale.
- [ ] Selling more than recorded stock without the override permission is refused.
- [ ] With the permission, an oversell demands a reason of ≥10 characters and the reason is
      persisted and audited.
- [ ] A finalisation retried with the **same** `Idempotency-Key` replays the stored
      response and does **not** create a second invoice (header `Idempotent-Replay: true`).
- [ ] `stock_movements` has one row per stock change, in the same transaction.
- [ ] `audit_logs` records the finalisation.

### 15.6 Printing — a go-live gate in its own right

On the shop's **real printer and real paper**.

- [ ] Headers are exactly `S R · ITEM · QTY · PRICE · TOTAL`.
- [ ] `ESTIMATE` is left-aligned in column A on the DATE row — not a centred banner.
- [ ] **No invoice number anywhere** (OD-3 re-answered: *"No need of invoice
      numbering."*).
- [ ] `PRICE` and `GRAND TOTAL` print with **no decimal places**, no separators, no
      currency symbol.
- [ ] Empty item rows print **blank**, never the reference's `0`.
- [ ] No signature block, no amount in words, no tax/GST line, no paid/balance block on a
      cash bill — all four are **verified absent** from the owner's workbook.
- [ ] A credit bill prints the payment/balance block; a cash bill does not. Same layout
      otherwise.
- [ ] Reprint is marked `DUPLICATE COPY` and **never** calls a finalisation endpoint.
- [ ] Long item names wrap fully on paper.
- [ ] Compared side by side against the owner's own Excel bill, by the owner.

> ⚠️ The print **layout** is verified from the workbook. The workbook sets **no page setup,
> no print area, no paper size, no orientation and no scaling** (OD-6, §12.1b), so paper
> and orientation are still an acceptance input. A verified layout is not a verified print.

### 15.7 Offline — the design's whole point

DevTools → Network → Offline, or unplug the shop's network.

- [ ] The app opens, the cached catalog searches, drafts create/edit/switch/delete.
- [ ] A customer can be created offline.
- [ ] A counter slip prints — watermarked, un-numbered, "not an invoice".
- [ ] `Print Bill` **and** `Credit Bill` are both visibly **disabled with the reason
      stated**. Credit is a sale: it is refused offline exactly like cash.
- [ ] Payments, purchase confirmations, returns, adjustments and import applies are all
      disabled.
- [ ] Every cached price and stock figure is rendered with its `as_of` time.
- [ ] Back online: drafts and customers sync; outcomes are explicit
      (`accepted`/`replayed`/`conflicted`/`rejected`) and conflicted or rejected items
      stay visible until somebody acts.
- [ ] **Nothing is dropped silently, and no draft is ever auto-closed or auto-finalised.**
- [ ] Blocking the IndexedDB quota produces a **loud** warning that offline safety is off,
      then online-only operation. Never a silent degrade.

### 15.8 Back office

- [ ] Customers, products, purchases, credit, reports and admin all load.
- [ ] Stock movement report matches the movements made above.
- [ ] A file import: upload → parse → row decisions → apply. **Needs the worker service**
      ([§5](#5-railway-environment-variables)); without it the upload is accepted and
      never parsed.
- [ ] Re-uploading the same file → `409 DUPLICATE_FILE`.
- [ ] A `price_list` import produces **zero** stock movements.
- [ ] Uploads survive a Railway redeploy — proves the Volume is mounted.
- [ ] No cost price or margin is visible to a role without `catalog.cost.read`.

### 15.9 Accessibility and speed

- [ ] Every billing task completable by keyboard alone.
- [ ] Visible focus everywhere; zero serious/critical axe violations per screen.
- [ ] Product search feels instant on the shop's real machine and connection.
- [ ] No HTTP status code, stack trace or jargon is ever shown; every error says what to do
      next.

### 15.10 Go-live gate

`DEPLOYMENT.md §11.2` and `PRD.md §14` are the authority. The items **this** deployment
adds or settles:

- [ ] Neon PITR enabled, and **a restore demonstrated** (§7.1). An untested backup is
      treated as no backup; a failed or skipped drill blocks the release.
- [ ] `BM_PASSWORD_PEPPER` backed up outside Railway and outside the developer machine.
- [ ] Alerting configured with a **named** person, or consciously accepted as an open
      OD-19 gate.
- [ ] The rollback procedure (§16) rehearsed once.
- [ ] Owner briefed that `ESTIMATE` documents are not tax invoices.
- [ ] Break-glass procedure stored outside the system (`SECURITY.md §12.2`).

---

## 16. Rollback procedure

### 16.1 Decide what is actually broken

| Symptom | Likely cause | Go to |
|---|---|---|
| Front end broken, API healthy | A Vercel build | §16.2 |
| API 5xx or crash-looping, schema unchanged | A Railway build or a variable | §16.3 |
| `/readyz` 503 `migrations.status: mismatch` | Code and schema disagree | §16.4 |
| Wrong data written | A logic error | §16.5 |
| Data lost or corrupted | | §16.6 |

### 16.2 Vercel — seconds

Vercel → Deployments → the last known-good one → **Promote to Production**. Instant
atomic switch, no rebuild.

If the bad deploy shipped a service worker, a returning browser may hold it. The worker
uses `registerType: 'prompt'` with `skipWaiting: false`, so it will not silently swap —
but tell the shop to accept the update prompt, or hard-reload.

### 16.3 Railway — minutes

Railway → Deployments → the last known-good one → **Redeploy**.

With **no migration in between, this is always safe** and is the first option to reach for.
It is only safe because of expand → migrate → contract (`DATA-MODEL.md §6.3`): a release
never renames or drops a column in the same release that stops using it, so older code
still runs against the newer schema.

If the cause is a variable rather than the build, fix the variable and redeploy — do not
roll back code for a configuration error.

### 16.4 A migration is involved

| Case | Action | Time |
|---|---|---|
| **Additive** (new nullable column, new table, new index) | **Roll back the app only. Leave the schema.** It is harmless. This is the default and the reason expand/contract is mandatory. | <10 min |
| **Destructive**, with a tested `downgrade()` that preserves data | `./scripts/prod.ps1 alembic downgrade -1`, then redeploy the previous image. Confirm a fresh Neon snapshot timestamp **first**. | <30 min |
| **Destructive**, no safe downgrade | Neon PITR restore to just before the migration. §16.6. | ≤4 h (RTO) |

Every migration in this repository ships with a reviewed `downgrade()` tested in CI
(upgrade → downgrade to base → upgrade again → `alembic check`). That is what makes the
middle row a real option rather than a hope.

🔴 **Never hand-edit `invoices`, `invoice_items`, `payments` or `stock_movements` in SQL**
to undo a bad release. Use the application's cancellation, return, credit-note and reversal
workflows (ADR-015). The privilege model means `bm_app` cannot do it anyway — which is the
point.

### 16.5 Bad data from a logic error

1. Stop the bleeding: roll back the app (§16.3).
2. Scope it: `audit_logs` records every state change with before/after values for
   sensitive fields, in the same transaction as the change. That is what makes this
   answerable.
3. Correct it **forward**: cancellation, return, credit note or reversal. A financial
   record is never edited or deleted.
4. If the volume makes that impractical, PITR (§16.6) and re-enter — a smaller problem
   when it is caught the same day.

### 16.6 Neon point-in-time restore

```
1  Note the exact timestamp to restore to (just before the damage).
2  Neon Console → branch `production` → Restore → that timestamp.
   Neon keeps the pre-restore state as a separate branch, so the restore is itself
   reversible. Do not delete that branch until the restore is accepted.
3  ./scripts/prod.ps1 python scripts/production_db.py verify
4  Spot-check: the last 10 invoices, stock for 10 products, one customer's outstanding.
5  Confirm /readyz is green and the Alembic head still matches the deployed code.
6  Record the date, duration, restored timestamp and result in this repository.
```

Target RTO ≤ 4 hours; the drill measures the real number and the measured figure replaces
the target once known. **Rehearse this on a scratch Neon branch before go-live** — it is a
release gate (NFR-REL-003, AC-10), not a nice-to-have.

### 16.7 During any backend outage

The counter keeps creating and editing drafts, and nothing finalises. The offline design
turns an outage into a delay rather than a stoppage
(`OFFLINE-SYNCHRONIZATION.md §8`). Tell the shop: keep billing into drafts, finalise when
the API is back, and **nothing you have typed is lost.**

---

## 17. Future Alembic migrations

Migrations are a separate, explicit, manual step. They never run on deploy (ADR-016).

### The procedure

```
 1  Write the migration. One logical change. A TESTED downgrade() — no downgrade, no merge.
 2  Expand -> migrate -> contract for any table holding live financial data. Never rename
    or drop a column in the same release that stops using it.
 3  CI green: upgrade head, alembic check, downgrade base, upgrade head again, alembic check.
 4  Confirm a fresh Neon snapshot exists. Note its timestamp.
 5  ./scripts/prod.ps1 python scripts/production_db.py inspect
       -> expect BEHIND. Read the revision it is at.
 6  ./scripts/prod.ps1 alembic upgrade head
       -> `production_db.py migrate` is for a FIRST, EMPTY database: it refuses a database
          holding business data, by design. For a live schema upgrade, run Alembic directly
          through the wrapper — prod.ps1 still loads the verified target and still refuses
          pytest, and `inspect` in step 5 is what confirmed the target.
 7  ./scripts/prod.ps1 alembic current     -> must equal the code's head
 8  Deploy the new Railway image.
 9  /readyz turns green only when the heads match.
10  Smoke test: sign in, search, create a draft, finalise a one-line test sale, cancel it.
11  Watch the error rate and latency for 15 minutes.
12  Record the rollback decision point.
```

Steps 4 and 11 are the ones most often skipped and most often regretted.

### Rules that do not relax

- **One logical change per migration**, with a tested `downgrade()`.
- **Expand → migrate → contract** is mandatory on any table with live financial data. A
  release that renames or drops a column in the same release that stops using it is
  rejected in review.
- **`CREATE INDEX CONCURRENTLY` on a populated table**, in a non-transactional migration.
  None of the current 21 needs this; the first one on a populated `products` or
  `stock_movements` will.
- **Data migrations touching financial rows must be idempotent and log row counts.**
- **Never hand-edit `invoices`, `invoice_items`, `payments` or `stock_movements` in SQL.**
  Use the reversal workflows.
- **Seeds cover permissions, system roles and units only.** Never a business, a tax rate,
  a product or demo data.
- Prefer a database `CHECK` over a comment: if an invariant can be a constraint, make it
  one.

### Deploy outside shop hours

A migration is quick, but `/readyz` goes 503 between the migration and the new image
reaching every replica. During that window the API refuses traffic and the counter stays on
drafts — correct behaviour, and still a window.

---

## 18. Keeping demo data out of production

The shop's database must never contain a placeholder. This is not a matter of care at the
keyboard; it is enforced in several independent places, and worth knowing because the
project has already been bitten by all three failure modes.

### 18.1 What already protects it

| Control | Where | What it stops |
|---|---|---|
| **Migrations seed reference data only** | `CLAUDE.md §7`; migrations 0005, 0016 | `alembic upgrade head` can never create a business, a tax rate, a product, a customer, a demo account or a default password. |
| **`provision` cannot write business data** | `scripts/production_db.py` | It contains no statement touching `products`, `customers`, `invoices`, `stock_movements`, `payments`, `draft_bills` or `purchases`. Not a policy — an absence of code. |
| **`provision` refuses to run twice** | same | A second business would be a second tenant, and every query is scoped by `business_id` from the token, so the shop would silently see an empty catalogue. |
| **The test suite refuses a non-scratch database** | `tests/conftest.py` | The suite truncates every business table between tests. Its allow-list is keyed on the **database name** in the resolved URL, and `neondb` is not on it and does not end `_test`/`_ci`/`_scratch`. A refusal, not a skip — a skip would report green. |
| **`prod.ps1` refuses to run `pytest`** | `scripts/prod.ps1` | The same thing, one layer earlier. Belt and braces, because the failure is unrecoverable and the check costs nothing. |
| **`production_db.py` refuses a local or placeholder URL** | `_guard_target` | The reverse accident: a production command aimed at `bm`. |
| **`seed_dev.py` is not published** | `.gitignore` | It hard-codes a password that is in live use on the owner's own install, and these repositories are public. |
| **`seed_demo.py` / `seed_e2e_tenant.py` name themselves** | both scripts | `DEMO — NOT A REAL BUSINESS` and `E2E FIXTURE — NOT A REAL BUSINESS` — names that could not be mistaken for the shop, so a stray row is visible rather than plausible. |
| **`verify` counts every business table** | `production_db.py` | `count(*)` on all 42, computed by subtraction from `Base.metadata`, so a table added later is checked automatically. |

### 18.2 Rules for the operator

🔴 **Never run any of these against Neon:**

```
scripts/seed_dev.py          # a demo tenant with a hard-coded, live password
scripts/seed_demo.py         # 100 000 products and 200 customers
scripts/seed_e2e_tenant.py   # the E2E fixture tenant
pytest / bt.ps1              # truncates every business table between tests
```

They take their URL from `BM_MIGRATION_DATABASE_URL` or `BM_DATABASE_URL`, so running one
inside `prod.ps1` would aim it at Neon. `prod.ps1` only blocks `pytest` by name. **Do not
run a seeder through it.**

**Other rules:**

- **One Neon branch per environment.** Staging gets its own **empty** branch, migrated the
  same way and provisioned with an obviously-fake business name. 🔴 Never branch *from*
  `production` for staging once the shop is live — a Neon branch is a copy-on-write clone
  and would contain real customers, prices and takings behind a different connection
  string with no audit trail.
- **Opening stock arrives as `opening` movements through the application**, never a direct
  `INSERT` into `inventory_balances`. Every stock change is a movement row in the same
  transaction (`CLAUDE.md §5` invariant 4); a hand-inserted balance has no movement behind
  it and the nightly reconciliation will correctly report drift it cannot explain — and
  reconciliation must **alert**, never auto-repair, because drift means a bug to find.
- **Business identity comes from the owner's own file, or stays NULL.** `legal_name` and
  `tax_identifier` are NULL today and that is correct: the workbook shows a trading name
  rather than a registered legal name, and no GSTIN appears in it. OD-4 is a go-live gate,
  not a display problem. **Never invent either.** A plausible placeholder in a database is
  exactly how a placeholder reaches a customer's bill — which is why
  `set_business_identity.py` exists at all.
- **`tax.mode` stays `estimate_only`** until the FR-TAX-003 gate passes. Never emit "Tax
  Invoice", never invent a GSTIN, a rate or an HSN code.

### 18.3 Check it after go-live

Run the queries in [§14](#14-empty-production-database-verification) — the `ILIKE '%DEMO%'`
and `username IN ('demo-owner','e2e-owner')` ones in particular — once a week for the first
month, then whenever a developer has touched the production configuration.

---

## 19. Local vs Railway vs Vercel — who does what

| Action | Local machine | Railway | Vercel | Neon |
|---|---|---|---|---|
| Run the test suite | ✅ `bt.ps1` → `bm_test` | ❌ | ❌ | ❌ never |
| Build the API image | ❌ | ✅ from the Dockerfile | ❌ | — |
| Build the front-end bundle | ✅ for verification | ❌ | ✅ authoritative | — |
| Create the `bm_app` role | ✅ run the SQL | ❌ | ❌ | ✅ executes it |
| **Run `alembic upgrade head`** | ✅ **only here** | ❌ never (ADR-016) | ❌ | ✅ receives it |
| Hold `BM_MIGRATION_DATABASE_URL` | ✅ `.env.production` | 🔴 **never** | 🔴 never | — |
| Hold `BM_DATABASE_URL` | ✅ for admin scripts | ✅ runtime | 🔴 never | — |
| Hold the three secrets | ✅ `.env.production` | ✅ runtime | 🔴 never | — |
| Provision business/branch/counter/owner | ✅ `production_db.py provision` | ❌ | ❌ | ✅ receives it |
| Verify the schema and emptiness | ✅ `production_db.py verify` | ❌ | ❌ | ✅ read |
| Serve the API | ❌ | ✅ | ❌ | — |
| Serve the SPA | ❌ | ❌ | ✅ | — |
| Store uploaded files | ❌ | ✅ on a Volume | ❌ | — |
| Run the job worker | ❌ | ✅ optional 2nd service | ❌ | — |
| Point-in-time restore | ❌ initiate only | ❌ | ❌ | ✅ |
| Hold the shop's data | 🔴 never | ❌ | ❌ | ✅ only here |

**The short version.** The developer machine is the **only** place that ever holds a
DDL-capable production credential, and the **only** place a migration is ever run from.
Railway runs the API as a role that cannot alter the schema or mutate a finalised financial
record. Vercel holds one public URL and no secret at all. Neon holds the data, and nothing
else does.

---

## 20. Deployment order and dependencies

Every step depends on the one before it. The dependencies are real, not procedural — doing
them out of order produces the failures named in the right-hand column.

```
 0  PREPARE (local, no external effect)
    ├─ copy .env.production.example -> .env.production
    ├─ generate three secrets (DIFFERENT values)
    ├─ git check-ignore -v .env.production          -> must be ignored
    └─ decide the TOPOLOGY (§10.4): own domain, provider domains, or Vercel proxy
                                                       ^ decide now; it sets 3 variables

 1  NEON
    ├─ 1a  run scripts/neon-init-roles.sql as neondb_owner      [CREATES bm_app]
    ├─ 1b  collect the DIRECT url (neondb_owner) and POOLED url (bm_app)
    ├─ 1c  paste both into .env.production
    ├─ 1d  production_db.py inspect                 -> expect FRESH
    ├─ 1e  production_db.py migrate --yes           -> 0001..0021
    └─ 1f  production_db.py verify                  -> every check PASS
                                                       ^ ~50 assertions, §13/§14

 2  PROVISION  (depends on 1f)
    ├─ 2a  $env:BM_PROVISION_OWNER_PASSWORD = "<passphrase>"
    ├─ 2b  production_db.py provision --dry-run
    ├─ 2c  production_db.py provision --yes
    └─ 2d  Remove-Item Env:\BM_PROVISION_OWNER_PASSWORD

 3  RAILWAY  (depends on 1f — deploy before migrating and /readyz is correctly 503)
    ├─ 3a  project from GitHub, region SINGAPORE, builder Dockerfile target runtime
    ├─ 3b  attach a Volume at /var/lib/bm/storage
    ├─ 3c  variables (§5). BM_PASSWORD_PEPPER identical to .env.production
    ├─ 3d  port 8000, healthcheck /healthz
    ├─ 3e  deploy; generate the public domain
    ├─ 3f  GET /healthz  -> 200
    └─ 3g  GET /readyz   -> 200, migrations.status "ok"
                              ^ 503 mismatch here means step 1e did not happen

 4  VERCEL  (depends on 3e — you need the API URL first)
    ├─ 4a  import the repo, preset Vite, root ".", Node 22
    ├─ 4b  VITE_API_BASE_URL = <per the topology chosen in step 0>
    ├─ 4c  deploy
    └─ 4d  note the production domain
                              ^ needed by step 5

 5  CLOSE THE LOOP  (depends on 4d and 3e — mutual, so it is two passes)
    ├─ 5a  Railway: BM_CORS_ALLOWED_ORIGINS = the Vercel origin (exactly)
    ├─ 5b  Railway: BM_REFRESH_COOKIE_SAMESITE per the topology
    ├─ 5c  Railway redeploy (variables are read once at startup)
    └─ 5d  Vercel: confirm VITE_API_BASE_URL matches the Railway domain; REDEPLOY if not
                              ^ read at BUILD time — a restart is not enough

 6  VERIFY  (§11, §12, §13, §14)
    ├─ 6a  health checks, startup log clean
    ├─ 6b  sign in; change the owner password
    ├─ 6c  🔴 hard-reload       -> still signed in
    ├─ 6d  🔴 idle 16+ minutes  -> still signed in
    └─ 6e  database verification, zero business rows

 7  ACCEPTANCE  (§15) — on the shop's machine, browser and printer

 8  REAL DATA  (only after 7)
    ├─ products, prices, aliases via the import or catalog screens
    ├─ opening stock as `opening` MOVEMENTS through the application
    └─ customers as they arrive

 9  GO-LIVE GATE  (§15.10 + DEPLOYMENT.md §11.2 + PRD.md §14)
    ├─ Neon PITR on, and a restore DEMONSTRATED
    ├─ BM_PASSWORD_PEPPER backed up off this machine
    ├─ alerting to a NAMED person, or OD-19 consciously accepted as open
    ├─ the print sample accepted by the owner on real paper
    └─ rollback rehearsed
```

### The circular dependency in step 5, and why it is two passes

Railway needs the Vercel origin for CORS; Vercel needs the Railway origin for
`VITE_API_BASE_URL`. Neither can be known first. So: deploy Railway with a placeholder
CORS value, deploy Vercel against the real Railway domain, then come back and set the real
CORS value and redeploy Railway.

The asymmetry that catches people: **Railway reads its variables at process start** — a
redeploy or restart is enough. **Vercel bakes `VITE_API_BASE_URL` into the bundle at build
time** — a restart does nothing; it needs a **rebuild**.

### Three things that are not steps, and are not optional either

- **The topology decision in step 0.** It sets `BM_REFRESH_COOKIE_SAMESITE`,
  `BM_CORS_ALLOWED_ORIGINS` and `VITE_API_BASE_URL` together. Deciding it late means
  redoing steps 3c, 4b and 5 — and the wrong choice is invisible until step 6d.
- **`neon-init-roles.sql` before `migrate`.** §2.2. Out of order, you get a complete schema
  with no append-only protection, a passing migration and no error anywhere.
- **`BM_PASSWORD_PEPPER` identical in Railway and `.env.production`.** Different values and
  the owner provisioned in step 2 cannot sign in at step 6b, with
  `INVALID_CREDENTIALS` — indistinguishable from a wrong password.
