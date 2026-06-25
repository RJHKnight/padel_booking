"""
booker — a general activity-booking worker.

Public surface:
    BookingRequest, BookingResult, BookingOutcome, Credentials  (models)
    get_provider(name)                                          (provider registry)
    Orchestrator, OrchestratorConfig                            (engine)
    build_session_store_from_env                                (session storage)
"""

from .models import (
    BookingRequest,
    BookingResult,
    BookingOutcome,
    Credentials,
)
from .providers import get_provider, available_providers
from .orchestrator import Orchestrator, OrchestratorConfig
from .session import build_session_store_from_env

__all__ = [
    "BookingRequest",
    "BookingResult",
    "BookingOutcome",
    "Credentials",
    "get_provider",
    "available_providers",
    "Orchestrator",
    "OrchestratorConfig",
    "build_session_store_from_env",
]
