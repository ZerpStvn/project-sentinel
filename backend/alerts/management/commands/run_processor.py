import asyncio
import datetime
import json
import uuid

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import OperationalError

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
        delay = 1
        while True:
            try:
                cache = {}
                async for s in SensorStatus.objects.all():
                    cache[s.sensor_id] = s.status
                self.stdout.write(self.style.SUCCESS(
                    f"[processor] {consumer_name} starting; reclaiming any stale pending entries"
                ))
                reclaimed = await self._reclaim(r, consumer_name, channel_layer, cache)
                break
            except OperationalError as exc:
                self.stderr.write(self.style.ERROR(
                    f"[processor] database unavailable at startup ({exc}); retrying in {delay}s"
                ))
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

        if reclaimed:
            self.stdout.write(self.style.WARNING(f"[processor] recovered {reclaimed} in-flight event(s) from a prior run"))

        asyncio.create_task(self._sweep_loop(r, channel_layer, cache))
        asyncio.create_task(self._reclaim_loop(r, consumer_name, channel_layer, cache))
        asyncio.create_task(self._retention_loop())

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
                try:
                    await self._process_entries(entries, r, channel_layer, cache)
                except OperationalError as exc:
                    self.stderr.write(self.style.ERROR(
                        f"[processor] database unavailable, entries remain pending for retry: {exc}"
                    ))
                    await asyncio.sleep(3)

    async def _process_entries(self, entries, r, channel_layer, cache):
        if not entries:
            return 0

        now = datetime.datetime.now(datetime.timezone.utc)
        parsed = []
        for stream_id, fields in entries:
            payload = fields.get("payload")
            event = None
            if payload:
                try:
                    event = json.loads(payload)
                except (TypeError, ValueError) as exc:
                    self.stderr.write(self.style.ERROR(f"[processor] dropping malformed entry {stream_id}: {exc}"))
            parsed.append((stream_id, event, fields.get("ingested_ts")))

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

        for sid, (site_id, etype) in sensor_latest.items():
            desired = "offline" if etype == "camera_offline" else "online"
            if cache.get(sid) == desired:
                continue
            status_obj, _ = await SensorStatus.objects.aupdate_or_create(
                sensor_id=sid, defaults={"site_id": site_id, "status": desired, "last_seen": now}
            )
            cache[sid] = desired
            await channel_layer.group_send(DASHBOARD_GROUP, {"type": "sensor.update", "sensor": sensor_to_dict(status_obj)})

        to_create = []
        for _, event, ingested_ts in parsed:
            if not event or event.get("type") == HEARTBEAT_TYPE:
                continue
            eid = event.get("event_id")
            if not first_time.get(eid, True):
                continue
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
        while True:
            await asyncio.sleep(5)
            try:
                await self._reclaim(r, consumer_name, channel_layer, cache)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(self.style.ERROR(f"[processor] reclaim-loop error: {exc}"))

    async def _retention_loop(self):
        while True:
            await asyncio.sleep(settings.RETENTION_SWEEP_SECONDS)
            try:
                cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                    days=settings.ALERT_RETENTION_DAYS
                )
                deleted, _ = await sync_to_async(Alert.objects.filter(received_at__lt=cutoff).delete)()
                if deleted:
                    self.stdout.write(f"[processor] pruned {deleted} alert(s) older than {settings.ALERT_RETENTION_DAYS}d")
            except OperationalError as exc:
                self.stderr.write(self.style.ERROR(f"[processor] retention sweep: database unavailable: {exc}"))
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(self.style.ERROR(f"[processor] retention sweep error: {exc}"))
