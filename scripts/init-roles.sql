-- Two database roles, created once at cluster init (`SECURITY.md §7`).
--
--   bm_migrate — owns the schema, holds DDL rights, used only by the migration step.
--   bm_app     — the application role. No DDL. Its UPDATE/DELETE rights on the
--                append-only tables are revoked by the migrations that create them,
--                so immutability is enforced by privilege rather than by code
--                discipline (NFR-DATA-006).
--
-- The local passwords here are development-only and are not secrets. Production
-- credentials come from the platform secret store and never from a committed file.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bm_app') THEN
        CREATE ROLE bm_app LOGIN PASSWORD 'bm_app_local_only';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE bm TO bm_app;
GRANT USAGE ON SCHEMA public TO bm_app;

-- The app role may read and write business data, but never alter the schema.
REVOKE CREATE ON SCHEMA public FROM bm_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Default privileges for tables created later by bm_migrate. Individual migrations
-- then REVOKE the mutations that must be impossible, which is why the default is
-- deliberately broad here and narrowed there rather than the other way round: a
-- forgotten GRANT breaks a feature loudly, a forgotten REVOKE breaks an invariant
-- silently.
ALTER DEFAULT PRIVILEGES FOR ROLE bm_migrate IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bm_app;
ALTER DEFAULT PRIVILEGES FOR ROLE bm_migrate IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO bm_app;
