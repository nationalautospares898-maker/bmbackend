# Railway environment variables — `bmbackend`

Every variable below was read out of `src/bm/config.py`, which is the only place this
application reads configuration from. Nothing here is aspirational: if a variable is
listed as "read by nothing", that is because the code was checked and it is.

`Settings` uses `env_prefix="BM_"`, so the environment variable name is `BM_` + the field
name upper-cased. `extra="ignore"`, so a typo is **silently discarded** rather than
rejected — `BM_CORS_ALOWED_ORIGINS` would leave CORS switched off with no error anywhere.
Check the spelling of every name against this document, not against memory.

**Startup fails fast.** `load_settings()` wraps pydantic's error and the API refuses to
boot on a missing or malformed required variable. On Railway that surfaces as a crash loop
with the reason in the deploy log — which is the intended behaviour, not a deployment
problem to work around.

Related: `docs/PRODUCTION-ENVIRONMENT-VARIABLES.md` (both repositories in one table),
`docs/PRODUCTION-DEPLOYMENT-GUIDE.md` (the order to do things in),
`.env.production.example` (the local migration half).

---

## 0. The shape of the column headings

| Column | Means |
|---|---|
| **Mandatory** | The process will not start without it. |
| **Secret** | Set it with Railway's value field hidden / sealed. Never in a commit, a screenshot, a chat message or a report. |
| **Env-specific** | The value differs between local, staging and production, so it cannot be copied. |
| **Reuse locally?** | Whether the *production* value may also sit in a developer `.env`. "No" means copying it is a security incident, not a shortcut. |

---

## 1. Mandatory — the service will not boot without these

### `BM_DATABASE_URL`

| | |
|---|---|
| Mandatory | **Yes** |
| Secret | **Yes** — it contains the `bm_app` password |
| Env-specific | Yes |
| Reuse locally | **No** |

Format — note the driver suffix and the required TLS parameter:

```
postgresql+psycopg://bm_app:<password>@ep-xxxx-123456-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
```

Where it comes from: Neon Console → project **national auto spares** → branch
**production** → Connect → role **bm_app**, database **neondb**, **Connection pooling
ON**. Then change the scheme Neon shows you (`postgresql://`) to
`postgresql+psycopg://`. Nothing else about the string changes.

**Use the POOLED endpoint here** (the hostname contains `-pooler`). Neon's pooler is
PgBouncer in transaction mode, which is what a web API wants: Railway restarts a container
on every deploy and the application opens a fresh pool each time, and the pooler absorbs
that without consuming Neon's direct-connection allowance.

One consequence to be aware of rather than surprised by: `infra/db/session.py` issues
`SET statement_timeout`, `SET lock_timeout` and `SET idle_in_transaction_session_timeout`
on every new connection. Under PgBouncer's transaction pooling those are scoped to the
transaction that carries them rather than the whole session. The practical effect is that
the timeouts still apply to the statement that matters — a finalisation runs inside one
transaction — but they are not a standing session guarantee. If you would rather have that
guarantee than the pooling, use the direct endpoint and keep `BM_DB_POOL_SIZE` small; both
are correct and the pooled endpoint is the better default on Railway.

**Validated at startup:** must begin `postgresql`. A `sqlite:` URL is rejected outright
with an ADR-017 message.

⚠️ Do **not** use the `neondb_owner` credential here. That role owns the schema and holds
DDL rights; the whole append-only privilege model (`SECURITY.md §7`) depends on the
application connecting as a role that *cannot* `UPDATE audit_logs` or
`DELETE FROM stock_movements`. Running the API as the owner silently removes
NFR-DATA-006 and nothing reports it.

---

### `BM_JWT_SIGNING_KEY`

| | |
|---|---|
| Mandatory | **Yes** |
| Secret | **Yes** |
| Env-specific | Yes |
| Reuse locally | **No** |

At least 32 characters — validated, and startup fails below it. Generate:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Signs the 15-minute access token. Rotatable with an overlap window; see
`BM_JWT_PREVIOUS_KEY` and `docs/DEPLOYMENT.md §10`. Changing it without the overlap signs
everybody out, which is recoverable — they sign in again.

---

