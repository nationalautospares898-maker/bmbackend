# Production environment variables — master reference

Every environment variable in both repositories, in one place. Derived from the source:
`bmbackend/src/bm/config.py` (the only configuration reader in the backend),
`bmfrontend/src/vite-env.d.ts` plus a grep of `import.meta.env` across `bmfrontend/src/`.

Per-platform detail with full reasoning:
[`RAILWAY-ENVIRONMENT-VARIABLES.md`](RAILWAY-ENVIRONMENT-VARIABLES.md) ·
[`../../bmfrontend/docs/VERCEL-ENVIRONMENT-VARIABLES.md`](../../bmfrontend/docs/VERCEL-ENVIRONMENT-VARIABLES.md)
· order of operations in [`PRODUCTION-DEPLOYMENT-GUIDE.md`](PRODUCTION-DEPLOYMENT-GUIDE.md).

---

## How to read this

**Where** — the places a variable legitimately exists.

| Code | Place |
|---|---|
| `R` | Railway API service |
| `W` | Railway worker service (optional second service) |
| `L` | `bmbackend/.env.production` on a developer machine — the migration/admin path |
| `V` | Vercel project settings |
| `D` | `bmbackend/.env` / `bmfrontend/.env.local` — local development only |

**Req** — `●` the process will not start without it · `◐` it starts and is wrong ·
`○` genuinely optional · `✗` must not be set here.

**Secret** — `🔒` set hidden/sealed; never committed, screenshotted or pasted into a
report.

**Reuse** — may the *production* value also live in a developer `.env`?

---

## 1. Backend — `bmbackend`

Prefix `BM_`, set by `env_prefix` in `config.py`. `extra="ignore"`, so **a misspelled name
is silently discarded** — there is no error for `BM_CORS_ALOWED_ORIGINS`, just CORS
quietly switched off.

### 1.1 Required

