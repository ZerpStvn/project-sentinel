"""
Severity policy.

The generator's `severity_hint` is documented as "optional, may be
missing/wrong", so it can never be trusted on its own. We compute an
authoritative severity from the event `type`, and only ever let a hint
*raise* the severity we show an operator, never lower it. A bad hint can at
worst make us over-cautious, never make us miss something real.
"""

RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

TYPE_SEVERITY = {
    "fire_alarm": "critical",
    "panic_button": "critical",
    "smoke_detected": "critical",
    "perimeter_breach": "high",
    "door_forced": "high",
    "camera_offline": "high",
    "object_detected": "medium",
    "motion_detected": "low",
}

HEARTBEAT_TYPE = "heartbeat"
# Synthetic alert raised by the processor's silence sweep, not by the sensor feed.
SENSOR_SILENT_TYPE = "sensor_silent"
TYPE_SEVERITY[SENSOR_SILENT_TYPE] = "high"

DEFAULT_SEVERITY = "medium"


def resolve_severity(event_type: str, severity_hint: str | None) -> str:
    base = TYPE_SEVERITY.get(event_type, DEFAULT_SEVERITY)
    if severity_hint in RANK and RANK[severity_hint] > RANK[base]:
        return severity_hint
    return base