### `BM_PASSWORD_PEPPER`

| | |
|---|---|
| Mandatory | **Yes** |
| Secret | **Yes** |
| Env-specific | Yes |
| Reuse locally | **No** |

At least 32 characters. Generate a value **different** from the signing key.

🔴 **This one is permanent.** It is mixed into every Argon2id password hash, so changing it
invalidates every password in the database. There is no rotation procedure and
`docs/DEPLOYMENT.md §10` says so outright. Two consequences:

1. Back it up somewhere outside Railway and outside this machine, today. If you lose it,
   every account including the owner's is unrecoverable and the only remedy is a
   direct-SQL password reset performed with a *new* pepper for every user at once.
2. It must be **identical** in Railway and in `.env.production`, because
   `production_db.py provision` hashes the owner's first password with it. A hash peppered
   with a different value authenticates nobody, and the failure is indistinguishable from
   a wrong password.

---

### `BM_IP_HASH_PEPPER`

| | |
|---|---|
| Mandatory | **Yes** |
| Secret | **Yes** |
| Env-specific | Yes |
| Reuse locally | **No** |

At least 32 characters. A third distinct value. Peppers the hashed client IP that
`login_attempts` records, which is what makes login lockout work without storing raw
addresses. Rotatable — old hashes simply become uncorrelatable, which is acceptable.

---

## 2. Effectively mandatory — the service boots, and is wrong

### `BM_ENV`

| | |
|---|---|
| Mandatory | No (defaults to `production`) |
| Secret | No |
| Env-specific | Yes |
| Reuse locally | n/a |

**Set it to `production` explicitly.** The default already is `production`, and relying on
a default for the value that controls whether the refresh cookie is `Secure` is not a risk
worth taking.

Allowed: `local` · `test` · `staging` · `production`. What it changes:

- `api/v1/auth.py`: `secure = env != "local"` — the refresh cookie is `Secure` here.
- `main.py`: logs a warning if API docs are on, or the CORS list is empty, in production.
- `infra/alerting/channel.py`: refuses a plain-`http` alert webhook in production.
- `GET /api/v1/meta` reports it.

---

### `BM_CORS_ALLOWED_ORIGINS`

| | |
|---|---|
| Mandatory | No — **but omitting it breaks the browser client entirely** |
| Secret | No |
| Env-specific | Yes |
| Reuse locally | No |

```
https://app.your-domain.com
```

Comma-separated **origins**: scheme + host, with a port only if non-default. No path, no
trailing slash, never `*`. `config.py::cors_origins` splits on commas and strips blanks,
and a wildcard is never produced (`SECURITY.md §6`).

`main.py` adds `CORSMiddleware` **only when this list is non-empty**. Leave it unset and
the API answers `curl` perfectly while every request from the Vercel site fails the
browser's preflight — which looks like an outage and is a missing variable.

What to put in it:

- The Vercel **production** domain, and your custom domain if you add one. Both, if both
  are in use.
- Vercel **preview** deployments get a new hostname per commit, so they cannot be listed
  ahead of time. Either add a specific preview origin while you are testing it, or point
  previews at a separate staging API. Do not solve this with a wildcard: with
  `allow_credentials=True` a wildcard would let any site on the internet make
  credentialed requests.
- Nothing at all if you use the Vercel-proxy topology (`docs/PRODUCTION-DEPLOYMENT-GUIDE.md
  §10` option B) — then the browser only ever sees one origin and CORS is not involved.

The middleware is already configured with everything the client sends: `Authorization`,
`Content-Type`, `X-Request-Id`, `X-Device-Id`, `X-Counter-Id`, `Idempotency-Key`,
`If-Match`, and exposes `X-Request-Id`, `Idempotent-Replay`, `X-Api-Version`. You do not
need to add headers anywhere.

---

### `BM_ENABLE_API_DOCS`

| | |
|---|---|
| Mandatory | No (defaults to `false`) |
| Secret | No |
| Env-specific | Yes |
| Reuse locally | n/a |

`false` in production. Set it explicitly so the intent is visible in the dashboard.

