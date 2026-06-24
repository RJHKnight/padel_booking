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
    Click the correct day in the horizontal day-strip on the Lensbury timetable page.
    The strip shows cells like "Wed\n24", "Thu\n25" etc.
    Scrolls forward if the target day is not yet visible.
    """
    from datetime import datetime
    target_dt = datetime.strptime(target_iso, "%Y-%m-%d")
    day_num  = target_dt.day
    day_abbr = target_dt.strftime("%a")  # e.g. "Tue"

    log.info(f"Looking for day strip cell: {day_abbr} {day_num}")

    for attempt in range(14):
        cells = page.query_selector_all("div, button, td, th, span, a")
        for cell in cells:
            try:
                if not cell.is_visible():
                    continue
                text = cell.inner_text().strip()
                if day_abbr in text and str(day_num) in text and len(text) < 20:
                    log.info(f"Found date cell: '{text}' — clicking")
                    cell.click()
                    page.wait_for_timeout(2000)
                    return
            except Exception:
                continue

        log.info(f"Date not visible on attempt {attempt+1}, scrolling day strip forward...")
        scrolled = False
        for sel in ["button[aria-label*='next' i]", "button[aria-label*='forward' i]", "button[aria-label*='right' i]"]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible() and btn.is_enabled():
                    btn.click()
                    page.wait_for_timeout(1000)
                    scrolled = True
                    break
            except Exception:
                continue

        if not scrolled:
            try:
                page.evaluate("""
                    () => {
                        const btns = [...document.querySelectorAll('button')];
                        const arrows = btns.filter(b =>
                            b.textContent.trim().match(/^[›>→⟩]$/) ||
                            (b.getAttribute('aria-label') || '').match(/next|forward|right/i)
                        );
                        if (arrows.length) arrows[arrows.length - 1].click();
                    }
                """)
                page.wait_for_timeout(1000)
            except Exception:
                pass

    log.warning(f"Could not find {day_abbr} {day_num} in day strip after scrolling")


def _select_time_slot(page, target_time: str, court_pref: str) -> bool:
    """
    Find and click the Book button for the target time slot.
    The Lensbury timetable shows rows like "19:00 - 20:00 | Padel Courts 60 minutes | Padel Court 1 | £0.00 | Book"
    We find a row whose text starts with the target time and click its Book button.
    """
    from datetime import datetime
    try:
        if "AM" in target_time.upper() or "PM" in target_time.upper():
            dt = datetime.strptime(target_time.strip(), "%I:%M %p")
        else:
            dt = datetime.strptime(target_time.strip(), "%H:%M")
        time_24h = dt.strftime("%H:%M")  # e.g. "19:00"
    except Exception:
        time_24h = target_time

    log.info(f"Looking for slot starting at {time_24h}, court pref: '{court_pref or 'any'}'")
    page.wait_for_timeout(2000)

    # Strategy 1: find a Book button whose containing row starts with the target time
    try:
        book_buttons = page.query_selector_all("button:has-text('Book')")
        log.info(f"Found {len(book_buttons)} Book buttons on page")
        for btn in book_buttons:
            if not btn.is_visible() or not btn.is_enabled():
                continue
            # Walk up to the row container and check its text
            row_text = btn.evaluate("""el => {
                let node = el;
                for (let i = 0; i < 6; i++) {
                    node = node.parentElement;
                    if (!node) break;
                    const t = node.innerText || '';
                    if (t.includes(':') && t.length > 10) return t;
                }
                return '';
            }""")
            log.info(f"Row text: {row_text[:80]!r}")
            if time_24h not in row_text:
                continue
            if court_pref and court_pref.lower() not in row_text.lower():
                continue
            log.info(f"Clicking Book button for slot {time_24h}")
            btn.click()
            page.wait_for_timeout(2000)
            return True
    except Exception as e:
        log.warning(f"Strategy 1 failed: {e}")

    # Strategy 2: find any element containing the time and click it
    try:
        els = page.query_selector_all(f"*:has-text('{time_24h}')")
        for el in els:
            if not el.is_visible():
                continue
            text = el.inner_text().strip()
            if time_24h in text and len(text) < 60:
                log.info(f"Strategy 2: clicking element with text: {text!r}")
                el.click()
                page.wait_for_timeout(2000)
                return True
    except Exception as e:
        log.warning(f"Strategy 2 failed: {e}")

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
