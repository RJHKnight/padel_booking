"""
The orchestrator: the platform-agnostic booking engine.

It owns the browser, drives a ``BookingProvider`` through the fixed sequence
(load → login-if-needed → activity → date → slot → confirm), reuses a saved
session when possible, and runs the retry loop for slots that aren't live yet.

It knows nothing about any specific platform's DOM — that's the provider's job.
This is the component a future job queue / multi-user backend calls; it takes a
``BookingRequest`` + provider + credentials and returns a ``BookingResult``.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from .models import BookingRequest, BookingResult, BookingOutcome, Credentials
from .providers.base import BookingProvider
from .session import SessionStore

log = logging.getLogger(__name__)


@dataclass
class OrchestratorConfig:
    headless: bool = True
    max_retries: int = 30
    retry_delay_s: int = 5
    session_path: str = "/tmp/flow_session.json"
    nav_timeout_ms: int = 30_000


class Orchestrator:
    def __init__(
        self,
        provider: BookingProvider,
        credentials: Credentials,
        session_store: SessionStore,
        config: OrchestratorConfig,
    ):
        self.provider = provider
        self.credentials = credentials
        self.session_store = session_store
        self.config = config

    # ── public API ────────────────────────────────────────────────────────────
    def book(self, request: BookingRequest) -> BookingResult:
        """Run the full flow with retries. Returns a structured result."""
        iso_date, human_date = request.resolve_date()
        last_detail = ""

        for attempt in range(1, self.config.max_retries + 1):
            log.info(f"Attempt {attempt}/{self.config.max_retries} — "
                     f"{request.activity} on {human_date} at {request.target_time}")
            try:
                outcome, detail = self._attempt(request, iso_date)
                last_detail = detail
                if outcome == BookingOutcome.SUCCESS:
                    return BookingResult(BookingOutcome.SUCCESS, detail, attempt,
                                         iso_date, request.target_time)
                if outcome == BookingOutcome.AUTH_FAILED:
                    # Not retryable without new credentials
                    return BookingResult(BookingOutcome.AUTH_FAILED, detail, attempt,
                                         iso_date, request.target_time)
                # SLOT_UNAVAILABLE — retry after a delay
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_delay_s)
            except Exception as e:
                log.error(f"Unexpected error on attempt {attempt}: {e}", exc_info=True)
                last_detail = str(e)
                # Treat unexpected errors as retryable up to the limit
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_delay_s)

        return BookingResult(BookingOutcome.SLOT_UNAVAILABLE,
                             last_detail or "exhausted retries",
                             self.config.max_retries, iso_date, request.target_time)

    def seed_session(self, request: BookingRequest) -> BookingResult:
        """Log in once and persist the session, without booking. For setup."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.config.headless)
            context, page = self._new_context(browser, use_session=True)
            try:
                page.goto(self.provider.booking_url(request),
                          wait_until="networkidle", timeout=self.config.nav_timeout_ms)
                page.wait_for_timeout(1500)
                if not self.provider.is_logged_in(page):
                    if not self.provider.login(page, self.credentials):
                        return BookingResult(BookingOutcome.AUTH_FAILED, "login failed")
                    self._persist_session(context)
                else:
                    self._persist_session(context)
                return BookingResult(BookingOutcome.SUCCESS, "session seeded")
            finally:
                browser.close()

    # ── internals ─────────────────────────────────────────────────────────────
    def _attempt(self, request: BookingRequest, iso_date: str) -> tuple[BookingOutcome, str]:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.config.headless)
            context, page = self._new_context(browser, use_session=True)
            try:
                page.goto(self.provider.booking_url(request),
                          wait_until="networkidle", timeout=self.config.nav_timeout_ms)
                page.wait_for_timeout(1500)

                # Login only if the reused session didn't carry us in
                if not self.provider.is_logged_in(page):
                    log.info("Not logged in — performing fresh login")
                    if not self.provider.login(page, self.credentials):
                        self._screenshot(page, "auth_failed")
                        return BookingOutcome.AUTH_FAILED, "login failed"
                    self._persist_session(context)
                else:
                    log.info("Reused session is valid — skipped login")

                self.provider.navigate_to_activity(page, request)
                self.provider.select_date(page, request, iso_date)

                if not self.provider.select_slot(page, request):
                    return BookingOutcome.SLOT_UNAVAILABLE, "slot not available"

                if not self.provider.confirm(page, request):
                    self._screenshot(page, "confirm_failed")
                    return BookingOutcome.ERROR, "slot selected but not confirmed"

                return BookingOutcome.SUCCESS, "booking confirmed"
            finally:
                browser.close()

    def _new_context(self, browser, use_session: bool):
        kwargs = {
            "viewport": {"width": 1280, "height": 900},
            "user_agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36"),
        }
        if use_session:
            path = self.session_store.load()
            if path:
                log.info("Using saved session state")
                kwargs["storage_state"] = path
        context = browser.new_context(**kwargs)
        return context, context.new_page()

    def _persist_session(self, context) -> None:
        try:
            context.storage_state(path=self.config.session_path)
            self.session_store.save(self.config.session_path)
        except Exception as e:
            log.warning(f"Could not persist session: {e}")

    def _screenshot(self, page, tag: str) -> None:
        try:
            path = f"/tmp/booking_{tag}.png"
            page.screenshot(path=path)
            log.info(f"Saved screenshot: {path}")
        except Exception:
            pass
