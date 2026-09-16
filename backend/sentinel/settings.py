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
            # Default capacity (100) is tuned for typical app traffic, not a
            # firehose of alerts. Each dashboard connection's channel is a
            # bounded queue; group_send silently drops messages beyond
            # capacity rather than blocking the sender. Raised here so a
            # connected dashboard can absorb a real burst (~500 events) of
            # push messages without losing its own ack/resolve confirmation
            # or an alert notification. This is a UI-freshness safeguard,
            # not part of the alert durability guarantee -- the alert data
            # itself is safe in Redis Streams/DB regardless; a dropped push
            # just means the dashboard catches up on next snapshot/reconnect.
            "capacity": 3000,
            "expiry": 30,
        },
    },
}

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # Deployed setups (e.g. Railway) run web/ingest/processor as separate
    # services with no shared filesystem, so SQLite's single-file model
    # doesn't work -- use a real Postgres instead (DATABASE_URL is provided
    # by Railway's Postgres plugin). See DEPLOY.md.
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

# --- Sentinel-specific config -------------------------------------------------

SENSOR_WS_URL = os.getenv("SENSOR_WS_URL", "ws://localhost:8765")
STREAM_KEY = os.getenv("SENTINEL_STREAM_KEY", "sentinel:events")
STREAM_GROUP = os.getenv("SENTINEL_STREAM_GROUP", "processors")
STREAM_MAXLEN = int(os.getenv("SENTINEL_STREAM_MAXLEN", "200000"))
DEDUPE_TTL_SECONDS = int(os.getenv("SENTINEL_DEDUPE_TTL", "300"))
# With 200 sensors sharing the feed, the mean gap between events for any one
# sensor is roughly (num_sensors / RATE) seconds -- at the default RATE=25
# that's ~8s. This threshold must sit well above that mean or ordinary
# Poisson variance triggers false "silent" alerts constantly. 90s keeps
# random false positives to roughly ~1/hour across all 200 sensors; for a
# quick demo of real silence detection, lower this (or just stop
# sensor-sim) rather than lowering it to something close to the mean gap.
SENSOR_SILENCE_SECONDS = float(os.getenv("SENTINEL_SILENCE_SECONDS", "90"))
SWEEP_INTERVAL_SECONDS = float(os.getenv("SENTINEL_SWEEP_INTERVAL", "2"))
PENDING_CLAIM_IDLE_MS = int(os.getenv("SENTINEL_CLAIM_IDLE_MS", "5000"))

# Optional HTTP basic auth in front of the dashboard (stretch goal). Leave
# either unset to disable.
DASHBOARD_BASIC_AUTH_USER = os.getenv("DASHBOARD_BASIC_AUTH_USER", "")
DASHBOARD_BASIC_AUTH_PASS = os.getenv("DASHBOARD_BASIC_AUTH_PASS", "")
