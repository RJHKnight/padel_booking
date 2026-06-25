"""
HTTP wrapper so Cloud Scheduler can trigger the booking run.

Cloud Run serves this Flask app. Cloud Scheduler sends an authenticated POST to
/book at the scheduled time (sub-second accuracy, unlike GitHub Actions).

Endpoints:
  GET  /          - health check (Cloud Run readiness)
  POST /book      - run the booking flow; returns JSON {status, detail}
  POST /seed      - log in once and persist the session to GCS (run manually)

Auth: Cloud Scheduler is configured with an OIDC token; Cloud Run is deployed
with --no-allow-unauthenticated so only the scheduler's service account can call
it. No app-level secret handling needed.

Session persistence:
  The saved Playwright storage_state lives in a GCS bucket. On each request we
  download it to SESSION_STATE_PATH before running, and upload it back after a
  fresh login. This survives Cloud Run's ephemeral filesystem between invocations.
"""

import os
import sys
import logging
import traceback

from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
log = logging.getLogger("server")

app = Flask(__name__)

SESSION_STATE_PATH = os.environ.get("SESSION_STATE_PATH", "/tmp/flow_session.json")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")           # e.g. "rjh-padel-session"
GCS_SESSION_OBJECT = os.environ.get("GCS_SESSION_OBJECT", "flow_session.json")


def _download_session_from_gcs() -> bool:
    """Pull the saved session file from GCS into SESSION_STATE_PATH. Returns True if found."""
    if not GCS_BUCKET:
        return False
    try:
        from google.cloud import storage
        client = storage.Client()
        blob = client.bucket(GCS_BUCKET).blob(GCS_SESSION_OBJECT)
        if blob.exists():
            blob.download_to_filename(SESSION_STATE_PATH)
            log.info(f"Downloaded session from gs://{GCS_BUCKET}/{GCS_SESSION_OBJECT}")
            return True
        log.info("No saved session object in GCS yet")
    except Exception as e:
        log.warning(f"Could not download session from GCS: {e}")
    return False


def _upload_session_to_gcs() -> None:
    """Push the (possibly refreshed) session file back to GCS."""
    if not GCS_BUCKET or not os.path.exists(SESSION_STATE_PATH):
        return
    try:
        from google.cloud import storage
        client = storage.Client()
        blob = client.bucket(GCS_BUCKET).blob(GCS_SESSION_OBJECT)
        blob.upload_from_filename(SESSION_STATE_PATH)
        log.info(f"Uploaded session to gs://{GCS_BUCKET}/{GCS_SESSION_OBJECT}")
    except Exception as e:
        log.warning(f"Could not upload session to GCS: {e}")


@app.get("/")
def health():
    return "ok", 200


@app.post("/book")
def book():
    """Run the full booking flow."""
    # Hydrate session before running
    _download_session_from_gcs()

    # Import here so the module-level browser work doesn't run at boot
    import importlib
    book_court = importlib.import_module("scripts.book_court")

    try:
        # Run with the retry loop. book_court.run raises on failure.
        last_error = None
        for attempt in range(1, book_court.MAX_RETRIES + 1):
            try:
                book_court.run(attempt)
                _upload_session_to_gcs()   # persist any refreshed session
                return jsonify({"status": "success",
                                "detail": f"Booked on attempt {attempt}"}), 200
            except ValueError as e:        # slot not live yet — retry
                last_error = str(e)
                if attempt < book_court.MAX_RETRIES:
                    import time
                    time.sleep(book_court.RETRY_DELAY_S)
            except Exception as e:         # hard failure — stop
                last_error = str(e)
                log.error(traceback.format_exc())
                break

        _upload_session_to_gcs()
        return jsonify({"status": "failed", "detail": last_error}), 500

    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "detail": str(e)}), 500


@app.post("/seed")
def seed():
    """Log in once and persist the session to GCS. Run this manually first."""
    _download_session_from_gcs()
    os.environ["SEED_SESSION_ONLY"] = "true"

    import importlib
    book_court = importlib.import_module("scripts.book_court")
    importlib.reload(book_court)   # pick up the env change

    try:
        book_court.run(1)
        _upload_session_to_gcs()
        return jsonify({"status": "seeded"}), 200
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "detail": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
