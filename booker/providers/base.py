"""
The BookingProvider interface.

Each booking platform (Flow/flow.onl today; BRS, ClubV1, intelligentgolf, etc.
later) implements this contract. The orchestrator drives these methods in a
fixed sequence but knows nothing about any platform's DOM. This is the seam
that keeps the worker general.

A provider operates against a Playwright ``Page`` that the orchestrator owns.
Providers should be stateless beyond the page they're handed — anything durable
(like a saved session) is managed by the orchestrator and session layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import BookingRequest, Credentials


class BookingProvider(ABC):
    """Contract every booking-platform adapter must satisfy."""

    #: Short stable identifier, e.g. "flow". Used to select a provider by name.
    name: str = "base"

    @abstractmethod
    def booking_url(self, request: BookingRequest) -> str:
        """The URL to load for this venue+activity before booking begins."""
        raise NotImplementedError

    @abstractmethod
    def is_logged_in(self, page) -> bool:
        """Return True if the current page state indicates an authenticated session."""
        raise NotImplementedError

    @abstractmethod
    def login(self, page, credentials: Credentials) -> bool:
        """
        Perform a fresh login. Returns True on success. Should be safe to call
        after loading the booking URL. The orchestrator only calls this when
        ``is_logged_in`` is False (e.g. no/expired saved session).
        """
        raise NotImplementedError

    @abstractmethod
    def navigate_to_activity(self, page, request: BookingRequest) -> None:
        """
        From the loaded booking page, click through to the activity's timetable
        if the platform requires an intermediate step. No-op if not needed.
        """
        raise NotImplementedError

    @abstractmethod
    def select_date(self, page, request: BookingRequest, iso_date: str) -> None:
        """Navigate the timetable to the target date."""
        raise NotImplementedError

    @abstractmethod
    def select_slot(self, page, request: BookingRequest) -> bool:
        """
        Find and select the slot matching request.target_time (and court_pref
        if set). Return True only if a bookable slot was selected. Returning
        False signals 'not available' — the orchestrator treats this as
        retryable.
        """
        raise NotImplementedError

    @abstractmethod
    def confirm(self, page, request: BookingRequest) -> bool:
        """
        Complete any checkout/confirmation flow (filling required fields such as
        player names) and finalise the booking. Return True only if the booking
        is verifiably confirmed.
        """
        raise NotImplementedError
