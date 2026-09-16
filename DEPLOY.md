# Deploying to Railway (live HTTPS URL)

Railway was chosen because it can build straight from this repo's existing
Dockerfiles, gives every public service a free managed TLS certificate on
a `*.up.railway.app` domain with zero config, and its private networking
lets the worker services (ingest/processor/sensor-sim) talk to each other
without being exposed to the internet.

## One real architecture change for this deploy

Locally, `docker-compose.yml` runs `web` and `processor` as separate
containers sharing one SQLite file over a Docker volume. Railway services
don't share a filesystem, so that doesn't work across services. The
codebase already supports this: if a `DATABASE_URL` env var is present,
`sentinel/settings.py` uses it (Postgres) instead of the local SQLite
file. **This path has been tested** — migrations, `abulk_create` with
`ignore_conflicts`, `aupdate_or_create`, and async saves were all run
against a real Postgres container before writing this guide. Local
`docker compose up` is completely unaffected (no `DATABASE_URL` is set
there, so it still uses SQLite).

## Topology on Railway

One Railway **project**, six things in it:

| Service | Root dir | Public? | Start command |
|---|---|---|---|
| Postgres | (plugin) | no | — |
| Redis | (plugin) | no | — |
| `sensor-sim` | `sensor-sim/` | no | (Dockerfile default) |
| `ingest` | `backend/` | no | `python manage.py run_ingest` |
| `processor` | `backend/` | no | `python manage.py run_processor` |
| `web` | `backend/` | **yes** | `sh -c "python manage.py migrate --noinput && daphne -b 0.0.0.0 -p $PORT sentinel.asgi:application"` |

Three of the four app services point at the same GitHub repo but with
different **Root Directory** settings — that's what makes this a
monorepo deploy on Railway, each building its own Dockerfile.

## Steps (dashboard)

1. **Push this repo to GitHub** (Railway deploys from a repo, not a local
   folder).
2. **railway.app → New Project → Deploy from GitHub repo** → pick the
   repo. This creates one service — you'll repurpose it as `web` and add
   three more.
3. **Add the plugins**: in the project, `+ New` → *Database* → *Postgres*.
   Repeat: `+ New` → *Database* → *Redis*. Note the exact service names
   Railway gives them (default `Postgres` / `Redis` — used below).
4. **Configure the `web` service** (the one created in step 2):
   - *Settings → Source → Root Directory*: `backend`
   - *Settings → Deploy → Start Command*:
     `sh -c "python manage.py migrate --noinput && daphne -b 0.0.0.0 -p $PORT sentinel.asgi:application"`
   - *Settings → Networking → Generate Domain* → this gives you
     `https://<something>.up.railway.app` with a managed cert already
     attached, no extra steps.
   - *Variables*:
     ```
     REDIS_URL=${{Redis.REDIS_URL}}
     DATABASE_URL=${{Postgres.DATABASE_URL}}
     SENSOR_WS_URL=ws://sensor-sim.railway.internal:8765
     DJANGO_DEBUG=false
     ```
5. **Add `processor`**: `+ New` → *GitHub Repo* → same repo again.
   - Rename the service `processor`.
   - *Root Directory*: `backend`
   - *Start Command*: `python manage.py run_processor`
   - *Variables*: `REDIS_URL=${{Redis.REDIS_URL}}`,
     `DATABASE_URL=${{Postgres.DATABASE_URL}}`
   - Leave networking off (no public domain needed).
6. **Add `ingest`**: `+ New` → *GitHub Repo* → same repo again.
   - Rename `ingest`.
   - *Root Directory*: `backend`
   - *Start Command*: `python manage.py run_ingest`
   - *Variables*: `REDIS_URL=${{Redis.REDIS_URL}}`,
     `SENSOR_WS_URL=ws://sensor-sim.railway.internal:8765`
7. **Add `sensor-sim`**: `+ New` → *GitHub Repo* → same repo again.
   - Rename `sensor-sim`.
   - *Root Directory*: `sensor-sim`
   - *Start Command*: leave default (uses the Dockerfile's `CMD`).
   - *Variables*: `RATE=25`, `BURST_CHANCE=0.01`, `BURST_SIZE=500`
     (raise `RATE` for a stress-test deploy).
8. Deploy all four app services (Railway does this automatically as you
   save each one; watch the build logs go green).
9. Open `https://<your-web-service>.up.railway.app` — the dashboard
   should be live over HTTPS immediately.

## Gotchas

- **Private networking** must be enabled for the project (on by default
  for new projects) for `<service>.railway.internal` hostnames to
  resolve between services.
- The `${{Redis.REDIS_URL}}` / `${{Postgres.DATABASE_URL}}` syntax is
  Railway's variable-reference syntax — the names inside `{{ }}` must
  match your actual plugin service names exactly (check the sidebar if
  you renamed them).
- If a service's build fails because it can't find `manage.py` or a
  Dockerfile, double check its **Root Directory** — this is the most
  common misconfiguration with a monorepo deploy like this one.
- Cost: this is 4 always-on worker/web services + Postgres + Redis. It
  fits comfortably in Railway's free trial credit for a demo/assessment
  window; beyond that it's usage-based (small — a few dollars/month for
  workers this light).

## Verifying the deploy

Same reconciliation approach as local — `/api/metrics/` is public on the
`web` service:

```bash
curl https://<your-web-service>.up.railway.app/api/metrics/
```

`ingested_total` and `processed_total` should track closely together
during steady load, exactly as verified locally (see the README's
"How this was verified" section). To repeat the crash-recovery test
against the deployed stack, restart the `processor` service from the
Railway dashboard (⋮ → Restart) while load is flowing and confirm the
count catches back up with no gap.

## Custom domain + SSL

*Settings → Networking → Custom Domain* on the `web` service, add the
CNAME Railway shows you at your DNS provider — Railway auto-issues and
renews a Let's Encrypt certificate for it, same as it does for the
`up.railway.app` domain.
