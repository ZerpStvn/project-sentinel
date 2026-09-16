from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _enable_sqlite_wal(sender, connection, **kwargs):
    """
    Default SQLite commits fsync on every write ("journal_mode=delete"),
    which measured ~17ms/commit on this Docker volume -- an easy throughput
    ceiling of ~60 writes/sec no matter how the rest of the pipeline is
    tuned. WAL mode batches fsyncs and is the standard fix for a
    write-heavy SQLite workload like ours (one write per unique alert).
    """
    if connection.vendor != "sqlite":
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")


class AlertsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "alerts"

    def ready(self):
        connection_created.connect(_enable_sqlite_wal)