`true` serves the interactive Swagger UI at `/api/v1/docs`. `/api/v1/openapi.json` is
served **either way** — that is the contract the front end generates its types from and
the `contract` CI job diffs against — so turning docs off costs nothing.

---

### `BM_REFRESH_COOKIE_SAMESITE`

| | |
|---|---|
| Mandatory | No (defaults to `lax`) |
| Secret | No |
| Env-specific | Yes |
| Reuse locally | Yes (`lax`) |

`lax` · `strict` · `none`. **The value depends entirely on your domain topology, and
getting it wrong is the single most likely way for this deployment to look healthy and
then fail at the counter.**

`SameSite` compares the **registrable domain**, not the origin:

| Front end | API | Same-site? | Set this to |
|---|---|---|---|
| `app.your-domain.com` | `api.your-domain.com` | Yes | **`lax`** |
| `your-domain.com` | `api.your-domain.com` | Yes | **`lax`** |
| `nas.vercel.app` | `nas-api.up.railway.app` | **No** | **`none`** |
| any | same origin (Vercel proxy) | Yes | **`lax`** |

Row three is cross-site because `vercel.app` and `up.railway.app` are both public
suffixes — so even two subdomains of `vercel.app` are cross-site. With `lax` there, the
browser never sends `bm_refresh` to `POST /api/v1/auth/refresh`. The symptoms, in order:
every cold start lands on the sign-in form even though the user signed in yesterday; then
15 minutes after each login — the access-token TTL — the session dies mid-bill.

`none` requires `Secure`, which is why `config.py` refuses `none` together with
`BM_ENV=local`.

Prefer two subdomains of one owned domain and leave this at `lax`: it keeps the CSRF
property the cookie was designed around. `none` is a correct fallback, not a free one.

#### ⚠️ This deployment is row three, and was set to `lax`

`bmfrontend-seven.vercel.app` + `bmbackend-production.up.railway.app` is **exactly** the
cross-site row, and the shipped template carried `lax`. On 2026-11-08 the owner reported
it in the only terms the counter can see it:

> *"IT'S LOGGING OUT AFTER EVERY 13 MIN AND THE WORK I WAS DOING WAS GONE."*

**13 minutes, not 15**, because `src/api/renewal.ts` renews `RENEW_MARGIN_MS` =
120 s ahead of the 900 s access token. The failure therefore lands on the *renewal*
(900 − 120 = 780 s = 13 min), not on the expiry — which is why the number in the report
did not match the TTL in the configuration and the two were never connected.

`.env.production.example` now carries `none` for this topology. **The value that
actually matters is the one in the Railway dashboard**, which this file does not set:
changing the template fixes the next deployment, not the running one.

#### It is no longer silent

Two checks were added on 2026-11-08 so this cannot be invisible again:

* **Startup** — `main.py` logs `startup_check` with
  `check=refresh_cookie_never_sent_cross_site`, the offending origins and the fix.
* **`GET /readyz`** — reports `checks.refresh_cookie`:

  ```json
  "refresh_cookie": {
    "samesite": "lax",
    "access_token_ttl_seconds": 900,
    "status": "cross_site_cookie_blocked",
    "blocked_origins": ["https://bmfrontend-seven.vercel.app"],
    "fix": "Set BM_REFRESH_COOKIE_SAMESITE=none — see PRODUCTION-DEPLOYMENT-GUIDE §10.4"
  }
  ```

  `status` is `ok` when the cookie can be delivered. **It never makes the service
  unready** — a deployment with this fault still bills correctly for fifteen minutes at a
  time, and refusing traffic would be far worse than the fault. It is reported for the
  same reason, and with the same consequence, as the alerting check beside it.

  Detection is narrow by design: it fires only for `production` + `lax` + an allow-listed
  origin on a known provider suffix, so topologies A and C never trip it
  (`tests/unit/test_config.py`).

---

### `BM_RELEASE`

| | |
|---|---|
| Mandatory | No (defaults to `unknown`) |
| Secret | No |
| Env-specific | Yes |
| Reuse locally | n/a |

```
${{RAILWAY_GIT_COMMIT_SHA}}
```

Paste that literal string — Railway substitutes the deployed commit SHA. Reported by
`GET /api/v1/meta` and attached to every alert payload, so a bug report maps to an exact
build. Left unset it reports `unknown`, which makes "which version was the counter running
when this happened?" unanswerable.

