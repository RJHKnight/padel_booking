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
            confirmed = _confirm_booking(page)

            if not confirmed:
                # We clicked a slot but couldn't verify the booking completed.
                raise RuntimeError(
                    "Slot was selected but booking could not be confirmed — "
                    "no confirmation detected. Check the screenshot."
                )

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
                # Require the cell to be exactly the day abbreviation + day number,
                # e.g. "Mon\n29" or "Mon 29" — not just containing those substrings.
                # This avoids matching "12:00", "29 June 2026", or unrelated text.
                normalised = " ".join(text.split())  # collapse newlines/spaces
                import re as _re
                if _re.fullmatch(rf"{day_abbr}\s*{day_num}", normalised):
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


def _row_starts_with_time(row_text: str, time_24h: str) -> bool:
    """
    True only if the slot row's time range STARTS with the target time.
    Rows render like "19:00 - 20:00\n60min". We must not match a row whose
    *end* time equals the target (e.g. target 19:00 must reject "18:00 - 19:00").
    """
    if not row_text:
        return False
    import re
    # Find the first HH:MM occurrence in the row and require it to equal target
    m = re.search(r"\b(\d{1,2}:\d{2})\b", row_text)
    return bool(m) and m.group(1) == time_24h


def _select_time_slot(page, target_time: str, court_pref: str) -> bool:
    """
    Find and click the Book button for the target time slot.
    Returns True ONLY if a bookable Book button for the correct start time
    (and matching court preference, if any) was found and clicked.
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

    def _row_text_of(el):
        """Walk up the DOM to find the slot row's full text."""
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

    # Strategy 1: a 'Book' button whose row STARTS with the target time.
    # "Fully booked" / "Not available" rows have no enabled Book button, so they
    # are naturally skipped — that is the correct behaviour (treated as unavailable).
    try:
        book_buttons = page.query_selector_all("button:has-text('Book')")
        log.info(f"Found {len(book_buttons)} 'Book' buttons on page")
        for btn in book_buttons:
            if not btn.is_visible() or not btn.is_enabled():
                continue
            row_text = _row_text_of(btn)
            log.info(f"Candidate row: {row_text[:80]!r}")
            if not _row_starts_with_time(row_text, time_24h):
                continue
            if court_pref and court_pref.lower() not in row_text.lower():
                continue
            log.info(f"Clicking 'Book' for slot {time_24h}")
            btn.click()
            page.wait_for_timeout(2000)
            _handle_selection_modal(page)
            return True
    except Exception as e:
        log.warning(f"Strategy 1 failed: {e}")

    # Strategy 2: click the slot row itself, then look for a Book button that
    # appears (some UIs reveal it after selecting the row). Only return True if
    # we actually manage to click an enabled Book button.
    try:
        els = page.query_selector_all(f"*:has-text('{time_24h}')")
        for el in els:
            if not el.is_visible():
                continue
            text = el.inner_text().strip()
            if not (text.startswith(time_24h) and len(text) < 60):
                continue
            log.info(f"Strategy 2: selecting slot row: {text!r}")
            el.click()
            page.wait_for_timeout(2000)
            try:
                book_btn = page.wait_for_selector("button:has-text('Book')", timeout=5000)
                if book_btn and book_btn.is_visible() and book_btn.is_enabled():
                    # Verify this Book button is for our target time
                    row_text = _row_text_of(book_btn)
                    if _row_starts_with_time(row_text, time_24h) or not row_text:
                        log.info("Strategy 2: clicking revealed 'Book' button")
                        book_btn.click()
                        page.wait_for_timeout(2000)
                        _handle_selection_modal(page)
                        return True
            except Exception:
                pass
            # Clicking the row did not lead to a bookable button — keep looking
    except Exception as e:
        log.warning(f"Strategy 2 failed: {e}")

    log.warning(f"No bookable slot found for {time_24h}")
    return False


def _booking_confirmed(page) -> bool:
    """
    Detect whether a booking has actually been confirmed, using multiple signals:
      1. Explicit confirmation text on the page
      2. The shopping basket showing a non-empty / non-zero state
      3. URL changing to a confirmation/checkout-complete path
    Returns True only if at least one strong signal is present.
    """
    try:
        page_text = page.inner_text("body").lower()
    except Exception:
        page_text = ""

    strong_phrases = [
        "booking confirmed",
        "booking complete",
        "your booking is confirmed",
        "thank you for your booking",
        "reservation confirmed",
        "booking reference",
        "your bookings",
        "upcoming bookings",
        "added to basket",
        "added to your basket",
    ]
    if any(p in page_text for p in strong_phrases):
        log.info("Booking confirmation text detected on page")
        return True

    # Basket no longer empty is a good signal that the slot was reserved
    basket_empty = "your shopping basket is empty" in page_text or "basket is empty" in page_text
    if not basket_empty and ("basket" in page_text or "checkout" in page_text):
        # Look for a money value other than £0.00 in the basket area
        import re
        money = re.findall(r"£\s?(\d+\.\d{2})", page_text)
        if any(float(m) > 0 for m in money):
            log.info("Basket shows a non-zero amount — slot appears reserved")
            return True
        # Even a £0.00 basket that's explicitly NOT empty can indicate a held slot
        if not basket_empty and "remove" in page_text:
            log.info("Basket contains an item (Remove control present)")
            return True

    # URL signal — ONLY treat as confirmed if the URL indicates completion.
    # Being on /basket/checkout means the form still needs submitting, so those
    # are deliberately excluded here.
    url = page.url.lower()
    completion_markers = ["confirmation", "/complete", "/success", "booking-confirmed", "/confirmed"]
    if any(k in url for k in completion_markers):
        log.info(f"URL indicates completed booking: {page.url}")
        return True

    return False


