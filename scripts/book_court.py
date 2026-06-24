"""
Lensbury Padel Court Auto-Booker
=================================
Logs into https://lensburyclub.bookings.flow.onl, navigates to the padel courts
page, and books a slot 5 days in advance at the configured time.

Configuration is via environment variables (set as GitHub Secrets):
  FLOW_EMAIL      - your login email
  FLOW_PASSWORD   - your login password
  TARGET_TIME     - slot time to book, e.g. "20:00" (24h) or "8:00 PM"
  COURT_PREF      - optional court name preference, e.g. "Court 1" (leave blank for any)
  NOTIFY_EMAIL    - optional email to send booking confirmation to (via stdout log)

Run locally:
  pip install playwright python-dotenv
  playwright install chromium
  python scripts/book_court.py

In GitHub Actions this is triggered by cron (see .github/workflows/book_padel.yml).
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load .env for local dev; in GHA these come from GitHub Secrets
load_dotenv()

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ── Config ──────────────────────────────────────────────────────────────────
BOOKING_URL   = "https://lensburyclub.bookings.flow.onl/location/lensbury-club/padel-courts"
EMAIL         = os.environ["FLOW_EMAIL"]
PASSWORD      = os.environ["FLOW_PASSWORD"]
TARGET_TIME   = os.environ.get("TARGET_TIME", "20:00")   # 24h format preferred
COURT_PREF    = os.environ.get("COURT_PREF", "")         # blank = take any available
DAYS_AHEAD    = int(os.environ.get("DAYS_AHEAD", "5"))   # book this many days in advance
MAX_RETRIES   = int(os.environ.get("MAX_RETRIES", "10"))  # retry attempts if slot not yet live
RETRY_DELAY_S = int(os.environ.get("RETRY_DELAY_S", "20")) # seconds between retries
HEADLESS      = os.environ.get("HEADLESS", "true").lower() != "false"

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)


def target_date_str() -> str:
    """Return the target booking date as a string (today + DAYS_AHEAD)."""
    target = datetime.now() + timedelta(days=DAYS_AHEAD)
    return target.strftime("%Y-%m-%d"), target.strftime("%A %d %B %Y")  # ISO + human


def run(attempt: int = 1):
    target_iso, target_human = target_date_str()
    log.info(f"Attempt {attempt}/{MAX_RETRIES} — targeting {target_human} at {TARGET_TIME}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        try:
            # ── 1. Load the booking page ──────────────────────────────────
            log.info("Loading booking page…")
            page.goto(BOOKING_URL, wait_until="networkidle", timeout=30_000)
            page.wait_for_timeout(2000)

            # ── 2. Log in ─────────────────────────────────────────────────
            # Flow.onl typically shows a login modal or redirect; look for common triggers
            login_triggers = [
                "text=Log in",
                "text=Sign in",
                "text=Login",
                "[data-testid='login-button']",
                "button:has-text('Sign in')",
                "a:has-text('Log in')",
            ]
            logged_in = False
            for selector in login_triggers:
                try:
                    btn = page.wait_for_selector(selector, timeout=5000)
                    if btn and btn.is_visible():
                        log.info(f"Found login trigger: {selector}")
                        btn.click()
                        page.wait_for_timeout(1500)
                        logged_in = True
                        break
                except PlaywrightTimeout:
                    continue

            # Fill credentials wherever the form appeared
            _fill_login_form(page)

            # ── 3. Click through to the activity if needed ───────────────
            activity_selectors = [
                "text=Padel Courts 60 minutes",
                "a:has-text('Padel Courts 60 minutes')",
                "button:has-text('Padel Courts 60 minutes')",
                "[class*='activity']:has-text('Padel Courts')",
            ]
            for sel in activity_selectors:
                try:
                    btn = page.wait_for_selector(sel, timeout=5000)
                    if btn and btn.is_visible():
                        log.info(f"Clicking activity: {sel}")
                        btn.click()
                        page.wait_for_load_state("networkidle", timeout=15_000)
                        page.wait_for_timeout(2000)
                        break
                except PlaywrightTimeout:
                    continue

            # ── 5. Navigate to padel courts if not already there ──────────
            if "/padel-courts" not in page.url:
                log.info("Navigating to padel courts page…")
                page.goto(BOOKING_URL, wait_until="networkidle", timeout=30_000)
                page.wait_for_timeout(2000)

            # ── 4. Select the target date ─────────────────────────────────
            log.info(f"Selecting date: {target_human}")
            _select_date(page, target_iso)

            # ── 5. Pick the time slot ─────────────────────────────────────
            log.info(f"Looking for time slot: {TARGET_TIME}")
            slot_found = _select_time_slot(page, TARGET_TIME, COURT_PREF)

            if not slot_found:
                raise ValueError(f"Time slot {TARGET_TIME} not yet available or already taken")

            # ── 6. Confirm booking ────────────────────────────────────────
            log.info("Confirming booking…")
            _confirm_booking(page)

            log.info(f"✅ SUCCESS — Booked padel court for {target_human} at {TARGET_TIME}")

        except Exception as e:
            # Save a screenshot for debugging
            screenshot_path = f"/tmp/padel_error_attempt{attempt}.png"
            page.screenshot(path=screenshot_path)
            log.error(f"Error on attempt {attempt}: {e}")
            log.info(f"Screenshot saved to {screenshot_path}")
            raise

        finally:
            browser.close()


def _fill_login_form(page):
    """Fill in email and password fields wherever they appear."""
    email_selectors = [
        "input[placeholder*='Email address or customer ID' i]",
        "input[placeholder*='customer ID' i]",
        "input[type='email']",
        "input[name='email']",
        "input[name='username']",
        "input[placeholder*='email' i]",
        "input[placeholder*='Email' i]",
        # Last resort: first visible text/email input in the form
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
                field.fill(EMAIL)
                break
        except PlaywrightTimeout:
            continue

    for sel in password_selectors:
        try:
            field = page.query_selector(sel)
            if field and field.is_visible():
                log.info(f"Filling password with selector: {sel}")
                field.fill(PASSWORD)
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
                page.wait_for_load_state("networkidle", timeout=15_000)
                break
        except Exception:
            continue

    log.info(f"Post-login URL: {page.url}")


def _select_date(page, target_iso: str):
    """
    Attempt to find and click the target date in a calendar widget.
    Flow.onl renders a calendar; we look for the date by aria-label or text.
    """
    year, month, day = target_iso.split("-")
    day_int = int(day)

    # Common calendar date cell patterns
    date_selectors = [
        f"[aria-label*='{target_iso}']",
        f"[data-date='{target_iso}']",
        f"td:has-text('{day_int}')",          # fallback: find by day number
        f"button:has-text('{day_int}')",
    ]

    for sel in date_selectors:
        try:
            cells = page.query_selector_all(sel)
            # Filter to visible, enabled cells
            for cell in cells:
                if cell.is_visible() and cell.is_enabled():
                    cell.click()
                    page.wait_for_timeout(1500)
                    log.info(f"Selected date using selector: {sel}")
                    return
        except Exception:
            continue

    # If we got here, try navigating forward in the calendar month
    log.warning("Could not find date cell directly, trying to navigate calendar…")
    _navigate_calendar_to_date(page, target_iso)


def _navigate_calendar_to_date(page, target_iso: str):
    """Navigate calendar forward/back until the target month is visible, then click the day."""
    from datetime import datetime
    target_dt = datetime.strptime(target_iso, "%Y-%m-%d")
    day_int = target_dt.day

    next_btn_selectors = [
        "button[aria-label='Next month']",
        "button[aria-label='next']",
        "button:has-text('›')",
        "button:has-text('>')",
        "[class*='next']",
    ]

    for _ in range(3):  # try at most 3 months forward
        for sel in next_btn_selectors:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    # Check if target day is now visible
                    day_cells = page.query_selector_all(f"button:has-text('{day_int}'), td:has-text('{day_int}')")
                    for cell in day_cells:
                        if cell.is_visible() and cell.is_enabled():
                            cell.click()
                            page.wait_for_timeout(1500)
                            return
                    btn.click()
                    page.wait_for_timeout(1000)
                    break
            except Exception:
                continue

    log.warning("Calendar navigation exhausted — proceeding anyway")


def _select_time_slot(page, target_time: str, court_pref: str) -> bool:
    """
    Find and click the target time slot. Returns True if found and clicked.
    Handles both 24h ("20:00") and 12h ("8:00 PM") formats.
    """
    # Normalise to both 24h and 12h for matching
    try:
        from datetime import datetime
        if "AM" in target_time.upper() or "PM" in target_time.upper():
            dt = datetime.strptime(target_time.strip(), "%I:%M %p")
        else:
            dt = datetime.strptime(target_time.strip(), "%H:%M")
        time_24h = dt.strftime("%H:%M")
        time_12h = dt.strftime("%-I:%M %p")   # e.g. "8:00 PM"
        time_12h_zero = dt.strftime("%I:%M %p")  # e.g. "08:00 PM"
    except Exception:
        time_24h = target_time
        time_12h = target_time
        time_12h_zero = target_time

    search_texts = [time_24h, time_12h, time_12h_zero]
    log.info(f"Searching for slot text variants: {search_texts}")

    # Wait a moment for slots to render after date selection
    page.wait_for_timeout(2000)

    for text in search_texts:
        # Try court preference first, then any court
        courts_to_try = [court_pref, ""] if court_pref else [""]
        for court in courts_to_try:
            slot_selectors = [
                f"button:has-text('{text}')",
                f"[class*='slot']:has-text('{text}')",
                f"[class*='time']:has-text('{text}')",
                f"td:has-text('{text}')",
                f"li:has-text('{text}')",
                f"div[role='button']:has-text('{text}')",
            ]
            for sel in slot_selectors:
                try:
                    slots = page.query_selector_all(sel)
                    for slot in slots:
                        if not slot.is_visible() or not slot.is_enabled():
                            continue
                        # If court preference, check parent/ancestor text
                        if court:
                            parent_text = slot.evaluate("el => el.closest('[class*=\"court\"], [class*=\"row\"], tr')?.innerText || ''")
                            if court.lower() not in parent_text.lower():
                                continue
                        log.info(f"Clicking slot: '{text}' with selector {sel}")
                        slot.click()
                        page.wait_for_timeout(1500)
                        return True
                except Exception:
                    continue

    return False


def _confirm_booking(page):
    """
    Click through any confirmation dialogs/buttons to finalise the booking.
    """
    confirm_selectors = [
        "button:has-text('Confirm')",
        "button:has-text('Book')",
        "button:has-text('Complete booking')",
        "button:has-text('Pay')",
        "button:has-text('Reserve')",
        "button[type='submit']",
    ]

    for sel in confirm_selectors:
        try:
            btn = page.wait_for_selector(sel, timeout=8000)
            if btn and btn.is_visible():
                log.info(f"Confirming with: {sel}")
                btn.click()
                page.wait_for_timeout(3000)
                page.wait_for_load_state("networkidle", timeout=15_000)

                # Check for success indicators
                success_texts = ["confirmed", "booking confirmed", "thank you", "success", "booked"]
                page_text = page.inner_text("body").lower()
                if any(t in page_text for t in success_texts):
                    log.info("Confirmation text detected on page ✅")
                    return
                # Continue looking for more confirm steps (multi-step checkout)
        except PlaywrightTimeout:
            continue

    # Final page state
    log.info(f"Final page URL after confirmation: {page.url}")


if __name__ == "__main__":
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            run(attempt)
            sys.exit(0)
        except ValueError as e:
            # Slot not yet available — retry
            last_error = e
            if attempt < MAX_RETRIES:
                log.info(f"Slot not available yet. Waiting {RETRY_DELAY_S}s before retry…")
                time.sleep(RETRY_DELAY_S)
        except Exception as e:
            # Hard failure — don't retry
            last_error = e
            log.error(f"Hard failure: {e}")
            break

    log.error(f"All attempts exhausted. Last error: {last_error}")
    sys.exit(1)
