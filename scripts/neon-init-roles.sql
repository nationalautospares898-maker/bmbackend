-- Two database roles on Neon, created ONCE and BEFORE the first migration.
-- =========================================================================
--
-- This is the Neon counterpart of `scripts/init-roles.sql`, which runs at cluster init
-- under Docker Compose. Neon has no cluster-init hook, so it is a deliberate step.
--
--   neondb_owner  — Neon's built-in owner. Takes the place of `bm_migrate`: it owns the
--                   schema, holds DDL rights, and is used ONLY by `alembic upgrade head`.
--   bm_app        — the application role. No DDL. Its UPDATE/DELETE rights on the
--                   append-only tables are revoked by the migrations that create them,
--                   so immutability is enforced by PRIVILEGE rather than by code
--                   discipline (NFR-DATA-006, SECURITY.md §7).
--
--
-- ⚠ RUN THIS BEFORE `alembic upgrade head`, NOT AFTER.
-- ----------------------------------------------------
-- Every `GRANT`/`REVOKE` in the migrations is wrapped in `_app_role_exists()`:
--
--     def _app_role_exists() -> bool:
--         return bool(bind.execute(
--             sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": "bm_app"}
--         ).first())
--
-- If `bm_app` does not exist when a migration runs, that migration SKIPS its grants
-- silently and reports success. The schema is then complete and the append-only
-- protection is simply absent — `UPDATE audit_logs`, `DELETE FROM stock_movements` and
-- `UPDATE invoices SET total = …` all succeed, and nothing anywhere reports it. The
-- `migrations` job in `.github/workflows/ci.yml` runs in exactly that state on purpose,
-- which is how the same guard came to hide a missing foreign key until migration 0021.
--
-- `scripts/production_db.py verify` asserts the privileges really landed, so a run of
-- this file in the wrong order is caught rather than assumed. If it did happen, the fix
-- is to create the role and re-apply the grants — never to drop and re-migrate.
--
--
-- HOW TO RUN IT
-- -------------
-- Neon Console → project "national auto spares" → branch "production" → SQL Editor,
-- connected as `neondb_owner` to database `neondb`. Paste and run.
--
-- Or from this machine, using the DIRECT (non-pooler) connection string:
--     psql "<neon direct URL, postgresql:// form>" -v ON_ERROR_STOP=1 -f scripts/neon-init-roles.sql
--
--
-- THE PASSWORD
-- ------------
-- Replace BOTH occurrences of `REPLACE_WITH_A_STRONG_GENERATED_PASSWORD` with the same
-- generated value, then put that same value in the `bm_app` half of
-- `BM_DATABASE_URL` in `.env.production` and in Railway.
--
--     python -c "import secrets; print(secrets.token_urlsafe(32))"
--
-- Do not commit the filled-in version of this file. Generate, paste, run, and keep the
-- password only in the two places that need it.
--
-- Neon note: a role created with SQL here appears in the Neon Console under Roles, and
-- Neon can issue a pooled connection string for it like any other. `neondb_owner` is a
-- member of `neon_superuser`, which carries CREATEROLE — so this works without any
-- Neon-side privilege request. It is NOT a superuser, which is why the `ALTER DEFAULT
-- PRIVILEGES` statement below is scoped `FOR ROLE neondb_owner` rather than globally.


-- --- 1. the application role -------------------------------------------------
--
-- Idempotent so a second run is a no-op rather than an error, and so re-running after a
-- partial failure is safe. `NOCREATEDB NOCREATEROLE NOSUPERUSER` is explicit: the
-- defaults already give none of them, and stating it means a later `ALTER ROLE` that
-- granted one would stand out as a change rather than blend into an omission.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bm_app') THEN
        CREATE ROLE bm_app LOGIN PASSWORD 'REPLACE_WITH_A_STRONG_GENERATED_PASSWORD'
            NOCREATEDB NOCREATEROLE NOSUPERUSER NOINHERIT;
    ELSE
        ALTER ROLE bm_app WITH LOGIN PASSWORD 'REPLACE_WITH_A_STRONG_GENERATED_PASSWORD';
    END IF;
END
$$;


-- --- 2. connect and read the schema, nothing more ----------------------------
GRANT CONNECT ON DATABASE neondb TO bm_app;
GRANT USAGE ON SCHEMA public TO bm_app;

-- The app role may read and write business data, but never alter the schema.
--
-- `REVOKE ... FROM PUBLIC` matters on a database created from `template1`, where PUBLIC
-- holds CREATE on `public` by default: without it, `bm_app` could create its own tables
-- in the schema it is only supposed to read and write. PostgreSQL 15+ removed that
-- default, so on a current Neon branch this is a no-op that documents the requirement.
REVOKE CREATE ON SCHEMA public FROM bm_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;


-- --- 3. default privileges for the tables the migrations are about to create --
--
-- These apply to objects created LATER by `neondb_owner`, which is the whole point: the
-- migrations run next, and each table they create picks these up automatically. Then the
-- individual migrations `REVOKE` the mutations that must be impossible.
--
-- The default is deliberately broad here and narrowed there, rather than the other way
-- round: a forgotten GRANT breaks a feature loudly, a forgotten REVOKE breaks an
-- invariant silently.
ALTER DEFAULT PRIVILEGES FOR ROLE neondb_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bm_app;
ALTER DEFAULT PRIVILEGES FOR ROLE neondb_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO bm_app;


-- --- 4. report ----------------------------------------------------------------
--
-- So the operator sees a result rather than silence. Expect one row, `bm_app`, with
-- every boolean false.
SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole
FROM pg_roles
WHERE rolname IN ('bm_app', 'neondb_owner')
ORDER BY rolname;
