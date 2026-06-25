# Activity Booker

Automated slot booking for club booking platforms. Currently books padel/tennis
courts at Lensbury Club (via flow.onl); structured to extend to other activities,
venues, and platforms (e.g. a golf club) — see [ARCHITECTURE.md](ARCHITECTURE.md).

## Run it

**Cloud Run + Cloud Scheduler** (recommended — reliable ~1s trigger, fast
session-reuse polling): see [DEPLOY_CLOUDRUN.md](DEPLOY_CLOUDRUN.md).

**Locally / on a VPS:**
```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # fill in your details
python -m booker.cli seed   # one-off: log in and save the session
python -m booker.cli book   # run the booking flow
```

## Configuration (env vars)

| Var              | Default              | Meaning                                  |
|------------------|----------------------|------------------------------------------|
| `FLOW_EMAIL`     | —                    | Login email (required)                   |
| `FLOW_PASSWORD`  | —                    | Login password (required)                |
| `PROVIDER`       | `flow`               | Booking-platform adapter                 |
| `VENUE`          | `lensbury-club`      | Venue slug                               |
| `ACTIVITY`       | `padel-courts`       | Activity slug (entry page)               |
| `TARGET_TIME`    | `19:00`              | Slot start time, 24h                     |
| `DAYS_AHEAD`     | `5`                  | Booking-window lead time                 |
| `ON_DATE`        | —                    | Explicit ISO date (overrides days_ahead) |
| `COURT_PREF`     | —                    | Preferred court (blank = any)            |
| `PLAYER_NAMES`   | `Amelia Fink`        | Comma-separated fellow players           |
| `MAX_RETRIES`    | `30`                 | Retry attempts if slot not yet live      |
| `RETRY_DELAY_S`  | `5`                  | Seconds between retries                  |
| `HEADLESS`       | `true`               | Set `false` to watch the browser locally |

## Project layout

```
booker/
  models.py          BookingRequest / BookingResult / Credentials
  orchestrator.py    platform-agnostic engine (retries, session, timing)
  session.py         session persistence (local file / GCS)
  config.py          env → typed objects
  cli.py             CLI entrypoint
  providers/
    base.py          BookingProvider interface
    flow.py          flow.onl / Lensbury adapter
    __init__.py      provider registry
server.py            Flask app for Cloud Run
```