| Variable | Where | Req | Secret | Env-specific | Reuse | Format / source |
|---|---|---|---|---|---|---|
| `BM_DATABASE_URL` | `R` `W` `L` `D` | ● | 🔒 | yes | **no** | `postgresql+psycopg://bm_app:…@<pooled-neon-host>/neondb?sslmode=require` — Neon Console, role `bm_app`, pooling **on**. Validated: must start `postgresql`; `sqlite:` rejected (ADR-017). |
| `BM_JWT_SIGNING_KEY` | `R` `W` `L` `D` | ● | 🔒 | yes | **no** | ≥32 chars. `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `BM_PASSWORD_PEPPER` | `R` `W` `L` `D` | ● | 🔒 | yes | **no** | ≥32 chars, a **different** value. 🔴 **Permanent — cannot be rotated.** Must be identical in `R` and `L`. |
| `BM_IP_HASH_PEPPER` | `R` `W` `L` `D` | ● | 🔒 | yes | **no** | ≥32 chars, a third value. Rotatable. |

### 1.2 Boots without them, and is wrong

| Variable | Where | Req | Secret | Env-specific | Reuse | Value / why |
|---|---|---|---|---|---|---|
| `BM_ENV` | `R` `W` `L` `D` | ◐ | | yes | n/a | `production`. Default is already `production`; set it anyway — it controls whether the refresh cookie is `Secure`. |
| `BM_CORS_ALLOWED_ORIGINS` | `R` `L` `D` | ◐ | | yes | no | Comma-separated origins, no path, no trailing slash, never `*`. `main.py` installs `CORSMiddleware` **only when non-empty** — unset means `curl` works and every browser request fails preflight. |
| `BM_REFRESH_COOKIE_SAMESITE` | `R` `L` | ◐ | | yes | yes | `lax` \| `strict` \| `none`. 🔴 `none` is **required** when the front end and API are on different registrable domains (`*.vercel.app` + `*.up.railway.app`), or sessions die 15 min after each login. `none` is refused with `BM_ENV=local`. |
| `BM_STORAGE_PATH` | `R` `W` `L` `D` | ◐ | | yes | no | On Railway, the **mount path of a Volume** (`/var/lib/bm/storage`). Without one, uploads vanish on every redeploy. The worker must mount the same volume. |
| `BM_ENABLE_API_DOCS` | `R` `L` `D` | ◐ | | yes | n/a | `false`. `/api/v1/openapi.json` is served either way — only the Swagger page is gated. |
| `BM_UNATTENDED_ACCESS` | `R` `L` | ◐ | | yes | no | 🔴 `false`. On = **anyone reachable gets a session with no password**. `main.py` logs a WARNING every start while on. |
| `BM_UNATTENDED_USERNAME` | `L` `D` | ○ | | yes | no | Read only when the flag is on; startup fails if the flag is on without it. |
| `BM_RELEASE` | `R` `W` `L` | ◐ | | yes | n/a | `${{RAILWAY_GIT_COMMIT_SHA}}` on Railway. Reported by `GET /api/v1/meta`; unset reports `unknown`. |

### 1.3 Tuned for Neon / production

| Variable | Where | Default | Production | Why |
|---|---|---|---|---|
| `BM_DB_POOL_SIZE` | `R` `W` `L` `D` | `10` | `5` | Neon's pooler does the multiplexing; a big client pool just holds server connections open. 1–100. |
| `BM_DB_MAX_OVERFLOW` | `R` `W` `L` `D` | `5` | `2` | As above. 0–100. |
| `BM_DB_STATEMENT_TIMEOUT_MS` | `R` `W` `L` `D` | `15000` | keep | ≥100. |
| `BM_DB_LOCK_TIMEOUT_MS` | `R` `W` `L` `D` | `3000` | keep | ≥100. Felt at the counter: a finalisation that cannot get its stock locks fails fast instead of hanging the till. |
| `BM_LOG_FORMAT` | `R` `W` `L` `D` | `json` | `json` | One object per line with correlation fields. `console` for a terminal. |
| `BM_LOG_LEVEL` | `R` `W` `L` `D` | `INFO` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`, case-insensitive, validated. |
| `BM_ACCESS_TOKEN_TTL_SECONDS` | `R` `L` `D` | `900` | keep | 60–3600. This is how long a session lasts if the refresh cookie is misconfigured. |
| `BM_REFRESH_TOKEN_TTL_DAYS` | `R` `L` `D` | `30` | keep | 1–365. |
| `BM_MAX_UPLOAD_MB` | `R` `W` `L` `D` | `10` | keep | 1–100. Effective cap is `min(settings.imports.max_file_mb, this)`. |
| `BM_MAX_BODY_BYTES` | `R` `L` `D` | `1048576` | keep | ≥1024. 1 MB on every request except `/api/v1/imports`, which streams. |
| `BM_WORKER_CONCURRENCY` | `W` `L` `D` | `2` | keep | 1–32. Only read by `python -m bm.worker`. |

### 1.4 Optional

| Variable | Where | Secret | Notes |
|---|---|---|---|
| `BM_JWT_PREVIOUS_KEY` | `R` `L` | 🔒 | Set **only** during a signing-key rotation, removed after. Both keys verify for one access-token lifetime, so nobody is signed out. Leaving a retired key extends the window a stolen old token works in. |
| `BM_ALERTING_ENABLED` | `R` `W` `L` | | `false` by default. **OD-19 is a go-live gate.** |
| `BM_ALERT_WEBHOOK_URL` | `R` `W` `L` | 🔒 | `https://` only (plain `http` refused when `BM_ENV=production`). For most providers **the path is the credential** — `/readyz` reports only scheme + host. |
| `BM_ALERT_RECIPIENT_NAME` | `R` `W` `L` | | A **person**, not a shared inbox. Without it the state is `no_named_recipient`. |
| `BM_ALERT_MIN_SEVERITY` | `R` `W` `L` | | `info` \| `warning` \| `critical`. Default `warning`. |
| `BM_ALERT_TIMEOUT_SECONDS` | `R` `W` `L` | | 0–30. Default `5.0`. Delivery never raises into the caller. |

Enabling alerting **without** a webhook is strictly worse than leaving it off: every alert
becomes an `ERROR` log line that reaches nobody, and `infra/alerting/channel.py` classes
that as a monitoring outage.

### 1.5 🔴 Must never exist on Railway

