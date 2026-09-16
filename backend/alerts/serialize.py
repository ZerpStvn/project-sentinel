def alert_to_dict(alert):
    return {
        "id": alert.id,
        "event_id": alert.event_id,
        "sensor_id": alert.sensor_id,
        "site_id": alert.site_id,
        "type": alert.type,
        "severity": alert.severity,
        "confidence": alert.confidence,
        "severity_hint": alert.severity_hint,
        "event_ts": alert.event_ts.isoformat() if alert.event_ts else None,
        "received_at": alert.received_at.isoformat() if alert.received_at else None,
        "processing_latency_ms": alert.processing_latency_ms,
        "status": alert.status,
        "ack_by": alert.ack_by,
        "ack_at": alert.ack_at.isoformat() if alert.ack_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
    }


def sensor_to_dict(sensor):
    return {
        "sensor_id": sensor.sensor_id,
        "site_id": sensor.site_id,
        "last_seen": sensor.last_seen.isoformat() if sensor.last_seen else None,
        "status": sensor.status,
    }
