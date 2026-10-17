#!/bin/sh
# Runs once per container start, before gunicorn. Migrations need a real,
# reachable database — compose.hetzner.yaml's `depends_on: condition:
# service_healthy` is what makes that true by the time this runs, not
# anything in this script. Both commands are idempotent, so running them on
# every start (not just the first) is safe — same reasoning as
# docs/RAILWAY_DEPLOYMENT.md's startCommand.
set -eu

python manage.py migrate --noinput

exec "$@"
