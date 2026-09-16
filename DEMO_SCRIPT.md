# Demo video script (3–5 min)

Record your screen (terminal + browser) start to finish, single take is
fine. Timings are a guide, not a stopwatch — prioritize the flow over
hitting exact seconds. Say the bracketed lines out loud (or close to it);
everything else is what to click/type.

Before recording, make sure the stack is running fresh:
```bash
docker compose down -v
docker compose up -d --build
```
Wait ~10s, then open http://localhost:8000 in the browser and arrange
your recording so both the browser and a terminal are visible (split
screen or two tabs you can switch between).

The feed starts **stopped** by default (a safety control so it never
silently fills the database unattended) — you'll see a modal prompting
you to click **Start Live Feed** as soon as the page loads. That's
expected; starting it is part of the demo, not something to cut around.

---

## 0:00–0:25 — Intro

*(Browser: dashboard open, "Live feed is stopped" modal showing)*

> "This is Project Sentinel — the real-time core of an alarm monitoring
> service. It ingests a high-frequency stream of sensor events, prioritizes
> them by severity, and pushes them to this live dashboard — built so a
> traffic burst or a crashed process never loses an alert. The feed's off
> by default so it never runs up the database unattended — let's start it."

*(Click "Start Live Feed" — modal closes, alerts begin streaming in)*

## 0:25–1:15 — Sustained load, live dashboard

*(Point at the alert feed scrolling, the metrics in the header)*

> "This is the sustained stream — events/sec and average latency are live
> in the header. Alerts are ranked by severity, critical ones pinned with
> a border and this banner up top."

- Point out `events/s`, `avg latency`, `active` counters.
- Click **Ack** on one alert, then **Resolve** on another — show the row
  updating in place.
- Trigger a critical alert if one's visible: point at the red banner,
  click it to jump to the alert.
- Switch to the **Sites & Sensors** panel:
> "Each site tracks its sensors live — online, silent, or explicitly
> offline. 'Silent' means we haven't heard from a sensor at all, even a
> heartbeat — different from it explicitly reporting itself offline."

## 1:15–2:15 — Burst absorption

*(Terminal)*

> "The generator sends occasional 500-event bursts automatically, but
> let's force sustained heavy load on camera so it's obvious."

```bash
RATE=800 docker compose up -d sensor-sim
```

*(Switch back to browser immediately)*

> "Watch events/sec climb and the feed keep scrolling smoothly — no
> freeze, no lag."

*(Terminal, second window/pane — run this a couple times a few seconds apart)*
```bash
curl -s http://localhost:8000/api/metrics/
```
> "`ingested_total` and `processed_total` are tracking each other closely
> even under this load — nothing's backing up."

## 2:15–3:15 — Zero missed alerts: kill the processor mid-stream

*(Terminal)*

> "Now the part I actually care about most: does this survive a crash?
> I'm going to kill the processor — not a clean shutdown, a hard kill —
> while events are still flowing."

```bash
docker compose ps
docker kill -s SIGKILL $(docker compose ps -q processor)
```

*(Browser)*
> "The dashboard's still connected, but it'll stop getting new alerts —
> the processor's down. Ingest is still running underneath, durably
> buffering everything into Redis."

*(Terminal)*
```bash
curl -s http://localhost:8000/api/metrics/
```
> "See `ingested_total` still climbing, `processed_total` frozen — that
> gap is exactly how many events are safely queued, not lost."

```bash
docker compose up -d processor
docker compose logs processor --tail 5
```
> "And there — 'recovered N in-flight events from a prior run'. That's
> Redis consumer-group recovery kicking in automatically."

```bash
python scripts/verify_no_loss.py
```
> "This reconciliation script confirms it directly: nothing pending,
> ingested count matches processed count. No events lost."

*(Browser)*
> "And the dashboard's caught right back up."

## 3:15–3:45 — Wrap-up

> "So: Redis Streams for a durable, bounded, at-least-once ingest buffer,
> consumer groups for crash recovery, idempotent writes so a redelivered
> event never shows twice, and a Django Channels dashboard on top with
> sub-millisecond in-process latency even under this load. Full writeup —
> architecture, latency numbers, and exactly how I tested the delivery
> guarantee — is in the README."

*(End recording)*

---

## Notes for editing / re-recording

- If a real burst or crash moment doesn't look dramatic enough on
  playback, it's fine to cut and redo just that segment — this doesn't
  need to be one perfect take.
- If demoing the deployed Railway URL instead of/alongside local: swap
  `localhost:8000` for your `*.up.railway.app` URL, and use the Railway
  dashboard's **Restart** button on the `processor` service instead of
  `docker kill` for the crash-recovery segment (same effect, no terminal
  needed).
- Reset to a clean state before recording (`docker compose down -v` then
  `up -d --build`) so the alert feed doesn't already look overwhelming
  from earlier testing.
- After recording: upload to Google Drive, set sharing to "Anyone with
  the link can view", and double-check the link works in a private/
  incognito window before submitting.