---

### `BM_STORAGE_PATH`

| | |
|---|---|
| Mandatory | No (defaults to `./storage`) |
| Secret | No |
| Env-specific | Yes |
| Reuse locally | No |

```
/var/lib/bm/storage
```

🔴 **This must be the mount path of a Railway Volume.** Railway's container filesystem is
ephemeral: without a volume, every uploaded price list and purchase file is deleted on the
next deploy, restart or crash. The *parsed rows* survive in `import_rows`, so the audit
history is intact — but the original file, which is what a dispute is settled against, is
gone.

Attach a Volume to the API service and use its mount path here. The Dockerfile already
creates `/var/lib/bm/storage` owned by the non-root `bm` user, so that is the path of least
resistance. If you run a separate worker service it must mount the **same** volume — the
worker is what parses the file the API stored.

---

## 3. Recommended — defaults are wrong for Neon or for production

### `BM_DB_POOL_SIZE` / `BM_DB_MAX_OVERFLOW`

| | |
|---|---|
| Mandatory | No (`10` / `5`) |
| Secret | No |
| Env-specific | Yes |
| Reuse locally | Yes |

Set `5` and `2`. Behind Neon's pooler the pooler does the multiplexing, so a large
client-side pool only holds server-side connections open against Neon's limit. Five is
ample for one API replica at the design load (5 counters, ~500 invoices/day) — which is
roughly three orders of magnitude below what a single modest Postgres instance handles.

If you add a worker service, its pool counts too. Keep the total under Neon's connection
allowance for your plan.

### `BM_DB_STATEMENT_TIMEOUT_MS` / `BM_DB_LOCK_TIMEOUT_MS`

| | |
|---|---|
| Mandatory | No (`15000` / `3000`) |
| Secret | No |
| Env-specific | No |
| Reuse locally | Yes |

Leave both at their defaults. `lock_timeout` is the one that is felt at a counter: a
finalisation that cannot get its stock locks within 3 s fails fast with a retryable error
instead of hanging the till while a customer waits.

Minimum 100 ms each, validated.

### `BM_LOG_FORMAT`

| | |
|---|---|
| Mandatory | No (defaults to `json`) |
| Secret | No |
| Env-specific | Yes |
| Reuse locally | n/a |

`json` on Railway. One object per line with the request-correlation fields attached, which
is what makes the Railway log search usable. Every record passes through the redaction
filter in `infra/logging/setup.py` either way — JWTs, `Authorization` headers,
`password=`/`pepper=`/`token=` pairs and Postgres URLs carrying a password are all
scrubbed. `console` is for a human reading a terminal.

### `BM_LOG_LEVEL`

| | |
|---|---|
| Mandatory | No (defaults to `INFO`) |
| Secret | No |
| Env-specific | No |
| Reuse locally | Yes |

`INFO`. One line per request plus startup and shutdown. `DEBUG` in production is a privacy
risk as much as a volume one. Accepts `DEBUG` `INFO` `WARNING` `ERROR` `CRITICAL`,
case-insensitive, validated.

---

## 4. Security switches — set them explicitly even though the defaults are safe

### `BM_UNATTENDED_ACCESS`

| | |
|---|---|
| Mandatory | No (defaults to `false`) |
| Secret | No |
| Env-specific | Yes |
| Reuse locally | No |

🔴 **Keep this `false` on Railway, and set it explicitly so it is visible.**

With it on, anyone who can reach the server gets a working session **with no password and
no second factor**. There is no prompt to fail. `api/v1/auth.py::unattended` states the
risk in full: exposing a server with this enabled to the open internet publishes the shop's
customers, prices, takings and the ability to issue bills.

It exists for a counter machine bound to `127.0.0.1` or a LAN the shop owns. A Railway
service is neither. `main.py` logs a WARNING on every start while it is on, so if you ever
see `unattended_access_enabled` in a Railway deploy log, treat it as an incident.

The counter application asks for an unattended session once per cold start and quietly
falls back to the sign-in form when the server refuses — so leaving this off costs the
front end nothing (`src/app/bootstrap.ts`).

