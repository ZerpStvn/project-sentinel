# Project Sentinel

The real-time core of an alarm monitoring service: ingest a high-frequency,
multi-sensor event stream, prioritize and dedupe it, keep live per-site/
per-sensor state, and push it to an operator dashboard — without ever
losing an alert, even through a crash or a burst of traffic.

Built with **Python / Django** (Django Channels for the real-time
WebSocket layer), **Redis Streams** as the durable ingest buffer, and
**SQLite** for persisted alert/sensor state.

## Quick start

```bash
docker compose up -d --build
```

Then open **http://localhost:8000**. Click "Enable sound" once (browsers
require a user gesture before audio can play) so critical alerts audibly
alert you, not just visually.

That's it — `docker compose up` brings up 5 containers: `redis`,
`sensor-sim` (the reference generator from the assessment appendix,
lightly extended), `ingest`, `processor`, and `web` (the dashboard). A
one-shot `migrate` service runs the Django migrations first.

To stress-test at a higher rate:

```bash
RATE=1000 docker compose up -d --build sensor-sim
```

(see [How this was tested](#how-this-was-verified--nothing-is-lost) for what happens at very high rates)

For a live public HTTPS URL instead of `localhost`, see [DEPLOY.md](DEPLOY.md) (a tested Railway runbook).

## Architecture

```
                                                 ┌─────────────────────┐
 ┌──────────────┐   WebSocket    ┌─────────┐     │        web           │
 │  sensor-sim   │──────────────▶│ ingest  │     │  (Django + Channels) │
 │ (reference    │   (client     │ worker  │     │                      │
 │  generator)   │    connects)  └────┬────┘     │  dashboard (WS push) │
 └──────────────┘                     │ XADD     │  ack/resolve (WS)    │
                                       ▼          │  /api/metrics/       │
                              ┌────────────────┐  └──────────▲───────────┘
                              │  Redis Stream   │             │ group_send
                              │ sentinel:events │             │ (Channels
                              │ (bounded, AOF)  │             │  layer,
                              └───────┬────────┘             │  Redis pub/sub)
                                      │ XREADGROUP            │
                                      ▼                       │
                              ┌────────────────┐   persist    │
                              │   processor     │─────────────┘
                              │  worker         │──────▶ SQLite (Alert,
                              │ (dedupe,        │         SensorStatus)
                              │  severity,      │
                              │  sensor state)  │
                              └────────────────┘
```

* **Ingest** (`alerts/management/commands/run_ingest.py`) is a reconnecting
  WebSocket *client*. The reference generator is itself a WebSocket
  *server* that streams to whoever connects, so ingest connects to it,
  and for every message durably `XADD`s it onto a Redis Stream before
  doing anything else.
* **Processor** (`run_processor.py`) reads the stream via a consumer
  group, normalizes/dedupes/prioritizes events, updates per-sensor
  liveness state, persists new alerts, and pushes them to every connected
  dashboard over Django Channels.
* **Dashboard** (`templates/dashboard.html` + `static/dashboard.js`) is a
  single WebSocket-driven page: live alert feed ranked by severity,
  per-site sensor status grid, acknowledge/resolve, and a pinned red
  banner + audible beep for unacknowledged critical alerts.

### Why Redis Streams (not a raw queue or just WebSocket fan-out)

Streams give us, essentially for free:
- **A durable, bounded buffer** between ingest and processing (the
  assessment's "bounded buffering" requirement) — `XADD` with an
  approximate `MAXLEN` (`SENTINEL_STREAM_MAXLEN`, default 200,000).
- **Consumer groups with per-entry acknowledgment** — the basis of our
  at-least-once delivery guarantee (below).
- **Crash recovery via `XAUTOCLAIM`** — a dead consumer's unacked entries
  can be reclaimed and reprocessed by the next one, with no extra
  bookkeeping of our own.

This also happens to satisfy the "message broker" stretch goal.

### Why SQLite (not Postgres)

Simplicity for a "runs anywhere from a clean checkout" exercise — no extra
service, no credentials to manage. The trade-off is real and is called out
below (see "SQLite is a single point of write contention").

## How we hit low latency

The ingest→processor→dashboard hop is measured end-to-end: `ingested_ts`
is stamped the moment an event lands in the Redis stream, and each
broadcast alert carries `processing_latency_ms` = (broadcast time −
`ingested_ts`). This is visible live in the dashboard header ("avg
latency") and in the WebSocket payload for every alert.

Measured on this machine, sustained load at the default demo rate: **p50
≈ 0.6ms, p95 ≈ 1ms, max ≈ 1–3ms** — comfortably under the sub-100ms aim.
(This excludes the sensor→ingest network hop and browser render time,
which are outside the "in-process" scope the brief asks about.)

Getting there took fixing two real bottlenecks discovered while load
testing this build (kept here because they're the actual engineering
story, not just "we picked async and called it done"):

1. **SQLite fsync cost.** The default SQLite journal mode fsyncs on every
   commit. Measured on this Docker volume: **~17ms per commit** — an
   ~60 writes/sec ceiling regardless of anything else in the pipeline.
   Fixed by enabling WAL mode + `synchronous=NORMAL` on every SQLite
   connection (`alerts/apps.py`), the standard fix for a write-heavy
   SQLite workload.
2. **Per-event round trips.** The processor batches an entire
   `XREADGROUP` read (up to 500 entries) through the pipeline together:
   one Redis pipeline for all dedupe checks + sensor-liveness updates in
   the batch, one `abulk_create` for all new alerts, one batched `XACK`.
   That took us from ~150 events/sec (serial per-event awaits) to
   comfortably ahead of the generator's peak burst rate.

## Delivery guarantee: zero missed alerts

**At-least-once, end to end, with idempotent persistence** — an event is
never dropped between "ingest received it" and "it's durably stored and
shown", across a burst or a process crash. Concretely:

1. **Ingest → Redis**: `ingest` doesn't move on to the next WebSocket
   message until `XADD` has returned successfully. Once that returns
   (and Redis has fsynced it, `appendonly yes appendfsync everysec`), the
   event survives an ingest crash, a processor crash, or a processor
   restart.
2. **Backpressure, not drops, under overload**: ingest reads sequentially
   (`await` before the next `recv()`), and the WebSocket client is opened
   with a bounded `max_queue`. If Redis (or the processor) is ever the
   slow side, that backpressure propagates all the way back to the TCP
   socket instead of buffering unboundedly in process memory.
3. **Processor crash / restart recovery**: Redis consumer groups track
   per-entry delivery in a Pending Entries List (PEL). An entry only
   leaves the PEL when the processor `XACK`s it — *after* the DB write
   and dashboard broadcast are done. On startup (and every 5s while
   running), the processor runs `XAUTOCLAIM` to reclaim anything left
   pending by a dead consumer (including a crashed instance of itself)
   and reprocesses it exactly like a fresh entry.
4. **Idempotent persistence**: `Alert.event_id` is a unique DB constraint.
   A reclaimed/redelivered entry that already made it to the DB before a
   crash is absorbed by `bulk_create(..., ignore_conflicts=True)` rather
   than shown twice. A short-lived Redis `SETNX` dedupe key
   (`DEDUPE_TTL_SECONDS`, default 300s) catches the common case cheaply,
   before it even reaches the DB.
5. **Bounded, not unbounded**: the stream is trimmed at `SENTINEL_STREAM_MAXLEN`
   (default 200k entries, ~a very long time at any sane rate). This is an
   explicit, honest trade-off — the guarantee holds for events still
   inside that bounded window, not forever if a consumer is down for an
   extremely long time. See "what's next" for how you'd move this to an
   unbounded/tiered guarantee.

**Heartbeats vs. "camera_offline" vs. silence** — the assessment's note
that "losing a heartbeat should never look the same as a sensor going
offline" is handled as three distinct states in `SensorStatus`:
- `online` — an event or heartbeat has been seen recently.
- `offline` — the sensor *explicitly* reported a `camera_offline` event.
- `silent` — *nothing* (event or heartbeat) has been seen from that
  sensor for `SENSOR_SILENCE_SECONDS` (default 90s at the default demo
  rate — see the comment in `sentinel/settings.py` for why that number
  isn't arbitrary). This is what actually detects "a sensor has gone
  dark", as distinct from "a sensor told us it's offline".

### Severity policy

`severity_hint` is documented as "optional, may be missing/wrong", so
it's never trusted alone (`alerts/severity.py`). Severity is computed
from the event `type` (fire/panic/smoke → critical, breach/forced-entry/
camera_offline → high, object detection → medium, motion → low), and a
hint is only ever allowed to *raise* the shown severity, never lower it —
a bad hint can make us over-cautious, never make us miss something real.

## How this was verified — nothing is lost

1. **Reconciliation script**: `scripts/verify_no_loss.py` compares the
   ingest worker's durable-write counter, the processor's ack counter,
   the consumer group's pending count (`XPENDING`), and the persisted
   alert count via `/api/metrics/`. It's automatically the source of
   truth on whether anything is stuck or missing:
   ```bash
   python scripts/verify_no_loss.py
   ```
2. **Crash-recovery test actually run against this build**:
   - Started the full stack under load, confirmed `ingested_total ==
     processed_total` (steady-state, near-real-time).
   - `docker kill -s SIGKILL sentinel-processor-1` mid-stream (an
     *ungraceful* kill — no clean shutdown to lean on).
   - Verified `ingested_total` kept climbing (ingest unaffected) while
     `processed_total` froze, and `pending_unacked` reflected exactly the
     in-flight batch that was never acked.
   - `docker compose up -d processor` — the log line
     `"recovered N in-flight event(s) from a prior run"` confirmed
     `XAUTOCLAIM` picked the orphaned entries back up.
   - Watched `processed_total` catch up to `ingested_total` with the
     reconciliation script reporting `OK` — no gap, no duplicate alert
     visible on the dashboard (dedupe/`ignore_conflicts` did its job).
3. **Burst absorption**: watched `/api/metrics/` across one of the
   generator's ~1%-chance 500-event bursts; `stream_length` jumped by
   ~500+ in one polling interval while `processed_total` stayed within a
   handful of events of `ingested_total` the whole time — no growing
   backlog, no pending entries left over.
4. **Latency**: a small WebSocket test client subscribed to the live feed
   and recorded `processing_latency_ms` off real broadcasts (see
   "How we hit low latency" above for numbers).

### A bug this testing actually caught (kept here deliberately)

Under sustained load well above what a human dashboard needs to display
(~700+ alerts/sec, which is what the *unmodified* reference generator's
`RATE=200` default produces on average once its 1%-chance 500-event
bursts are factored in), a connected dashboard's own acknowledge/resolve
confirmation would occasionally never arrive. The alert data itself was
never at risk — it was already safely committed to the database — but
Django Channels' per-connection channel has a default capacity of 100
messages and *silently drops* `group_send` messages beyond that rather
than blocking. Fixed by raising that capacity (`CHANNEL_LAYERS.CONFIG.capacity`
in `settings.py`) and, more importantly, by setting the default demo
`RATE` to something a human operator dashboard can actually make use of
(no one reads 700 new alerts/sec). This is documented rather than hidden
because it's a real, generalizable lesson: the *delivery* guarantee
(Streams → DB) and the *live push* channel (Channels groups) are
different mechanisms with different failure modes, and only the former
is meant to be lossless — a dropped push is a UI-freshness issue, healed
by the next reconnect/snapshot, not a lost alert.

## Where corners were cut

- **SQLite is a single point of write contention** in the local/default
  setup. It comfortably handles the demo/stress-test load here (after the
  WAL fix), but a second heavy writer (e.g. a second processor replica)
  would start contending for the single write lock. `settings.py` already
  switches to Postgres when `DATABASE_URL` is set (used for the Railway
  deploy in [DEPLOY.md](DEPLOY.md)); SQLite stays the local default to
  keep "clean checkout, one `docker compose up`" true with no extra
  service required.
- **Single processor instance.** The consumer-group design supports
  running several (`docker compose up -d --scale processor=3`) and they'd
  correctly split the stream and reclaim each other's abandoned work —
  but SQLite writes would then need to move off single-writer SQLite
  first (see above) to actually benefit.
- **No auth on the dashboard by default.** `DASHBOARD_BASIC_AUTH_USER` /
  `DASHBOARD_BASIC_AUTH_PASS` env vars turn on HTTP Basic Auth
  (`alerts/middleware.py`) if you want it for a public deployment; off by
  default for local/demo convenience.
- **Static files are served by Django's dev-grade `staticfiles` view**,
  not whitenoise/nginx/a CDN — fine at this scale, not what you'd ship to
  real production traffic.
- **No live video/telemetry feed, no incident-history timeline** — out of
  scope for the focused core; see "what's next".
- **The stream buffer is bounded, not infinite** (see delivery guarantee
  section) — a deliberate, documented trade-off rather than an oversight.

## What's next

- Postgres + multiple processor replicas for horizontal scale past a
  single writer.
- A latency histogram + throughput graph on the metrics endpoint (today
  it's point-in-time counters; `/api/metrics/` is already there to build
  on).
- Per-site incident timeline / correlation (e.g. escalate repeated
  `motion_detected` → `perimeter_breach` on the same site within a short
  window).
- A mock video/telemetry tile per site next to its alerts (HLS/looping
  clip).
- A deployed, publicly reachable instance — see [DEPLOY.md](DEPLOY.md) for
  a tested Railway runbook (managed TLS out of the box). It requires one
  real change from the local setup: separate services need Postgres
  instead of SQLite (already wired into `settings.py` behind
  `DATABASE_URL`, and verified against a real Postgres container).

## Project layout

```
sensor-sim/          reference generator (WebSocket server), lightly extended
backend/
  sentinel/          Django project (settings, ASGI, urls)
  alerts/            the app: models, Channels consumer, severity policy,
                      ingest + processor management commands
  templates/, static/  the dashboard page
scripts/verify_no_loss.py   reconciliation / loss-verification tool
docker-compose.yml   redis, sensor-sim, migrate, web, ingest, processor
```

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `SENSOR_WS_URL` | `ws://sensor-sim:8765` | where ingest connects |
| `SENTINEL_STREAM_MAXLEN` | 200000 | bounded stream size |
| `SENTINEL_DEDUPE_TTL` | 300 | seconds a dedupe key lives in Redis |
| `SENTINEL_SILENCE_SECONDS` | 90 | no-event threshold before a sensor is "silent" |
| `SENTINEL_CLAIM_IDLE_MS` | 5000 | how long an entry sits unacked before it's reclaimable |
| `DASHBOARD_BASIC_AUTH_USER/PASS` | unset | optional HTTP basic auth on the dashboard |
| `RATE`, `BURST_CHANCE`, `BURST_SIZE` | 25, 0.01, 500 | sensor-sim generator tuning |
