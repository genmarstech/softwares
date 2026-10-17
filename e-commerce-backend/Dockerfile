# syntax=docker/dockerfile:1.7

# ─────────────────────────────────────────────────────────────────────────────
# Genmars commerce platform — Django API
#
# Written for the shared Hetzner host (see docs/HETZNER_DEPLOYMENT.md), not for
# Railway — Railway's own path (Nixpacks, no Dockerfile) is unaffected and still
# described in docs/RAILWAY_DEPLOYMENT.md if that candidate is ever revisited.
#
# Pinned to 3.14, matching this repo's own venv (confirmed via `python
# --version` before this file was written) and Django 6.1 / DRF 3.18, which
# both already support it — this is NOT gen-portal's Dockerfile pinned to
# 3.13: that pin exists there for an older Django/DRF pair that doesn't
# support 3.14 yet. Copying that pin here would be wrong, not just cautious.
# ─────────────────────────────────────────────────────────────────────────────

ARG REGISTRY=docker.io/library

# ---- build ------------------------------------------------------------------

FROM ${REGISTRY}/python:3.14-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# psycopg builds a C extension; the toolchain stays in this stage only.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential libpq-dev; \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# ---- runtime ------------------------------------------------------------------

FROM ${REGISTRY}/python:3.14-slim AS runtime

LABEL org.opencontainers.image.title="genmars-commerce-api" \
      org.opencontainers.image.description="Multi-tenant commerce backend for Genmars Tech Limited" \
      org.opencontainers.image.vendor="Genmars Tech Limited"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings

# libpq5 is the runtime half of libpq-dev — psycopg needs the shared library,
# not the headers. curl is for the HEALTHCHECK below.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libpq5 curl; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd -g 10010 -r app; \
    useradd -u 10010 -r -g app -s /usr/sbin/nologin -d /app app

WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY --chown=10010:10010 . .

# collectstatic at BUILD time, not at boot — see gen-portal/backend/Dockerfile
# for the full reasoning (a restarting container would otherwise race another
# instance writing the same files, and a read-only root filesystem, below,
# could not write them at boot anyway). No real DB or secret exists yet at
# build time, so both are stubbed for this one command; nothing here is baked
# into the image, settings are read from the environment at container start.
RUN set -eux; \
    SECRET_KEY=build-time-only-not-a-secret \
    DEBUG=True \
    python manage.py collectstatic --noinput --clear; \
    chown -R 10010:10010 /app/staticfiles

# Where uploaded product images land. Created here, owned by the app user, so
# that when compose.hetzner.yaml mounts a named volume over this path the
# volume inherits this ownership (Docker seeds an empty named volume from the
# image directory it covers, including permissions) — without this the volume
# would be created root-owned and every image upload would fail.
RUN mkdir -p /app/media && chown 10010:10010 /app/media

USER 10010:10010

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8010/health/ || exit 1

# Migrations need a real database, which only exists once the container is
# actually running (compose's depends_on: condition: service_healthy gates
# this) — so migrate runs here, in an entrypoint, not at build time above.
ENTRYPOINT ["./docker-entrypoint.sh"]

# Plain sync workers: PRODUCT_IMAGE_MAX_BYTES defaults to 5MB (config/settings.py),
# small enough on any real connection to stay well inside the 30s timeout — this
# is not gen-portal's API, which raised its ceiling to 200MB and had to switch
# worker classes because of it. Revisit only if that default is ever raised a
# lot, not by reflex.
# --no-control-socket: confirmed live, this review, against
# compose.hetzner.yaml's read_only root filesystem — gunicorn 26's control
# socket defaults to ./.gunicorn/gunicorn.ctl (relative to WORKDIR, inside
# /app), which cannot be created on a read-only root and logged "Read-only
# file system: '/app/.gunicorn'" on every boot. Harmless to serving requests
# (workers still started, health checks still answered 200) but noisy, and
# nothing here uses gunicorn's runtime control interface anyway.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8010", \
     "--workers", "2", \
     "--timeout", "30", \
     "--graceful-timeout", "30", \
     "--no-control-socket", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
