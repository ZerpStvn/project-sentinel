from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Alert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.CharField(max_length=64, unique=True)),
                ("sensor_id", models.CharField(db_index=True, max_length=64)),
                ("site_id", models.CharField(db_index=True, max_length=64)),
                ("type", models.CharField(max_length=32)),
                ("severity", models.CharField(db_index=True, max_length=16)),
                ("confidence", models.FloatField(default=0)),
                ("severity_hint", models.CharField(blank=True, max_length=16, null=True)),
                ("event_ts", models.DateTimeField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("processing_latency_ms", models.FloatField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("acknowledged", "Acknowledged"), ("resolved", "Resolved")],
                        default="active",
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("ack_by", models.CharField(blank=True, max_length=64, null=True)),
                ("ack_at", models.DateTimeField(blank=True, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-received_at"],
            },
        ),
        migrations.CreateModel(
            name="SensorStatus",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sensor_id", models.CharField(max_length=64, unique=True)),
                ("site_id", models.CharField(db_index=True, max_length=64)),
                ("last_seen", models.DateTimeField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("online", "Online"), ("silent", "Silent"), ("offline", "Offline")],
                        default="online",
                        max_length=16,
                    ),
                ),
            ],
            options={
                "ordering": ["site_id", "sensor_id"],
            },
        ),
        migrations.AddIndex(
            model_name="alert",
            index=models.Index(fields=["status", "severity"], name="alerts_aler_status_2f0a9a_idx"),
        ),
    ]