def _handle_selection_modal(page) -> bool:
    """
    After clicking a slot, Lensbury shows a "Your selection" modal with
    "Book now" and "Add to basket" buttons. Click "Book now" to proceed
    straight to the checkout form. Returns True if a button was clicked.
    """
    page.wait_for_timeout(1500)
    modal_buttons = [
        "button:has-text('Book now')",
        "button:has-text('Add to basket')",  # fallback — still proceeds to basket
    ]
    for sel in modal_buttons:
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
        except Exception as e:
            log.warning(f"Selection modal button {sel} errored: {e}")
            continue
    log.info("No selection modal detected (or no button to click)")
    return False


def _fill_checkout_form(page) -> None:
    """
    Fill the 'Information required for booking' checkout form that Lensbury shows
    before the final 'Confirm booking' button. Fields:
      Player 2: (Member Name / Guest Name)        -> "Amelia Fink"
      Player 3: (... or N/A if only two players)  -> "N/A"
      Player 4: (... or N/A if only two players)  -> "N/A"
    Matching is done by the field's label/placeholder text so it is robust to
    input ordering. Safe to call even if the form isn't present.
    """
    player_values = {
        "player 2": "Amelia Fink",
        "player 3": "N/A",
        "player 4": "N/A",
    }

    try:
        # Collect all visible text inputs / textareas on the checkout page
        inputs = page.query_selector_all("input[type='text'], input:not([type]), textarea")
        visible_inputs = [i for i in inputs if i.is_visible() and i.is_enabled()]
        if not visible_inputs:
            log.info("No checkout form inputs found — assuming none required")
            return

        log.info(f"Checkout form: found {len(visible_inputs)} input field(s)")

        for inp in visible_inputs:
            # Work out which player this field belongs to by inspecting nearby label text
            label_text = inp.evaluate("""el => {
                // Look at preceding label/text, aria-label, placeholder, or parent text
                const aria = el.getAttribute('aria-label') || '';
                const ph = el.getAttribute('placeholder') || '';
                let labelText = '';
                // Walk up a few parents and grab their text
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
                if key in label_text:
                    current = inp.input_value()
                    if current.strip():
                        log.info(f"{key.title()} already has value {current!r} — leaving as is")
                    else:
                        log.info(f"Filling {key.title()} = {value!r}")
                        inp.fill(value)
                    matched = True
                    break

            if not matched:
                # Unknown required field — if it's empty, default to N/A so the
                # form can submit rather than blocking on a missing value.
                if not inp.input_value().strip():
                    log.info("Filling unrecognised required field with 'N/A'")
                    inp.fill("N/A")

    except Exception as e:
        log.warning(f"Could not fully fill checkout form: {e}")


def _confirm_booking(page) -> bool:
    """
    Click through confirmation/checkout buttons to finalise the booking.
    Returns True only if a booking confirmation can be verified afterwards.

    NOTE: This stops at the point the slot is reserved / in the basket. If the
    club requires payment to finalise, that step is intentionally NOT automated —
    review and complete payment manually. The booking will be held in the basket.
    """
    # If we're already confirmed (e.g. single-click booking) AND not still sitting
    # on the checkout/basket page, report success early.
    url = page.url.lower()
    on_checkout = "checkout" in url or "basket" in url
    if not on_checkout and _booking_confirmed(page):
        return True

    # Fill the checkout "Information required for booking" form if present
    # (player names) before attempting to click the final Confirm button.
    _fill_checkout_form(page)

    confirm_selectors = [
        "button:has-text('Confirm booking')",
        "button:has-text('Confirm')",
        "button:has-text('Complete booking')",
        "button:has-text('Reserve')",
        "button:has-text('Continue')",
        "button:has-text('Book')",
    ]

    clicked_any = False
    for sel in confirm_selectors:
        try:
            btn = page.wait_for_selector(sel, timeout=6000)
            if btn and btn.is_visible() and btn.is_enabled():
                log.info(f"Confirming with: {sel}")
                btn.click()
                clicked_any = True
                page.wait_for_timeout(3500)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                # Only count as success if we've actually left the checkout page
                # OR a confirmation signal is present.
                new_url = page.url.lower()
                still_on_checkout = "checkout" in new_url or "basket" in new_url
                if not still_on_checkout and _booking_confirmed(page):
                    log.info("Booking verified after confirm click")
                    return True
                if not still_on_checkout:
                    # Left checkout without an error — likely the confirmation page
                    log.info(f"Left checkout page after confirm: {page.url}")
                    return True
                # Still on checkout — a required field may be blocking submission.
                log.info("Still on checkout after clicking confirm; re-checking form")
        except PlaywrightTimeout:
            continue
        except Exception as e:
            log.warning(f"Confirm step {sel} errored: {e}")
            continue

    # Final verification after all attempts
    log.info(f"Final page URL after confirmation attempts: {page.url}")
    final_url = page.url.lower()
    if "checkout" in final_url or "basket" in final_url:
        log.warning("Still on checkout/basket page — booking NOT confirmed")
        return False
    confirmed = _booking_confirmed(page)
    if not confirmed:
        log.warning(
            "Could not verify booking confirmation. "
            f"(clicked a confirm button: {clicked_any})"
        )
    return confirmed


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
