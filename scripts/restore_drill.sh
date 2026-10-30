#!/usr/bin/env bash
#
# Restore drill (`DEPLOYMENT.md §7.1`). Executes the documented seven steps.
#
#   1  Provision a scratch database
#   2  Restore the dump into it
#   3  Point the app's migration check at it — `alembic current`
#   4  Run the invariant suite against the restored data   <- the actual proof
#   5  Spot-check: last 10 invoices, stock for 10 products, one customer's outstanding
#   6  Record the result
#   7  Destroy the scratch resources
#
# **Step 4 is the drill.** Steps 1–3 only prove that bytes moved; the proof that the
# backup is usable is that the restored database still satisfies the invariants —
# `balance = Σ movements`, gapless numbering, allocations summing to `amount_paid`. A
# restore that completes and leaves the ledger inconsistent is a failed restore, and it
# would look like a success at every step except this one.
#
# Safety: the scratch database name is generated and the script **refuses to touch a
# database it did not create**. It never drops the source. A drill that can destroy
# production is not a drill.
#
# Usage:
#   BM_DRILL_ADMIN_URL=postgresql://bm_migrate@localhost:55439/postgres \
#   BM_DRILL_DUMP=./backups/bm-20261030T000000Z.dump \
#     ./scripts/restore_drill.sh
#
# What this script cannot do, and says so: a **production** restore drill needs the
# managed platform's snapshot and PITR, so RTO against real data and the real restore
# path is an external acceptance item (NFR-REL-002/003, OD-19). What runs here proves
# the dump is restorable and the restored data is sound.

set -euo pipefail

: "${BM_DRILL_ADMIN_URL:?set BM_DRILL_ADMIN_URL (a maintenance-database URL with CREATEDB)}"
: "${BM_DRILL_DUMP:?set BM_DRILL_DUMP (path to a pg_dump custom-format archive)}"

if [ ! -f "$BM_DRILL_DUMP" ]; then
  echo "FAIL: no such dump: $BM_DRILL_DUMP" >&2
  exit 1
fi

SCRATCH="bm_drill_$(date -u +%Y%m%d%H%M%S)_$$"
START="$(date -u +%s)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Rebuild the admin URL against the scratch database, without string-mangling a
# password: everything up to the last `/` is reused verbatim.
BASE="${BM_DRILL_ADMIN_URL%/*}"
SCRATCH_URL="$BASE/$SCRATCH"

cleanup() {
  # Step 7. Runs on every exit path, including a failed drill — a scratch database left
  # behind after a failure is how a disk fills up a month later.
  echo "==> [7] destroying the scratch database"
  psql --dbname="$BM_DRILL_ADMIN_URL" --quiet --no-psqlrc \
    --command="DROP DATABASE IF EXISTS \"$SCRATCH\" WITH (FORCE)" > /dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> [1] provisioning scratch database $SCRATCH"
psql --dbname="$BM_DRILL_ADMIN_URL" --quiet --no-psqlrc \
  --command="CREATE DATABASE \"$SCRATCH\" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"

echo "==> [2] restoring $BM_DRILL_DUMP"
# --no-owner / --no-privileges: the scratch database's roles are not production's.
# --exit-on-error so a partially restored database can never be reported as a success;
# the whole point of the drill is to find that out here.
pg_restore \
  --dbname="$SCRATCH_URL" \
  --no-owner \
  --no-privileges \
  --exit-on-error \
  "$BM_DRILL_DUMP"

echo "==> [3] checking the migration state of the restored database"
BM_MIGRATION_DATABASE_URL="$SCRATCH_URL" BM_DATABASE_URL="$SCRATCH_URL" \
  uv run alembic current

echo "==> [4] running the invariant suite against the restored data"
# The invariant suite reads the database it is pointed at and asserts the properties
# that must hold of any consistent NAS database. This is the step that decides whether
# the drill passed.
BM_MIGRATION_DATABASE_URL="$SCRATCH_URL" BM_DATABASE_URL="$SCRATCH_URL" \
  uv run pytest tests/invariants -q

echo "==> [5] spot-checking the restored data"
psql --dbname="$SCRATCH_URL" --quiet --no-psqlrc --echo-errors <<'SQL'
\pset footer off
\echo '-- last 10 invoices'
SELECT id, total, payment_status, created_at
  FROM invoices ORDER BY created_at DESC LIMIT 10;
\echo '-- stock for 10 products, balance vs ledger'
SELECT b.product_id,
       b.quantity                                   AS balance,
       coalesce(sum(m.quantity_delta), 0)           AS ledger,
       b.quantity - coalesce(sum(m.quantity_delta), 0) AS difference
  FROM inventory_balances b
  LEFT JOIN stock_movements m
         ON m.product_id = b.product_id AND m.branch_id = b.branch_id
 GROUP BY b.product_id, b.quantity
 LIMIT 10;
\echo '-- one customer outstanding'
SELECT customer_id, sum(total - amount_paid) AS outstanding
  FROM invoices
 WHERE customer_id IS NOT NULL AND status <> 'cancelled'
 GROUP BY customer_id
 ORDER BY outstanding DESC
 LIMIT 1;
SQL

ELAPSED=$(( $(date -u +%s) - START ))
echo
echo "==> [6] result"
echo "    drill            : PASSED"
echo "    started          : $STARTED_AT"
echo "    elapsed          : ${ELAPSED}s"
echo "    dump             : $BM_DRILL_DUMP"
echo "    scratch database : $SCRATCH (destroyed on exit)"
echo
echo "    Record this in docs/DEPLOYMENT.md §7.1 alongside the previous drills."
echo "    NOTE: this drill restores a logical dump. RTO against production snapshots"
echo "          and PITR remains an external acceptance item (NFR-REL-002/003)."
