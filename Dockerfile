FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# chromium pulls in its own runtime dependencies; fonts and CA certs are the only
# extras Selenium needs on top. (Do not add libgconf-2-4 — it no longer exists in
# Debian bookworm and its absence fails the whole apt step.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        fonts-liberation \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Where gn_ticket.py looks for the browser and driver.
ENV CHROME_BINARY=/usr/bin/chromium
ENV CHROMEDRIVER=/usr/bin/chromedriver

WORKDIR /app

# Pinned versions, so a deploy installs exactly what was tested.
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock

COPY . /app

# The one definition of how the web service starts. The cron job overrides this
# with `python run_scan.py` in render.yaml. Manual booking, where it is enabled,
# drives Chrome inside this process: measured peak is ~355 MB, so one worker with
# two threads is what a 512 MB instance can hold.
CMD gunicorn main:app --bind 0.0.0.0:${PORT:-10000} --timeout 120 --graceful-timeout 120 --workers 1 --threads 2
