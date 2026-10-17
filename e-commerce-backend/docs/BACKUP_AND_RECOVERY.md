# Backup and recovery

## What actually exists

`scripts/backup.sh` — already existed in the original MVP commit
(`e3af3fd`), extended in an earlier review to support optional encryption
at rest, and extended again in the Phase 6 pilot-readiness review to also
back up `MEDIA_ROOT` (product images) — a confirmed gap before that: a
database restore brought back every `ProductImage` *row*, pointing at
files a database-only backup never actually saved anywhere. It dumps the
database running in this repo's own `docker-compose.yml` `db` service with
`pg_dump`, gzips it, tars+gzips `media/` separately, and writes both to
`backups/` (gitignored). There is **no restore script**, by deliberate
design stated in the script's own comment: restoring is rare and
destructive enough that it should stay a reviewed, deliberate command a
human runs, not something automated enough to run on autopilot. This
document is that command.

```bash
./scripts/backup.sh
```

### Encryption at rest

Set `BACKUP_GPG_RECIPIENT` in `.env` to a GPG key ID, fingerprint, or email
this host already trusts, and every dump is encrypted to it before it
touches disk — the same convention `gen-portal`'s own backup script uses.
Left unset, the script still runs (so nothing breaks in a dev checkout that
has no GPG key configured) but prints a loud warning to stderr and writes a
plain, unencrypted `.sql.gz`. **Any environment holding real customer data
or real M-Pesa phone numbers must set this before its first backup** — a
dump in that state is personal data at rest, unencrypted, on whatever
storage holds `backups/`.

Nothing in this change generates or embeds a key. The key has to already
exist on the machine taking the backup, exactly as it does for `gen-portal`.

## Restore procedure (manual, by design)

1. **Stop anything writing to the target database** you're restoring into.
   Restoring into a database that's still receiving writes from the live
   app is how a restore corrupts itself.
2. **Restore into a database the running app is not currently pointed
   at** — a fresh container/instance, or the same instance under a
   temporary database name — until the restore is verified. Never restore
   directly over a production database's live, named database as the
   first step.
3. Run:
   ```bash
   gunzip -c backups/genmars_ecommerce_<timestamp>.sql.gz | \
     docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
   ```
   For a GPG-encrypted dump, decrypt first: `gpg --decrypt backups/<file>.sql.gz.gpg | gunzip | docker compose exec -T db psql ...`
3b. Restore media alongside it: `tar xzf backups/media_<timestamp>.tar.gz -C <restore target>` (or
   `gpg --decrypt backups/media_<timestamp>.tar.gz.gpg | tar xz -C <restore target>` if encrypted) —
   a database-only restore brings back `ProductImage` rows pointing at files that were never restored.
4. **Verify before cutting over** — at minimum:
   ```bash
   ./venv/bin/python manage.py check
   ./venv/bin/python manage.py makemigrations --check --dry-run
   ```
   and spot-check that the business/membership/catalog/inventory/order/
   payment relationships you expect are actually there (see the rehearsal
   below for exactly what "verified" means in practice).
5. Only once verified, point the application's `POSTGRES_*` env vars at the
   restored database and restart it.

## Restoration rehearsal — actually executed, this session

