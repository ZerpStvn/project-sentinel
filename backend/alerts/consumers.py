import datetime
import json

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import Alert, SensorStatus
from .serialize import alert_to_dict, sensor_to_dict

DASHBOARD_GROUP = "dashboard"


class DashboardConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add(DASHBOARD_GROUP, self.channel_name)
        await self.accept()
        snapshot = await self._snapshot()
        await self.send_json({"type": "snapshot", **snapshot})

    async def disconnect(self, code):
        await self.channel_layer.group_discard(DASHBOARD_GROUP, self.channel_name)

    async def receive_json(self, content, **kwargs):
        action = content.get("action")
        if action in ("ack", "resolve"):
            alert = await self._update_alert(content.get("id"), action, content.get("by") or "operator")
            if alert is not None:
                await self.channel_layer.group_send(
                    DASHBOARD_GROUP, {"type": "alert.update", "alert": alert_to_dict(alert)}
                )
        elif action == "ping":
            await self.send_json({"type": "pong"})

    # --- group_send handlers (dispatched by "type" with dots -> underscores) ---

    async def alert_new(self, event):
        await self.send_json({"type": "alert", "alert": event["alert"]})

    async def alert_update(self, event):
        await self.send_json({"type": "alert_update", "alert": event["alert"]})

    async def sensor_update(self, event):
        await self.send_json({"type": "sensor", "sensor": event["sensor"]})

    async def metrics_tick(self, event):
        await self.send_json({"type": "metrics", "metrics": event["metrics"]})

    # --- helpers ---

    async def _snapshot(self):
        alerts = [
            alert_to_dict(a)
            async for a in Alert.objects.exclude(status="resolved").order_by("-received_at")[:200]
        ]
        sensors = [s async for s in SensorStatus.objects.all()]
        return {
            "alerts": alerts,
            "sensors": [sensor_to_dict(s) for s in sensors],
        }

    async def _update_alert(self, alert_id, action, by):
        try:
            alert = await Alert.objects.aget(id=alert_id)
        except Alert.DoesNotExist:
            return None

        now = datetime.datetime.now(datetime.timezone.utc)
        if action == "ack":
            alert.status = "acknowledged"
            alert.ack_by = by
            alert.ack_at = now
        elif action == "resolve":
            alert.status = "resolved"
            alert.resolved_at = now
            if not alert.ack_at:
                alert.ack_at = now
                alert.ack_by = by
        await alert.asave()
        return alert
