# Architecture

This worker books activity slots on club booking platforms. It's structured so
that today's single use (Lensbury padel) can grow into a multi-activity,
multi-venue, multi-user platform without rewriting the core.

## Layers

```
            ┌─────────────────────────────────────────────┐
  future →  │  Web frontend / API / DB / auth / job queue  │   (not built yet)
            └───────────────────────┬─────────────────────┘
                                    │ produces
                                    ▼
                          booker.models.BookingRequest
                                    │
   entrypoints:  server.py (HTTP)   │   booker.cli (CLI)
                                    ▼
                       booker.orchestrator.Orchestrator
        owns the browser · session reuse · retry loop · timing
                                    │ drives
                                    ▼
                    booker.providers.base.BookingProvider
              (platform adapters: FlowProvider, future BRSGolfProvider…)
                                    │ uses
                                    ▼
                    booker.session.SessionStore (local file / GCS)
```

## Why these seams

- **`BookingRequest` / `BookingResult` (models.py)** — pure data, no Playwright.
  This is exactly what a frontend POSTs and a DB stores per user. The orchestrator
  and providers both speak this language.

- **`BookingProvider` (providers/base.py)** — the abstraction over *platforms*,
  not activities. Tennis and padel at Lensbury are the same `FlowProvider` with a
  different `activity`. The golf club will run a different platform (BRS, ClubV1,
  intelligentgolf…), so it becomes a new provider implementing the same six
  methods. The orchestrator never changes.

- **`Orchestrator` (orchestrator.py)** — platform-agnostic. Owns the retry loop,
  session reuse, screenshots, and the fixed booking sequence. This is the unit a
  future job queue calls.

- **`SessionStore` (session.py)** — abstracts *where* the saved login lives, so
  the same orchestrator runs locally (file) or on Cloud Run (GCS). Multi-user
  later: key sessions per `(user, provider)`.

## Adding a new platform (e.g. the golf club)

1. Create `booker/providers/brs.py` with a `BRSGolfProvider(BookingProvider)`.
2. Implement the six methods against that platform's DOM.
3. Register it in `booker/providers/__init__.py`.
4. Set `PROVIDER=brs` (plus that venue's `VENUE`/`ACTIVITY`) for that job.

No change to the orchestrator, models, server, or session layers.

## What's intentionally deferred

- **Database / users / auth / frontend** — the `BookingRequest` boundary is ready
  for them; they just become the producer of requests.
- **Job queue / scheduling for many users** — today Cloud Scheduler → one HTTP
  call is enough. Many concurrent scheduled bookings will want a real queue
  (e.g. Cloud Tasks) in front of the orchestrator.
- **A second live provider** — the interface proves the seam; we build the golf
  adapter when we actually wire up the golf club.

## Entry points

- `server.py` — Flask app for Cloud Run (`/book`, `/seed`, `/`).
- `python -m booker.cli book|seed` — local / VPS runs.

Both do the same thing: build a request + provider + orchestrator from env
(`booker/config.py`) and run it.
