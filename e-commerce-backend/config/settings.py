"""
Django settings for the Genmars e-commerce backend.

Every value that differs between a developer's machine and production lives in
.env (gitignored); .env.example documents what's expected. Nothing here should
need to change between dev and prod — only the environment does.
"""

from datetime import timedelta
from pathlib import Path

from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


_DEV_ONLY_SECRET_KEY = "django-insecure-dev-only-key"

SECRET_KEY = os.environ.get("SECRET_KEY", _DEV_ONLY_SECRET_KEY)
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

# `manage.py check --deploy` already flags a weak/default SECRET_KEY
# (security.W009), but that check is opt-in — nothing stops this process
# from simply booting and serving real traffic with the well-known dev
# fallback above if whoever deployed it forgot to set the env var. That
# fallback is also djangorestframework-simplejwt's SIGNING_KEY by default
# (SIMPLE_JWT below sets no override), so a SECRET_KEY left at this value
# in production doesn't just weaken session/CSRF signing — it means every
# access and refresh token this API issues is forgeable by anyone who has
# read this file. Missing-critical-config must fail loudly here, not
# silently serve traffic with a key that's sitting in this repo's own
# source. Scoped to `not DEBUG` so local dev (which never sets SECRET_KEY)
# is unaffected.
if not DEBUG and SECRET_KEY == _DEV_ONLY_SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY must be set to a real, random value when DEBUG=False — "
        "refusing to start with the well-known dev default in what looks "
        "like a production configuration."
    )


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "core",
    "businesses",
    "accounts",
    "catalog",
    "inventory",
    "cart",
    "orders",
    "payments",
    "delivery",
    "analytics",
]