| Variable | Why |
|---|---|
| `BM_MIGRATION_DATABASE_URL` | `L` **only**. The DDL-capable credential exists in the migration step and nowhere else (`CLAUDE.md §11`, `alembic.ini`). On the API service it would let any container that can run Alembic bypass the column-level `REVOKE`s that make finalised financial records immutable — the control that makes T-1 and T-14 structural rather than a matter of discipline. |
| `BM_TEST_DATABASE_URL`, `BM_TEST_MIGRATION_DATABASE_URL` | `tests/conftest.py` only. The suite **truncates every business table between tests**. |
| `BM_PROVISION_OWNER_PASSWORD` | `L` only, and only for the duration of one `production_db.py provision` run. Not a runtime variable. |
| `BM_DEV_*`, `BM_E2E_*`, `BM_LOAD_*`, `BM_DRILL_*`, `BM_BACKUP_*` | Script and fixture variables (`scripts/seed_*.py`, `tests/load/`, `scripts/restore_drill.sh`, `scripts/backup.sh`). None is a `Settings` field. |

### 1.6 Declared, and read by nothing

Verified against the source, not assumed. Each is a field on `Settings` with no reader
anywhere in `src/`. Setting one changes behaviour nowhere — and creates the impression a
feature is configured when it is not.

| Variable | Why inert |
|---|---|
| `BM_SENTRY_DSN` | No Sentry SDK in `pyproject.toml`; nothing reads the field. |
| `BM_STORAGE_BACKEND` | Read **only** for a startup log line. `infra/storage/objects.py` is a filesystem store; there is no S3 code path. `s3` passes validation and does nothing. |
| `BM_STORAGE_BUCKET` | A documented seam for an object store later. No reader. |
| `BM_RATE_LIMIT_ENABLED` | **There is no HTTP rate limiter in this codebase.** `RateLimited` is an error class with no raiser. Login lockout is separate, implemented, and database-backed (5 failures in 5 min → 15-min lockout, `domain/auth/service.py`) — which also means it works across replicas. |

`docs/DEPLOYMENT.md §3` additionally lists `BM_STORAGE_ACCESS_KEY` and
`BM_STORAGE_SECRET`. Those are **not fields on `Settings` at all** — they were written for
the S3 backend that was never built. With `extra="ignore"` they would be discarded
silently.

---

## 2. Frontend — `bmfrontend`

Prefix `VITE_`, and that prefix means **compiled into a publicly readable bundle**. There
is no such thing as a secret in this repository (NFR-SEC-012).

Four names are allow-listed in `src/vite-env.d.ts`;
`tests/structural/rules.test.ts` fails the build on a fifth. Exactly **one** has a reader.

| Variable | Where | Req | Secret | Read at | Read by |
|---|---|---|---|---|---|
| `VITE_API_BASE_URL` | `V` `D` | ◐ | no | **build** | `src/api/client.ts:36` |
| `VITE_APP_VERSION` | — | ○ | no | — | **nothing in `src/`** |
| `VITE_SENTRY_DSN` | — | ○ | no | — | **nothing in `src/`**; no SDK installed |
| `VITE_ENABLE_DEVTOOLS` | — | ○ | no | — | **nothing in `src/`** |

### `VITE_API_BASE_URL`

```ts
const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '/api/v1').replace(/\/$/, '');
```

**Must include `/api/v1`.** `client.ts` appends endpoint paths directly, so a bare host
produces `https://api.example.com/auth/login` — a 404 on every call.

| Topology | Value | Also needs |
|---|---|---|
| Absolute — browser → Railway | `https://api.your-domain.com/api/v1` | `BM_CORS_ALLOWED_ORIGINS` on Railway; `BM_REFRESH_COOKIE_SAMESITE=none` **if** the domains differ |
| Relative — Vercel proxies | `/api/v1` | the `/api/:path*` rewrite in `vercel.json`. No CORS, no CSP change, no cookie change |

Read at **build** time: changing it needs a **redeploy**, not a restart.

The CSP's `connect-src` is derived from this same variable by the `bm-csp-connect-src`
plugin in `vite.config.ts`, so the policy and the request target cannot drift apart.

### Not `VITE_`-prefixed

`BM_DEV_API_PROXY` is read by `vite.config.ts` for local development and the browser
acceptance run. **Do not set it on Vercel** — Vite's dev/preview proxy does not exist in a
static production build, so it would be inert while looking like production API
configuration.

### 🔴 Never on Vercel

