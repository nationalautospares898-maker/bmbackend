# Deployment — `bmbackend`

| Field | Value |
|---|---|
| Status | **Phase 1 proposal — nothing has been provisioned, deployed or configured** |
| Last updated | 2026-10-29 |

> **Nothing was deployed during Phase 1.** No cloud resource was created, no environment variable
> set, no migration run, no container built or pushed. Hosting provider and region are open
> questions (Q-OPS-1).

---

## 1. Environments

| Environment | Purpose | Data | Who deploys |
|---|---|---|---|
| **local** | Development on a workstation | Synthetic seed only | Developer, via Docker Compose |
| **staging** | Pre-release verification, migration rehearsal, restore drills | Synthetic or anonymised; **never a copy of production credentials** | CI, on merge to `main` |
| **production** | The shop | Real | CI, on an explicit tagged release with manual approval |

Hard rules:
- Separate database instance, separate secrets, separate storage bucket per environment.
- A staging deploy **cannot reach production data** — enforced by distinct credentials and network
  rules, not by convention.
- Production deploys are tagged releases with a human approval step. No direct deploy from a laptop.

---

## 2. Runtime topology (v1)

```
            ┌──────────────────────────┐
  Internet  │  Platform ingress / TLS   │  HTTPS, HSTS, body-size cap
 ───────────►  (managed load balancer)  │
            └─────────────┬────────────┘
                          │
              ┌───────────▼───────────┐      ┌────────────────────────┐
              │  api container  ×1     │─────►│ Managed PostgreSQL 16   │
              │  uvicorn, /healthz,    │      │ single primary          │
              │  /readyz               │      │ PITR, daily snapshots   │
              └───────────┬───────────┘      └────────────┬───────────┘
                          │                               │
              ┌───────────▼───────────┐                   │
              │  worker container ×1   │───────────────────┘
              │  job queue consumer    │
              └───────────┬───────────┘
                          │
              ┌───────────▼───────────┐
              │ Object storage bucket  │ private, uploads only
              └────────────────────────┘
```

**One API replica at launch.** This is a deliberate choice, not an oversight: the in-process rate
limiter and in-process scheduling assumptions are only correct at one replica. §9 documents exactly
what must change before scaling out.

Frontend is deployed **separately from its own repository** (static hosting / CDN) and is not part of
this pipeline.

---

## 3. Configuration

All configuration is environment variables, read once into a typed settings object at startup.
**Startup fails fast on a missing or malformed required variable** — a half-configured financial
system must not accept traffic.

| Variable | Required | Example / default | Notes |
|---|---|---|---|
| `BM_ENV` | ✅ | `production` | `local` / `staging` / `production` |
| `BM_DATABASE_URL` | ✅ | `postgresql+psycopg://bm_app:…@host:5432/bm` | App role, **not** the migration role |
| `BM_MIGRATION_DATABASE_URL` | deploy step only | — | DDL-capable role; never present in the API container |
| `BM_JWT_SIGNING_KEY` | ✅ | 32+ random bytes | Rotation: see §10 |
| `BM_JWT_PREVIOUS_KEY` | – | — | Accepted during a rotation overlap |
| `BM_ACCESS_TOKEN_TTL_SECONDS` | – | `900` | |
| `BM_REFRESH_TOKEN_TTL_DAYS` | – | `30` | |
| `BM_PASSWORD_PEPPER` | ✅ | secret | |
| `BM_IP_HASH_PEPPER` | ✅ | secret | |
| `BM_CORS_ALLOWED_ORIGINS` | ✅ | `https://billing.example.com` | Explicit list, no wildcard |
| `BM_STORAGE_BACKEND` | ✅ | `s3` / `local` | |
| `BM_STORAGE_BUCKET` / `BM_STORAGE_PATH` | ✅ | — | Private; outside any served path |
| `BM_STORAGE_ACCESS_KEY` / `BM_STORAGE_SECRET` | if `s3` | — | Secret store |
| `BM_MAX_UPLOAD_MB` | – | `10` | Also capped at ingress |
| `BM_LOG_LEVEL` | – | `INFO` | |
| `BM_LOG_FORMAT` | – | `json` | |
| `BM_SENTRY_DSN` | – | — | Error reporting |
| `BM_DB_POOL_SIZE` / `BM_DB_MAX_OVERFLOW` | – | `10` / `5` | |
| `BM_DB_STATEMENT_TIMEOUT_MS` | – | `15000` | `5000` for interactive routes |
| `BM_DB_LOCK_TIMEOUT_MS` | – | `3000` | Fail fast instead of hanging a counter |
| `BM_WORKER_CONCURRENCY` | – | `2` | |
| `BM_ENABLE_API_DOCS` | – | `false` in production | |
| `BM_RATE_LIMIT_ENABLED` | – | `true` | |

