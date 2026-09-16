"""
Processor worker.

Reads events from the Redis Stream via a consumer group, normalizes and
deduplicates them, resolves severity, updates live per-sensor/per-site
state, persists alerts, and broadcasts to every connected dashboard over
the Channels layer.

Restart / crash survival (the "zero missed alerts" core):
- Redis consumer groups track delivery per-consumer. An entry is only
  removed from the group's Pending Entries List (PEL) when we `XACK` it
  *after* it has been fully processed (DB write + broadcast). If this
  process is killed at any point before that ack, the entry simply stays
  pending.
- On startup (and periodically while running), `XAUTOCLAIM` reclaims any
  entry that has been pending longer than PENDING_CLAIM_IDLE_MS from a
  consumer that never acked it -- including a previous instance of this
  same worker that crashed. That reclaimed entry is processed exactly
  like a fresh one.
- Persisting the resulting Alert is idempotent: `event_id` is unique in
  the database, so if a crash happens *after* the DB write but *before*
  the XACK (causing the same entry to be reclaimed and reprocessed), the
  duplicate is absorbed by `get_or_create` rather than shown twice.
- A short-lived Redis SETNX dedupe key additionally absorbs the common
  case cheaply, without a DB round trip.

This gives an explicit at-least-once delivery guarantee end-to-end
(ingest -> stream -> processor -> DB/dashboard), with the DB unique
constraint providing effectively-once *visibility* to operators.
"""
import asyncio
import datetime
import json
import uuid

from channels.layers import get_channel_layer
from django.conf import settings
from django.core.management.base import BaseCommand

from alerts.models import Alert, SensorStatus
from alerts.redis_client import ensure_group, get_redis
from alerts.serialize import alert_to_dict, sensor_to_dict
from alerts.severity import HEARTBEAT_TYPE, SENSOR_SILENT_TYPE, resolve_severity