No Neon URL, no `BM_JWT_SIGNING_KEY`, no `BM_PASSWORD_PEPPER`, no `BM_IP_HASH_PEPPER`, no
storage credential — **including without the `VITE_` prefix**. A non-prefixed variable is
not in the bundle, but it is still a production credential in a place with no reason to
hold one, readable by everyone with access to the Vercel project.

---

## 3. Values that must match across platforms

Three pairs. A mismatch in any of them produces a failure that does not look like a
configuration problem.

| Value | Must be identical in | Symptom of a mismatch |
|---|---|---|
| `BM_PASSWORD_PEPPER` | Railway **and** `.env.production` | `production_db.py provision` writes a hash peppered with one value, the API verifies with another. The owner cannot sign in, and the error is `INVALID_CREDENTIALS` — indistinguishable from a wrong password. |
| The front-end origin | Vercel domain ↔ `BM_CORS_ALLOWED_ORIGINS` | Browser: "blocked by CORS policy". `curl` works perfectly. Compare character by character, including `https://` and any `www.`. |
| The API origin | `VITE_API_BASE_URL` ↔ Railway domain ↔ CSP `connect-src` | Requests blocked by the page's own policy, visible only as a console violation with nothing in the network tab. `vite.config.ts` keeps the CSP in step automatically; the other two are manual. |

And one triple that is not a value but a topology:

| `VITE_API_BASE_URL` host | Railway host | `BM_REFRESH_COOKIE_SAMESITE` |
|---|---|---|
| `app.your-domain.com` | `api.your-domain.com` | `lax` |
| `nas.vercel.app` | `nas-api.up.railway.app` | **`none`** |
| same origin (proxy) | — | `lax` |

---

## 4. What is NOT an environment variable

Business rules are database `settings` rows so the owner can change them without a deploy
(`CLAUDE.md §11`, FR-ADMIN-004). Defaults live in
`src/bm/domain/settings/defaults.py` and are applied **in code**, so a fresh production
database correctly contains **zero** `settings` rows and behaves identically to a
configured one.

Never put any of these in the environment. `tests/unit/test_config.py` asserts no field on
`Settings` is named after one:

`tax.mode` · `drafts.max_open_per_counter` · `drafts.stale_days` ·
`drafts.retention_days` · `sales.cancel_window_hours` · `sales.rounding_mode` ·
`sales.round_grand_total_to_rupee` · `returns.window_days` ·
`credit.default_terms_days` · `credit.enforce_limit` ·
`discount.cashier_max_percent` · `inventory.allow_oversell_override` ·
`imports.max_file_mb` · `idempotency.retention_days` ·
`sync.catalog_cache_max_rows` · `sync.finalization_stale_minutes` ·
`print.mark_reprints_duplicate` · `print.paper_profile` · `print.date_format` ·
`print.show_credit_block`

Business **identity** is likewise a database row, not configuration: legal name, address,
phone numbers, tax identifier and the financial-year start month are columns on
`businesses`. Set by `scripts/production_db.py provision` at creation, corrected
afterwards with `scripts/set_business_identity.py`.

Invoice series are not configured at all — they are created lazily inside the first
finalisation transaction (`domain/sales/numbering.py`, `P2-D-09`), so a correct fresh
database has **zero** `invoice_series` rows and the first sale after each 1 April cannot
fail for want of one.

---

## 5. Generating the secrets

Three distinct values. Run it three times; do not reuse one across two variables.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

| Secret | Rotation |
|---|---|
| `BM_JWT_SIGNING_KEY` | Rotatable with no sign-outs: new key primary, old key as `BM_JWT_PREVIOUS_KEY`, both accepted for one access-token lifetime (15 min), then remove the old. |
| `BM_IP_HASH_PEPPER` | Rotatable. Old IP hashes become uncorrelatable, which is acceptable. |
| `BM_PASSWORD_PEPPER` | 🔴 **Not rotatable.** Changing it invalidates every password in the database. Back it up outside Railway and outside the developer machine **before** go-live; if it is lost, every account including the owner's is unrecoverable. |
| Neon `bm_app` password | Rotate in the Neon console, update Railway, redeploy. Drafts survive offline, so a short outage is tolerable. |
| Neon `neondb_owner` password | Rotate in the Neon console, update `.env.production`. Never goes near Railway. |
