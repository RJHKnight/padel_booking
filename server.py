"""
HTTP wrapper so Cloud Scheduler can trigger a booking run.

Cloud Run serves this Flask app; Cloud Scheduler POSTs to /book at the scheduled
time. All booking logic lives in the ``booker`` package — this file only adapts
HTTP to the orchestrator.

Endpoints:
  GET  /       - health check
  POST /book   - run the booking flow; returns the BookingResult as JSON
  POST /seed   - log in once and persist the session (run manually first)
"""

import sys
import logging
import traceback

from flask import Flask, jsonify

from booker import Orchestrator, get_provider, build_session_store_from_env
from booker.config import (
    credentials_from_env,
    request_from_env,
    provider_name_from_env,
    orchestrator_config_from_env,
)

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
log = logging.getLogger("server")

app = Flask(__name__)


def _build():
    provider = get_provider(provider_name_from_env())
    credentials = credentials_from_env()
    config = orchestrator_config_from_env()
    store = build_session_store_from_env(config.session_path)
    return Orchestrator(provider, credentials, store, config), request_from_env()


@app.get("/")
def health():
    return "ok", 200


@app.post("/book")
def book():
    try:
        orch, request = _build()
        result = orch.book(request)
        status = 200 if result.ok else 500
        return jsonify(result.to_dict()), status
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"outcome": "error", "detail": str(e)}), 500


@app.post("/seed")
def seed():
    try:
        orch, request = _build()
        result = orch.seed_session(request)
        status = 200 if result.ok else 500
        return jsonify(result.to_dict()), status
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"outcome": "error", "detail": str(e)}), 500


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
