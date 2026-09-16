import asyncio
import json
import os
import random
import uuid
import datetime
import websockets

SENSORS = [f"sensor-{s:03d}" for s in range(1, 201)]
SITES = [f"site-{n}" for n in range(100, 140)]
TYPES = [
    "motion_detected", "perimeter_breach", "door_forced", "smoke_detected",
    "fire_alarm", "object_detected", "camera_offline", "panic_button", "heartbeat",
]
HINTS = ["low", "medium", "high", "critical"]

RATE = float(os.getenv("RATE", "200"))
BURST_CHANCE = float(os.getenv("BURST_CHANCE", "0.01"))
BURST_SIZE = int(os.getenv("BURST_SIZE", "500"))


def make_event():
    event = {
        "event_id": "evt_" + uuid.uuid4().hex[:12],
        "sensor_id": random.choice(SENSORS),
        "site_id": random.choice(SITES),
        "type": random.choice(TYPES),
        "confidence": round(random.uniform(0.4, 0.99), 2),
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if random.random() < 0.3:
        event["severity_hint"] = random.choice(HINTS)
    return event


async def handler(ws):
    interval = 1.0 / RATE
    print(f"[sensor-sim] client connected: {ws.remote_address}")
    try:
        while True:
            burst = BURST_SIZE if random.random() < BURST_CHANCE else 1
            if burst > 1:
                print(f"[sensor-sim] BURST: emitting {burst} events")
            for _ in range(burst):
                await ws.send(json.dumps(make_event()))
            await asyncio.sleep(interval)
    except websockets.exceptions.ConnectionClosed:
        print("[sensor-sim] client disconnected")


async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765, ping_interval=20, ping_timeout=20):
        print(f"Sensor fleet streaming ~{RATE}/s on ws://0.0.0.0:8765 "
              f"(burst chance {BURST_CHANCE}, burst size {BURST_SIZE})")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
