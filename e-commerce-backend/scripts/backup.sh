#!/usr/bin/env bash
# Dumps the Postgres database to a timestamped, gzip-compressed file.
#
# Minimal on purpose — a cron job pointing at this plus offsite copying of
# ./backups is enough for the MVP. There is no restore automation here
# deliberately: restoring is rare and destructive enough that it should stay
# a deliberate, reviewed command, not a script someone runs on autopilot.
#
#   ./scripts/backup.sh
#   gunzip -c backups/genmars_ecommerce_2026-10-14T120000.sql.gz | psql ...
set -euo pipefail

cd "$(dirname "$0")/.."
set -a
. ./.env
set +a

mkdir -p backups
timestamp="$(date -u +%Y-%m-%dT%H%M%SZ)"
out="backups/${POSTGRES_DB}_${timestamp}.sql.gz"

# Runs pg_dump INSIDE the db container — the dev host doesn't have the
# Postgres client tools installed, and a production host shouldn't need them
# either just to take a backup of a container it's already running.
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" db \
  pg_dump --username "$POSTGRES_USER" --no-owner --no-privileges "$POSTGRES_DB" \
  | gzip > "$out"

echo "Backed up to $out"
