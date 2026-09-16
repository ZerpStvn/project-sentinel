from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render

from .models import Alert
from .redis_client import get_redis


def dashboard_view(request):
    return render(request, "dashboard.html")


async def metrics_view(request):
    r = get_redis()
    try:
        stream_len = await r.xlen(settings.STREAM_KEY)
    except Exception:
        stream_len = None

    pending_count = None
    try:
        info = await r.xpending(settings.STREAM_KEY, settings.STREAM_GROUP)
        pending_count = info.get("pending") if info else 0
    except Exception:
        pending_count = None

    ingested = await r.get("sentinel:ingest:count")
    processed = await r.get("sentinel:processor:count")

    active_count = await Alert.objects.filter(status="active").acount()
    critical_active = await Alert.objects.filter(status="active", severity="critical").acount()
    total_alerts = await Alert.objects.acount()

    return JsonResponse({
        "stream_length": stream_len,
        "pending_unacked": pending_count,
        "ingested_total": int(ingested) if ingested else 0,
        "processed_total": int(processed) if processed else 0,
        "active_alerts": active_count,
        "critical_active": critical_active,
        "total_alerts_persisted": total_alerts,
    })
