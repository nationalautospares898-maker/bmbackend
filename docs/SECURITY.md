# Security — `bmbackend`

| Field | Value |
|---|---|
| Status | **Phase 1 proposal — no controls are implemented yet** |
| Scope | Backend application, database, uploads, secrets, logging, operations |
| Last updated | 2026-10-29 |

> This is a small-business financial system handling money, credit balances and customer contact
> details. The realistic threat model is **insider error and casual insider abuse**, plus **internet
> exposure of an API**. It is not a high-value target for sophisticated attackers, so the controls
> below are chosen to be strong where the real risk is (authorisation, tenant isolation, audit,
> immutability, upload handling) without ceremony that a two-person shop cannot operate.

---

## 1. Threat model

| # | Threat | Likelihood | Impact | Primary control |
|---|---|---|---|---|
| T-1 | A cashier edits a past invoice or deletes a sale to pocket cash | Medium | High | Immutable financial tables + DB privileges + audit log + no update endpoints |
| T-2 | A cashier silently discounts or changes prices for friends | Medium | Medium | `billing.price.override` / discount caps + price-change detection + audit with before/after |
| T-3 | Stock written down to hide shrinkage | Medium | Medium | Adjustments require permission + mandatory reason + audit + reconciliation report |
| T-4 | Counter PC stolen or taken home with offline data | Low–Medium | Medium | Device registry + remote revoke + bounded local cache + disk-encryption requirement |
| T-5 | Credential sharing / weak passwords | High | Medium | Argon2id, lockout, forced change of seeded passwords, per-user PIN switch instead of shared logins |
| T-6 | Internet-exposed API attacked (credential stuffing, scraping) | Medium | Medium | Rate limiting, lockout, HTTPS/HSTS, no tenant id in requests, 404-not-403 for cross-tenant |
| T-7 | Malicious or malformed file upload | Low | High | Type/size/content checks, generated names, storage outside any served path, never executed, parsing in a worker |
| T-8 | SQL injection | Low | Critical | ORM parameterisation only; a lint rule forbids raw string-built SQL |
| T-9 | Cross-tenant/cross-branch data leak | Low | High | Scope derived from token, repository-level predicates, automated isolation matrix test |
| T-10 | Secrets committed to Git | Medium | High | `.env` ignored, example files only, CI secret scanning, no secrets in the frontend repo |
| T-11 | Backup exists but cannot be restored | Medium | Critical | Quarterly restore drill is a release gate (NFR-REL-003) |
| T-12 | Dependency vulnerability | Medium | Medium | CI scanning; high/critical blocks release |
| T-13 | XSS in the client stealing a session | Low | High | Access token in memory only, refresh token `HttpOnly`, CSP (frontend-owned) |
| T-14 | Audit log tampering | Low | High | Append-only at the database privilege level |
| T-15 | Owner locked out of their own data | Low | High | Documented break-glass procedure (§12) |

---

## 2. Authentication

### 2.1 Passwords

- **Argon2id** via `argon2-cffi`. Starting parameters: `time_cost=3`, `memory_cost=64 MiB`,
  `parallelism=4`, 16-byte salt, 32-byte hash. Tuned at deploy time to ≈250 ms on the production
  instance and recorded in `DEPLOYMENT.md`.
- Hash parameters are stored with the hash, so a future upgrade re-hashes transparently on next login.
- Minimum length 10 characters, checked against a small blocklist of obvious choices. **No**
  composition rules and no forced rotation — they push counter staff toward sticky notes.
- Seeded/admin-created accounts carry `must_change_password = true`; the API refuses every
  non-auth endpoint until it is changed.

### 2.2 Tokens

| Token | Lifetime | Storage | Notes |
|---|---|---|---|
| Access (JWT) | 15 min | **Client memory only** | Claims: `sub`, `business_id`, `branch_id`, `counter_id`, `device_id`, `role`, `permissions`, `jti`, `exp`, `iat`. Signed HS256 with a rotating secret, or RS256 if multi-service later. |
| Refresh (opaque, random 256-bit) | 30 days | `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth` cookie | Only the SHA-256 hash is stored server-side. Bound to `device_id`. |

- **Rotation with reuse detection.** Every refresh consumes the old token and issues a new one in the
  same family. Presenting a consumed token means the cookie was copied → the entire family is
  revoked, an audit event is written, and the user must log in again (FR-AUTH-002).
- Tokens never go to `localStorage`/`sessionStorage` (NFR-SEC-003). This is restated as a hard rule
  in `../../bmfrontend/CLAUDE.md`.
