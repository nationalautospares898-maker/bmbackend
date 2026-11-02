# Run the backend test suite against the throwaway `bm_test` database.
#
# Why this script exists at all: `conftest._migration_database_url()` falls back to
# `BM_MIGRATION_DATABASE_URL`, and `.env` points that at the **real** `bm`. Setting only
# `BM_TEST_DATABASE_URL` therefore leaves the migration session aimed at live data, which
# is how a bare `uv run pytest` truncated the demo database on 2026-10-31. Both variables
# have to be set, every time, so they are set here rather than remembered.
#
# The suite's own guard (`_assert_scratch_database`) still has the final say — this script
# makes the safe thing convenient, it does not replace the refusal.

$ErrorActionPreference = 'Stop'

$env:BM_TEST_DATABASE_URL = 'postgresql+psycopg://bm_app:bm_app_local_only@127.0.0.1:55439/bm_test'
$env:BM_TEST_MIGRATION_DATABASE_URL = 'postgresql+psycopg://bm_migrate:bm_migrate_local_only@127.0.0.1:55439/bm_test'

Write-Host "app       $env:BM_TEST_DATABASE_URL"
Write-Host "migration $env:BM_TEST_MIGRATION_DATABASE_URL"

& uv run pytest @args
exit $LASTEXITCODE