**Never an environment variable:** tax rates, business identity, invoice prefixes, draft limits,
credit terms. Those are **database settings** so the owner can change them without a deploy
(FR-ADMIN-004). The split matters: anything the owner may legitimately change lives in `settings`;
anything that is infrastructure lives in the environment.

`.env` is git-ignored. Only `.env.example` with placeholder values is committed (NFR-SEC-009).

---

## 4. Local development

```bash
# one-time
cp .env.example .env            # placeholders are safe for local
docker compose up -d db         # PostgreSQL 16 with pg_trgm, unaccent, citext
uv sync                         # locked dependencies
uv run alembic upgrade head
uv run python -m bm.cli seed-reference   # permissions, roles, units ONLY

# run
uv run uvicorn bm.main:app --reload --port 8000
uv run python -m bm.worker

# verify
uv run pytest
uv run ruff check . && uv run mypy src/
```

`seed-reference` seeds permissions, system roles and default units. It **does not** seed a business,
tax rates, products or demo data — a developer must create a business explicitly, so nobody ever
confuses seeded placeholders for owner-supplied facts (`DATA-MODEL §6`).

> These commands are the **intended** interface. They are not yet runnable: no `pyproject.toml`,
> `docker-compose.yml`, migration or source file exists. They are specified here so Phase 2A builds
> to a known contract, and they are mirrored in `README.md` and `CLAUDE.md`.

---

## 5. Build and release

```
  commit ──► CI: lint, types, tests, audit ──► build image ──► push to registry
                                                      │
                                      tag vX.Y.Z ─────┴──► staging deploy (automatic)
                                                              │
                                                     manual approval
                                                              │
                                                     production deploy
```

- Image tagged with the git SHA **and** the semver tag; the SHA is what gets deployed, so a rollback
  is unambiguous.
- Build is reproducible: locked dependencies, multi-stage Dockerfile, non-root runtime user,
  no build tools in the final layer.
- `BM_RELEASE` (git SHA) is injected and reported by `/api/v1/meta` and in error reports, so a bug
  report maps to an exact build.

---

## 6. Database provisioning and migrations

### 6.1 Provisioning

Managed PostgreSQL 16 with: automated daily snapshots, **PITR enabled** (RPO ≤ 5 min, NFR-REL-002),
encryption at rest, TLS-only connections, private networking, and two roles —
`bm_migrate` (DDL) and `bm_app` (DML, with the `REVOKE`s from `SECURITY.md §7`).

Extensions installed in migration `0001`: `pg_trgm`, `unaccent`, `citext`.

### 6.2 Migration procedure — the important part

Migrations are a **separate, explicit deploy step. They never run on application start**
(ADR-016). Automatic start-up migrations mean every replica restart is a potential schema change,
and a crash-loop becomes a migration loop.

```
1  Announce a maintenance moment if the migration is not backward-compatible
2  Verify a fresh snapshot exists and note its timestamp
3  Run `alembic upgrade head` with BM_MIGRATION_DATABASE_URL  (one-off job/container)
4  Confirm `alembic current` == code's expected head
5  Deploy the new application image
6  /readyz turns green only when the heads match  (NFR-OPS-001)
7  Smoke test: login, search, create draft, finalise a 1-line test sale, cancel it
8  Watch error rate and latency for 15 minutes
```

**Expand → migrate → contract** is mandatory for any change to a table holding live financial data
(`DATA-MODEL §6.3`). A deploy that renames or drops a column in the same release that stops using it
is rejected in review.

### 6.3 Migration rollback

| Case | Action |
|---|---|
| Additive migration (new nullable column/table/index) | Roll back the **app only**; the schema is harmless |
| Destructive migration | `alembic downgrade -1` **if** the tested downgrade preserves data; otherwise PITR restore |
| Data migration already applied | Prefer a forward fix. Never hand-edit financial rows; use the application's reversal/credit-note workflows |

Every migration ships with a reviewed `downgrade()` tested in CI, and the **first** rollback option
is always "roll back the app, leave the schema" — which is only possible because of expand/contract.

---

## 7. Backups and restore

| Item | Setting |
|---|---|
| Snapshots | Daily, retained 30 days |
| PITR | Continuous WAL, 7-day window → RPO ≤ 5 min |
| Weekly off-provider copy | Encrypted logical dump to separate storage, retained 90 days — protects against account-level loss, not just instance failure |
| Uploads | Bucket versioning + lifecycle retention |
| Encryption | At rest and in transit |
| Access | Restricted; restores are audited |