MIDDLEWARE = [
    # First, so every middleware and view after it — including anything
    # that logs from inside CorsMiddleware or SecurityMiddleware — has a
    # request id to tag its log lines with (core/logging.py's filter reads
    # it). See core/request_id.py.
    "core.request_id.RequestIDMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# Postgres always — see the MVP checklist's own recommendation. There is no
# sqlite fallback: a schema that only runs against sqlite in dev and Postgres
# in prod is a schema nobody actually tested.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "genmars_ecommerce"),
        "USER": os.environ.get("POSTGRES_USER", "genmars_ecommerce"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "accounts.User"


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization

LANGUAGE_CODE = "en-ke"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True


# Static files

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    # Explicit, not Django's implicit fallback — customizing STORAGES at
    # all (for "staticfiles" below) stops Django from merging in its own
    # default "default" entry, so leaving this key out doesn't fall back to
    # FileSystemStorage, it breaks every model FileField/ImageField save
    # with "Could not find config for 'default' in settings.STORAGES."
    # That's exactly what was happening here — found by the first test that
    # ever actually exercised ProductImage's save path end-to-end.
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Uploaded product images. Served by nginx/whitenoise in front in production;
# local dev serves them via Django's own static-files runserver helper.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# config/urls.py wires MEDIA_ROOT through django.views.static.serve when
# this is True, and ALWAYS when DEBUG is True regardless of this flag -- a
# ProductImage.image URL the API returns must be reachable locally without
# a separate web server in front. In production, set this only when nothing
# else (nginx, object storage) is already serving /media/ -- see that
# route's own comment in config/urls.py.
SERVE_MEDIA_VIA_DJANGO = env_bool("SERVE_MEDIA_VIA_DJANGO", False)

# Enforced in catalog/serializers.py::ProductImageSerializer.validate_image
# — DRF's ImageField already confirms Pillow can open the file (a real
# image, not just a renamed binary) before these run; these two catch the
# "real image, but huge" case Pillow's own check doesn't.
PRODUCT_IMAGE_MAX_BYTES = env_int("PRODUCT_IMAGE_MAX_BYTES", 5 * 1024 * 1024)  # 5MB
PRODUCT_IMAGE_MAX_DIMENSION_PX = env_int("PRODUCT_IMAGE_MAX_DIMENSION_PX", 4000)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Cache / Celery — same Redis instance, different logical databases via the URL.

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE

# Everything in inventory/tasks.py and delivery/tasks.py fires on_commit
# from a request, never on a clock -- these two are the only periodic tasks.
# No django-celery-beat here: two entries, and Celery's own beat process
# (`celery -A config beat`) reads this dict directly from app config --
# Charter 03 section I, a second package buys nothing a dict does not
# already do at this scale.
CELERY_BEAT_SCHEDULE = {
    "report-commerce-metrics-daily": {
        "task": "analytics.tasks.report_commerce_metrics",
        "schedule": crontab(hour=1, minute=0),
    },
    # Separate from the metrics report above on purpose -- see
    # analytics/reporting.py::post_heartbeat's own note. Every five minutes
    # keeps this comfortably inside gen-portal's 30-minute heartbeat-is-stale
    # window (portal/models.py::System.heartbeat_is_stale) with room to spare
    # for a missed tick or two.
    "report-commerce-heartbeat": {
        "task": "analytics.tasks.report_commerce_heartbeat",
        "schedule": crontab(minute="*/5"),
    },
}


# CORS — explicit allowlist only. Never CORS_ALLOW_ALL_ORIGINS; this API
# carries payment and personal-data endpoints.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")

# CSRF_TRUSTED_ORIGINS — Django's own default (empty) is correct and safe
# for this deployment's normal shape: the only session-authenticated,
# CSRF-checked surface is /admin/, reached directly at ALLOWED_HOSTS'
# own scheme+host, where Origin/Referer already match without needing
# this. Left as an explicit, env-driven empty default (mirroring
# ALLOWED_HOSTS/CORS_ALLOWED_ORIGINS above) rather than omitted, so the
# knob exists without guessing a value: set it only if admin sign-in
# starts failing with "CSRF verification failed" in a real deployment
# despite ALLOWED_HOSTS being correct — e.g. admin reached through a
# path-rewriting proxy that changes the Host the browser sees.
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")


# Email — Django's MAILERS config (EMAIL_HOST/EMAIL_PORT/etc. are deprecated
# in favour of this as of this Django version). OPTIONS is backend-specific —
# the console backend (the dev default) rejects host/port/etc. as unknown, so
# those are only included when actually talking SMTP.

_MAILER_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
_MAILER_OPTIONS = {}
if _MAILER_BACKEND.endswith("smtp.EmailBackend"):
    _MAILER_OPTIONS = {
        "host": os.environ.get("EMAIL_HOST", ""),
        "port": int(os.environ.get("EMAIL_PORT", "587")),
        "username": os.environ.get("EMAIL_HOST_USER", ""),
        "password": os.environ.get("EMAIL_HOST_PASSWORD", ""),
        "use_tls": env_bool("EMAIL_USE_TLS", True),
    }

MAILERS = {
    "default": {"BACKEND": _MAILER_BACKEND, "OPTIONS": _MAILER_OPTIONS},
}
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "store@genmars.co.ke")


# M-Pesa Daraja — see payments/mpesa.py for everything this must never do.

MPESA_ENABLED = env_bool("MPESA_ENABLED", False)
MPESA_BASE_URL = os.environ.get("MPESA_BASE_URL", "https://sandbox.safaricom.co.ke")
MPESA_CONSUMER_KEY = os.environ.get("MPESA_CONSUMER_KEY", "")
MPESA_CONSUMER_SECRET = os.environ.get("MPESA_CONSUMER_SECRET", "")
MPESA_SHORT_CODE = os.environ.get("MPESA_SHORT_CODE", "")
MPESA_CONSUMER_PASSKEY = os.environ.get("MPESA_CONSUMER_PASSKEY", "")
MPESA_TILL_NUMBER = os.environ.get("MPESA_TILL_NUMBER", "")
MPESA_TRANSACTION_TYPE = os.environ.get("MPESA_TRANSACTION_TYPE", "CustomerBuyGoodsOnline")
MPESA_CALLBACK_URL = os.environ.get("MPESA_CALLBACK_URL", "")

# Daraja signs nothing — there is no header or body field that proves a
# callback POST actually came from Safaricom. The mitigation this app can
# actually implement is a pre-shared secret as the final path segment of
# the URL registered with Daraja (MPESA_CALLBACK_URL above must end in it);
# payments/views.py::MpesaCallbackView rejects anything that doesn't match,
# and rejects everything if this is left blank — unconfigured means closed,
# never open. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
MPESA_CALLBACK_SECRET = os.environ.get("MPESA_CALLBACK_SECRET", "")


# Genmars platform reporting -- this backend is a sibling product (see this
# repo's own CLAUDE.md), and analytics/reporting.py POSTs a periodic
# commerce-metrics snapshot to gen-portal, Genmars' core Django backend, a
# SEPARATE service/repo reached over the network, not imported. The origin
# is configurable because local dev talks to a gen-portal running on
# localhost, not the real api.genmars.co.ke.
GENMARS_API_ORIGIN = os.environ.get("GENMARS_API_ORIGIN", "https://api.genmars.co.ke")

# A per-system API key gen-portal issues from its own ops UI, bearer-token
# style. Treated exactly like the M-Pesa secrets just above (root CLAUDE.md,
# "Handling secrets"): never logged, never printed, never included in an
# exception message. It will genuinely be unset until a founder issues one
# -- that only happens after this ships -- so analytics/reporting.py must
# fail loudly (one log line) but safely (no raise that reaches Celery) when
# this is blank, never silently skip the report or retry forever.
GENMARS_SYSTEM_KEY = os.environ.get("GENMARS_SYSTEM_KEY", "")


# Django REST Framework

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "EXCEPTION_HANDLER": "core.exceptions.safe_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        # Only scoped views opt in via throttle_scope; everything else is
        # still unthrottled at the DRF layer — an MVP-era decision, revisited
        # in the staging-readiness review but not expanded further than
        # these three, which are the endpoints that are either a credential-
        # guessing surface (auth) or can move real stock/money (checkout,
        # payment-initiate) repeatedly if hammered.
        "auth": "20/min",
        "payment-initiate": "10/min",
        "checkout": "20/min",
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Genmars E-commerce API",
    "DESCRIPTION": "Minimal e-commerce backend MVP.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Four different models each have their own "status" field — without
    # this, drf-spectacular can't tell them apart and names the schema
    # component after a hash instead of something readable.
    "ENUM_NAME_OVERRIDES": {
        "ProductStatusEnum": "catalog.models.Product.Status",
        "OrderStatusEnum": "orders.models.Order.Status",
        "PaymentStatusEnum": "payments.models.Payment.Status",
        "ShipmentStatusEnum": "delivery.models.Shipment.Status",
        "BusinessStatusEnum": "businesses.models.Business.Status",
        "BusinessMembershipStatusEnum": "businesses.models.BusinessMembership.MembershipStatus",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}


# Production-only hardening. DEBUG=False is what flips these on, so a
# developer never has to remember to toggle them separately.
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# SECURE_SSL_REDIRECT above (and request.is_secure() generally) trusts the
# connection Django itself terminates — which is never TLS once this sits
# behind any reverse proxy or platform edge (Railway, nginx, a load
# balancer), because the proxy terminates TLS and forwards plain HTTP
# internally. Left alone, that makes every request look insecure to Django,
# and SECURE_SSL_REDIRECT then redirects every one of them — to itself,
# forever. The fix (trusting X-Forwarded-Proto) is also a real risk if
# Django is ever reachable by *any other path* than through that proxy: an
# attacker who can reach Django directly could then forge that header and
# defeat the HTTPS-redirect/secure-cookie protections it's there to
# enforce. So this is opt-in and off by default — flip it only once the
# deployment topology is actually confirmed, i.e. once Django's only
# network path is through a proxy you trust to set this header honestly
# and strip/overwrite any client-supplied one. Confirmed safe to enable
# on Railway specifically: a Railway service's container is reachable only
# through Railway's own edge, which sets this header itself.
if env_bool("DJANGO_TRUST_PROXY_HEADERS", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "core.logging.RequestIDLogFilter"},
    },
    "formatters": {
        "json": {"()": "core.logging.JSONFormatter"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "filters": ["request_id"], "formatter": "json"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