- Access tokens are not revocable individually; the 15-minute lifetime is the bound. Device
  revocation stops refresh immediately, so the worst case after a revoke is 15 minutes of read
  access — accepted, and documented so nobody assumes instant cutoff.

### 2.3 Device binding and PIN switching

- Every authenticated request carries `X-Device-Id`. Unknown device → `403 DEVICE_NOT_REGISTERED`.
  Revoked → `403 DEVICE_REVOKED` on every endpoint including sync (FR-OFFLINE-010).
- PIN quick-switch (FR-AUTH-005) works **only** on a device with a valid refresh family, is
  rate-limited to 5 attempts before a full password login is required, and the acting user is
  recorded on every resulting financial row. A PIN is never an alternative to a password from a cold
  start.

### 2.4 Rate limiting and lockout

- Login: 10 attempts / 5 min per username, 30 / 5 min per IP (hashed). After 10 consecutive
  failures, a 15-minute lockout with `423 ACCOUNT_LOCKED` and `retry_after_seconds`.
- Lockouts and failures are recorded in `login_attempts` and the audit log, with the IP **hashed**
  (HMAC with a server pepper) so logs are useful without storing raw addresses.
- Owner can clear a lockout; the action is audited.

---

## 3. Authorisation

### 3.1 Enforcement points

1. **Route declaration** — each endpoint declares its required permission in a dependency
   (`require("billing.sale.finalize")`). A route with no declared permission fails a CI check.
2. **Service re-assertion** — destructive or financially sensitive operations re-check the permission
   inside the service, so an internal caller cannot bypass it.
3. **Data layer scoping** — every business-scoped query carries `business_id`, and branch-scoped
   queries carry `branch_id`, taken from `AuthContext` and never from the request body.

UI-level hiding is not a control (NFR-SEC-004). Every permission has a negative test asserting the
API refuses the call even when the UI would not offer it.

### 3.2 Tenant and branch isolation

- `AuthContext` is the only source of tenant identity. Endpoints do not accept `business_id`.
- Repositories take the context (or a `Scope` derived from it) and build the predicate themselves. A
  CI test enumerates repository methods and asserts a tenant predicate is present.
- **Cross-tenant access returns `404 NOT_FOUND`, not `403`** — a 403 would confirm the row exists.
- An automated **isolation matrix test** (`tests/api/test_isolation.py`) walks every endpoint with
  two businesses × two branches × every role and asserts no cross-scope read or write succeeds
  (AC-8).
- Row-Level Security is *not* used in v1: with a single application role and repository-level
  scoping that is tested exhaustively, RLS adds operational complexity without closing a distinct
  gap. It is recorded as the hardening step to take if a second application ever shares the database
  (ADR-018).

### 3.3 Sensitive field redaction

- `cost_price`, `last_cost_price`, margin figures and `cost_total` are **omitted from the response
  body** (not nulled) without `catalog.cost.read`, so an over-eager client render cannot leak them.
- Serialisation is permission-aware in one place (`api/v1/schemas/`), not sprinkled through routers.

---

## 4. Input validation

- Every request body and query parameter is a Pydantic v2 model with explicit types, bounds and
  `extra="forbid"` — unknown fields are rejected rather than ignored, so a client typo surfaces
  immediately instead of silently dropping a discount.
- Money and quantity arrive as strings and are parsed to `Decimal` with explicit scale validation.
  A value with too many decimal places is a `422`, not a silent rounding.
- Identifiers are validated as UUIDs before any query.
- Free-text fields have length caps (names 200, notes 2000, reasons 500) to bound storage and log size.
- Enum values are validated against the closed set; an unknown value is a `422`.
- Pagination limits are capped server-side (NFR-PERF-006); a client asking for 10 000 rows gets the
  maximum, not a table scan.

### 4.1 Injection

- **ORM/parameterised queries only.** The small number of hand-written analytical queries use bound
  parameters; a lint rule flags f-strings and `%`-formatting inside anything passed to `text()`.
- Trigram/prefix search parameters are bound, and user input is normalised (not escaped into) the
  pattern. `LIKE` wildcards in user input are escaped so `%` cannot force a full scan.
- CSV export escapes leading `=`, `+`, `-`, `@` so a product name cannot become a formula in the
  accountant's spreadsheet (CSV injection).

---

## 5. File upload security

Applies to `POST /imports` (FR-IMP-003, T-7):