### 7.1 Restore drill — a release gate, not a nice-to-have

Quarterly, and before the first go-live (NFR-REL-003, AC-10):

```
1  Provision a scratch database
2  Restore the latest snapshot + PITR to a chosen timestamp
3  Point a staging app at it, run `alembic current`
4  Run the full invariant suite against the restored data   ← the actual proof
5  Spot-check: last 10 invoices, stock for 10 products, one customer's outstanding
6  Record the date, duration, restored timestamp and result in this repository
7  Destroy the scratch resources
```

**An untested backup is treated as no backup.** A failed or skipped drill blocks the next release.
Target RTO ≤ 4 hours; the drill measures the real number and the recorded figure replaces the target
once known.

---

## 8. Health checks and monitoring

| Endpoint | Checks | Used by |
|---|---|---|
| `GET /healthz` | Process responsive | Platform liveness |
| `GET /readyz` | DB reachable **and** Alembic head matches code | Platform readiness, deploy gate |

A replica with a mismatched migration head **must not receive traffic** — this is what prevents a
half-migrated fleet from writing inconsistent data.

### 8.1 Alerts (each must reach a real person before go-live)

| Alert | Condition | Severity |
|---|---|---|
| API down | Health probe fails 2× consecutively | Critical |
| Error rate | 5xx > 1% over 5 min | Critical |
| Finalisation failures | Non-business-rule finalisation errors > 0 in 5 min | Critical |
| Database connections | > 80% of pool for 5 min | Warning |
| Latency | Finalise p95 > 1 s for 10 min | Warning |
| Reconciliation discrepancy | Any nightly discrepancy | **Critical** — stock and ledger disagree |
| Oversell overrides | > 5/day | Warning — possible process problem or abuse |
| Job queue | Depth > 50 or oldest job > 15 min | Warning |
| Dead-letter jobs | Any | Warning |
| Sync conflicts | Rate > 5% of operations | Warning |
| Backup | Snapshot missing for 26 h | Critical |
| Disk | > 80% used | Warning |
| Certificate | Expiry < 14 days | Warning |

### 8.2 Dashboards

Request rate/latency/errors by route; finalisation outcomes by type (`committed`,
`insufficient_stock`, `price_changed`, `replayed`); sales per counter per hour; stock movements per
reason; sync queue depth and oldest pending operation; job queue lag; database size growth.

---

## 9. Scaling — and what must change first

The documented ceiling, so nobody scales out and quietly breaks an assumption:

| Step | Trigger | Prerequisite |
|---|---|---|
| Vertical database scale | CPU/IO sustained > 70% | None |
| Add API replicas | Latency budget breached at one replica | **Replace the in-process rate limiter with a shared store** (`SECURITY §15`, `API-SPECIFICATION §7`). Confirm no in-memory state is relied on. |
| Add worker replicas | Job queue lag | None — `SKIP LOCKED` is already safe for multiple consumers |
| Read replica for reports | Report load affects billing latency | Accept replica lag in reports and label the `as_of` accordingly; **never** read stock for finalisation from a replica |
| Per-counter invoice series | Series-row lock contention | Configuration only (`counters.invoice_series_id`); confirm the numbering answer to Q-TAX-3 still holds |
| Table partitioning | `invoice_items`/`stock_movements` beyond ~5×10⁷ rows | Partition by `issued_at`/`created_at`; column choices already allow it |
| Dedicated search engine | Search p95 > 150 ms after index tuning at real catalog size | Re-open ADR-005 with measurements, not intuition |

Capacity reality check: the design load (5 counters, ~500 invoices/day) is roughly three orders of
magnitude below what a single modest Postgres instance handles. Scaling is a contingency, not a plan.

---

## 10. Secret rotation

| Secret | Procedure |
|---|---|
| `BM_JWT_SIGNING_KEY` | Set the new key as primary and the old as `BM_JWT_PREVIOUS_KEY`; both accepted for verification for one access-token lifetime (15 min); then remove the old. No user is logged out. |
| Database password | Rotate in the provider, update the secret, restart with a brief drain. Drafts survive offline, so a short outage is tolerable. |
| Storage credentials | Create new, deploy, revoke old |
| `BM_PASSWORD_PEPPER` | **Cannot be rotated without invalidating all passwords.** Treat as permanent; documented so nobody tries. |
| `BM_IP_HASH_PEPPER` | Rotatable; old hashes become uncorrelatable, which is acceptable |

---

## 11. Deployment checklist

### 11.1 Every deploy

