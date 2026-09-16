from django.db import models


class Alert(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("acknowledged", "Acknowledged"),
        ("resolved", "Resolved"),
    ]

    # Unique on event_id: this is our DB-level idempotency guard. Even if the
    # at-least-once delivery path (Redis Streams) redelivers an event after a
    # crash, this constraint means it can only ever be persisted once.
    event_id = models.CharField(max_length=64, unique=True)
    sensor_id = models.CharField(max_length=64, db_index=True)
    site_id = models.CharField(max_length=64, db_index=True)
    type = models.CharField(max_length=32)
    severity = models.CharField(max_length=16, db_index=True)
    confidence = models.FloatField(default=0)
    severity_hint = models.CharField(max_length=16, null=True, blank=True)

    event_ts = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    processing_latency_ms = models.FloatField(default=0)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="active", db_index=True)
    ack_by = models.CharField(max_length=64, null=True, blank=True)
    ack_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["status", "severity"]),
        ]

    def __str__(self):
        return f"{self.type}@{self.site_id}/{self.sensor_id} ({self.severity})"


class SensorStatus(models.Model):
    STATUS_CHOICES = [
        ("online", "Online"),
        ("silent", "Silent"),   # no event/heartbeat within the silence window
        ("offline", "Offline"),  # sensor explicitly reported camera_offline
    ]

    sensor_id = models.CharField(max_length=64, unique=True)
    site_id = models.CharField(max_length=64, db_index=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="online")

    class Meta:
        ordering = ["site_id", "sensor_id"]

    def __str__(self):
        return f"{self.sensor_id}@{self.site_id}: {self.status}"
