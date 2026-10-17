# Deploying to Railway (candidate platform — not yet approved)

Railway is the initial hosting candidate for staging. **No hosting provider
has been approved and nothing has been deployed anywhere by this review** —
this document is the exact sequence to follow once that decision is made,
so it doesn't have to be re-derived under time pressure.

Everything below assumes the Nixpacks builder (Railway's default for a
Python project with `requirements.txt`) and the `railway.json` /
`Procfile` already in this repo — both just declare the build/start
commands explicitly rather than guessing what Nixpacks would infer, and
neither contains a hostname, secret, or anything specific to an actual
Railway project.

## 1. Provision

1. Create a new Railway project. Add a **Postgres** plugin/service to it —
   Railway provisions this and exposes its own connection variables.
2. Add this repository as a second service in the same project (GitHub
   repo connection, or `railway up` from a checkout).
3. **Attach a persistent Volume to the service**, mounted at the path this
   app's `MEDIA_ROOT` resolves to (`<repo root>/media` by default — see
   `config/settings.py`). Skip this and every uploaded product image is
   lost on the next deploy; the database rows pointing at them are not.

## 2. Map Railway's Postgres variables onto this app's own names

This app reads individual `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD`/
`POSTGRES_HOST`/`POSTGRES_PORT` variables (see `config/settings.py`), not a
single `DATABASE_URL`. Railway's Postgres plugin exposes both forms; **no
code change is needed** — in the backend service's own variables, reference
the Postgres service's variables directly (Railway's variable-reference
syntax, e.g. `${{Postgres.PGHOST}}`) rather than copying values by hand:

| This app needs | Reference Railway's Postgres service's |
|---|---|
| `POSTGRES_DB` | `PGDATABASE` |
| `POSTGRES_USER` | `PGUSER` |
| `POSTGRES_PASSWORD` | `PGPASSWORD` |
| `POSTGRES_HOST` | `PGHOST` |
| `POSTGRES_PORT` | `PGPORT` |

Referencing rather than copying means a Postgres credential rotation on
Railway's side doesn't silently go stale here.

## 3. Required environment variables (names only — see `docs/PRODUCTION_ENVIRONMENT.md` for the full table and reasoning)

`SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS` (the Railway-assigned domain,
and the real domain once one exists), `CORS_ALLOWED_ORIGINS` (the deployed
frontend's origin), `POSTGRES_*` (via the references above),
`EMAIL_BACKEND`+4 SMTP vars, `MPESA_*` (leave `MPESA_ENABLED=False` for
this phase's manual-payment pilot — see the Phase 7A report), `REDIS_URL`
(see note below), `BACKUP_GPG_RECIPIENT`.

**Railway-specific, decide together, not separately:**
- `DJANGO_TRUST_PROXY_HEADERS=True` — safe specifically on Railway, because
  a Railway service's container is reachable only through Railway's own
  edge network, which sets `X-Forwarded-Proto` itself. Leaving this unset
  on Railway causes an infinite HTTPS-redirect loop once `DEBUG=False`.
- `SERVE_MEDIA_VIA_DJANGO=True` — there's no separate static host for
  `/media/` in this architecture; see "Media storage" in
  `docs/PRODUCTION_ENVIRONMENT.md`. Acceptable at pilot scale, not a
  long-term answer.

**Redis**: Railway has a Redis plugin, referenced the same way as Postgres
above (`REDIS_URL` = that plugin's own connection string variable). Without
it, Celery-dependent features (low-stock alerts, order-status emails) don't
run — the request/response cycle itself doesn't depend on Redis (see
`core/health.py`'s own reasoning for not checking it).

## 4. First deploy

1. Push/connect the repo. Railway builds via Nixpacks and runs the command
   in `railway.json`'s `deploy.startCommand` (also mirrored in `Procfile`
   for any platform that reads that instead): `manage.py migrate` →
   `manage.py collectstatic` → `gunicorn config.wsgi`, in that order, on
   every deploy. Both `migrate` and `collectstatic` are idempotent, so
   running them on every deploy (not just the first) is safe.
2. Confirm `GET /health/` (configured as the Railway healthcheck path in
   `railway.json`) returns `{"status": "ok"}` before considering the
   deploy good.
3. Create the first platform admin: `railway run python manage.py
   createsuperuser` (runs the command against the deployed environment
   without a direct shell). Everything after that follows
   `docs/DEMO_STORE_RUNBOOK.md`'s bootstrap steps.

## 5. What this review deliberately did not do

- Did not create a Railway project, account, or any paid resource.
- Did not run a migration against any external/hosted database.
- Did not pick `DJANGO_TRUST_PROXY_HEADERS`/`SERVE_MEDIA_VIA_DJANGO` for
  you by setting them anywhere persistent — both default off; the table
  above is what to set once Railway is actually the approved target.
