"""
FlowProvider — adapter for the flow.onl booking platform (Lensbury Club).

All the platform-specific DOM logic lives here: login form shape, the horizontal
day-strip date picker, the slot-row layout, the "Your selection" modal, the
player-names checkout form, and confirmation detection. The orchestrator calls
the methods defined by ``BookingProvider``; nothing here leaks upward.

This adapter handles tennis and padel equally (same platform, different activity
slug) — only the ``BookingRequest.activity`` differs.
"""

from __future__ import annotations

import re
import logging

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .base import BookingProvider
from ..models import BookingRequest, Credentials

log = logging.getLogger(__name__)

_BASE = "https://lensburyclub.bookings.flow.onl"


class FlowProvider(BookingProvider):
    name = "flow"

    # ── URL ───────────────────────────────────────────────────────────────────
    def booking_url(self, request: BookingRequest) -> str:
        # e.g. https://lensburyclub.bookings.flow.onl/location/lensbury-club/padel-courts
        return f"{_BASE}/location/{request.venue}/{request.activity}"

    # ── Auth ──────────────────────────────────────────────────────────────────
    def is_logged_in(self, page) -> bool:
        try:
            body = page.inner_text("body").lower()
        except Exception:
            body = ""
        if any(s in body for s in ["my account", "log out", "logout", "sign out"]):
            return True
        if "log in" in body or "sign in" in body or "login" in body:
            return False
        try:
            pw = page.query_selector("input[type='password']")
            if pw and pw.is_visible():
                return False
        except Exception:
            pass
        # Unsure — report not-logged-in so the orchestrator attempts login (safe).
        return False

    def login(self, page, credentials: Credentials) -> bool:
        # Open the login form if it's behind a trigger
        for selector in (
            "text=Log in", "text=Sign in", "text=Login",
            "[data-testid='login-button']",
            "button:has-text('Sign in')", "a:has-text('Log in')",
        ):
            try:
                btn = page.wait_for_selector(selector, timeout=5000)
                if btn and btn.is_visible():
                    log.info(f"Found login trigger: {selector}")
                    btn.click()
                    page.wait_for_timeout(1500)
                    break
            except PlaywrightTimeout:
                continue

        self._fill_login_form(page, credentials)

        # Verify
        page.wait_for_timeout(1000)
        return self.is_logged_in(page)

    def _fill_login_form(self, page, credentials: Credentials) -> None:
        email_selectors = [
            "input[placeholder*='Email address or customer ID' i]",
            "input[placeholder*='customer ID' i]",
            "input[type='email']",
            "input[name='email']",
            "input[name='username']",
            "input[placeholder*='email' i]",
            "input[placeholder*='Email' i]",
            "form input[type='text']:visible",
            "dialog input:not([type='password']):visible",
        ]
        password_selectors = [
            "input[type='password']",
            "input[name='password']",
            "input[placeholder*='password' i]",
        ]
        submit_selectors = [
            "button[type='submit']",
            "button:has-text('Log in')",
            "button:has-text('Sign in')",
            "button:has-text('Login')",
            "input[type='submit']",
        ]

        for sel in email_selectors:
            try:
                field = page.wait_for_selector(sel, timeout=8000)
                if field and field.is_visible():
                    log.info(f"Filling email with selector: {sel}")
                    field.fill(credentials.username)
                    break
            except PlaywrightTimeout:
                continue

        for sel in password_selectors:
            try:
                field = page.query_selector(sel)
                if field and field.is_visible():
                    field.fill(credentials.password)
                    break
            except Exception:
                continue

        for sel in submit_selectors:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    log.info(f"Submitting login with: {sel}")
                    btn.click()
                    page.wait_for_timeout(3000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    except PlaywrightTimeout:
                        pass
                    break
            except Exception:
                continue
        log.info(f"Post-login URL: {page.url}")

    # ── Activity ──────────────────────────────────────────────────────────────
    def navigate_to_activity(self, page, request: BookingRequest) -> None:
        # Flow shows an activity card (e.g. "Padel Courts 60 minutes") to click
        # through to the timetable. Activity-card label is platform content, so we
        # match loosely on the activity words.
        candidates = [
            "text=Padel Courts 60 minutes",
            "a:has-text('Padel Courts 60 minutes')",
            "button:has-text('Padel Courts 60 minutes')",
            "[class*='activity']:has-text('Padel')",
            "[class*='activity']:has-text('Tennis')",
            "[class*='activity'] a",
        ]
        for sel in candidates:
            try:
                btn = page.wait_for_selector(sel, timeout=5000)
                if btn and btn.is_visible():
                    log.info(f"Clicking activity card: {sel}")
                    btn.click()
                    try:
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    except PlaywrightTimeout:
                        pass
                    page.wait_for_timeout(2000)
                    return
            except PlaywrightTimeout:
                continue
        log.info("No intermediate activity card found — assuming already on timetable")

    # ── Date ──────────────────────────────────────────────────────────────────
    def select_date(self, page, request: BookingRequest, iso_date: str) -> None:
        from datetime import datetime
        target_dt = datetime.strptime(iso_date, "%Y-%m-%d")
        day_num = target_dt.day
        day_abbr = target_dt.strftime("%a")
        log.info(f"Looking for day strip cell: {day_abbr} {day_num}")

        for attempt in range(14):
            for cell in page.query_selector_all("div, button, td, th, span, a"):
                try:
                    if not cell.is_visible():
                        continue
                    normalised = " ".join(cell.inner_text().split())
                    if re.fullmatch(rf"{day_abbr}\s*{day_num}", normalised):
                        log.info(f"Found date cell: {normalised!r} — clicking")
                        cell.click()
                        page.wait_for_timeout(2000)
                        return
                except Exception:
                    continue

            log.info(f"Date not visible (attempt {attempt+1}); scrolling strip forward")
            if not self._scroll_day_strip_forward(page):
                # JS fallback for the rightmost chevron
                try:
                    page.evaluate(
                        """() => {
                            const btns = [...document.querySelectorAll('button')];
                            const arrows = btns.filter(b =>
                                b.textContent.trim().match(/^[\u203a>\u2192\u27e9]$/) ||
                                (b.getAttribute('aria-label') || '').match(/next|forward|right/i));
                            if (arrows.length) arrows[arrows.length - 1].click();
                        }"""
                    )
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
        log.warning(f"Could not find {day_abbr} {day_num} in day strip")

    def _scroll_day_strip_forward(self, page) -> bool:
        for sel in ("button[aria-label*='next' i]",
                    "button[aria-label*='forward' i]",
                    "button[aria-label*='right' i]"):
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible() and btn.is_enabled():
                    btn.click()
                    page.wait_for_timeout(1000)
                    return True
            except Exception:
                continue
        return False

    # ── Slot ──────────────────────────────────────────────────────────────────
    def select_slot(self, page, request: BookingRequest) -> bool:
        time_24h = _to_24h(request.target_time)
        court_pref = request.court_pref
        log.info(f"Looking for slot starting {time_24h}, court pref: '{court_pref or 'any'}'")
        page.wait_for_timeout(2000)

        # Strategy 1: an enabled 'Book' button whose row STARTS with target time
        try:
            book_buttons = page.query_selector_all("button:has-text('Book')")
            log.info(f"Found {len(book_buttons)} 'Book' buttons")
            for btn in book_buttons:
                if not btn.is_visible() or not btn.is_enabled():
                    continue
                row_text = _row_text_of(btn)
                if not _row_starts_with_time(row_text, time_24h):
                    continue
                if court_pref and court_pref.lower() not in row_text.lower():
                    continue
                log.info(f"Clicking 'Book' for {time_24h}")
                btn.click()
                page.wait_for_timeout(2000)
                self._handle_selection_modal(page)
                return True
        except Exception as e:
            log.warning(f"Slot strategy 1 failed: {e}")

        # Strategy 2: click the slot row, then a revealed Book button
        try:
            for el in page.query_selector_all(f"*:has-text('{time_24h}')"):
                if not el.is_visible():
                    continue
                text = el.inner_text().strip()
                if not (text.startswith(time_24h) and len(text) < 60):
                    continue
                log.info(f"Strategy 2: selecting row {text!r}")
                el.click()
                page.wait_for_timeout(2000)
                try:
                    book_btn = page.wait_for_selector("button:has-text('Book')", timeout=5000)
                    if book_btn and book_btn.is_visible() and book_btn.is_enabled():
                        row_text = _row_text_of(book_btn)
                        if _row_starts_with_time(row_text, time_24h) or not row_text:
                            book_btn.click()
                            page.wait_for_timeout(2000)
                            self._handle_selection_modal(page)
                            return True
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"Slot strategy 2 failed: {e}")

        log.warning(f"No bookable slot for {time_24h}")
        return False

    def _handle_selection_modal(self, page) -> bool:
        page.wait_for_timeout(1500)
        for sel in ("button:has-text('Book now')", "button:has-text('Add to basket')"):
            try:
                btn = page.wait_for_selector(sel, timeout=5000)
                if btn and btn.is_visible() and btn.is_enabled():
                    log.info(f"Selection modal: clicking {sel}")
                    btn.click()
                    page.wait_for_timeout(2500)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    except PlaywrightTimeout:
                        pass
                    return True
            except PlaywrightTimeout:
                continue
        return False

    # ── Confirm ───────────────────────────────────────────────────────────────
    def confirm(self, page, request: BookingRequest) -> bool:
        url = page.url.lower()
        on_checkout = "checkout" in url or "basket" in url
        if not on_checkout and self._booking_confirmed(page):
            return True

        self._fill_checkout_form(page, request)

        for sel in (
            "button:has-text('Confirm booking')",
            "button:has-text('Confirm')",
            "button:has-text('Complete booking')",
            "button:has-text('Reserve')",
            "button:has-text('Continue')",
            "button:has-text('Book')",
        ):
            try:
                btn = page.wait_for_selector(sel, timeout=6000)
                if not (btn and btn.is_visible() and btn.is_enabled()):
                    continue
                log.info(f"Confirming with: {sel}")
                btn.click()
                page.wait_for_timeout(3500)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                new_url = page.url.lower()
                still_on_checkout = "checkout" in new_url or "basket" in new_url
                if not still_on_checkout:
                    log.info(f"Left checkout after confirm: {page.url}")
                    return True
            except PlaywrightTimeout:
                continue
            except Exception as e:
                log.warning(f"Confirm step {sel} errored: {e}")
                continue

        final_url = page.url.lower()
        if "checkout" in final_url or "basket" in final_url:
            log.warning("Still on checkout/basket — booking NOT confirmed")
            return False
        return self._booking_confirmed(page)

    def _fill_checkout_form(self, page, request: BookingRequest) -> None:
        # Map player slots from the request. Flow labels them "Player 2/3/4"
        # (the member is player 1). Default unused slots to "N/A".
        names = list(request.player_names)
        player_values = {
            "player 2": names[0] if len(names) > 0 else "N/A",
            "player 3": names[1] if len(names) > 1 else "N/A",
            "player 4": names[2] if len(names) > 2 else "N/A",
        }
        try:
            inputs = page.query_selector_all(
                "input[type='text'], input:not([type]), textarea")
            visible = [i for i in inputs if i.is_visible() and i.is_enabled()]
            if not visible:
                log.info("No checkout form inputs — assuming none required")
                return
            log.info(f"Checkout form: {len(visible)} input(s)")
            for inp in visible:
                label = inp.evaluate("""el => {
                    const aria = el.getAttribute('aria-label') || '';
                    const ph = el.getAttribute('placeholder') || '';
                    let labelText = '';
                    let node = el;
                    for (let i = 0; i < 4; i++) {
                        node = node.parentElement;
                        if (!node) break;
                        const t = node.innerText || '';
                        if (t.length > labelText.length) labelText = t;
                    }
                    return (aria + ' ' + ph + ' ' + labelText).toLowerCase();
                }""")
                matched = False
                for key, value in player_values.items():
                    if key in label:
                        if not inp.input_value().strip():
                            log.info(f"Filling {key.title()} = {value!r}")
                            inp.fill(value)
                        matched = True
                        break
                if not matched and not inp.input_value().strip():
                    inp.fill("N/A")
        except Exception as e:
            log.warning(f"Could not fully fill checkout form: {e}")

    def _booking_confirmed(self, page) -> bool:
        try:
            text = page.inner_text("body").lower()
        except Exception:
            text = ""
        strong = [
            "booking confirmed", "booking complete", "your booking is confirmed",
            "thank you for your booking", "reservation confirmed", "booking reference",
            "your bookings", "upcoming bookings", "added to basket", "added to your basket",
        ]
        if any(p in text for p in strong):
            log.info("Confirmation text detected")
            return True
        basket_empty = "your shopping basket is empty" in text or "basket is empty" in text
        if not basket_empty and ("basket" in text or "checkout" in text):
            money = re.findall(r"£\s?(\d+\.\d{2})", text)
            if any(float(m) > 0 for m in money):
                return True
            if "remove" in text:
                return True
        url = page.url.lower()
        if any(k in url for k in ["confirmation", "/complete", "/success",
                                  "booking-confirmed", "/confirmed"]):
            return True
        return False


# ── module helpers ────────────────────────────────────────────────────────────
def _to_24h(target_time: str) -> str:
    from datetime import datetime
    try:
        if "AM" in target_time.upper() or "PM" in target_time.upper():
            dt = datetime.strptime(target_time.strip(), "%I:%M %p")
        else:
            dt = datetime.strptime(target_time.strip(), "%H:%M")
        return dt.strftime("%H:%M")
    except Exception:
        return target_time


def _row_starts_with_time(row_text: str, time_24h: str) -> bool:
    if not row_text:
        return False
    m = re.search(r"\b(\d{1,2}:\d{2})\b", row_text)
    return bool(m) and m.group(1) == time_24h


def _row_text_of(el) -> str:
    return el.evaluate("""el => {
        let node = el;
        for (let i = 0; i < 6; i++) {
            node = node.parentElement;
            if (!node) break;
            const t = node.innerText || '';
            if (t.includes(':') && t.length > 10) return t;
        }
        return '';
    }""")