DASHBOARD_GROUP = "dashboard"


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class Command(BaseCommand):
    help = "Consume the Redis stream, dedupe/prioritize/persist alerts, and broadcast to the live dashboard."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        r = get_redis()
        await ensure_group(r)
        channel_layer = get_channel_layer()
        consumer_name = f"proc-{uuid.uuid4().hex[:8]}"

        cache = {}
        async for s in SensorStatus.objects.all():
            cache[s.sensor_id] = s.status

        self.stdout.write(self.style.SUCCESS(
            f"[processor] {consumer_name} starting; reclaiming any stale pending entries"
        ))
        reclaimed = await self._reclaim(r, consumer_name, channel_layer, cache)
        if reclaimed:
            self.stdout.write(self.style.WARNING(f"[processor] recovered {reclaimed} in-flight event(s) from a prior run"))

        asyncio.create_task(self._sweep_loop(r, channel_layer, cache))
        asyncio.create_task(self._reclaim_loop(r, consumer_name, channel_layer, cache))

        while True:
            resp = await r.xreadgroup(
                groupname=settings.STREAM_GROUP,
                consumername=consumer_name,
                streams={settings.STREAM_KEY: ">"},
                count=500,
                block=2000,
            )
            if not resp:
                continue
            for _stream_name, entries in resp:
                await self._process_entries(entries, r, channel_layer, cache)

    async def _process_entries(self, entries, r, channel_layer, cache):
        """
        Process a whole XREADGROUP/XAUTOCLAIM batch (up to ~100-200 entries)
        together instead of one event at a time. Batching two things is what
        makes this fast enough to hold sub-second latency under the
        generator's bursts:

        - Redis: every SETNX (dedupe) and HSET (last-seen) in the batch goes
          through one pipeline, i.e. one network round trip for the whole
          batch instead of one per event. XACK + the processed counter are
          likewise batched at the end.
        - SQLite: new alerts are written with one `abulk_create` instead of
          one `INSERT` (and fsync) per event -- measured ~17ms/commit doing
          it one row at a time on this Docker volume, which alone capped
          throughput at ~60 events/sec.
        """
        if not entries:
            return 0

        now = datetime.datetime.now(datetime.timezone.utc)
        parsed = []  # (stream_id, event_dict_or_None, ingested_ts)
        for stream_id, fields in entries:
            payload = fields.get("payload")
            event = None
            if payload:
                try:
                    event = json.loads(payload)
                except (TypeError, ValueError) as exc:
                    self.stderr.write(self.style.ERROR(f"[processor] dropping malformed entry {stream_id}: {exc}"))
            parsed.append((stream_id, event, fields.get("ingested_ts")))

        # --- batch 1: sensor liveness (collapse to latest per sensor) + dedupe SETNX ---
        sensor_latest = {}
        for _, event, _ in parsed:
            if event and event.get("sensor_id"):
                sensor_latest[event["sensor_id"]] = (event.get("site_id", ""), event.get("type"))

        dedupe_ids = [
            event["event_id"] for _, event, _ in parsed
            if event and event.get("type") != HEARTBEAT_TYPE and event.get("event_id")
        ]

        pipe = r.pipeline()
        for sid, (site_id, _etype) in sensor_latest.items():
            pipe.hset("sentinel:lastseen", sid, now.isoformat())
            pipe.hset("sentinel:sitemap", sid, site_id)
        for eid in dedupe_ids:
            pipe.set(f"sentinel:seen:{eid}", "1", nx=True, ex=settings.DEDUPE_TTL_SECONDS)
        results = await pipe.execute() if (sensor_latest or dedupe_ids) else []

        dedupe_offset = 2 * len(sensor_latest)
        first_time = {
            eid: bool(results[dedupe_offset + i]) for i, eid in enumerate(dedupe_ids)
        }

        # --- sensor status transitions: rare, so a per-sensor DB write here is fine ---
        for sid, (site_id, etype) in sensor_latest.items():
            desired = "offline" if etype == "camera_offline" else "online"
            if cache.get(sid) == desired:
                continue
            status_obj, _ = await SensorStatus.objects.aupdate_or_create(
                sensor_id=sid, defaults={"site_id": site_id, "status": desired, "last_seen": now}
            )
            cache[sid] = desired
            await channel_layer.group_send(DASHBOARD_GROUP, {"type": "sensor.update", "sensor": sensor_to_dict(status_obj)})

        # --- batch 2: build + bulk-insert new alerts ---
        to_create = []
        for _, event, ingested_ts in parsed:
            if not event or event.get("type") == HEARTBEAT_TYPE:
                continue
            eid = event.get("event_id")
            if not first_time.get(eid, True):
                continue  # duplicate delivery, already recorded
            ingested_dt = parse_iso(ingested_ts)
            latency_ms = (now - ingested_dt).total_seconds() * 1000 if ingested_dt else 0.0
            to_create.append(Alert(
                event_id=eid,
                sensor_id=event.get("sensor_id"),
                site_id=event.get("site_id"),
                type=event.get("type"),
                severity=resolve_severity(event.get("type"), event.get("severity_hint")),
                confidence=event.get("confidence", 0),
                severity_hint=event.get("severity_hint"),
                event_ts=parse_iso(event.get("ts")) or now,
                processing_latency_ms=round(latency_ms, 2),
            ))

        if to_create:
            # ignore_conflicts absorbs the rare case of a redelivery after the
            # Redis dedupe key already expired -- the unique event_id constraint
            # is the final backstop against a duplicate row.
            await Alert.objects.abulk_create(to_create, ignore_conflicts=True)
            eids = [a.event_id for a in to_create]
            async for alert in Alert.objects.filter(event_id__in=eids):
                await channel_layer.group_send(DASHBOARD_GROUP, {"type": "alert.new", "alert": alert_to_dict(alert)})

        stream_ids = [sid for sid, _, _ in parsed]
        ack_pipe = r.pipeline()
        ack_pipe.xack(settings.STREAM_KEY, settings.STREAM_GROUP, *stream_ids)
        ack_pipe.incrby("sentinel:processor:count", len(stream_ids))
        await ack_pipe.execute()
        return len(stream_ids)

    async def _sweep_loop(self, r, channel_layer, cache):
        """Detect sensors that have gone silent (no event AND no heartbeat),
        which is distinct from an explicit camera_offline event."""
        while True:
            await asyncio.sleep(settings.SWEEP_INTERVAL_SECONDS)
            try:
                now = datetime.datetime.now(datetime.timezone.utc)
                lastseen = await r.hgetall("sentinel:lastseen")
                sitemap = await r.hgetall("sentinel:sitemap")
                for sensor_id, iso in lastseen.items():
                    last = parse_iso(iso)
                    if not last:
                        continue
                    age = (now - last).total_seconds()
                    if age > settings.SENSOR_SILENCE_SECONDS and cache.get(sensor_id) == "online":
                        site_id = sitemap.get(sensor_id, "")
                        cache[sensor_id] = "silent"
                        status_obj, _ = await SensorStatus.objects.aupdate_or_create(
                            sensor_id=sensor_id, defaults={"site_id": site_id, "status": "silent", "last_seen": last}
                        )
                        await channel_layer.group_send(
                            DASHBOARD_GROUP, {"type": "sensor.update", "sensor": sensor_to_dict(status_obj)}
                        )
                        alert = await Alert.objects.acreate(
                            event_id=f"synthetic_silent_{sensor_id}_{int(now.timestamp())}",
                            sensor_id=sensor_id,
                            site_id=site_id,
                            type=SENSOR_SILENT_TYPE,
                            severity=resolve_severity(SENSOR_SILENT_TYPE, None),
                            confidence=1.0,
                            event_ts=now,
                            processing_latency_ms=0,
                        )
                        await channel_layer.group_send(
                            DASHBOARD_GROUP, {"type": "alert.new", "alert": alert_to_dict(alert)}
                        )
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(self.style.ERROR(f"[processor] sweep error: {exc}"))

    async def _reclaim(self, r, consumer_name, channel_layer, cache):
        total = 0
        cursor = "0-0"
        while True:
            cursor, entries, _deleted = await r.xautoclaim(
                settings.STREAM_KEY, settings.STREAM_GROUP, consumer_name,
                min_idle_time=settings.PENDING_CLAIM_IDLE_MS, start_id=cursor, count=200,
            )
            if entries:
                total += await self._process_entries(entries, r, channel_layer, cache)
            if cursor in ("0-0", "0", 0):
                break
        return total

    async def _reclaim_loop(self, r, consumer_name, channel_layer, cache):
        """Periodically reclaim entries left pending by *other* dead consumers
        while this process is running (multi-worker robustness)."""
        while True:
            await asyncio.sleep(5)
            try:
                await self._reclaim(r, consumer_name, channel_layer, cache)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(self.style.ERROR(f"[processor] reclaim-loop error: {exc}"))
