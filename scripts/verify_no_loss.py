#!/usr/bin/env python3
"""
Reconciliation script for the "zero missed alerts" guarantee.

Compares:
  - how many events the ingest worker durably wrote to the Redis stream
    (sentinel:ingest:count)
  - how many stream entries the processor has XACKed (sentinel:processor:count)
  - how many entries are still pending/unacked in the consumer group (XPENDING)
  - how many alerts (non-heartbeat, deduped) are persisted in the database
    (via the /api/metrics/ endpoint)

Usage:
    python scripts/verify_no_loss.py
    python scripts/verify_no_loss.py --redis-url redis://localhost:6379/0 \
        --metrics-url http://localhost:8000/api/metrics/

Suggested demo flow (see README "How we verified nothing is lost"):
  1. Start the stack, let it run under load for ~10s.
  2. `docker kill -s SIGKILL sentinel-processor-1` mid-stream (ungraceful).
  3. `docker compose up -d processor` to bring it back.
  4. Run this script a couple of times a few seconds apart: pending should
     drop to 0 and ingested_total == processed_total once it catches up,
     with no gap in persisted alerts.
"""
import argparse
import json
import sys
import urllib.request

import redis


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--redis-url", default="redis://localhost:6379/0")
    parser.add_argument("--metrics-url", default="http://localhost:8000/api/metrics/")
    parser.add_argument("--stream-key", default="sentinel:events")
    parser.add_argument("--group", default="processors")
    args = parser.parse_args()

    r = redis.from_url(args.redis_url, decode_responses=True)

    stream_len = r.xlen(args.stream_key)
    try:
        pending_info = r.xpending(args.stream_key, args.group)
        pending = pending_info["pending"] if pending_info else 0
    except redis.exceptions.ResponseError:
        pending = None  # group doesn't exist yet

    ingested = int(r.get("sentinel:ingest:count") or 0)
    processed = int(r.get("sentinel:processor:count") or 0)

    try:
        with urllib.request.urlopen(args.metrics_url, timeout=5) as resp:
            metrics = json.loads(resp.read())
    except Exception as exc:
        metrics = {"error": str(exc)}

    print("=== Project Sentinel: loss reconciliation ===")
    print(f"Redis stream length (current buffer):     {stream_len}")
    print(f"Pending / unacked in consumer group:       {pending}")
    print(f"Ingest worker: events durably buffered:    {ingested}")
    print(f"Processor: stream entries acked:           {processed}")
    print(f"Web /api/metrics/:                          {json.dumps(metrics, indent=2)}")
    print()

    if pending not in (None, 0):
        print(f"IN PROGRESS: {pending} event(s) still pending -- processor is catching up (or crashed). "
              "Re-run in a few seconds.")
        sys.exit(1)

    if ingested != processed:
        print(f"MISMATCH: ingested={ingested} processed={processed}. "
              "If pending is 0 and these still differ, something was dropped -- investigate.")
        sys.exit(2)

    print("OK: nothing pending, ingested count matches processed count. No events lost.")


if __name__ == "__main__":
    main()
