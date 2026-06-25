"""
Environment-driven configuration.

Bridges env vars (Cloud Run / local .env) to the typed objects the orchestrator
needs. When the web frontend + DB arrive, they replace this module as the source
of requests and credentials — the orchestrator itself won't change.
"""

from __future__ import annotations

import os

from .models import BookingRequest, Credentials
from .orchestrator import OrchestratorConfig


def credentials_from_env() -> Credentials:
    user = os.environ.get("FLOW_EMAIL", "")
    pw = os.environ.get("FLOW_PASSWORD", "")
    if not user or not pw:
        raise RuntimeError("FLOW_EMAIL and FLOW_PASSWORD must be set")
    return Credentials(username=user, password=pw)


def request_from_env() -> BookingRequest:
    names = [n.strip() for n in os.environ.get("PLAYER_NAMES", "").split(",") if n.strip()]
    return BookingRequest(
        venue=os.environ.get("VENUE", "lensbury-club"),
        activity=os.environ.get("ACTIVITY", "padel-courts"),
        target_time=os.environ.get("TARGET_TIME", "19:00"),
        days_ahead=int(os.environ.get("DAYS_AHEAD", "5")),
        on_date=os.environ.get("ON_DATE") or None,
        court_pref=os.environ.get("COURT_PREF", ""),
        player_names=names or ["Amelia Fink"],
    )


def provider_name_from_env() -> str:
    return os.environ.get("PROVIDER", "flow")


def orchestrator_config_from_env() -> OrchestratorConfig:
    return OrchestratorConfig(
        headless=os.environ.get("HEADLESS", "true").lower() != "false",
        max_retries=int(os.environ.get("MAX_RETRIES", "30")),
        retry_delay_s=int(os.environ.get("RETRY_DELAY_S", "5")),
        session_path=os.environ.get("SESSION_STATE_PATH", "/tmp/flow_session.json"),
    )
