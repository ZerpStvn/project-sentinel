"""
Ingest worker.

The sensor fleet simulator is itself a WebSocket *server* that streams
events to whoever connects (see the appendix generator). So our ingest
component is a reconnecting WebSocket *client*: it durably appends every
raw event onto a Redis Stream before doing anything else with it.

Why this design gives us our delivery guarantee:
- `XADD` only returns once Redis has accepted the write (and, with
  `appendonly yes` AOF enabled on the Redis server, once it's fsynced to
  disk on the interval configured there). Once XADD succeeds, the event
  survives an ingest crash, a processor crash, or a processor restart.
- We never call `recv()` again until the previous event's XADD has
  completed. Combined with a bounded `max_queue` on the websocket
  connection, that means a slow/unavailable Redis applies real
  backpressure all the way back to the TCP socket instead of buffering
  unboundedly in this process's memory.
- `async for ws in websockets.connect(...)` is the library's built-in
  reconnect-with-backoff loop: on any connection drop we automatically
  retry, so a sensor-fleet restart or network blip doesn't require any
  extra glue here.
- The stream itself is bounded (`STREAM_MAXLEN`, approximate trimming) so
  a fully stalled consumer can't grow Redis without limit. That's an
  explicit, documented trade-off: durability is guaranteed for events
  still inside that bounded window, not forever. See the README.
"""
import asyncio
import datetime

import websockets
from django.conf import settings
from django.core.management.base import BaseCommand

from alerts.redis_client import get_redis


class Command(BaseCommand):
    help = "Connect to the sensor fleet WebSocket feed and durably buffer events into Redis."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        r = get_redis()
        url = settings.SENSOR_WS_URL
        total = 0

        self.stdout.write(f"[ingest] connecting to {url}")
        async for ws in websockets.connect(url, max_queue=2048, ping_interval=20, ping_timeout=20):
            try:
                self.stdout.write(self.style.SUCCESS(f"[ingest] connected to {url}"))
                async for raw in ws:
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
