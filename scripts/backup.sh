#!/usr/bin/env bash
#
# Logical backup (`DEPLOYMENT.md §7`).
#
# This is the **weekly off-provider copy** — the one that protects against losing the
# account, not just the instance. Daily snapshots and continuous WAL are the managed
# platform's job and are configured there, not here; a script cannot provide PITR.
#
# Three things it deliberately does not do:
#
#   * **It never reads a credential from an argument.** A password on the command line
#     is visible in `ps` and lands in shell history. `PGPASSWORD` or, better, a
#     `~/.pgpass` entry, is the supported route.
#   * **It never writes the dump unencrypted when a key is configured.** An off-provider
#     dump is the one copy that leaves the provider's encryption at rest, so leaving it
#     in the clear would undo the point of taking it (`SECURITY.md §12`).
#   * **It verifies the dump it just wrote.** `pg_restore --list` has to be able to read
#     the archive. An untested backup is treated as no backup, and a zero-byte file that
#     nobody opened for ninety days is the classic way to discover that.
#
# Usage:
#   BM_BACKUP_DATABASE_URL=postgresql://user@host:5432/bm ./scripts/backup.sh [outdir]
#
# Optional:
#   BM_BACKUP_AGE_RECIPIENT  age public key; when set, the dump is encrypted and the
#                            plaintext is removed.
#   BM_BACKUP_RETAIN_DAYS    prune local dumps older than this (default 90, matching
#                            the documented retention).

set -euo pipefail

: "${BM_BACKUP_DATABASE_URL:?set BM_BACKUP_DATABASE_URL (no password on the command line)}"
OUT_DIR="${1:-./backups}"
RETAIN_DAYS="${BM_BACKUP_RETAIN_DAYS:-90}"

mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP="$OUT_DIR/bm-$STAMP.dump"

echo "==> dumping to $DUMP"
# --format=custom so a partial-table restore is possible during an incident, and so the
# archive carries its own table of contents for the verification step below.
# --no-owner / --no-privileges so the dump restores into a scratch database whose roles
# differ from production's; role grants are re-applied by the migrations, which are the
# authority on them (`SECURITY.md §7`).
pg_dump \
  --dbname="$BM_BACKUP_DATABASE_URL" \
  --format=custom \
  --compress=9 \
  --no-owner \
  --no-privileges \
  --file="$DUMP"

echo "==> verifying the archive is readable"
if ! pg_restore --list "$DUMP" > /dev/null; then
  echo "FAIL: the dump just written cannot be read back" >&2
  exit 1
fi
TABLES="$(pg_restore --list "$DUMP" | grep -c 'TABLE DATA' || true)"
echo "    $TABLES tables with data"
if [ "$TABLES" -lt 1 ]; then
  echo "FAIL: the dump contains no table data" >&2
  exit 1
fi

if [ -n "${BM_BACKUP_AGE_RECIPIENT:-}" ]; then
  echo "==> encrypting"
  age --recipient "$BM_BACKUP_AGE_RECIPIENT" --output "$DUMP.age" "$DUMP"
  shred -u "$DUMP" 2> /dev/null || rm -f "$DUMP"
  echo "    wrote $DUMP.age"
else
  # Said plainly rather than silently: an unencrypted off-provider dump is a decision,
  # and the operator should be making it knowingly.
  echo "NOTE: BM_BACKUP_AGE_RECIPIENT is unset, so this dump is NOT encrypted."
  echo "      An off-provider copy leaves the provider's encryption at rest behind."
fi

echo "==> pruning local dumps older than $RETAIN_DAYS days"
find "$OUT_DIR" -maxdepth 1 -name 'bm-*.dump*' -type f -mtime "+$RETAIN_DAYS" -print -delete

echo "==> done"