This was run for real, not assumed. Two disposable, throwaway
`docker compose` projects (never the project's own named containers or
volumes — see the note below on what was and wasn't touched), both torn
down with their volumes removed afterward:

1. Seeded a source database via the real app: `seed_catalog` (6 categories,
   21 products, 4 variants) plus a hand-written script creating a **second
   business** ("Acme Fashion"), two owner memberships, a customer with
   orders at **both** businesses, a successful M-Pesa payment on one order,
   and inventory records spanning both businesses.
2. Ran `scripts/backup.sh` unmodified against that database via
   `COMPOSE_PROJECT_NAME`, pointed at the disposable project. Produced a
   12,905-byte `.sql.gz` — confirmed by inspection to contain real `COPY`
   data for both businesses (`businesses_business` had exactly the 2 rows
   expected, by slug).
3. Brought up a **second, completely separate, freshly-initialized**
   Postgres container — empty, no migrations ever applied — and restored
   the dump into it with the exact `gunzip | psql` command documented
   above.
4. Verified afterward, against the restored database:
   - `manage.py check` → no issues. `manage.py makemigrations --check
     --dry-run` → no changes detected (the restored schema exactly matches
     the current migration state).
   - Row counts matched the source exactly: 2 businesses (correct slugs),
     2 memberships, 6 categories, 22 products, 4 variants, 25 inventory
     records, 2 carts, 2 orders, 1 payment, 2 `StoreSettings` rows (one per
     business).
   - **Relationships intact, not just row counts**: each order's `business`
     FK pointed at the correct business (`GM-23AFA9B0C72E` → `genmars-store`,
     `GM-BD25D0B182CE` → `acme-fashion`), each order's item count and
     status survived (`paid` / `awaiting_payment`), the payment's `order`
     FK and receipt number (`BKUPREHEARSAL1`) survived, and every inventory
     record's `business` matched its product's actual business, with the
     Acme Fashion item's quantity correctly reflecting the stock deduction
     from its own checkout (5 → 4).

**What this rehearsal did NOT touch**: the project's own `e-commerce-backend-db-1`
container/volume (which holds real data from earlier work in this project)
was never used for this rehearsal, and is untouched — confirmed by checking
its volume still exists and was never part of either disposable compose
project. An attempt to temporarily repoint the project's real
`docker-compose.yml` at a free port was made and then reverted via `git
checkout` the moment it became clear the container's volume already held
real session data, before anything was written to it beyond a single `SELECT
count(*)` read.

## Retention

Not currently automated — `backups/` is local to whichever host runs the
script and is gitignored. **Recommendation, not yet implemented**: a daily
cron entry calling `scripts/backup.sh`, with retention enforced by a
separate rotation (e.g. keep 7 daily + 4 weekly), and offsite copying
(object storage, a second host) — local-disk-only backups don't survive
the loss of the machine they're on, which is the single most likely real
failure this is meant to protect against.

## Access controls

- The backup file itself: readable only by whatever user/group runs the
  backup; `backups/` should not be world-readable on a shared host.
- The GPG private key needed to decrypt (if `BACKUP_GPG_RECIPIENT` is set):
  must exist only where restores are actually performed, never copied
  alongside the backups themselves — losing it makes every encrypted
  backup permanently unreadable, same caveat as `gen-portal`'s.
- `.env` (database credentials, the GPG recipient) is gitignored and must
  stay that way; nothing in this review changed that.

## Recovery objectives

- **RPO (how much data could be lost)**: bounded by backup frequency, which
  is currently **whatever a human remembers to run manually** — there is no
  cron job wired up yet. Until one exists, treat RPO as "since the last
  time someone ran it," not a number.
- **RTO (how long a restore takes)**: the rehearsal above, including
  spinning up a fresh Postgres container from nothing, took a few minutes
  at this data volume. That does not extrapolate linearly to a
  production-sized database — this is evidence for "the procedure works,"
  not a timing guarantee at scale.

## Remaining limitations

- No point-in-time recovery — this is periodic full dumps only, not WAL
  archiving. A failure between two backups loses everything written since
  the last one.
- No automated schedule yet (see Retention above) — this review tested and
  documented the procedure; it did not wire up a cron job, since that's an
  infrastructure decision about a host that doesn't exist yet for this
  project.
- Encryption is opt-in via an env var, not enforced — nothing in the script
  refuses to run unencrypted; it only warns.
- The rehearsal above predates the media-backup addition and only verified
  the database half. Re-verified separately in the Phase 6 pilot-readiness
  review: a real `pg_dump | gzip` of a seeded database (2 businesses, 21
  products, 24 inventory records), restored into a fresh, disposable
  Postgres container, came back with every row count and relationship
  intact (`manage.py check` and `makemigrations --check --dry-run` both
  clean against the restored schema). The media-tar logic was verified
  separately (a real `media/` directory with actual uploaded product
  images, tarred and listed back out correctly) but not yet exercised
  through a full combined database+media restore in one rehearsal.
