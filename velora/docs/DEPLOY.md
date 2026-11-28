# Deploying Velora

| Part | Where | Address |
| --- | --- | --- |
| Frontend (Vite + React, `frontend-reusable-modules/velora-software`) | Vercel | `https://getvelora.co.ke`, `https://www.getvelora.co.ke` |
| Backend (this folder) | Docker Compose on the office computer | `https://api.getvelora.co.ke` through Cloudflare Tunnel |

```
browser ─https─> Cloudflare ─tunnel─> cloudflared (host, systemd) ─> 127.0.0.1:8002
                                                                      nginx (web)
                                                   /static/ /media/ ┘    └─> gunicorn (api) ─> Postgres (bookingdb)
                                                                          scheduler ────────┘
```

## Containers

`docker-compose.yml` runs five services:

- **bookingdb**: Postgres 17, on the `velora_bookingdb_data` volume (the same one local development used),
  published on `127.0.0.1:5432` only.
- **migrate**: runs `migrate` and `collectstatic` on every `up`, then exits. `api` and `scheduler`
  wait for it.
- **api**: gunicorn, 3 workers.
- **web**: nginx. Serves `/static/` and `/media/` (business logos, bind-mounted from `./media`) and
  proxies everything else. It sets `X-Forwarded-Proto` and `X-Forwarded-For` (from
  `CF-Connecting-IP`) itself, so Django (`BEHIND_PROXY=1`) can trust them.
- **scheduler**: `python manage.py run_scheduler`. Runs `process_notifications` every minute and
  `send_summary_reports --days 7` once a week (Monday from 07:00 Nairobi). Whether this week's summary
  went out is read from the notifications table, so a restart does not skip or repeat it. This replaces
  the cron entries in the README.

Everything has `restart: unless-stopped`, so the stack comes back after a reboot once Docker starts
(`sudo systemctl enable docker`).

## Settings

Compose reads `.env` first, then `.env.production` on top of it (copy it from `.env.production.example`).
`.env.production` holds what differs in production: `DEBUG=False`, its own `SECRET_KEY`, `ALLOWED_HOSTS`,
`FRONTEND_URL`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`, and the bot tokens.

- Put a value in single quotes if it contains `$`. Compose would otherwise read it as a variable.
- Leave a line out (or commented) instead of empty. An empty value in `.env.production` wipes the one in `.env`.

The site and the API are the same site (`getvelora.co.ke`), so the session cookie works with
`SameSite=Lax`. A Vercel preview URL (`*.vercel.app`) is a different site: it can show public pages but
cannot sign in.

`check --deploy` reports `mail.E001` for the console `MAILERS` backend. Nothing sends through Django's
mailer (email goes through Resend in `notifications/providers.py`), so this can be ignored.

## Run it

```bash
cp .env.production.example .env.production   # fill it in
docker compose up -d --build
docker compose ps                             # api healthy, migrate exited 0
curl -I -H 'Host: api.getvelora.co.ke' http://127.0.0.1:8002/admin/login/
docker compose exec api python manage.py createsuperuser   # first time only
```

Update after a merge: `git pull && docker compose up -d --build`.
Logs: `docker compose logs -f api scheduler`.

If port 8002 is taken by a dev `runserver`, stop that first.

## Cloudflare

The computer already runs a locally managed tunnel (`/etc/cloudflared/config.yml`, tunnel
`85c2e244-…`) for `api.genmars.co.ke`. Velora joins it:

1. In `/etc/cloudflared/config.yml`, before the final `- service: http_status:404`:
   ```yaml
     - hostname: api.getvelora.co.ke
       service: http://127.0.0.1:8002
   ```
   then `sudo systemctl restart cloudflared`.
2. DNS in the `getvelora.co.ke` zone: `CNAME api → 85c2e244-da77-4d11-b9c2-12d23b13a126.cfargotunnel.com`,
   **proxied**. Do not use `cloudflared tunnel route dns`: its `cert.pem` belongs to genmars.co.ke.
3. Vercel's records (`A @ 76.76.21.21`, `CNAME www cname.vercel-dns.com`, or whatever Vercel shows)
   must be **DNS only**.
4. SSL/TLS → Edge Certificates → **Always Use HTTPS** on.

## Vercel

Import `genmarstech/frontend-reusable-modules`, root directory `velora-software`, framework Vite.
Environment variables (Production; they are read at build time, so redeploy after changing them):

```
VITE_API_URL=https://api.getvelora.co.ke/
VITE_SITE_URL=https://getvelora.co.ke
```

Add the domains `getvelora.co.ke` and `www.getvelora.co.ke` (redirect www to the apex).

## Bots and email

- Telegram: `docker compose exec api python manage.py telegram_webhook --url https://api.getvelora.co.ke`
- WhatsApp (Meta app > WhatsApp > Configuration): Callback URL
  `https://api.getvelora.co.ke/bots/whatsapp/webhook/`, Verify token = `WHATSAPP_VERIFY_TOKEN`,
  subscribe to `messages`.
- Resend: email sends from `info@genmars.co.ke` only once `genmars.co.ke` is verified in Resend. Until
  then sends fail and the scheduler retries them (5, 15, 45 minutes), then marks them failed.
