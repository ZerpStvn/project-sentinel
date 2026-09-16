import asyncio
import datetime

import websockets
from django.conf import settings
from django.core.management.base import BaseCommand

from alerts.redis_client import get_redis, is_feed_enabled


class Command(BaseCommand):
    help = "Connect to the sensor fleet WebSocket feed and durably buffer events into Redis."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        r = get_redis()
        url = settings.SENSOR_WS_URL
        total = 0
        feed_state = {"enabled": False}

        asyncio.create_task(self._watch_feed_state(r, feed_state))

        self.stdout.write(f"[ingest] connecting to {url}")
        async for ws in websockets.connect(url, max_queue=2048, ping_interval=20, ping_timeout=20):
            try:
                self.stdout.write(self.style.SUCCESS(f"[ingest] connected to {url}"))
                async for raw in ws:
                    if not feed_state["enabled"]:
                        continue

                    ingested_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    pipe = r.pipeline()
                    pipe.xadd(
                        settings.STREAM_KEY,
                        {"payload": raw, "ingested_ts": ingested_ts},
                        maxlen=settings.STREAM_MAXLEN,
                        approximate=True,
                    )
                    pipe.incr("sentinel:ingest:count")
                    await pipe.execute()

                    total += 1
                    if total % 1000 == 0:
                        self.stdout.write(f"[ingest] {total} events durably buffered")
            except websockets.exceptions.ConnectionClosed:
                self.stdout.write(self.style.WARNING("[ingest] connection lost, reconnecting..."))
                continue

    async def _watch_feed_state(self, r, feed_state):
        while True:
            try:
                enabled = await is_feed_enabled(r)
                if enabled != feed_state["enabled"]:
                    self.stdout.write(f"[ingest] feed {'ENABLED' if enabled else 'disabled'}")
                feed_state["enabled"] = enabled
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(self.style.ERROR(f"[ingest] feed-state check failed: {exc}"))
            await asyncio.sleep(1)