`BM_UNATTENDED_USERNAME` is only read when the flag is on, and startup fails if the flag is
on without it.

### `BM_RATE_LIMIT_ENABLED`

| | |
|---|---|
| Mandatory | No (defaults to `true`) |
| Secret | No |
| Env-specific | No |
| Reuse locally | Yes |

Leave it alone. **It is read by nothing** — `config.py` declares the field and no module in
`src/` references it. There is no HTTP rate limiter in this codebase; `RateLimited` exists
as an error class with no raiser.

What *is* implemented, and is unaffected by this variable, is login lockout:
`domain/auth/service.py` records every attempt against the username and a hashed IP, and 5
failures in 5 minutes produce a 15-minute lockout reported with `retry_after_seconds`. It
is **database-backed**, so it works correctly across multiple replicas.

That also narrows `docs/DEPLOYMENT.md §9`, which lists "replace the in-process rate limiter
with a shared store" as a prerequisite for scaling out: there is no in-process rate limiter
to replace. One replica is still the right choice at this load, but not for that reason.

### `BM_ACCESS_TOKEN_TTL_SECONDS` / `BM_REFRESH_TOKEN_TTL_DAYS`

| | |
|---|---|
| Mandatory | No (`900` / `30`) |
| Secret | No |
| Env-specific | No |
| Reuse locally | Yes |

Leave at the defaults. 900 s is bounded 60–3600; 30 days is bounded 1–365.

Worth knowing: the 15-minute access token is why the refresh cookie has to work. If
`BM_REFRESH_COOKIE_SAMESITE` is wrong, this number is how long a session lasts.

### `BM_MAX_UPLOAD_MB`

| | |
|---|---|
| Mandatory | No (defaults to `10`) |
| Secret | No |
| Env-specific | No |
| Reuse locally | Yes |

`10`. Bounded 1–100. The effective cap is `min(business_limit_mb, max_upload_mb)` — the
per-business limit is the `imports.max_file_mb` setting, so the owner can lower it without
a deploy but never raise it past this.

### `BM_MAX_BODY_BYTES`

| | |
|---|---|
| Mandatory | No (defaults to `1048576`) |
| Secret | No |
| Env-specific | No |
| Reuse locally | Yes |

Leave unset. A 1 MB cap on every request except `/api/v1/imports`, which is exempted and
capped separately with a streaming byte counter so an attacker-controlled blob is never
buffered.

---

## 5. Optional

### `BM_JWT_PREVIOUS_KEY`

| | |
|---|---|
| Mandatory | No | 
| Secret | **Yes** |
| Env-specific | Yes |
| Reuse locally | No |

Set **only** during a signing-key rotation, and removed afterwards. Both keys are accepted
for verification for one access-token lifetime, so nobody is signed out
(`docs/DEPLOYMENT.md §10`). Leaving a retired key in place extends the window in which a
stolen old token still works.

### `BM_ALERTING_ENABLED` / `BM_ALERT_WEBHOOK_URL` / `BM_ALERT_RECIPIENT_NAME` / `BM_ALERT_MIN_SEVERITY` / `BM_ALERT_TIMEOUT_SECONDS`

| | |
|---|---|
| Mandatory | No (`false` / unset / unset / `warning` / `5.0`) |
| Secret | `BM_ALERT_WEBHOOK_URL` — **yes** |
| Env-specific | Yes |
| Reuse locally | No |

**OD-19 is a go-live gate, not an optional extra**, and the sentence that governs it is
"an alert that reaches nobody is not monitoring."

`/readyz` and `GET /api/v1/notifications/alerting` report one of four states:

| State | Meaning |
|---|---|
| `disabled` | `BM_ALERTING_ENABLED=false`. Deliberately off. |
| `unconfigured` | Enabled with no webhook. **Every alert is dropped** and logged at `ERROR`. A monitoring outage. |
| `no_named_recipient` | Enabled with a webhook but no named human. OD-19 asks for a person, not an endpoint. |
| `configured` | Enabled, with a destination and a name. |

So: either leave it `false` until you have a webhook, or set all three together. Turning it
on alone is strictly worse than leaving it off.