- [ ] CI green (lint, types, all test suites, audit, secret scan)
- [ ] Migration reviewed; `downgrade()` tested; expand/contract respected
- [ ] Fresh snapshot confirmed, timestamp noted
- [ ] Deployed outside shop hours where possible
- [ ] Migration job run and head confirmed
- [ ] `/readyz` green
- [ ] Smoke test: login → search → draft → finalise test sale → reprint → cancel
- [ ] Error rate and latency watched 15 min
- [ ] Rollback decision point recorded

### 11.2 First production release — go-live gate

Mirrors `PRD.md §14`; all must be ticked:

- [ ] Every blocking question in `PRD.md §5.1` answered and configured
- [ ] `settings.tax.mode` explicitly set; if `gst_enabled`, the `FR-TAX-003` gate passes
- [ ] Business legal name, address, contact entered from owner-supplied values — **no placeholders**
- [ ] Print layout verified against the owner's actual Excel invoice on the real printer and paper
- [ ] Owner-approved invoice series format configured
- [ ] Opening stock loaded via `opening` movements and physically verified
- [ ] Products, prices and aliases loaded and spot-checked by the owner
- [ ] Users created with real names; **all seeded credentials rotated**; forced password change verified
- [ ] Counters registered; `max_open_drafts` ≥ 20 confirmed
- [ ] Devices registered; counter machines have full-disk encryption (`SECURITY §11`)
- [ ] All invariant, concurrency and isolation suites green
- [ ] Performance budgets met on production-like data
- [ ] Backups enabled **and a restore demonstrated** (§7.1)
- [ ] Monitoring, alerting and error reporting live, alerting a real recipient
- [ ] No high/critical dependency vulnerabilities
- [ ] Rollback procedure rehearsed on staging
- [ ] Staff trained on: reprint vs new sale, the "not yet confirmed" offline state and counter slip
      (E-1), oversell override policy, and what offline does **not** do
- [ ] Owner briefed that `ESTIMATE` documents are not tax invoices
- [ ] Break-glass procedure stored outside the system (`SECURITY §12.2`)

---

## 12. Rollback

| Scenario | Action | Expected time |
|---|---|---|
| Bad app release, additive migration | Redeploy previous image SHA | < 10 min |
| Bad app release, destructive migration | Tested `alembic downgrade -1`, then previous image | < 30 min |
| Data corruption discovered | Stop writes, PITR restore to just before, run invariant suite, resume | ≤ 4 h (RTO) |
| Provider outage | Wait; counters keep capturing drafts offline; communicate expected recovery | Provider-dependent |

During any backend outage the counter can still create and edit drafts, and nothing finalises — the
offline design turns an outage into a delay rather than a stoppage (`OFFLINE-SYNCHRONIZATION §8`).

---

## 13. Disaster recovery

| Scenario | Plan |
|---|---|
| Database instance lost | Restore from snapshot + PITR into a new instance, repoint, verify invariants |
| Region outage | Restore the weekly off-provider dump into another region; accept up to one week of RPO on this path and **say so plainly** rather than implying hot standby |
| Storage bucket lost | Uploads are re-uploadable; import jobs retain their parsed rows in the database, so audit history survives |
| Total account compromise | Off-provider encrypted backup + documented rebuild; secrets rotated; `SECURITY §12.4` |

Multi-region hot standby is **not** in v1 and is not implied by the 99.5% target.

---

## 14. Open deployment questions

| ID | Question | Recommended default |
|---|---|---|
| Q-OPS-1 | Hosting provider and region | A managed platform (e.g. Fly.io / Render / Railway / AWS Lightsail-class) in the region nearest the shop, with managed Postgres |
| Q-OPS-2 | Acceptable RPO | 5 minutes (PITR) |
| Q-OPS-3 | Who receives production alerts out of hours | Owner's phone + the developer; needs a named person before go-live |
| Q-OPS-4 | Custom domain | A subdomain of the owner's domain if one exists, else a provider domain initially |
| Q-OPS-5 | Monthly infrastructure budget | Target under a small fixed monthly figure; the single-replica topology is chosen with this in mind |
| Q-OPS-6 | Shop internet reliability and whether a 4G fallback router is justified | Measure during pilot; a cheap failover router may remove most offline scenarios |

---

## 15. Related documents

`docs/PRD.md §14` (release gates) · `docs/ARCHITECTURE.md` · `docs/DATA-MODEL.md §6` (migrations) ·
`docs/SECURITY.md` (secrets, privileges, DR) · `docs/TESTING-STRATEGY.md` (CI) ·
`docs/DECISIONS.md` (ADR-016, ADR-019) · `README.md`