1. **Allow-list of types:** `.xlsx`, `.csv`, `.pdf` only, matched on extension **and** sniffed
   magic bytes **and** declared content type. Any disagreement → `415 UNSUPPORTED_FILE_TYPE`.
2. **Size cap** enforced at the reverse proxy *and* the application (`imports.max_file_mb`, default
   10) → `413 FILE_TOO_LARGE`. Streaming read with a hard byte counter; the request is aborted at
   the limit rather than buffering an attacker-controlled blob.
3. **Generated storage key** (`{business_id}/{uuid}.{ext}`). The original filename is metadata only
   and is never used to build a path — no user-controlled path segment ever reaches the filesystem.
4. **Stored outside any served directory**, in a private bucket or a non-webroot volume. There is no
   endpoint that serves an uploaded file back by path; downloads (if ever added) go through an
   authenticated handler with a content-disposition header and a non-HTML content type.
5. **Never executed, never rendered.** Files are only read by the parser.
6. **Parsed in the worker**, not in the request, with limits: max rows (default 50 000), max sheets,
   max PDF pages, and a parse timeout. XLSX parsing uses `openpyxl` in read-only mode; external
   links and macros are ignored.
7. **XML entity expansion**: `openpyxl` reads XLSX as XML — zip-bomb and entity-expansion limits are
   applied (max uncompressed size, max compression ratio) before parsing.
8. **Content-hash deduplication** (`file_sha256` unique per business) prevents accidental
   double-imports and makes repeat uploads cheap to detect (E-9).
9. Parse failures are recorded as a job error with a safe message; the raw parser exception is logged
   server-side only.

---

## 6. Transport and HTTP hardening

| Control | Setting |
|---|---|
| TLS | HTTPS only; HTTP redirects to HTTPS; TLS 1.2+ |
| HSTS | `max-age=31536000; includeSubDomains` once the domain is confirmed stable |
| CORS | Explicit allow-list of frontend origins, `credentials: true`, no wildcard. Dev and prod origins are separate configuration. |
| Cookies | `HttpOnly`, `Secure`, `SameSite=Lax`, narrow `Path` |
| Headers | `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY`, `Permissions-Policy` minimal |
| CSP | Primarily the frontend's responsibility; API responses are JSON with `Content-Type: application/json` and a restrictive CSP of their own |
| Body size | Global cap (1 MB) except the upload route |
| Request timeout | Server-side `statement_timeout` and `lock_timeout`; slow requests fail fast rather than pile up |
| Docs endpoints | `/docs` and `/redoc` disabled or authenticated in production |

CSRF: the refresh cookie is `SameSite=Lax` and scoped to `/api/v1/auth`, and every other endpoint
authenticates with a bearer header rather than the cookie — so a cross-site form post cannot act as
the user. The one cookie-authenticated endpoint (`/auth/refresh`) only mints a token into the same
browser and is additionally bound to `X-Device-Id`.

---

## 7. Database security

- Application connects as a **non-superuser role** that owns no DDL privileges in production.
- Migrations run as a **separate role** with DDL rights, used only by the deploy step.
- **Append-only enforcement at the privilege level** — this is the control that makes T-1 and T-14
  structural rather than a matter of code discipline:

```sql
-- illustrative; final form lives in a migration
REVOKE UPDATE, DELETE ON audit_logs         FROM bm_app;
REVOKE UPDATE, DELETE ON stock_movements    FROM bm_app;
REVOKE UPDATE, DELETE ON invoice_items      FROM bm_app;  -- except returned_quantity, see below
REVOKE UPDATE, DELETE ON payments           FROM bm_app;
REVOKE DELETE          ON invoices          FROM bm_app;  -- cancellation is an UPDATE of status
GRANT  UPDATE (returned_quantity) ON invoice_items TO bm_app;
GRANT  UPDATE (status, cancelled_at, cancelled_by_user_id, cancellation_reason,
               amount_paid, payment_status, updated_at) ON invoices TO bm_app;
```

Column-level grants are used so the few legitimately mutable columns (cancellation fields, the
derived payment cache, `returned_quantity`) are explicitly enumerated and everything else is
impossible to change — including by a future bug or a careless migration using the app role.

- Connections over TLS; credentials from the environment/secret manager, never in code.
- Connection pooling with a bounded pool; `statement_timeout` (default 15 s, 5 s for interactive
  endpoints) and `lock_timeout` (3 s) set per session.
- Backups are encrypted at rest; PITR enabled (`DEPLOYMENT.md §7`).
- No production database access from developer machines by default; access is via a documented,
  time-bounded, audited path.