`BM_ALERT_WEBHOOK_URL` is an `https://` webhook (Slack, Teams, a pager provider). For most
providers **the path is the credential**, which is why `/readyz` only ever reports the
scheme and host. A plain-`http` webhook is refused outright when `BM_ENV=production`.

`BM_ALERT_RECIPIENT_NAME` is a person's name, not a shared inbox.

`BM_ALERT_MIN_SEVERITY` is `info` · `warning` · `critical`. `BM_ALERT_TIMEOUT_SECONDS` is
bounded 0–30. Delivery never raises into the caller: a failed webhook must not fail the
sale that triggered it.

### `BM_WORKER_CONCURRENCY`

| | |
|---|---|
| Mandatory | No (defaults to `2`) |
| Secret | No |
| Env-specific | No |
| Reuse locally | Yes |

Only meaningful on a service whose start command is `python -m bm.worker`. Bounded 1–32.

---

## 6. Declared, and read by nothing

Checked in the source, not inferred. Each is a field on `Settings` with no reader in
`src/`, so setting it changes behaviour nowhere. Listed so nobody has to find that out by
grepping — and so nobody concludes a feature is configured when it is not.

| Variable | Why it is inert | What to do |
|---|---|---|
| `BM_SENTRY_DSN` | No Sentry SDK in `pyproject.toml`. Nothing reads the field. | Leave unset. Setting a DSN would create the impression errors are being reported. |
| `BM_STORAGE_BACKEND` | Read **only** for a startup log line. `infra/storage/objects.py` is a filesystem store; there is no S3 code path. | `local`. Setting `s3` passes validation and changes one log line. |
| `BM_STORAGE_BUCKET` | A documented seam for an object store later. No reader. | Leave unset. |
| `BM_RATE_LIMIT_ENABLED` | No rate limiter exists — see §4. | Leave unset. |

`docs/DEPLOYMENT.md §3` also lists `BM_STORAGE_ACCESS_KEY` and `BM_STORAGE_SECRET`. Those
are **not fields on `Settings` at all** — they were written for the S3 backend that was
never built. Do not set them; with `extra="ignore"` they would be discarded silently.

---

## 7. Must NEVER be set on the Railway service

### `BM_MIGRATION_DATABASE_URL`

`CLAUDE.md §11` and `alembic.ini` both say it: the DDL-capable credential exists only in
the migration step, never in the API container.

If it is present on the API service, every container that can be made to run Alembic — or
any code path that reads the variable — can alter the schema and bypass the column-level
`REVOKE`s that make finalised financial records immutable. That privilege model is the only
thing making T-1 (a cashier editing history) and T-14 (audit tampering) structural rather
than a matter of code discipline.

Migrations are run from a developer machine against the Neon **direct** endpoint, through
`scripts/prod.ps1` + `scripts/production_db.py migrate`. Not from Railway, not from a
start command, not on deploy (ADR-016: automatic start-up migrations turn every replica
restart into a potential schema change and a crash loop into a migration loop).

### `BM_TEST_DATABASE_URL` / `BM_TEST_MIGRATION_DATABASE_URL`

Test-suite variables, read only by `tests/conftest.py`. The suite **truncates every
business table between tests**. They have no meaning in a deployed service and must not be
present.

### `BM_DEV_*` / `BM_E2E_*` / `BM_LOAD_*` / `BM_DRILL_*` / `BM_BACKUP_*`

Script and fixture variables, read only by `scripts/seed_*.py`, `tests/load/`,
`scripts/restore_drill.sh` and `scripts/backup.sh`. None of them is a `Settings` field.

---

## 8. Railway service settings, not variables

