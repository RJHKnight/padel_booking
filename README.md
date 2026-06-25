# Lensbury Padel Court Auto-Booker

> **Two ways to run this:**
> - **GitHub Actions** (simple, free, but scheduled runs can be delayed 5–30 min)
> - **Cloud Run + Cloud Scheduler** (reliable ~1s trigger, fast session-reuse polling) — see [DEPLOY_CLOUDRUN.md](DEPLOY_CLOUDRUN.md). **Recommended for fast-moving slots.**


Automatically books a padel court at Lensbury Club via `flow.onl` every week, firing 
at slot-release time (08:00 UK) to get in first.

## How it works

1. GitHub Actions triggers on a cron schedule at ~07:58–08:00 UK time
2. Playwright launches a headless Chromium browser
3. Logs into your Flow account, navigates to the padel courts page
4. Selects the date 5 days ahead, clicks your target time slot
5. Confirms the booking
6. If the slot isn't live yet (race condition at 08:00), retries every 15s for up to 3 minutes
7. On failure, uploads a screenshot as a GitHub Actions artifact for debugging

---

## Setup

### 1. Fork / create the repo

Push this code to a GitHub repository (public or private — private is better for security).

### 2. Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name    | Value                              |
|----------------|------------------------------------|
| `FLOW_EMAIL`   | Your Lensbury/Flow login email     |
| `FLOW_PASSWORD`| Your password                      |
| `TARGET_TIME`  | e.g. `20:00` (8pm in 24h format)  |
| `COURT_PREF`   | e.g. `Court 1` or leave blank      |

### 3. Adjust the cron schedule

Edit `.github/workflows/book_padel.yml` — the `cron` lines control when the job fires.

Default is **every Tuesday at 07:58–07:59 UTC** (to book the slot 5 days ahead = Sunday).

To change the target day, update the day-of-week field (0=Sun, 1=Mon, 2=Tue … 6=Sat):
```yaml
- cron: '58 6 * * 2'  # 2 = Tuesday
```

### 4. Enable Actions

In your repo go to **Actions → Enable workflows** if prompted.

You can also trigger manually via **Actions → Book Padel Court → Run workflow**.

---

## Local testing

```bash
# Install deps
pip install -r requirements.txt
playwright install chromium

# Configure
cp .env.example .env
# Edit .env with your credentials

# Run (HEADLESS=false lets you watch the browser)
HEADLESS=false python scripts/book_court.py
```

---

## Customisation

| Variable       | Default | Description                                      |
|----------------|---------|--------------------------------------------------|
| `DAYS_AHEAD`   | `5`     | How far ahead to book                            |
| `TARGET_TIME`  | `20:00` | Time slot in 24h format                          |
| `COURT_PREF`   | _(any)_ | Preferred court name (blank = first available)   |
| `MAX_RETRIES`  | `12`    | Retry attempts if slot not yet live              |
| `RETRY_DELAY_S`| `15`    | Seconds between retries                          |

---

## Debugging

If a booking fails, GitHub Actions will upload a screenshot under  
**Actions → [failed run] → Artifacts → booking-error-screenshot**.

Download it to see exactly what state the browser was in.

---

## Important notes

- **Payment**: If Lensbury requires card payment at checkout, the script will need extending 
  to fill in payment details. Check if your membership covers court bookings — if so, 
  it likely just needs a "Confirm" click.
- **Flow.onl SPA**: The site is a React SPA. If the selectors break after a platform update, 
  run locally with `HEADLESS=false` to inspect and update the selectors in `scripts/book_court.py`.
- **BST/GMT**: The workflow runs at both 06:58 and 07:58 UTC to cover both UK time zones. 
  One run will be a no-op (slot already booked or not yet open).
