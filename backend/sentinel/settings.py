import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-for-take-home-assessment-only")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "channels",
    "alerts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "alerts.middleware.BasicAuthMiddleware",
]

ROOT_URLCONF = "sentinel.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
            ],
        },
    },
]

ASGI_APPLICATION = "sentinel.asgi.application"

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
            "capacity": 3000,
            "expiry": 30,
        },
    },
}

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    import dj_database_url

    DATABASES = {
        "default": dj_database_url.parse(DATABASE_URL, conn_max_age=600)
    }
else:
    DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "db.sqlite3"))
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": DATABASE_PATH,
            "OPTIONS": {
                "timeout": 20,
            },
        }
    }

USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SENSOR_WS_URL = os.getenv("SENSOR_WS_URL", "ws://localhost:8765")
STREAM_KEY = os.getenv("SENTINEL_STREAM_KEY", "sentinel:events")
STREAM_GROUP = os.getenv("SENTINEL_STREAM_GROUP", "processors")
STREAM_MAXLEN = int(os.getenv("SENTINEL_STREAM_MAXLEN", "200000"))
DEDUPE_TTL_SECONDS = int(os.getenv("SENTINEL_DEDUPE_TTL", "300"))
SENSOR_SILENCE_SECONDS = float(os.getenv("SENTINEL_SILENCE_SECONDS", "90"))
SWEEP_INTERVAL_SECONDS = float(os.getenv("SENTINEL_SWEEP_INTERVAL", "2"))
PENDING_CLAIM_IDLE_MS = int(os.getenv("SENTINEL_CLAIM_IDLE_MS", "5000"))

DASHBOARD_BASIC_AUTH_USER = os.getenv("DASHBOARD_BASIC_AUTH_USER", "")
DASHBOARD_BASIC_AUTH_PASS = os.getenv("DASHBOARD_BASIC_AUTH_PASS", "")
