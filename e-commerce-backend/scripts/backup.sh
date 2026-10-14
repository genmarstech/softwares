#!/usr/bin/env bash
# Dumps the Postgres database to a timestamped, gzip-compressed file.
#
# Minimal on purpose — a cron job pointing at this plus offsite copying of
# ./backups is enough for the MVP. There is no restore automation here
# deliberately: restoring is rare and destructive enough that it should stay
# a deliberate, reviewed command, not a script someone runs on autopilot.
# See docs/BACKUP_AND_RECOVERY.md for the restore procedure and the
# rehearsal that proved it against real business/membership/catalog/
# inventory/order/payment data.
#
#   ./scripts/backup.sh
#   gunzip -c backups/genmars_ecommerce_2026-10-14T120000.sql.gz | psql ...
#
# Optional encryption at rest: set BACKUP_GPG_RECIPIENT in .env to a GPG key
# ID/fingerprint or email this host trusts, and the dump is encrypted to it
# before it touches disk (same convention gen-portal's own backup script
# uses — see that repo's CLAUDE.md). Left unset, this script behaves
# exactly as before: a plain gzip file. Nothing here can generate or embed
# a real key — that has to already exist on the machine doing the backup,
# the same way gen-portal's does.
set -euo pipefail

cd "$(dirname "$0")/.."
set -a
. ./.env
set +a

mkdir -p backups
timestamp="$(date -u +%Y-%m-%dT%H%M%SZ)"

# Runs pg_dump INSIDE the db container — the dev host doesn't have the
# Postgres client tools installed, and a production host shouldn't need them
# either just to take a backup of a container it's already running.
dump() {
  docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" db \
    pg_dump --username "$POSTGRES_USER" --no-owner --no-privileges "$POSTGRES_DB"
}

if [ -n "${BACKUP_GPG_RECIPIENT:-}" ]; then
  out="backups/${POSTGRES_DB}_${timestamp}.sql.gz.gpg"
  dump | gzip | gpg --batch --yes --encrypt --recipient "$BACKUP_GPG_RECIPIENT" --output "$out"
else
  out="backups/${POSTGRES_DB}_${timestamp}.sql.gz"
  dump | gzip > "$out"
  echo "WARNING: BACKUP_GPG_RECIPIENT is not set — this dump is NOT encrypted " \
       "and contains real customer PII and M-Pesa phone numbers if run against " \
       "a live database. Set BACKUP_GPG_RECIPIENT before running this in production." >&2
fi

echo "Backed up to $out"