---

## 8. Secret management

| Secret | Source |
|---|---|
| `DATABASE_URL` | Environment / platform secret store |
| `JWT_SIGNING_KEY` (+ previous key for rotation) | Secret store; rotated with an overlap window |
| `PASSWORD_PEPPER`, `IP_HASH_PEPPER` | Secret store; rotation documented as a one-way event for IP hashes |
| Storage credentials | Secret store |
| Error-reporting DSN | Environment |

Rules:
- `.env` is git-ignored; only `.env.example` with **placeholder** values is committed.
- No secret is ever logged, echoed by an endpoint, or included in an error body.
- CI runs a secret scanner on every push; a hit fails the build.
- **No database credential or privileged secret may exist in `bmfrontend`** (NFR-SEC-012). The
  frontend's only configuration is the API base URL and public feature flags; this is restated in
  `../../bmfrontend/CLAUDE.md`.
- Staging and production use different credentials. A staging deploy cannot reach production data.

---

## 9. Audit logging

Every state-changing operation writes an `audit_logs` row (FR-AUDIT-001) with actor, device,
counter, branch, action, entity, timestamp, request id, hashed IP, and a reason where one is
required.

**Before/after values are recorded** for: price override, discount application, oversell override,
invoice cancellation, credit write-off, stock adjustment, product price change, role/permission
change, device revocation, user activation/deactivation, settings change, and tax-mode change
(FR-AUDIT-003).

Rules:
- Append-only (§7). No API can update or delete an audit row (FR-AUDIT-002).
- **Never** contains passwords, PINs, tokens, cookie values or full secrets (FR-AUDIT-004).
- Written in the **same transaction** as the change it describes, so an audited action cannot commit
  without its audit row.
- Queryable by entity, actor and date range for `audit.read` (FR-AUDIT-005).

---

## 10. Logging and privacy

- Structured JSON. Fields: `ts, level, msg, request_id, user_id, business_id, branch_id, route,
  status, duration_ms`.
- **Never logged:** passwords, PINs, tokens, cookies, `Authorization` headers, full request bodies
  of auth endpoints, raw IP addresses (hashed instead).
- Customer PII in logs is limited to ids. Names and phone numbers appear in logs only where
  operationally necessary and never at `INFO` level.
- A redaction filter runs on every log record and on error-reporting payloads before send, with a
  test asserting that a token-shaped string is scrubbed.
- Log retention: 30 days hot, 1 year archived (configurable).

### 10.1 Data minimisation

The system stores only what billing requires: customer name, optional business name, phone,
optional address, vehicle numbers. **No payment instrument data is ever stored** — a payment records
a method and a free-text reference such as a UPI transaction id (FR-PAY-001). There is no card
number, no CVV, no bank account field, so PCI scope is avoided entirely rather than managed.

---

## 11. Offline and client-side security

| Concern | Position |
|---|---|
| IndexedDB is not encrypted | **Accepted and documented.** A stolen counter machine can expose cached products, stock figures and open drafts. Mitigations: bounded cache (`sync.catalog_cache_max_rows`), no cost prices cached without permission, remote device revocation, short access-token life, and a **stated operational requirement that counter machines use full-disk encryption**. |
| Offline tokens | An expired access token cannot be refreshed offline. Drafts keep working locally; sync requires re-authentication. No long-lived token is cached to make offline convenient. |
| Lost device | Owner/manager revokes it (`POST /admin/devices/{id}:revoke`). Refresh family invalidated, sync refused. Unsynced drafts are lost by design — they were never sales (E-11). |
| Local tampering | A user can edit their own IndexedDB. This is harmless because **the server revalidates everything**: prices, stock, permissions and totals are recomputed at finalisation (FR-SALE-001/003). No client-asserted figure is ever trusted. |
| Service worker | Scoped to the app origin, versioned, with a documented update/skip-waiting strategy (frontend-owned) so a stale cached client cannot persist indefinitely after a security fix. |

---

## 12. Operational security

### 12.1 Access control for the team

- Production access is named-individual only; no shared accounts.
- Deploy credentials live in CI secrets, not on laptops.
- Database superuser credentials are sealed and used only for break-glass.

### 12.2 Break-glass procedure (T-15)

Documented, rehearsed and stored outside the system: how to regain owner access if the only owner
account is lost. Requires sealed database credentials plus a scripted, audited `make
reset-owner-password` executed against production with two-person awareness. The procedure writes an
audit row like any other action.

