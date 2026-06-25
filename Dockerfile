# Cloud Run container for the Lensbury padel booker.
# Uses the official Playwright image which already bundles headless Chromium
# and all the OS dependencies — much faster cold starts than installing at runtime.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt flask gunicorn

# App code
COPY booker/ ./booker/
COPY server.py .

# Cloud Run sets PORT (default 8080). Gunicorn serves the Flask app.
ENV PORT=8080
ENV HEADLESS=true

# Single worker, long timeout — a booking run with retries can take many minutes.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 1 --timeout 1800 server:app
