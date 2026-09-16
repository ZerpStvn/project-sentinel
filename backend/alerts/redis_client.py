import redis.asyncio as aioredis
from django.conf import settings

_client = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


async def ensure_group(r: aioredis.Redis):
    try:
        await r.xgroup_create(settings.STREAM_KEY, settings.STREAM_GROUP, id="0", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise
