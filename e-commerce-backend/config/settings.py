"""
Django settings for the Genmars e-commerce backend.

Every value that differs between a developer's machine and production lives in
.env (gitignored); .env.example documents what's expected. Nothing here should
need to change between dev and prod — only the environment does.
"""

from datetime import timedelta
from pathlib import Path

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


SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-dev-only-key")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")


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
    "accounts",
    "catalog",
    "inventory",
    "cart",
    "orders",
    "payments",
    "delivery",
]

MIDDLEWARE = [
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
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Uploaded product images. Served by nginx/whitenoise in front in production;
# local dev serves them via Django's own static-files runserver helper.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

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


# CORS — explicit allowlist only. Never CORS_ALLOW_ALL_ORIGINS; this API
# carries payment and personal-data endpoints.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")


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
        # Only scoped views (auth, payment-initiate) opt in via throttle_scope;
        # everything else is unthrottled at the DRF layer for the MVP.
        "auth": "20/min",
        "payment-initiate": "10/min",
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Genmars E-commerce API",
    "DESCRIPTION": "Minimal e-commerce backend MVP.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
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


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
