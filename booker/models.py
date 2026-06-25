"""
Core data model for the booking system.

These types are deliberately free of any Playwright / platform specifics. They
describe *what* to book, not *how*. A web frontend or database layer produces
``BookingRequest`` objects; the orchestrator hands them to a ``BookingProvider``
to execute. Keeping this boundary clean is what lets us add tennis, classes, or
a different golf platform later without touching the worker's core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class BookingOutcome(str, Enum):
    """The result status of a booking attempt."""
    SUCCESS = "success"          # slot booked and confirmed
    SLOT_UNAVAILABLE = "slot_unavailable"   # slot not (yet) bookable — retryable
    AUTH_FAILED = "auth_failed"  # login/session problem — not retryable without new creds
    ERROR = "error"              # unexpected failure — not retryable


@dataclass(frozen=True)
class Credentials:
    """Login details for a booking platform. Sourced from secrets, never logged."""
    username: str
    password: str

    def __repr__(self) -> str:  # avoid leaking the password in logs/tracebacks
        return f"Credentials(username={self.username!r}, password=***)"


@dataclass
class BookingRequest:
    """
    A platform-agnostic description of a slot to book.

    ``days_ahead`` expresses the booking-window rule (e.g. "courts open 5 days
    out"); the concrete target date is derived at run time so the same request
    can be reused on a weekly schedule. Alternatively an explicit ``on_date``
    can be supplied for one-off bookings.
    """
    venue: str                       # e.g. "lensbury-club"
    activity: str                    # e.g. "padel-courts-60-min"
    target_time: str                 # 24h "HH:MM", e.g. "19:00"
    days_ahead: Optional[int] = 5    # booking-window lead time
    on_date: Optional[str] = None    # explicit ISO date "YYYY-MM-DD" (overrides days_ahead)
    court_pref: str = ""             # optional sub-resource preference, "" = any
    player_names: list[str] = field(default_factory=list)  # for platforms that require them

    def resolve_date(self, now: Optional[datetime] = None) -> tuple[str, str]:
        """Return (iso_date, human_date) for the target booking day."""
        if self.on_date:
            dt = datetime.strptime(self.on_date, "%Y-%m-%d")
        else:
            base = now or datetime.now()
            dt = base + timedelta(days=self.days_ahead or 0)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%A %d %B %Y")


@dataclass
class BookingResult:
    """The outcome of an attempt, suitable for returning over HTTP or storing."""
    outcome: BookingOutcome
    detail: str = ""
    attempts: int = 0
    booked_date: str = ""
    booked_time: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == BookingOutcome.SUCCESS

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "detail": self.detail,
            "attempts": self.attempts,
            "booked_date": self.booked_date,
            "booked_time": self.booked_time,
        }