### 12.3 Vulnerability management

- Dependency scanning on every CI run (`uv`/`pip-audit` for Python, plus the frontend's own scan in
  its repository). **High/critical findings block release** (NFR-SEC-010).
- Base image patched on a monthly cadence and on any critical advisory.
- A documented 30-day target for patching high-severity findings, 7 days for critical.

### 12.4 Incident response (right-sized)

1. Detect (alert, error spike, owner report).
2. Contain — revoke devices/tokens, disable an account, or take the API offline. Drafts survive
   offline, so a short shutdown does not stop the counter from capturing bills.
3. Assess with audit logs and request ids.
4. Recover — restore from PITR if data integrity is in question; re-run invariant tests on the
   recovered database before resuming writes.
5. Record the incident, the cause and the fix. Add a regression test.

### 12.5 Backup security

- Backups encrypted at rest and in transit; restricted access.
- **Restores are tested quarterly** into an isolated environment, and the invariant suite is run on
  the restored database (NFR-REL-003, AC-10). An untested backup is treated as no backup.

---

## 13. Compliance posture

- **No claim of statutory tax compliance is made by this system.** While `tax.mode =
  estimate_only`, documents are titled `ESTIMATE` and carry no tax lines or tax identifier. Enabling
  GST is gated on a verified configuration checklist (FR-TAX-003) and is an explicit, audited owner
  action (FR-TAX-004).
- Legal advice on invoice format, retention periods and tax obligations is the owner's and their
  accountant's responsibility. The blocking questions are listed in `PRD.md §5.1`.
- Financial records are retained indefinitely and immutably, which satisfies the common requirement
  to preserve books for several years; the exact statutory retention period is an open question
  (Q-LEGAL-1 family).

---

## 14. Security testing requirements

| Test | Layer |
|---|---|
| Tenant isolation matrix: every endpoint × 2 businesses × 2 branches × every role | `tests/api/test_isolation.py` |
| Permission negative tests: every endpoint called without its permission returns 403 | `tests/api/test_permissions.py` |
| Route coverage: every route declares a permission (or is explicitly public) | CI structural test |
| Immutability: attempts to update/delete invoices, items, movements, payments, audit rows fail | `tests/invariants/` |
| Auth flows: rotation, reuse detection, lockout, revoked device, expired token | `tests/api/test_auth.py` |
| Upload abuse: wrong type, oversized, zip bomb, path-traversal filename, HTML masquerading as CSV | `tests/api/test_uploads.py` |
| Cost-price redaction without `catalog.cost.read` | `tests/api/test_redaction.py` |
| Log redaction: token-shaped and password-shaped strings never reach a log record | `tests/unit/test_logging.py` |
| CSV injection: a product named `=cmd()` exports safely | `tests/api/test_exports.py` |
| Rate limiting on login and search | `tests/api/test_rate_limits.py` |
| Secret scanning and dependency audit | CI |

Details in `docs/TESTING-STRATEGY.md`.

---

## 15. Known accepted risks

| Risk | Why accepted | Revisit when |
|---|---|---|
| Access token not individually revocable for up to 15 min | Token introspection on every request would add a database round trip to the hot path | A real abuse case appears, or token lifetime proves too long |
| In-process rate limiting | Single API replica at launch | A second replica is deployed (see `DEPLOYMENT.md §9`) |
| No RLS | Single application role, exhaustively tested repository scoping | Another application shares the database |
| IndexedDB unencrypted | No browser primitive makes this genuinely safe; the cache is bounded and the server revalidates everything | Sensitive data is ever cached beyond catalog/drafts |
| Single-region deployment | Matches the 99.5% target and the cost reality of one shop | Multi-branch expansion with a stricter SLA |
| Best-effort PDF parsing | Mandatory human review means a bad parse cannot corrupt stock | Import volume makes review a bottleneck |
| No MFA | Counter staff on a shared PC; device binding + PIN is the practical control | Owner/manager accounts are accessed from outside the shop |

---

## 16. Related documents

`docs/PRD.md` (NFR-SEC-*) · `docs/ARCHITECTURE.md` · `docs/DATA-MODEL.md` ·
`docs/API-SPECIFICATION.md` · `docs/OFFLINE-SYNCHRONIZATION.md` · `docs/DEPLOYMENT.md` ·
`docs/TESTING-STRATEGY.md` · `../../bmfrontend/docs/ARCHITECTURE.md` *(client-side CSP, token
handling, service worker)*