| Setting | Value | Why |
|---|---|---|
| Builder | **Dockerfile** | The repository has one, and it is the thing CI's `packaging` job proves importable. Nixpacks would guess an install and a start command and miss `PYTHONPATH=/app/src`. |
| Dockerfile path | `Dockerfile` | |
| Build target | `runtime` | The multi-stage build's final stage. |
| Start command | *(leave empty)* | The Dockerfile's `CMD` is already `uvicorn bm.main:create_app --factory --host 0.0.0.0 --port 8000`. |
| Port | `8000` | `EXPOSE 8000`, and the `CMD` hard-codes `--port 8000`. Railway's injected `PORT` variable is **not** read by that command, so set the service's public port to `8000` explicitly (or `PORT=8000`) rather than relying on detection. A mismatch here is the classic "deploy succeeded, domain returns 502". |
| Healthcheck path | `/healthz` | Liveness only, and deliberately does not touch the database: a database blip must not make the platform kill a healthy process. |
| Region | **Singapore** | Same region as the Neon branch. Every request does several database round trips, so cross-region latency is multiplied, not added. |
| Replicas | **1** | `docs/DEPLOYMENT.md §2`. Not because of a rate limiter (there is none) but because one replica is the documented, tested topology at this load. |
| Volume | required, mounted at `BM_STORAGE_PATH` | §3, `BM_STORAGE_PATH`. |
| Restart policy | on failure | A config error should crash-loop visibly rather than be retried into silence. |

**`/readyz` is not the healthcheck.** It returns 503 until the Alembic head in the database
matches the head in the code (ADR-016), which is correct for a deploy gate and wrong for
liveness: a replica waiting on a migration would be killed and restarted forever. Use
`/healthz` for the platform probe and check `/readyz` by hand after each deploy.

### An optional second service: the worker

The same image with `python -m bm.worker` as the start command. It needs
`BM_DATABASE_URL`, the three secrets, `BM_LOG_*`, `BM_STORAGE_*` (the **same** volume) and
`BM_WORKER_CONCURRENCY` — and not `BM_CORS_ALLOWED_ORIGINS` or `BM_ENABLE_API_DOCS`, which
it never reads.

Without it, file imports are accepted and never parsed: `POST /api/v1/imports` stores the
upload and enqueues an `import.parse` job that nothing claims. Everything else — billing,
stock, credit, reports — works without a worker.

---

## 9. Copy-paste checklist

Fill in the four `<…>` values. This is the complete set for the API service.

```
BM_ENV=production
BM_RELEASE=${{RAILWAY_GIT_COMMIT_SHA}}

BM_DATABASE_URL=postgresql+psycopg://bm_app:<password>@<pooled-neon-host>/neondb?sslmode=require
BM_DB_POOL_SIZE=5
BM_DB_MAX_OVERFLOW=2
BM_DB_STATEMENT_TIMEOUT_MS=15000
BM_DB_LOCK_TIMEOUT_MS=3000

BM_JWT_SIGNING_KEY=<secret 1, 48 urlsafe bytes>
BM_PASSWORD_PEPPER=<secret 2, 48 urlsafe bytes — PERMANENT, back it up>
BM_IP_HASH_PEPPER=<secret 3, 48 urlsafe bytes>
BM_ACCESS_TOKEN_TTL_SECONDS=900
BM_REFRESH_TOKEN_TTL_DAYS=30

BM_CORS_ALLOWED_ORIGINS=https://<your-vercel-or-custom-domain>
BM_ENABLE_API_DOCS=false
BM_UNATTENDED_ACCESS=false
# `none` for the *.vercel.app + *.up.railway.app pairing this deployment uses; `lax`
# only on one owned domain or behind the Vercel proxy. See the section above — this is
# the variable that produced the owner's 13-minute sign-out.
BM_REFRESH_COOKIE_SAMESITE=none

BM_STORAGE_BACKEND=local
BM_STORAGE_PATH=/var/lib/bm/storage
BM_MAX_UPLOAD_MB=10

BM_LOG_LEVEL=INFO
BM_LOG_FORMAT=json

BM_ALERTING_ENABLED=false
```

Then verify, in this order:

1. `GET https://<railway-domain>/healthz` → `{"status":"ok"}`
2. `GET https://<railway-domain>/readyz` → `200`, `migrations.status: "ok"`,
   `alerting.state` as you configured it. A `503` with
   `migrations.status: "mismatch"` means the Neon migration has not been run — the
   database and the code disagree, and the service is correctly refusing traffic.
3. The deploy log contains `startup` with `env=production`, and **does not** contain
   `unattended_access_enabled`, `api_docs_enabled_in_production` or
   `cors_allow_list_empty`.
